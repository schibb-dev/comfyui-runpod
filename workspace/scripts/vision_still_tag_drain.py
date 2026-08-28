#!/usr/bin/env python3
"""
Index-hour / ops drain for still auto-tagger.

Gallery enqueue builds a SQLite backlog; this command burns it on Comfy
(prefer front-of-queue) inside the configured window — or immediately with --force.

Examples:
  python3 workspace/scripts/vision_still_tag_drain.py --respect-schedule
  python3 workspace/scripts/vision_still_tag_drain.py --force --front --max-items 24 \\
      --comfy-server http://127.0.0.1:8188
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Drain still-tag backlog (index hour)")
    ap.add_argument("--data-root", default=None)
    ap.add_argument("--status-dir", default=None)
    ap.add_argument("--respect-schedule", action="store_true", help="No-op outside enabled window")
    ap.add_argument("--force", action="store_true", help="Ignore schedule window/enabled")
    ap.add_argument("--front", action="store_true", default=None, help="Comfy front=true (default: schedule)")
    ap.add_argument("--no-front", action="store_true", help="Disable front even if schedule says front")
    ap.add_argument("--max-items", type=int, default=None)
    ap.add_argument("--until-minutes", type=float, default=None)
    ap.add_argument("--comfy-server", default=None)
    ap.add_argument("--provider", default=None, choices=["comfy", "runpod", "dry-run"])
    ap.add_argument("--show-schedule", action="store_true")
    ap.add_argument("--show-backlog", action="store_true")
    args = ap.parse_args(argv)

    scripts = Path(__file__).resolve().parent
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))

    from vision_still_tags import (  # noqa: E402
        backlog_stats,
        default_db_path,
        drain_backlog,
        index_window_status,
        load_schedule,
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

    if args.show_schedule or args.show_backlog:
        sch = load_schedule(data_root=data_root)
        payload: dict = {
            "db_path": str(default_db_path(data_root=data_root)),
            "schedule": sch,
            "window": index_window_status(sch),
        }
        if args.show_backlog:
            payload["backlog"] = backlog_stats(data_root=data_root)
        print(json.dumps(payload, indent=2))
        return 0

    if not args.force and not args.respect_schedule:
        ap.error("pass --respect-schedule (cron) or --force (ops drain now)")

    front: bool | None
    if args.no_front:
        front = False
    elif args.front:
        front = True
    else:
        front = None

    out = drain_backlog(
        data_root=data_root,
        status_dir=status_dir,
        force=bool(args.force),
        respect_schedule=bool(args.respect_schedule) and not bool(args.force),
        front=front,
        max_items=args.max_items,
        until_minutes=args.until_minutes,
        provider_override=args.provider,
        comfy_server_override=args.comfy_server,
    )
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
