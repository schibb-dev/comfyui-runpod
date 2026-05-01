#!/usr/bin/env python3
"""
Experiment Run Queue manager: scan experiments, apply rules, submit runs to ComfyUI up to experiment cap.

Default: at most EXPERIMENT_QUEUE_MAX_RUNS (12) distinct experiments can have a run in the queue.
Cap is read each iteration (e.g. from env or config file) so it can be adjusted at runtime.
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from comfyui_submit import submit_run_to_comfyui
from experiment_run_queue_rules import order_runs

STOPPED_SENTINEL = "experiment_stopped"
ERQ_SCHEMA = 1


def _read_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def _run_dir_from_manifest_dir_field(repo_root: Path, dir_field: str) -> Path:
    p = Path(dir_field)
    if p.is_absolute():
        return p
    return (repo_root / p).resolve()


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
    """Return set of prompt_ids in ComfyUI queue (running + pending), or None on error."""
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


def populate_eligible_runs(
    experiments_root: Path,
    repo_root: Path,
    *,
    queue_prompt_ids: Optional[Set[str]] = None,
    clear_submit_if_not_in_queue: bool = False,
) -> List[Dict[str, Any]]:
    """
    Scan experiments root; return list of run descriptors for eligible runs.

    Eligible: has prompt.json, no history.json, experiment not stopped.
    If queue_prompt_ids is set and a run has submit.json with prompt_id in queue, exclude it.
    If clear_submit_if_not_in_queue and run has submit.json but prompt_id not in queue, remove submit.json.
    """
    entries: List[Dict[str, Any]] = []
    exp_dirs = [p for p in experiments_root.iterdir() if p.is_dir() and (p / "manifest.json").is_file()]
    for exp_dir in exp_dirs:
        if (exp_dir / STOPPED_SENTINEL).exists():
            continue
        mf_path = exp_dir / "manifest.json"
        try:
            mf = _read_json(mf_path)
        except Exception:
            continue
        if not isinstance(mf, dict):
            continue
        runs = mf.get("runs")
        if not isinstance(runs, list):
            continue
        exp_id = mf.get("exp_id") if isinstance(mf.get("exp_id"), str) else exp_dir.name
        for r in runs:
            if not isinstance(r, dict):
                continue
            run_id = r.get("run_id")
            dir_field = r.get("dir")
            if not isinstance(run_id, str) or not isinstance(dir_field, str) or not dir_field.strip():
                continue
            run_dir = _run_dir_from_manifest_dir_field(repo_root, dir_field.strip())
            prompt_path = run_dir / "prompt.json"
            if not prompt_path.exists():
                continue
            hist_path = run_dir / "history.json"
            if hist_path.exists():
                continue
            submit_path = run_dir / "submit.json"
            if submit_path.exists():
                pid = _prompt_id_from_submit(submit_path)
                if queue_prompt_ids is not None and pid is not None and pid in queue_prompt_ids:
                    continue
                if clear_submit_if_not_in_queue and queue_prompt_ids is not None and (pid is None or pid not in queue_prompt_ids):
                    try:
                        submit_path.unlink()
                    except OSError:
                        pass
            entries.append({"exp_id": exp_id, "run_id": run_id, "run_dir": run_dir})
    return entries


def get_experiment_cap() -> int:
    """Current cap (max distinct experiments in queue). Default from env EXPERIMENT_QUEUE_MAX_RUNS=12."""
    val = os.environ.get("EXPERIMENT_QUEUE_MAX_RUNS") or os.environ.get("EXPERIMENT_QUEUE_MAX_EXPERIMENTS")
    if val is not None:
        try:
            return max(0, int(val))
        except ValueError:
            pass
    return 12


def run_one_iteration(
    experiments_root: Path,
    repo_root: Path,
    server: str,
    *,
    experiment_cap: Optional[int] = None,
    write_erq_path: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    One manager iteration: scan, order, fetch queue, submit up to cap.
    Returns a small stats dict (submitted, cap, etc.).
    """
    cap = experiment_cap if experiment_cap is not None else get_experiment_cap()
    queue_prompt_ids = fetch_queue_prompt_ids(server)
    if queue_prompt_ids is None:
        queue_prompt_ids = set()
    entries = populate_eligible_runs(
        experiments_root,
        repo_root,
        queue_prompt_ids=queue_prompt_ids,
        clear_submit_if_not_in_queue=True,
    )
    if os.environ.get("ERQ_DEBUG"):
        print(f"DEBUG populate_eligible_runs -> {len(entries)} entries", file=__import__("sys").stderr)
    ordered = order_runs(entries, {"experiments_root": str(experiments_root), "server": server})

    if write_erq_path is not None:
        write_erq_path.parent.mkdir(parents=True, exist_ok=True)
        erq = {
            "schema": ERQ_SCHEMA,
            "entries": [{"exp_id": e["exp_id"], "run_id": e["run_id"], "run_dir": str(e["run_dir"])} for e in ordered],
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        with open(write_erq_path, "w", encoding="utf-8") as f:
            json.dump(erq, f, indent=2)

    # Count distinct experiments currently in queue (our runs only)
    in_queue_by_exp: Set[str] = set()
    for e in ordered:
        submit_path = Path(e["run_dir"]) / "submit.json"
        if submit_path.exists():
            pid = _prompt_id_from_submit(submit_path)
            if pid and pid in queue_prompt_ids:
                in_queue_by_exp.add(e["exp_id"])
    our_experiment_count = len(in_queue_by_exp)

    submitted = 0
    for entry in ordered:
        if our_experiment_count >= cap:
            break
        run_dir = Path(entry["run_dir"])
        prompt_path = run_dir / "prompt.json"
        if not prompt_path.exists():
            continue
        submit_path = run_dir / "submit.json"
        if submit_path.exists():
            pid = _prompt_id_from_submit(submit_path)
            if pid and pid in (queue_prompt_ids or set()):
                if entry["exp_id"] not in in_queue_by_exp:
                    in_queue_by_exp.add(entry["exp_id"])
                    our_experiment_count += 1
                continue
        if dry_run:
            if entry["exp_id"] not in in_queue_by_exp:
                our_experiment_count += 1
                in_queue_by_exp.add(entry["exp_id"])
            submitted += 1
            continue
        try:
            pid = submit_run_to_comfyui(prompt_path, run_dir, server)
            if pid:
                if entry["exp_id"] not in in_queue_by_exp:
                    in_queue_by_exp.add(entry["exp_id"])
                    our_experiment_count += 1
                submitted += 1
                queue_prompt_ids = (queue_prompt_ids or set()) | {pid}
        except Exception:
            continue

    return {
        "eligible": len(ordered),
        "cap": cap,
        "our_experiment_count": our_experiment_count,
        "submitted": submitted,
    }


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Experiment queue manager: submit eligible runs to ComfyUI up to cap.")
    ap.add_argument("--experiments-root", default="", help="Root of experiment dirs (default: workspace/output/output/experiments)")
    ap.add_argument("--server", default="http://127.0.0.1:8188", help="ComfyUI server URL")
    ap.add_argument("--cap", type=int, default=None, help="Max experiments in queue (default: env EXPERIMENT_QUEUE_MAX_RUNS or 12)")
    ap.add_argument("--write-erq", nargs="?", const=True, default=None, metavar="PATH", help="Write ERQ snapshot (default: experiments_root/_status/experiment_run_queue.json)")
    ap.add_argument("--dry-run", action="store_true", help="Do not submit, only report what would be done")
    args = ap.parse_args()

    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent.parent
    exp_root = Path(args.experiments_root) if args.experiments_root else (repo_root / "workspace" / "output" / "output" / "experiments")
    exp_root = exp_root.resolve()
    if not exp_root.is_dir():
        print(f"ERROR: experiments root not found: {exp_root}", file=__import__("sys").stderr)
        return 2
    # Manifest run "dir" paths are relative to workspace (e.g. output\output\experiments\...).
    repo_root = exp_root.parent.parent.parent

    write_path: Optional[Path] = None
    if args.write_erq:
        write_path = Path(args.write_erq).resolve() if isinstance(args.write_erq, str) else exp_root / "_status" / "experiment_run_queue.json"

    result = run_one_iteration(
        exp_root,
        repo_root,
        args.server,
        experiment_cap=args.cap,
        write_erq_path=write_path,
        dry_run=args.dry_run,
    )
    print(f"eligible={result['eligible']} cap={result['cap']} our_experiments_in_queue={result['our_experiment_count']} submitted={result['submitted']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
