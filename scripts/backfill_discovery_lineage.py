#!/usr/bin/env python3
"""
Gently crawl the Discovery library index and persist lineage edges
(same inference as GET /api/discovery/asset-lineage?persist=1).

Fills ancestry for each row; descendants appear as a side-effect when children
are crawled (parent→child edges). Prefer this over hammering the API from the UI.

Paths mirror experiments_ui_server.main() — keep in sync when defaults change.

Examples:
  # Gentle one-shot batch (newest first, skip rows already in the graph as children)
  python3 scripts/backfill_discovery_lineage.py --gentle --batch-size 40

  # Resume across runs (checkpoint under output/_status/)
  python3 scripts/backfill_discovery_lineage.py --gentle --resume --batch-size 40

  # Continuous low-priority loop (sleep between items + between batches)
  python3 scripts/backfill_discovery_lineage.py --gentle --resume --loop --batch-size 25

  python3 scripts/backfill_discovery_lineage.py --max-depth 12 --limit 50
  python3 scripts/backfill_discovery_lineage.py --dry-run --embedded-only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import experiments_ui_server as eus  # noqa: E402


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def _default_state_path(cfg: eus.ServerConfig) -> Path:
    return eus._output_status_dir(cfg.output_root) / "lineage_backfill_state.json"


def _load_state(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {"schema_version": "comfyui-runpod.lineage-backfill-state.v0", "done": {}}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": "comfyui-runpod.lineage-backfill-state.v0", "done": {}}
    if not isinstance(doc, dict):
        return {"schema_version": "comfyui-runpod.lineage-backfill-state.v0", "done": {}}
    done = doc.get("done")
    if not isinstance(done, dict):
        doc["done"] = {}
    return doc


def _save_state(path: Path, doc: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = dict(doc)
    doc["updated_at"] = _utc_now()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _child_gids_in_graph(cfg: eus.ServerConfig) -> Set[str]:
    """group_ids that already appear as children in the persisted graph (parent-infer done at least once)."""
    path = eus._discovery_lineage_edges_path(cfg)
    out: Set[str] = set()
    if not path.is_file():
        return out
    try:
        doc = eus._discovery_load_lineage_graph(path)
    except Exception:
        return out
    edges = doc.get("edges") if isinstance(doc, dict) else None
    if not isinstance(edges, list):
        return out
    for e in edges:
        if not isinstance(e, dict):
            continue
        # Skip spurious edges if the helper exists (post-scrub crawls).
        look = getattr(eus, "_discovery_lineage_edge_looks_spurious", None)
        if callable(look) and look(e):
            continue
        cid = str(e.get("child_group_id") or "").strip()
        if cid and not cid.startswith("input:"):
            out.add(cid)
    return out


def _item_mtime(it: Dict[str, Any]) -> float:
    try:
        return float(it.get("mtime") or 0)
    except (TypeError, ValueError):
        return 0.0


def _should_skip_done(
    it: Dict[str, Any],
    *,
    done: Dict[str, Any],
    resume: bool,
) -> bool:
    if not resume:
        return False
    rel = _primary_relpath(it) or ""
    entry = done.get(rel)
    if not isinstance(entry, dict):
        return False
    try:
        prev_m = float(entry.get("mtime") or -1)
    except (TypeError, ValueError):
        prev_m = -1.0
    # Re-process when the library row's mtime moved (new companion / rewrite).
    return prev_m >= 0 and abs(prev_m - _item_mtime(it)) < 0.5


def _select_rows(
    items: List[Any],
    *,
    embedded_only: bool,
    prefer_missing: bool,
    known_children: Set[str],
    newest_first: bool,
    resume: bool,
    done: Dict[str, Any],
    limit: int,
    offset: int,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if embedded_only and "has_embedded_prompt" in it and it.get("has_embedded_prompt") is not True:
            continue
        rel = _primary_relpath(it)
        if not rel:
            continue
        if _should_skip_done(it, done=done, resume=resume):
            continue
        rows.append(it)

    def sort_key(it: Dict[str, Any]) -> Tuple[int, float, str]:
        gid = str(it.get("group_id") or "")
        miss_rank = 0 if (prefer_missing and gid not in known_children) else 1
        mtime = _item_mtime(it)
        rel = _primary_relpath(it) or ""
        return (miss_rank, -mtime if newest_first else mtime, rel)

    rows.sort(key=sort_key)
    if offset > 0:
        rows = rows[offset:]
    if limit and limit > 0:
        rows = rows[:limit]
    return rows


def _process_one(
    cfg: eus.ServerConfig,
    idx: Dict[str, Any],
    it: Dict[str, Any],
    *,
    max_depth: int,
    persist: bool,
    infer_children: bool = False,
) -> Tuple[bool, int, int, str]:
    """Returns (ok, session_edges, persisted_new, label)."""
    rel = str(it.get("relpath") or "").strip()
    gid = str(it.get("group_id") or "")
    label = rel or gid or "?"
    try:
        payload = eus._discovery_compute_asset_lineage(
            cfg,
            idx,
            rel,
            max_depth=int(max_depth),
            persist=persist,
            peek_group_id=None,
            infer_parents=True,
            infer_children=bool(infer_children),
        )
        # Always keep the inverted citation index warm for this child (skip if already scanned).
        try:
            eus._discovery_citations_index_child_item(cfg, it, force=False)
        except Exception:
            pass
    except Exception as e:
        return False, 0, 0, f"{label}: {e}"
    if not isinstance(payload, dict) or not payload.get("ok"):
        detail = payload.get("detail") if isinstance(payload, dict) else None
        err = payload.get("error") if isinstance(payload, dict) else "bad_payload"
        return False, 0, 0, f"{label}: {err} {detail or ''}".strip()
    edges = payload.get("edges")
    n_edges = len(edges) if isinstance(edges, list) else 0
    added = int(payload.get("persisted_new_edges") or 0)
    return True, n_edges, added, label


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Persist discovery lineage edges for many library rows (gentle / resumable crawler)."
    )
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
    ap.add_argument("--max-depth", type=int, default=0, help="Upstream BFS depth (0 = gentle default 2, else 12).")
    ap.add_argument("--limit", type=int, default=0, help="Process at most N rows this invocation (0 = all remaining).")
    ap.add_argument(
        "--batch-size",
        type=int,
        default=0,
        help="Alias for --limit when >0; useful with --loop.",
    )
    ap.add_argument("--offset", type=int, default=0, help="Skip the first N selected rows (after filters/sort).")
    ap.add_argument("--dry-run", action="store_true", help="Compute lineage but do not persist new edges.")
    ap.add_argument(
        "--embedded-only",
        action="store_true",
        help="Skip rows without has_embedded_prompt=True when that field exists.",
    )
    ap.add_argument(
        "--gentle",
        action="store_true",
        help="Sane crawl defaults: sleep between items, newest-first, prefer-missing, embedded-only, depth 2.",
    )
    ap.add_argument(
        "--sleep",
        type=float,
        default=-1.0,
        help="Seconds to sleep between items (−1 = 0.75 with --gentle, else 0).",
    )
    ap.add_argument(
        "--loop",
        action="store_true",
        help="After a batch/pass, sleep --loop-sleep and continue (CTRL+C to stop).",
    )
    ap.add_argument(
        "--loop-sleep",
        type=float,
        default=45.0,
        help="Seconds between loop iterations when the batch is empty or finished (default 45).",
    )
    ap.add_argument("--resume", action="store_true", help="Skip relpaths recorded in the checkpoint state file.")
    ap.add_argument(
        "--state-path",
        default="",
        help="Checkpoint JSON (default: <output>/_status/lineage_backfill_state.json).",
    )
    ap.add_argument(
        "--prefer-missing",
        action="store_true",
        help="Prioritize rows that are not yet a child in discovery_lineage_edges.json.",
    )
    ap.add_argument(
        "--newest-first",
        action="store_true",
        help="Sort by library mtime descending (after prefer-missing).",
    )
    ap.add_argument(
        "--infer-children",
        action="store_true",
        help="Also forward-fill via citation index (warm stem candidates on cold miss) and persist child edges.",
    )
    ap.add_argument(
        "--reset-state",
        action="store_true",
        help="Clear the checkpoint before starting.",
    )
    args = ap.parse_args()

    gentle = bool(args.gentle)
    if gentle:
        if not args.embedded_only:
            args.embedded_only = True
        if not args.prefer_missing:
            args.prefer_missing = True
        if not args.newest_first:
            args.newest_first = True
        if not args.resume:
            args.resume = True

    max_depth = int(args.max_depth)
    if max_depth <= 0:
        max_depth = 2 if gentle else 12

    sleep_s = float(args.sleep)
    if sleep_s < 0:
        sleep_s = 0.75 if gentle else 0.0

    limit = int(args.limit) or int(args.batch_size) or 0
    if gentle and limit <= 0 and not args.loop:
        # One gentle invocation without an explicit limit still processes everything,
        # but recommend batching via --batch-size when looping.
        pass

    base = Path(args.workspace_root) if args.workspace_root.strip() else _default_workspace_root()
    ws = eus._resolve_workspace_root(base)
    cfg = _build_cfg(ws, output_root_override=args.output_root)
    idx_path = cfg.discovery_index_path
    state_path = Path(args.state_path).expanduser() if args.state_path.strip() else _default_state_path(cfg)

    if args.reset_state and state_path.is_file():
        state_path.unlink()
        print(f"[backfill] reset state {state_path}")

    persist = not bool(args.dry_run)
    total_edges = 0
    total_persisted = 0
    failures = 0
    processed = 0
    loop_n = 0
    done: Dict[str, Any] = {}

    print(f"[backfill] workspace={cfg.workspace_root}")
    print(f"[backfill] discovery_index={idx_path}")
    print(f"[backfill] graph_out={eus._discovery_lineage_edges_path(cfg)}")
    print(
        f"[backfill] gentle={gentle} persist={persist} max_depth={max_depth} "
        f"sleep={sleep_s}s resume={bool(args.resume)} loop={bool(args.loop)} "
        f"infer_children={bool(args.infer_children)}"
    )
    print(f"[backfill] state={state_path}")

    try:
        while True:
            loop_n += 1
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

            state = _load_state(state_path)
            done = state.get("done") if isinstance(state.get("done"), dict) else {}
            known_children = _child_gids_in_graph(cfg) if args.prefer_missing else set()

            rows = _select_rows(
                items,
                embedded_only=bool(args.embedded_only),
                prefer_missing=bool(args.prefer_missing),
                known_children=known_children,
                newest_first=bool(args.newest_first),
                resume=bool(args.resume),
                done=done,
                limit=limit,
                offset=int(args.offset) if loop_n == 1 else 0,
            )

            print(
                f"[backfill] pass={loop_n} candidates={len(rows)} "
                f"known_child_gids={len(known_children)} checkpoint_done={len(done)}"
            )
            if not rows:
                if args.loop:
                    print(f"[backfill] idle — sleeping {args.loop_sleep}s (nothing left to do)")
                    time.sleep(max(0.0, float(args.loop_sleep)))
                    # Allow re-crawl of unchanged rows only if state was reset; otherwise keep idling.
                    continue
                break

            for i, it in enumerate(rows):
                ok, n_edges, added, label = _process_one(
                    cfg,
                    idx,
                    it,
                    max_depth=max_depth,
                    persist=persist,
                    infer_children=bool(args.infer_children),
                )
                processed += 1
                if not ok:
                    failures += 1
                    print(f"[backfill] FAIL {label}", file=sys.stderr)
                else:
                    total_edges += n_edges
                    total_persisted += added
                    rel = _primary_relpath(it) or label
                    done[rel] = {
                        "group_id": str(it.get("group_id") or ""),
                        "mtime": _item_mtime(it),
                        "persisted_new": added,
                        "session_edges": n_edges,
                        "at": _utc_now(),
                        "ok": True,
                    }
                    state["done"] = done
                    state["last_relpath"] = rel
                    if persist and not args.dry_run:
                        _save_state(state_path, state)

                if (i + 1) % 10 == 0 or i == 0 or i + 1 == len(rows):
                    print(
                        f"[backfill] progress {i + 1}/{len(rows)} "
                        f"last={label} edges={n_edges} persisted={added} …"
                    )

                if sleep_s > 0 and i + 1 < len(rows):
                    time.sleep(sleep_s)

            if not args.loop:
                break
            print(f"[backfill] batch done — sleeping {args.loop_sleep}s before next pass")
            time.sleep(max(0.0, float(args.loop_sleep)))

    except KeyboardInterrupt:
        print("\n[backfill] interrupted — checkpoint saved", file=sys.stderr)
        if persist and not args.dry_run:
            st = _load_state(state_path)
            prev = st.get("done") if isinstance(st.get("done"), dict) else {}
            prev.update(done)
            st["done"] = prev
            _save_state(state_path, st)
        return 130

    print(
        f"[backfill] done processed={processed} failures={failures} "
        f"sum_session_edges_reported={total_edges} sum_persisted_new={total_persisted}"
    )
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
