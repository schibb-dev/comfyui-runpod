#!/usr/bin/env python3
"""
Asset job worker — drain ``asset_job_queue.jsonl`` with catalog-driven stub handlers.

V2 phase C: stubs only (would_run). No GPU / Florence / CLIP compute.

  python3 scripts/asset_job_worker.py --status-dir /path/to/output/_status --once
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "workspace" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import asset_job_lib as aj  # noqa: E402


def _default_status_dir() -> Path:
    for cand in (
        Path("/home/yuji/comfyui-runpod-data/output/_status"),
        REPO / "workspace" / "output" / "_status",
    ):
        if cand.is_dir():
            return cand
    return Path("/home/yuji/comfyui-runpod-data/output/_status")


def drain_once(
    *,
    status_dir: Path,
    catalog_path: Path,
    job_types: list[str] | None,
    limit: int,
) -> dict:
    catalog = aj.load_catalog(catalog_path)
    allowed = set(aj.active_job_types(catalog, include_stub=True))
    types = [t for t in (job_types or sorted(allowed)) if t in allowed]
    queue_path = aj.default_queue_path(status_dir)
    state_path = aj.default_worker_state_path(status_dir)
    state = aj.load_worker_state(state_path)

    # Group by job_type so each stub handler gets a typed batch.
    pending = aj.read_batch(queue_path, state, job_types=types, limit=limit)
    if not pending:
        return {
            "ok": True,
            "drained": 0,
            "queue_depth": aj.queue_depth(queue_path),
            "types": types,
        }

    by_type: dict[str, list] = {}
    for row in pending:
        by_type.setdefault(str(row.get("job_type") or ""), []).append(row)

    results = []
    for jt, batch in by_type.items():
        spec = aj.catalog_job(catalog, jt) or {}
        status = str(spec.get("status") or "stub").lower()
        if status in {"deferred", "planned"}:
            results.append({"job_type": jt, "skipped": status, "n": len(batch)})
            continue
        stub = aj.run_stub_handler(jt, batch, status_dir=status_dir)
        results.append({"job_type": jt, "stub": stub, "n": len(batch)})

    state = aj.commit_cursor(state, pending)
    aj.save_worker_state(state_path, state)
    return {
        "ok": True,
        "drained": len(pending),
        "queue_depth": aj.queue_depth(queue_path),
        "types": types,
        "results": results,
        "offset": state.get("offset"),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--status-dir", type=Path, default=None)
    p.add_argument("--catalog", type=Path, default=None)
    p.add_argument("--job-types", default="", help="Comma-separated filter (default: all stub/active)")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--once", action="store_true", help="Drain one batch and exit (default)")
    args = p.parse_args(argv)

    status_dir = (args.status_dir or _default_status_dir()).expanduser().resolve()
    catalog_path = (args.catalog or aj.default_catalog_path(REPO)).expanduser().resolve()
    types = [t.strip() for t in str(args.job_types or "").split(",") if t.strip()] or None

    out = drain_once(
        status_dir=status_dir,
        catalog_path=catalog_path,
        job_types=types,
        limit=max(1, int(args.limit)),
    )
    print(json_dumps(out))
    return 0 if out.get("ok") else 1


def json_dumps(obj: dict) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
