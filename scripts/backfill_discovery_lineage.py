#!/usr/bin/env python3
"""
Walk the Discovery library index and call the same lineage inference used by
GET /api/discovery/asset-lineage with persist=1 so discovery_lineage_edges.json
fills in for ancestry / siblings / descendants across the corpus.

Paths mirror experiments_ui_server.main() — keep in sync when defaults change.

Examples:
  python3 scripts/backfill_discovery_lineage.py --workspace-root /workspace
  python3 scripts/backfill_discovery_lineage.py --max-depth 12 --limit 50
  python3 scripts/backfill_discovery_lineage.py --dry-run --embedded-only
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import experiments_ui_server as eus  # noqa: E402


def _default_workspace_root() -> Path:
    env_ws = os.environ.get("WORKSPACE_PATH", "").strip()
    if env_ws:
        return Path(env_ws)
    bind_out = os.environ.get("COMFYUI_BIND_OUTPUT_DIR", "").strip()
    if bind_out:
        # Server layout: <ws>/output/output/_status/... ; bind mount is usually <ws>/output.
        return Path(bind_out).resolve().parent
    return Path(__file__).resolve().parent.parent


def _build_cfg(ws: Path, *, output_root_override: str = "") -> eus.ServerConfig:
    output_root = Path(output_root_override).resolve() if output_root_override else (ws / "output")
    experiments_root = eus._prefer_flat_library_dir(output_root, "experiments")
    wip_override = os.environ.get("EXPERIMENTS_UI_WIP_ROOT", "").strip()
    wip_root = eus._resolve_wip_root(ws, output_root, wip_override)
    discovery_index_path = eus._output_status_dir(output_root) / "discovery_og_wip_index.json"
    factory_db_path = Path(
        os.environ.get("SNOWFLAKE_FACTORY_DB", str(ws / "comfyui_user" / "default" / "snowflake_factory.sqlite"))
    )
    tune_script = ws / "scripts" / "tune_experiment.py"
    alt_tune = ws / "ws_scripts" / "tune_experiment.py"
    if not tune_script.exists() and alt_tune.exists():
        tune_script = alt_tune
    exp_status = eus._prefer_flat_library_dir(output_root, "experiments") / "_status"
    return eus.ServerConfig(
        workspace_root=ws,
        experiments_root=experiments_root,
        output_root=output_root,
        wip_root=wip_root,
        static_dir=ws / "experiments_ui" / "dist",
        tune_script=tune_script,
        comfy_server=os.environ.get("COMFYUI_SERVER", "http://127.0.0.1:8188"),
        orchestrator_state_path=ws / "output" / "orchestrator" / "state.json",
        queue_ledger_state_path=exp_status / "comfy_queue_ledger_state.json",
        queue_ledger_events_path=exp_status / "comfy_queue_ledger.jsonl",
        discovery_index_path=discovery_index_path,
        factory_db_path=factory_db_path,
        factory_browse_roots=eus._factory_browse_roots(ws, output_root),
    )


def _primary_relpath(item: Dict[str, Any]) -> Optional[str]:
    rel = item.get("relpath")
    if isinstance(rel, str) and rel.strip():
        return rel.strip()
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Persist discovery lineage edges for many library rows.")
    ap.add_argument(
        "--workspace-root",
        default="",
        help="Workspace root (default: WORKSPACE_PATH, else parent of COMFYUI_BIND_OUTPUT_DIR, else repo parent).",
    )
    ap.add_argument(
        "--output-root",
        default=os.environ.get("COMFYUI_BIND_OUTPUT_DIR", "").strip(),
        help="Output root for /files resolution (default: COMFYUI_BIND_OUTPUT_DIR or <workspace>/output).",
    )
    ap.add_argument("--max-depth", type=int, default=12, help="Upstream BFS depth (capped server-side).")
    ap.add_argument("--limit", type=int, default=0, help="Process at most N rows (0 = all).")
    ap.add_argument("--dry-run", action="store_true", help="Compute lineage but do not persist new edges.")
    ap.add_argument(
        "--embedded-only",
        action="store_true",
        help="Skip rows without has_embedded_prompt=True when that field exists.",
    )
    args = ap.parse_args()

    base = Path(args.workspace_root) if args.workspace_root.strip() else _default_workspace_root()
    ws = eus._resolve_workspace_root(base)
    cfg = _build_cfg(ws, output_root_override=args.output_root)
    idx_path = cfg.discovery_index_path
    idx = eus._load_discovery_index_disk(idx_path) if idx_path.exists() else None
    if not isinstance(idx, dict):
        print(f"[backfill] discovery index missing or invalid: {idx_path}", file=sys.stderr)
        print(
            "[backfill] hint: pass your bind-data root, e.g.\n"
            "  --workspace-root /home/yuji/comfyui-runpod-data\n"
            "or export COMFYUI_BIND_OUTPUT_DIR=/home/yuji/comfyui-runpod-data/output and re-run.",
            file=sys.stderr,
        )
        return 2

    items = idx.get("items")
    if not isinstance(items, list):
        print("[backfill] index has no items[]", file=sys.stderr)
        return 2

    rows: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if args.embedded_only and "has_embedded_prompt" in it and it.get("has_embedded_prompt") is not True:
            continue
        rel = _primary_relpath(it)
        if rel:
            rows.append(it)

    if args.limit and args.limit > 0:
        rows = rows[: int(args.limit)]

    persist = not bool(args.dry_run)
    total_edges = 0
    total_persisted = 0
    failures = 0

    print(f"[backfill] workspace={cfg.workspace_root}")
    print(f"[backfill] discovery_index={idx_path}")
    print(f"[backfill] graph_out={eus._discovery_lineage_edges_path(cfg)}")
    print(f"[backfill] rows={len(rows)} persist={persist} max_depth={args.max_depth}")

    for i, it in enumerate(rows):
        rel = str(it.get("relpath") or "").strip()
        gid = str(it.get("group_id") or "")
        label = rel or gid or f"#{i}"
        try:
            payload = eus._discovery_compute_asset_lineage(
                cfg,
                idx,
                rel,
                max_depth=int(args.max_depth),
                persist=persist,
                peek_group_id=None,
            )
        except Exception as e:
            failures += 1
            print(f"[backfill] FAIL {label}: {e}", file=sys.stderr)
            continue
        if not isinstance(payload, dict) or not payload.get("ok"):
            failures += 1
            detail = payload.get("detail") if isinstance(payload, dict) else None
            err = payload.get("error") if isinstance(payload, dict) else "bad_payload"
            print(f"[backfill] skip {label}: {err} {detail or ''}".strip(), file=sys.stderr)
            continue
        edges = payload.get("edges")
        n_edges = len(edges) if isinstance(edges, list) else 0
        total_edges += n_edges
        added = int(payload.get("persisted_new_edges") or 0)
        total_persisted += added
        if (i + 1) % 25 == 0 or i == 0:
            print(f"[backfill] progress {i + 1}/{len(rows)} last_edges={n_edges} last_persisted={added} …")

    print(
        f"[backfill] done rows_ok={len(rows) - failures} failures={failures} "
        f"sum_session_edges_reported={total_edges} sum_persisted_new={total_persisted}"
    )
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
