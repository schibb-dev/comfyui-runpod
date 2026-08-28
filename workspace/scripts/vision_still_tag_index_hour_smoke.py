#!/usr/bin/env python3
"""
Demoable index-hour path without Florence GPU.

  enqueue (dry-run) → backlog grows → force drain (dry-run) → provisional tags

Usage:
  python3 workspace/scripts/vision_still_tag_index_hour_smoke.py --limit 3
  python3 workspace/scripts/vision_still_tag_index_hour_smoke.py --data-root /path/to/.data --limit 5
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Smoke: enqueue≠drain index-hour (dry-run)")
    ap.add_argument("--data-root", default=None)
    ap.add_argument("--status-dir", default=None)
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--force-retag", action="store_true")
    args = ap.parse_args(argv)

    scripts = Path(__file__).resolve().parent
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))

    from vision_still_tags import (  # noqa: E402
        backlog_stats,
        default_db_path,
        drain_backlog,
        enqueue_run,
        index_window_status,
        load_schedule,
        should_auto_drain_on_enqueue,
    )

    repo = scripts.parents[1]
    data_root = Path(
        args.data_root
        or os.environ.get("SHAPE_FACTORY_DATA_ROOT")
        or (repo / ".data")
    ).expanduser().resolve()
    status_dir = Path(
        args.status_dir
        or os.environ.get("VISION_STATUS_DIR")
        or "/home/yuji/comfyui-runpod-data/output/_status"
    ).expanduser()

    sch = load_schedule(data_root=data_root)
    win = index_window_status(sch)
    before = backlog_stats(data_root=data_root)
    print(
        json.dumps(
            {
                "step": "before",
                "data_root": str(data_root),
                "db_path": str(default_db_path(data_root=data_root)),
                "auto_drain_default": should_auto_drain_on_enqueue(data_root=data_root),
                "window": win,
                "backlog": before,
            },
            indent=2,
        )
    )

    enq = enqueue_run(
        data_root=data_root,
        only_missing=not args.force_retag,
        force=bool(args.force_retag),
        limit=max(1, int(args.limit)),
        dry_run=True,
        status_dir=status_dir,
    )
    mid = backlog_stats(data_root=data_root)
    print(
        json.dumps(
            {
                "step": "enqueued",
                "enqueue": enq,
                "queued_for_index_hour": True,
                "backlog": mid,
            },
            indent=2,
        )
    )
    if not enq.get("ok") or int(enq.get("enqueued") or 0) < 1:
        print(json.dumps({"ok": False, "error": "nothing_enqueued"}, indent=2))
        return 1

    drain = drain_backlog(
        data_root=data_root,
        status_dir=status_dir,
        force=True,
        respect_schedule=False,
        front=True,
        max_items=max(1, int(args.limit)),
        provider_override="dry-run",
    )
    after = backlog_stats(data_root=data_root)
    print(
        json.dumps(
            {
                "step": "drained",
                "drain": drain,
                "backlog": after,
                "ok": bool(drain.get("ok")) and not drain.get("skipped"),
            },
            indent=2,
        )
    )
    return 0 if drain.get("ok") and not drain.get("skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
