#!/usr/bin/env python3
"""
CLI / drain entry for still auto-tagger (local Comfy or dry-run).

Prefer index-hour drain for GPU work:
  vision_still_tag_run.py --enqueue-only --limit 12
  vision_still_tag_drain.py --force --front --max-items 12

Example (legacy one-shot smoke — processes immediately):
  python3 workspace/scripts/vision_still_tag_run.py --dry-run --limit 12
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Batch-tag input stills via Comfy PromptGen-large")
    ap.add_argument("--data-root", default=None, help="Shape-factory data root (default SHAPE_FACTORY_DATA_ROOT / .data)")
    ap.add_argument("--status-dir", default=None, help="NDJSON audit dir (default output/_status)")
    ap.add_argument("--content-id", action="append", default=[], help="Repeatable content_id")
    ap.add_argument("--collection-id", default=None)
    ap.add_argument("--only-missing", action="store_true", default=True)
    ap.add_argument("--include-tagged", action="store_true", help="Disable only-missing")
    ap.add_argument("--force", action="store_true", help="Retag even if provisional exists")
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--provider", default="comfy", choices=["comfy", "runpod", "dry-run"])
    ap.add_argument("--comfy-server", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--run-id", default=None, help="Process an existing queued run_id only")
    ap.add_argument("--enqueue-only", action="store_true", help="Enqueue and exit (no GPU; index-hour default)")
    ap.add_argument(
        "--drain-now",
        action="store_true",
        help="After enqueue, kick immediate drain (smoke escape hatch)",
    )
    ap.add_argument("--front", action="store_true", help="When draining/processing, use Comfy front=true")
    args = ap.parse_args(argv)

    scripts = Path(__file__).resolve().parent
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))

    from vision_still_tags import (  # noqa: E402
        default_db_path,
        enqueue_run,
        kick_worker,
        process_run,
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

    if args.run_id:
        out = process_run(
            data_root=data_root,
            run_id=args.run_id,
            status_dir=status_dir,
            front=bool(args.front),
        )
        print(json.dumps(out, indent=2))
        return 0 if out.get("ok") else 1

    only_missing = False if args.include_tagged or args.force else bool(args.only_missing)
    enq = enqueue_run(
        data_root=data_root,
        content_ids=args.content_id or None,
        collection_id=args.collection_id,
        only_missing=only_missing,
        limit=int(args.limit),
        force=bool(args.force),
        provider="dry-run" if args.dry_run else args.provider,
        comfy_server=args.comfy_server,
        dry_run=bool(args.dry_run),
        status_dir=status_dir,
    )
    print(json.dumps({**enq, "db_path": str(default_db_path(data_root=data_root))}, indent=2))
    if not enq.get("ok"):
        return 1

    if args.enqueue_only:
        if should_auto_drain_on_enqueue(data_root=data_root, drain_now=bool(args.drain_now)):
            kick_worker(data_root=data_root, status_dir=status_dir, front=bool(args.front))
            print(json.dumps({"auto_drain_kicked": True}, indent=2))
        return 0

    # Legacy one-shot: process this run immediately (smoke / debug).
    out = process_run(
        data_root=data_root,
        run_id=enq["run_id"],
        status_dir=status_dir,
        front=bool(args.front),
    )
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
