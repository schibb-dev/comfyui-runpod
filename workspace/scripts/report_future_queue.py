#!/usr/bin/env python3
"""
Report the "future" queue: what needs to be queued, what is actually queued (ComfyUI),
and the order runs will be executed. Suitable for UI/API consumption via --json.

States:
- needs_queued: run has no submit.json (and no history) — will be submitted by queue_incomplete_experiments
- queued: run has submit.json but no history — either in ComfyUI queue (pending/running) or completed but history not yet written
- done: run has history.json

With --server, fetches ComfyUI /queue to report running and pending order; prompt_id from
submit.json is used to correlate experiment runs with queue position.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

STOPPED_SENTINEL = "experiment_stopped"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _http_json(method: str, url: str, data: Any = None, timeout_s: int = 10) -> Any:
    req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8") if data else None, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return json.loads(r.read().decode("utf-8"))


def _is_repo_root(p: Path) -> bool:
    return (p / "workspace").is_dir() and (p / "workspace" / "scripts" / "tune_experiment.py").is_file()


def _resolve_repo_root() -> Path:
    here = Path(__file__).resolve()
    repo = here.parents[2] if here.name == "report_future_queue.py" else here.parent
    if _is_repo_root(repo):
        return repo
    for parent in here.parents:
        if _is_repo_root(parent):
            return parent
    raise RuntimeError(f"Could not locate repo root from {here}")


def _run_dir_from_manifest(repo_root: Path, dir_field: str) -> Path:
    p = Path(dir_field)
    if p.is_absolute():
        return p
    return (repo_root / p).resolve()


def _read_prompt_id(submit_path: Path) -> Optional[str]:
    try:
        obj = _read_json(submit_path)
        pid = obj.get("prompt_id") if isinstance(obj, dict) else None
        return pid if isinstance(pid, str) and pid.strip() else None
    except Exception:
        return None


def scan_experiment_runs(
    repo_root: Path,
    exp_dir: Path,
    exp_id: str,
) -> List[Dict[str, Any]]:
    """Return list of run dicts: exp_id, run_id, run_dir (str), state, prompt_id (if submitted)."""
    mf_path = exp_dir / "manifest.json"
    if not mf_path.is_file():
        return []
    try:
        mf = _read_json(mf_path)
    except Exception:
        return []
    if not isinstance(mf, dict):
        return []
    runs = mf.get("runs")
    if not isinstance(runs, list):
        return []

    out: List[Dict[str, Any]] = []
    for r in runs:
        if not isinstance(r, dict):
            continue
        dir_field = r.get("dir")
        run_id = r.get("run_id")
        if not isinstance(dir_field, str) or not dir_field.strip() or not isinstance(run_id, str):
            continue
        run_dir = _run_dir_from_manifest(repo_root, dir_field.strip())
        if not (run_dir / "prompt.json").exists():
            continue
        submit_path = run_dir / "submit.json"
        hist_path = run_dir / "history.json"
        prompt_id = _read_prompt_id(submit_path) if submit_path.exists() else None

        if hist_path.exists():
            state = "done"
        elif submit_path.exists():
            state = "queued"
        else:
            state = "needs_queued"

        out.append({
            "exp_id": exp_id,
            "run_id": run_id,
            "run_dir": str(run_dir),
            "state": state,
            "prompt_id": prompt_id,
        })
    return out


def fetch_comfy_queue(server: str, timeout_s: int = 10) -> Tuple[List[Tuple[int, str, str]], Optional[str]]:
    """
    Fetch /queue from ComfyUI. Returns (ordered_list, error).
    ordered_list: [(position, status, prompt_id), ...] with status "running" or "pending".
    """
    try:
        q = _http_json("GET", f"{server.rstrip('/')}/queue", None, timeout_s=timeout_s)
    except Exception as e:
        return [], str(e)
    if not isinstance(q, dict):
        return [], "queue response not a dict"
    result: List[Tuple[int, str, str]] = []
    pos = 0
    for key, status in (("queue_running", "running"), ("queue_pending", "pending")):
        arr = q.get(key)
        if not isinstance(arr, list):
            continue
        for item in arr:
            if isinstance(item, list) and len(item) >= 2 and isinstance(item[1], str):
                result.append((pos, status, item[1]))
                pos += 1
    return result, None


def build_report(
    repo_root: Path,
    exp_root: Path,
    server: Optional[str] = None,
    queue_timeout_s: int = 10,
) -> Dict[str, Any]:
    exp_dirs = [p for p in exp_root.iterdir() if p.is_dir() and (p / "manifest.json").is_file()]
    exp_dirs.sort(key=lambda p: p.name)

    all_runs: List[Dict[str, Any]] = []
    exp_id_from_name: Dict[str, str] = {}
    for exp_dir in exp_dirs:
        if (exp_dir / STOPPED_SENTINEL).exists():
            continue
        mf_path = exp_dir / "manifest.json"
        try:
            mf = _read_json(mf_path)
            exp_id = mf.get("exp_id") if isinstance(mf.get("exp_id"), str) else exp_dir.name
        except Exception:
            exp_id = exp_dir.name
        exp_id_from_name[exp_dir.name] = exp_id
        runs = scan_experiment_runs(repo_root, exp_dir, exp_id)
        all_runs.extend(runs)

    needs_queued = [r for r in all_runs if r["state"] == "needs_queued"]
    queued_runs = [r for r in all_runs if r["state"] == "queued"]
    done_count = sum(1 for r in all_runs if r["state"] == "done")

    prompt_id_to_run: Dict[str, Dict[str, Any]] = {}
    for r in queued_runs:
        pid = r.get("prompt_id")
        if isinstance(pid, str) and pid.strip():
            prompt_id_to_run[pid] = {"exp_id": r["exp_id"], "run_id": r["run_id"], "run_dir": r["run_dir"]}

    queue_order: List[Dict[str, Any]] = []
    queue_error: Optional[str] = None
    queue_prompt_ids: set = set()
    if server:
        order_list, queue_error = fetch_comfy_queue(server, timeout_s=queue_timeout_s)
        for position, status, prompt_id in order_list:
            queue_prompt_ids.add(prompt_id)
            entry: Dict[str, Any] = {
                "position": position,
                "status": status,
                "prompt_id": prompt_id,
            }
            if prompt_id in prompt_id_to_run:
                entry["exp_id"] = prompt_id_to_run[prompt_id]["exp_id"]
                entry["run_id"] = prompt_id_to_run[prompt_id]["run_id"]
                entry["run_dir"] = prompt_id_to_run[prompt_id]["run_dir"]
            queue_order.append(entry)

    # Runs with submit.json but no history: in queue vs submitted but not in queue (canceled/lost).
    submitted_not_in_queue: List[Dict[str, Any]] = []
    queued_with_in_queue: List[Dict[str, Any]] = []
    for r in queued_runs:
        pid = r.get("prompt_id")
        in_queue = bool(server and isinstance(pid, str) and pid.strip() and pid in queue_prompt_ids)
        item = {
            "exp_id": r["exp_id"],
            "run_id": r["run_id"],
            "run_dir": r["run_dir"],
            "prompt_id": pid,
        }
        if server:
            item["in_queue"] = in_queue
        queued_with_in_queue.append(item)
        if server and not in_queue:
            submitted_not_in_queue.append(item)

    return {
        "experiments_root": str(exp_root),
        "server": server,
        "queue_error": queue_error,
        "summary": {
            "needs_queued_count": len(needs_queued),
            "queued_count": len(queued_runs),
            "in_queue_count": len(queue_prompt_ids),
            "submitted_not_in_queue_count": len(submitted_not_in_queue),
            "running_count": sum(1 for e in queue_order if e.get("status") == "running"),
            "pending_count": sum(1 for e in queue_order if e.get("status") == "pending"),
            "done_count": done_count,
            "total_runs": len(all_runs),
        },
        "needs_queued": [{"exp_id": r["exp_id"], "run_id": r["run_id"], "run_dir": r["run_dir"]} for r in needs_queued],
        "queued": queued_with_in_queue,
        "submitted_not_in_queue": submitted_not_in_queue,
        "queue_order": queue_order,
        "experiments_stopped_skipped": sum(1 for p in exp_root.iterdir() if p.is_dir() and (p / "manifest.json").is_file() and (p / STOPPED_SENTINEL).exists()),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Report future queue: what needs queuing, what is queued, and execution order."
    )
    ap.add_argument(
        "--experiments-root",
        default="",
        help="Root folder containing experiment dirs (default: workspace/output/output/experiments)",
    )
    ap.add_argument(
        "--server",
        default="http://127.0.0.1:8188",
        help="ComfyUI server URL to fetch /queue (use empty to skip queue fetch)",
    )
    ap.add_argument(
        "--no-server",
        action="store_true",
        help="Do not fetch ComfyUI queue; only report needs_queued from disk.",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        default=False,
        dest="output_json",
        help="Output JSON for UI/API consumption.",
    )
    ap.add_argument("--indent", type=int, default=2, help="JSON indent (default 2)")
    ap.add_argument("--queue-timeout", type=int, default=10, help="Timeout in seconds for /queue request")
    args = ap.parse_args()

    repo_root = _resolve_repo_root()
    exp_root = Path(args.experiments_root) if args.experiments_root else (repo_root / "workspace" / "output" / "output" / "experiments")
    exp_root = exp_root.resolve()

    if not exp_root.is_dir():
        print(f"ERROR: experiments root not found: {exp_root}", file=sys.stderr)
        return 2

    server = None if args.no_server else args.server
    report = build_report(
        repo_root=repo_root,
        exp_root=exp_root,
        server=server,
        queue_timeout_s=args.queue_timeout,
    )

    if args.output_json:
        print(json.dumps(report, indent=args.indent))
        return 0

    # Human-readable
    s = report["summary"]
    print(f"Experiments root: {report['experiments_root']}")
    if report.get("experiments_stopped_skipped"):
        print(f"Stopped experiments skipped: {report['experiments_stopped_skipped']}")
    print(f"Summary: needs_queued={s['needs_queued_count']} queued(no history)={s['queued_count']} done={s['done_count']} total={s['total_runs']}")
    if server:
        if report.get("queue_error"):
            print(f"Queue fetch error: {report['queue_error']}")
        else:
            print(f"ComfyUI queue: running={s['running_count']} pending={s['pending_count']}")
            if s.get("submitted_not_in_queue_count", 0) > 0:
                print(f"Submitted but NOT in queue (canceled/lost): {s['submitted_not_in_queue_count']} (re-queued by queue_incomplete_experiments)")
    print()
    if report["needs_queued"]:
        print("Needs queued (no submit.json yet):")
        for r in report["needs_queued"][:50]:
            print(f"  {r['exp_id']} / {r['run_id']}")
        if len(report["needs_queued"]) > 50:
            print(f"  ... and {len(report['needs_queued']) - 50} more")
        print()
    queued = report.get("queued") or []
    if queued:
        print("Queued (submitted, no history yet) - actual jobs:")
        for r in queued[:100]:
            pid = r.get("prompt_id") or "-"
            in_q = " [in queue]" if r.get("in_queue") else " [NOT in queue]"
            print(f"  {r['exp_id']} / {r['run_id']}  prompt_id={pid}{in_q}")
        if len(queued) > 100:
            print(f"  ... and {len(queued) - 100} more")
        print()
    submitted_not = report.get("submitted_not_in_queue") or []
    if submitted_not:
        print("Submitted but NOT in queue (canceled/lost - re-queued by queue_incomplete_experiments):")
        for r in submitted_not[:50]:
            pid = r.get("prompt_id") or "-"
            print(f"  {r['exp_id']} / {r['run_id']}  prompt_id={pid}")
        if len(submitted_not) > 50:
            print(f"  ... and {len(submitted_not) - 50} more")
        print()
    if report["queue_order"]:
        print("ComfyUI queue order (will run in this order):")
        for e in report["queue_order"]:
            exp_run = f"{e.get('exp_id', '?')} / {e.get('run_id', '?')}" if e.get("exp_id") else f"prompt_id={e.get('prompt_id', '?')}"
            print(f"  [{e['position']}] {e['status']}: {exp_run}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
