#!/usr/bin/env python3
"""
Queue incomplete experiments: clear submit.json for runs no longer in ComfyUI queue, then run
the experiment queue manager once to submit eligible runs up to the experiment cap.

Submission is done by experiment_queue_manager.py (not tune_experiment). Safe to run repeatedly:
- Runs with history.json are not resubmitted.
- Runs with submit.json whose prompt_id is still in the ComfyUI queue are not resubmitted.
- Runs whose prompt_id is not in the queue get submit.json cleared, then the manager may resubmit.

Default experiments root: <repo>/workspace/output/output/experiments
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from experiment_queue_manager import run_one_iteration


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _prompt_id_from_submit(submit_path: Path) -> Optional[str]:
    try:
        obj = _read_json(submit_path)
        if not isinstance(obj, dict):
            return None
        pid = obj.get("prompt_id")
        return pid.strip() if isinstance(pid, str) and pid.strip() else None
    except Exception:
        return None


def fetch_queue_prompt_ids(server: str, timeout_s: int = 10) -> Optional[Set[str]]:
    """Return set of prompt_ids currently in ComfyUI queue (running + pending), or None on error."""
    try:
        req = urllib.request.Request(
            f"{server.rstrip('/')}/queue",
            headers={"Accept": "application/json"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            obj = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    out: Set[str] = set()
    for key in ("queue_running", "queue_pending"):
        items = obj.get(key)
        if not isinstance(items, list):
            continue
        for it in items:
            if isinstance(it, list) and len(it) >= 2 and isinstance(it[1], str) and it[1].strip():
                out.add(it[1].strip())
    return out


def _is_probably_repo_root(p: Path) -> bool:
    return (p / "workspace").is_dir() and (p / "workspace" / "scripts" / "tune_experiment.py").is_file()


def _resolve_repo_root() -> Path:
    here = Path(__file__).resolve()
    # .../<repo>/workspace/scripts/queue_incomplete_experiments.py
    repo = here.parents[2]
    if _is_probably_repo_root(repo):
        return repo
    # fallback: walk up a bit
    for parent in here.parents:
        if _is_probably_repo_root(parent):
            return parent
    raise RuntimeError(f"Could not locate repo root from {here}")


@dataclass
class ExpScan:
    exp_dir: Path
    exp_id: str
    total_runs: int
    missing_submit: int
    submitted_no_history: int


def _run_dir_from_manifest_dir_field(repo_root: Path, dir_field: str) -> Path:
    p = Path(dir_field)
    if p.is_absolute():
        return p
    # manifest often uses paths like "workspace\\output\\output\\experiments\\..."
    return (repo_root / p).resolve()


def scan_experiment(
    repo_root: Path,
    exp_dir: Path,
    queue_prompt_ids: Optional[Set[str]] = None,
    clear_submit_if_not_in_queue: bool = False,
) -> Optional[ExpScan]:
    """
    Scan experiment runs. If queue_prompt_ids is set and clear_submit_if_not_in_queue is True,
    runs that have submit.json but no history and whose prompt_id is not in the queue
    (e.g. job was canceled) get submit.json removed so they will be re-queued.
    """
    mf_path = exp_dir / "manifest.json"
    if not mf_path.is_file():
        return None
    try:
        mf = _read_json(mf_path)
    except Exception:
        return None
    if not isinstance(mf, dict):
        return None
    runs = mf.get("runs")
    if not isinstance(runs, list):
        return None

    exp_id = mf.get("exp_id") if isinstance(mf.get("exp_id"), str) else exp_dir.name
    total = 0
    missing_submit = 0
    submitted_no_history = 0

    for r in runs:
        if not isinstance(r, dict):
            continue
        dir_field = r.get("dir")
        if not isinstance(dir_field, str) or not dir_field.strip():
            continue
        run_dir = _run_dir_from_manifest_dir_field(repo_root, dir_field.strip())
        prompt_path = run_dir / "prompt.json"
        if not prompt_path.exists():
            continue
        total += 1
        submit_path = run_dir / "submit.json"
        hist_path = run_dir / "history.json"
        if hist_path.exists():
            continue
        if not submit_path.exists():
            missing_submit += 1
            continue
        # submit exists, no history
        prompt_id = _prompt_id_from_submit(submit_path)
        in_queue = queue_prompt_ids is not None and prompt_id is not None and prompt_id in queue_prompt_ids
        if not in_queue and clear_submit_if_not_in_queue and queue_prompt_ids is not None:
            try:
                submit_path.unlink()
            except OSError:
                pass
            missing_submit += 1
        else:
            submitted_no_history += 1

    return ExpScan(
        exp_dir=exp_dir,
        exp_id=exp_id,
        total_runs=total,
        missing_submit=missing_submit,
        submitted_no_history=submitted_no_history,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Queue incomplete experiments (missing submit.json) periodically-safe.")
    ap.add_argument("--experiments-root", default="", help="Root folder containing experiment dirs (default: workspace/output/output/experiments)")
    ap.add_argument("--server", default="http://127.0.0.1:8188", help="ComfyUI server URL")
    ap.add_argument("--max-experiments", type=int, default=0, help="Only process first N experiments (0=all)")
    ap.add_argument("--newest-first", action="store_true", help="Process newest experiment folders first (by mtime)")
    ap.add_argument("--dry-run", action="store_true", help="Print what would be queued but do not submit anything")
    ap.add_argument(
        "--collect-history",
        action="store_true",
        help="After queueing, wait+write history.json (may take a long time). Default is queue-only.",
    )
    args = ap.parse_args()

    repo_root = _resolve_repo_root()
    exp_root = Path(args.experiments_root) if args.experiments_root else (repo_root / "workspace" / "output" / "output" / "experiments")
    exp_root = exp_root.resolve()

    if not exp_root.is_dir():
        print(f"ERROR: experiments root not found: {exp_root}", file=sys.stderr)
        return 2

    exp_dirs = [p for p in exp_root.iterdir() if p.is_dir() and (p / "manifest.json").is_file()]
    if args.newest_first:
        exp_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    else:
        exp_dirs.sort(key=lambda p: p.name)

    if args.max_experiments and args.max_experiments > 0:
        exp_dirs = exp_dirs[: int(args.max_experiments)]

    # Fetch ComfyUI queue so we can treat "submitted but not in queue" (e.g. canceled) as needing re-queue.
    queue_prompt_ids: Optional[Set[str]] = fetch_queue_prompt_ids(str(args.server))
    if queue_prompt_ids is None:
        print("WARNING: could not fetch ComfyUI queue; only runs with no submit.json will be queued.", file=sys.stderr)
    else:
        print(f"comfy_queue_prompt_ids: {len(queue_prompt_ids)} (running+pending)")

    # Skip experiments marked as stopped (experiment_stopped sentinel file).
    STOPPED_SENTINEL = "experiment_stopped"
    # First pass: scan and clear submit.json for runs that are submitted but no longer in queue (canceled/lost).
    clear_submit = queue_prompt_ids is not None and not args.dry_run
    for d in exp_dirs:
        if (d / STOPPED_SENTINEL).exists():
            continue
        scan_experiment(
            repo_root,
            d,
            queue_prompt_ids=queue_prompt_ids,
            clear_submit_if_not_in_queue=clear_submit,
        )

    scans: List[ExpScan] = []
    for d in exp_dirs:
        if (d / STOPPED_SENTINEL).exists():
            continue
        s = scan_experiment(repo_root, d, queue_prompt_ids=queue_prompt_ids, clear_submit_if_not_in_queue=False)
        if s is not None:
            scans.append(s)

    need_queue = [s for s in scans if s.missing_submit > 0]
    total_missing = sum(s.missing_submit for s in need_queue)

    print(f"experiments_root: {exp_root}")
    print(f"experiments_total: {len(scans)}")
    print(f"experiments_need_queue: {len(need_queue)}")
    print(f"runs_missing_submit: {total_missing}")
    if need_queue:
        for s in need_queue:
            print(f"- {s.exp_id}: missing_submit={s.missing_submit} submitted_no_history={s.submitted_no_history} total_runs={s.total_runs}")

    if args.dry_run:
        return 0

    # Run queue manager once: submit eligible runs up to experiment cap.
    result = run_one_iteration(
        exp_root,
        repo_root,
        str(args.server),
        write_erq_path=exp_root / "_status" / "experiment_run_queue.json",
        dry_run=False,
    )
    print(f"manager: eligible={result['eligible']} cap={result['cap']} our_experiments_in_queue={result['our_experiment_count']} submitted={result['submitted']}")
    if args.collect_history:
        print("Note: --collect-history is not used by the queue manager; history is written when runs complete (e.g. via watch_queue or refresh_run_status).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

