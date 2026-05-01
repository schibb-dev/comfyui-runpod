#!/usr/bin/env python3
"""
Incremental status refresher for experiments.

Writes <run_dir>/status.json for each run, based on:
- history.json / submit.json presence
- submit.json prompt_id being present in ComfyUI /queue (pending/running)

This gives you "incremental" visibility without waiting for history.json to be collected.

Status schema (v1):
{
  "schema": 1,
  "updated_at": "2026-02-07T22:15:00Z",
  "server": "http://127.0.0.1:8188",
  "exp_id": "...",
  "run_id": "run_001",
  "prompt_id": "...",
  "phase": "done|running|queued|submitted|not_queued",
  "queue_state": "running|pending|none",
  "history_present": true/false,
  "submit_present": true/false
}
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


def _utc_iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_if_changed(path: Path, obj: Dict[str, Any]) -> bool:
    raw = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        prev = path.read_text(encoding="utf-8") if path.exists() else ""
    except Exception:
        prev = ""
    if prev == raw:
        return False
    path.write_text(raw, encoding="utf-8")
    return True


def _http_json(url: str, *, timeout_s: int = 10) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def fetch_queue_prompt_ids(server: str) -> Tuple[Set[str], Set[str]]:
    """
    Returns (pending_prompt_ids, running_prompt_ids).
    """
    server = server.rstrip("/")
    obj = _http_json(f"{server}/queue", timeout_s=10)
    pending: Set[str] = set()
    running: Set[str] = set()
    if isinstance(obj, dict):
        for key, out in (("queue_pending", pending), ("queue_running", running)):
            items = obj.get(key)
            if not isinstance(items, list):
                continue
            for it in items:
                if isinstance(it, list) and len(it) >= 2 and isinstance(it[1], str) and it[1].strip():
                    out.add(it[1].strip())
    return pending, running


def _resolve_repo_root() -> Path:
    here = Path(__file__).resolve()
    repo = here.parents[2]  # .../<repo>/workspace/scripts/...
    if (repo / "workspace" / "scripts" / "tune_experiment.py").exists():
        return repo
    # fallback: walk up
    for parent in here.parents:
        if (parent / "workspace" / "scripts" / "tune_experiment.py").exists():
            return parent
    raise RuntimeError(f"Could not locate repo root from {here}")


def _prompt_id_from_submit(submit_path: Path) -> Optional[str]:
    try:
        obj = _read_json(submit_path)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    pid = obj.get("prompt_id")
    return pid.strip() if isinstance(pid, str) and pid.strip() else None


def main() -> int:
    ap = argparse.ArgumentParser(description="Refresh per-run status.json from live ComfyUI /queue.")
    ap.add_argument("--experiments-root", default="", help="Experiments root (default: workspace/output/output/experiments)")
    ap.add_argument("--server", default="http://127.0.0.1:8188", help="ComfyUI server URL")
    ap.add_argument("--newest-first", action="store_true", help="Process newest experiment folders first (by mtime)")
    ap.add_argument("--limit-experiments", type=int, default=0, help="Only process first N experiments (0=all)")
    ap.add_argument("--dry-run", action="store_true", help="Compute but do not write status.json")
    args = ap.parse_args()

    repo = _resolve_repo_root()
    exp_root = Path(args.experiments_root) if args.experiments_root else (repo / "workspace" / "output" / "output" / "experiments")
    exp_root = exp_root.resolve()
    if not exp_root.is_dir():
        raise SystemExit(f"experiments root not found: {exp_root}")

    try:
        pending, running = fetch_queue_prompt_ids(str(args.server))
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise SystemExit(f"failed to read {str(args.server).rstrip('/')}/queue: {e}")

    exp_dirs = [p for p in exp_root.iterdir() if p.is_dir() and (p / "manifest.json").exists()]
    if args.newest_first:
        exp_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    else:
        exp_dirs.sort(key=lambda p: p.name)
    if args.limit_experiments and args.limit_experiments > 0:
        exp_dirs = exp_dirs[: int(args.limit_experiments)]

    now = time.time()
    writes = 0
    runs = 0

    for exp_dir in exp_dirs:
        mf_path = exp_dir / "manifest.json"
        try:
            mf = _read_json(mf_path) if mf_path.exists() else {}
        except Exception:
            mf = {}
        exp_id = mf.get("exp_id") if isinstance(mf, dict) and isinstance(mf.get("exp_id"), str) else exp_dir.name

        runs_dir = exp_dir / "runs"
        if not runs_dir.is_dir():
            continue
        for run_dir in sorted([p for p in runs_dir.iterdir() if p.is_dir() and p.name.startswith("run_")], key=lambda p: p.name):
            prompt_path = run_dir / "prompt.json"
            if not prompt_path.exists():
                continue
            runs += 1
            run_id = run_dir.name
            hist_path = run_dir / "history.json"
            submit_path = run_dir / "submit.json"
            status_path = run_dir / "status.json"

            history_present = hist_path.exists()
            submit_present = submit_path.exists()
            prompt_id = _prompt_id_from_submit(submit_path) if submit_present else None

            if history_present:
                phase = "done"
                queue_state = "none"
            elif prompt_id and prompt_id in running:
                phase = "running"
                queue_state = "running"
            elif prompt_id and prompt_id in pending:
                phase = "queued"
                queue_state = "pending"
            elif submit_present:
                phase = "submitted"
                queue_state = "none"
            else:
                phase = "not_queued"
                queue_state = "none"

            obj = {
                "schema": 1,
                "updated_at": _utc_iso(now),
                "server": str(args.server).rstrip("/"),
                "exp_id": exp_id,
                "run_id": run_id,
                "prompt_id": prompt_id,
                "phase": phase,
                "queue_state": queue_state,
                "history_present": bool(history_present),
                "submit_present": bool(submit_present),
            }

            if args.dry_run:
                continue
            try:
                changed = _write_json_if_changed(status_path, obj)
            except Exception:
                continue
            if changed:
                writes += 1

    print(f"experiments_root: {exp_root}")
    print(f"server: {str(args.server).rstrip('/')}")
    print(f"runs_scanned: {runs}")
    print(f"status_writes: {writes}")
    print(f"queue: pending={len(pending)} running={len(running)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

