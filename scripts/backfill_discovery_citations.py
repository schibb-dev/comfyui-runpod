#!/usr/bin/env python3
"""
Build / refresh the inverted discovery lineage citation index
(``output/_status/discovery_lineage_citations.sqlite``).

For each Discovery row with an embedded prompt, extract wired Load* paths that
feed a saved output and post them under every match key (basename, stem, …).
Forward-fill then becomes an O(children) SQLite lookup.

Examples:
  python3 scripts/backfill_discovery_citations.py --gentle --batch-size 80
  python3 scripts/backfill_discovery_citations.py --gentle --resume --loop --batch-size 50
  FORCE=1 python3 scripts/backfill_discovery_citations.py --force --batch-size 40
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Tuple

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import experiments_ui_server as eus  # noqa: E402
from backfill_discovery_lineage import (  # noqa: E402
    _build_cfg,
    _default_workspace_root,
    _item_mtime,
    _load_state,
    _primary_relpath,
    _save_state,
    _select_rows,
    _utc_now,
)


def _citations_state_path(cfg: eus.ServerConfig) -> Path:
    return eus._output_status_dir(cfg.output_root) / "lineage_citations_backfill_state.json"


def _process_one(
    cfg: eus.ServerConfig,
    it: Dict[str, Any],
    *,
    force: bool,
) -> Tuple[bool, Dict[str, Any], str]:
    rel = _primary_relpath(it) or str(it.get("group_id") or "?")
    try:
        result = eus._discovery_citations_index_child_item(cfg, it, force=force)
    except Exception as e:
        return False, {}, f"{rel}: {e}"
    if not isinstance(result, dict) or not result.get("ok"):
        return False, result if isinstance(result, dict) else {}, f"{rel}: index_failed"
    return True, result, rel


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill inverted discovery lineage citation index.")
    ap.add_argument("--workspace-root", default="")
    ap.add_argument(
        "--output-root",
        default=os.environ.get("COMFYUI_BIND_OUTPUT_DIR", "").strip(),
    )
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--batch-size", type=int, default=0)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--gentle", action="store_true")
    ap.add_argument("--sleep", type=float, default=-1.0)
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--loop-sleep", type=float, default=45.0)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--embedded-only", action="store_true")
    ap.add_argument("--newest-first", action="store_true")
    ap.add_argument("--force", action="store_true", help="Re-index rows even if already scanned.")
    ap.add_argument("--reset-state", action="store_true")
    ap.add_argument("--state-path", default="")
    args = ap.parse_args()

    gentle = bool(args.gentle)
    if gentle:
        args.embedded_only = True
        args.newest_first = True if not args.newest_first else True
        if not args.resume:
            args.resume = True

    sleep_s = float(args.sleep)
    if sleep_s < 0:
        sleep_s = 0.5 if gentle else 0.0

    limit = int(args.limit) or int(args.batch_size) or 0
    base = Path(args.workspace_root) if args.workspace_root.strip() else _default_workspace_root()
    ws = eus._resolve_workspace_root(base)
    cfg = _build_cfg(ws, output_root_override=args.output_root)
    idx_path = cfg.discovery_index_path
    state_path = Path(args.state_path).expanduser() if args.state_path.strip() else _citations_state_path(cfg)

    if args.reset_state and state_path.is_file():
        state_path.unlink()
        print(f"[citations] reset state {state_path}")

    print(f"[citations] workspace={cfg.workspace_root}")
    print(f"[citations] discovery_index={idx_path}")
    print(f"[citations] db={eus._discovery_citations_db_path(cfg)}")
    print(
        f"[citations] gentle={gentle} force={bool(args.force)} sleep={sleep_s}s "
        f"resume={bool(args.resume)} loop={bool(args.loop)}"
    )

    processed = 0
    indexed = 0
    skipped = 0
    failures = 0
    postings = 0
    loop_n = 0
    done: Dict[str, Any] = {}

    try:
        while True:
            loop_n += 1
            idx = eus._load_discovery_index_disk(idx_path) if idx_path.exists() else None
            if not isinstance(idx, dict):
                print(f"[citations] discovery index missing: {idx_path}", file=sys.stderr)
                return 2
            items = idx.get("items")
            if not isinstance(items, list):
                print("[citations] index has no items[]", file=sys.stderr)
                return 2

            state = _load_state(state_path)
            done = state.get("done") if isinstance(state.get("done"), dict) else {}
            rows = _select_rows(
                items,
                embedded_only=bool(args.embedded_only),
                prefer_missing=False,
                known_children=set(),
                newest_first=bool(args.newest_first) or gentle,
                resume=bool(args.resume) and not args.force,
                done=done,
                limit=limit,
                offset=int(args.offset) if loop_n == 1 else 0,
            )
            print(f"[citations] pass={loop_n} candidates={len(rows)} checkpoint_done={len(done)}")
            if not rows:
                if args.loop:
                    print(f"[citations] idle — sleeping {args.loop_sleep}s")
                    time.sleep(max(0.0, float(args.loop_sleep)))
                    continue
                break

            for i, it in enumerate(rows):
                ok, result, label = _process_one(cfg, it, force=bool(args.force))
                processed += 1
                if not ok:
                    failures += 1
                    print(f"[citations] FAIL {label}", file=sys.stderr)
                else:
                    if result.get("skipped"):
                        skipped += 1
                    else:
                        indexed += 1
                        postings += int(result.get("postings") or 0)
                    rel = _primary_relpath(it) or label
                    done[rel] = {
                        "group_id": str(it.get("group_id") or ""),
                        "mtime": _item_mtime(it),
                        "loader_count": result.get("loader_count"),
                        "postings": result.get("postings"),
                        "skipped": bool(result.get("skipped")),
                        "at": _utc_now(),
                        "ok": True,
                    }
                    state["done"] = done
                    state["last_relpath"] = rel
                    _save_state(state_path, state)

                if (i + 1) % 10 == 0 or i == 0 or i + 1 == len(rows):
                    print(
                        f"[citations] progress {i + 1}/{len(rows)} last={label} "
                        f"loaders={result.get('loader_count')} postings={result.get('postings')} "
                        f"skipped={result.get('skipped')}"
                    )
                if sleep_s > 0 and i + 1 < len(rows):
                    time.sleep(sleep_s)

            if not args.loop:
                break
            print(f"[citations] batch done — sleeping {args.loop_sleep}s")
            time.sleep(max(0.0, float(args.loop_sleep)))
    except KeyboardInterrupt:
        print("\n[citations] interrupted — checkpoint saved", file=sys.stderr)
        st = _load_state(state_path)
        prev = st.get("done") if isinstance(st.get("done"), dict) else {}
        prev.update(done)
        st["done"] = prev
        _save_state(state_path, st)
        return 130

    print(
        f"[citations] done processed={processed} indexed={indexed} skipped={skipped} "
        f"failures={failures} postings_written={postings} db={eus._discovery_citations_db_path(cfg)}"
    )
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
