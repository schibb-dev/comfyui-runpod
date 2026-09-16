#!/usr/bin/env python3
"""Fix i2v-480p-Q5 unet paths and return affected jobs to pending."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ws = Path(__file__).resolve().parent
if str(_ws) not in sys.path:
    sys.path.insert(0, str(_ws))

from shape_factory import atomic_write_json, retry_failed_job_to_pending
from shape_factory_map import resolve_shape_factory_data_root
from shape_factory_stack import normalize_unet_name


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    data_root = resolve_shape_factory_data_root(repo_root=repo)
    jobs_dir = data_root / "shape_factory" / "jobs"
    server = str(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8188").rstrip("/")

    fixed: list[str] = []
    retried: list[tuple[str, str, dict]] = []
    skipped: list[tuple[str, str]] = []

    for path in sorted(jobs_dir.rglob("*.job.json")):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        sid = str(
            job.get("stack_id")
            or (job.get("stack") or {}).get("stack_id")
            or (job.get("adhoc_overrides") or {}).get("stack")
            or ""
        ).strip()
        if sid != "i2v-480p-Q5":
            continue

        job_key = str(job.get("job_key") or path.stem)
        stack = job.get("stack") if isinstance(job.get("stack"), dict) else {}
        new_unet = normalize_unet_name(str(stack.get("unet_name") or "wan2.1-i2v-14b-480p-Q5_K_M.gguf"))
        if stack.get("unet_name") != new_unet:
            stack["unet_name"] = new_unet
            job["stack"] = stack
            atomic_write_json(path, job)
            fixed.append(job_key)

        submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
        status = str(submit.get("status") or "").strip().lower()
        pid = str(submit.get("prompt_id") or "").strip()

        if status == "complete":
            skipped.append((job_key, "complete"))
            continue
        if status == "pending" and not pid:
            skipped.append((job_key, "pending"))
            continue

        res = retry_failed_job_to_pending(
            data_root=data_root,
            job_key=job_key,
            server=server,
            pending_position="append",
        )
        retried.append((job_key, status or "(none)", res))

    print(f"fixed_unet={len(fixed)}")
    for key in fixed:
        print(f"  fix {key}")
    print(f"retried={len(retried)}")
    for key, status, res in retried:
        ok = res.get("ok")
        detail = res.get("error") or res.get("detail") or res.get("status")
        print(f"  retry {key} from={status} ok={ok} detail={detail}")
    print(f"skipped={len(skipped)}")
    for key, reason in skipped:
        print(f"  skip {key} ({reason})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
