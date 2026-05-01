#!/usr/bin/env python3
"""
Report experiment run statuses and correlate with the live ComfyUI queue.

Statuses per run (derived from on-disk artifacts + live /queue):
- done: history.json exists
- running: submit.json exists AND its prompt_id is in /queue.queue_running
- queued: submit.json exists AND its prompt_id is in /queue.queue_pending
- submitted: submit.json exists but prompt_id is not in running/pending and history.json missing
- not_queued: no submit.json and no history.json (but prompt.json exists)

Notes:
- If you used --no-wait, many runs will be "submitted" until you later collect history.json.
- ComfyUI's /queue includes jobs not launched by these experiments; we estimate overlap via prompt_id.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_probably_repo_root(p: Path) -> bool:
    return (p / "workspace").is_dir() and (p / "workspace" / "scripts" / "tune_experiment.py").is_file()


def _resolve_repo_root() -> Path:
    here = Path(__file__).resolve()
    repo = here.parents[2]  # .../<repo>/workspace/scripts/...
    if _is_probably_repo_root(repo):
        return repo
    for parent in here.parents:
        if _is_probably_repo_root(parent):
            return parent
    raise RuntimeError(f"Could not locate repo root from {here}")


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
                # ComfyUI shape: [queue_id:int, prompt_id:str, prompt:dict]
                if isinstance(it, list) and len(it) >= 2 and isinstance(it[1], str) and it[1].strip():
                    out.add(it[1].strip())
    return pending, running


def _run_dir_from_manifest_dir_field(repo_root: Path, dir_field: str) -> Path:
    p = Path(dir_field)
    if p.is_absolute():
        return p
    return (repo_root / p).resolve()


def _prompt_id_from_submit(path: Path) -> Optional[str]:
    try:
        obj = _read_json(path)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    pid = obj.get("prompt_id")
    return pid.strip() if isinstance(pid, str) and pid.strip() else None


@dataclass
class ExpCounts:
    exp_id: str
    exp_dir: Path
    created_at: str
    base_mp4: str
    phase: str
    total: int
    done: int
    running: int
    queued: int
    submitted: int
    not_queued: int
    submitted_no_pid: int
    missing_prompt: int


def scan_experiment(*, repo_root: Path, exp_dir: Path, pending: Set[str], running: Set[str]) -> Optional[ExpCounts]:
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
    created_at = mf.get("created_at") if isinstance(mf.get("created_at"), str) else ""
    base_mp4 = mf.get("base_mp4") if isinstance(mf.get("base_mp4"), str) else ""

    done = running_n = queued_n = submitted = not_queued = submitted_no_pid = missing_prompt = 0
    total = 0

    for r in runs:
        if not isinstance(r, dict):
            continue
        run_id = r.get("run_id")
        run_dir: Optional[Path] = None
        if isinstance(run_id, str) and run_id.strip():
            # Prefer the directory relative to THIS experiment folder, even if the manifest's
            # stored absolute/relative path is from an older root.
            run_dir = exp_dir / "runs" / run_id.strip()
        else:
            dir_field = r.get("dir")
            if isinstance(dir_field, str) and dir_field.strip():
                run_dir = _run_dir_from_manifest_dir_field(repo_root, dir_field.strip())
        if run_dir is None:
            continue
        prompt_path = run_dir / "prompt.json"
        if not prompt_path.exists():
            missing_prompt += 1
            continue
        total += 1

        hist_path = run_dir / "history.json"
        if hist_path.exists():
            done += 1
            continue

        submit_path = run_dir / "submit.json"
        if submit_path.exists():
            pid = _prompt_id_from_submit(submit_path)
            if not pid:
                submitted_no_pid += 1
            elif pid in running:
                running_n += 1
            elif pid in pending:
                queued_n += 1
            else:
                submitted += 1
            continue

        not_queued += 1

    # Exclusive phase (same priority order used in the summary).
    if running_n > 0:
        phase = "running"
    elif queued_n > 0:
        phase = "queued"
    elif not_queued > 0:
        phase = "not_queued"
    elif (submitted > 0) or (submitted_no_pid > 0):
        phase = "submitted"
    elif total > 0 and done == total:
        phase = "done"
    else:
        phase = "empty"

    return ExpCounts(
        exp_id=str(exp_id),
        exp_dir=exp_dir,
        created_at=created_at,
        base_mp4=base_mp4,
        phase=phase,
        total=total,
        done=done,
        running=running_n,
        queued=queued_n,
        submitted=submitted,
        not_queued=not_queued,
        submitted_no_pid=submitted_no_pid,
        missing_prompt=missing_prompt,
    )


def _fmt_row(cols: List[str], widths: List[int]) -> str:
    return "  ".join(c.ljust(w) for c, w in zip(cols, widths))


def _fmt_duration_s(sec: float) -> str:
    try:
        s = int(max(0, sec))
    except Exception:
        return "unknown"
    h = s // 3600
    m = (s % 3600) // 60
    ss = s % 60
    if h > 999:
        return f"{h}h"
    return f"{h:02d}:{m:02d}:{ss:02d}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Report experiment status counts and correlate with ComfyUI queue.")
    ap.add_argument("--experiments-root", default="", help="Root folder containing experiment dirs")
    ap.add_argument("--server", default="http://127.0.0.1:8188", help="ComfyUI server URL")
    ap.add_argument("--newest-first", action="store_true", help="Sort experiments by folder mtime desc")
    ap.add_argument("--limit", type=int, default=0, help="Only show first N experiments (0=all)")
    ap.add_argument("--show-base", action="store_true", help="Include base_mp4 (may be long)")
    ap.add_argument("--summary-only", action="store_true", help="Only print experiment-level summary counts")
    args = ap.parse_args()

    repo_root = _resolve_repo_root()
    exp_root = Path(args.experiments_root) if args.experiments_root else (repo_root / "workspace" / "output" / "output" / "experiments")
    exp_root = exp_root.resolve()
    if not exp_root.is_dir():
        print(f"ERROR: experiments root not found: {exp_root}", file=sys.stderr)
        return 2

    try:
        pending, running = fetch_queue_prompt_ids(str(args.server))
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        print(f"ERROR: failed to read {str(args.server).rstrip('/')}/queue: {e}", file=sys.stderr)
        return 3

    exp_dirs = [p for p in exp_root.iterdir() if p.is_dir() and (p / "manifest.json").is_file()]
    if args.newest_first:
        exp_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    else:
        exp_dirs.sort(key=lambda p: p.name)

    if args.limit and args.limit > 0:
        exp_dirs = exp_dirs[: int(args.limit)]

    counts: List[ExpCounts] = []
    # Track prompt_ids that appear in submit.json across all experiments, to estimate queue overlap.
    submitted_pids: Set[str] = set()
    # Best-effort map of prompt_id -> run info (for running duration reporting).
    pid_to_run: Dict[str, Dict[str, Any]] = {}

    for d in exp_dirs:
        c = scan_experiment(repo_root=repo_root, exp_dir=d, pending=pending, running=running)
        if c is None:
            continue
        counts.append(c)

        # best-effort pid collection (cheap: look at submit.json for runs; no need to parse manifest again)
        runs_dir = d / "runs"
        if runs_dir.is_dir():
            for run_dir in runs_dir.iterdir():
                sp = run_dir / "submit.json"
                if sp.is_file():
                    pid = _prompt_id_from_submit(sp)
                    if pid:
                        submitted_pids.add(pid)
                        # Submitted timestamp heuristic (prefer metrics.json if present).
                        submitted_ts: Optional[float] = None
                        submitted_src = "submit.json_mtime"
                        mp = run_dir / "metrics.json"
                        if mp.is_file():
                            try:
                                mobj = _read_json(mp)
                                if isinstance(mobj, dict) and isinstance(mobj.get("submitted_ts"), (int, float)):
                                    submitted_ts = float(mobj["submitted_ts"])
                                    submitted_src = "metrics.json"
                            except Exception:
                                pass
                        # Prefer WS-derived timing sources when available.
                        active_started_ts: Optional[float] = None
                        active_src: Optional[str] = None
                        exec_started_ts: Optional[float] = None
                        exec_src: Optional[str] = None
                        if mp.is_file():
                            try:
                                mobj2 = _read_json(mp)
                                if isinstance(mobj2, dict):
                                    if isinstance(mobj2.get("active_started_ts"), (int, float)):
                                        active_started_ts = float(mobj2["active_started_ts"])
                                        active_src = (
                                            str(mobj2.get("active_started_ts_source"))
                                            if isinstance(mobj2.get("active_started_ts_source"), str)
                                            else "metrics.json"
                                        )
                                    if isinstance(mobj2.get("exec_started_ts"), (int, float)):
                                        exec_started_ts = float(mobj2["exec_started_ts"])
                                        exec_src = (
                                            str(mobj2.get("exec_started_ts_source"))
                                            if isinstance(mobj2.get("exec_started_ts_source"), str)
                                            else "metrics.json"
                                        )
                            except Exception:
                                pass
                        if submitted_ts is None:
                            try:
                                submitted_ts = float(sp.stat().st_mtime)
                            except Exception:
                                submitted_ts = None
                        pid_to_run[pid] = {
                            "exp_id": c.exp_id,
                            "run_id": run_dir.name,
                            "run_dir": str(run_dir),
                            "submitted_ts": submitted_ts,
                            "submitted_ts_source": submitted_src,
                            "active_started_ts": active_started_ts,
                            "active_started_ts_source": active_src,
                            "exec_started_ts": exec_started_ts,
                            "exec_started_ts_source": exec_src,
                        }

    # Overlap between ComfyUI queue and experiments.
    queue_pending = len(pending)
    queue_running = len(running)
    queue_total = queue_pending + queue_running
    in_queue_from_exps = len((pending | running) & submitted_pids)

    # Totals across experiments.
    tot = ExpCounts(
        exp_id="(TOTAL)",
        exp_dir=exp_root,
        created_at="",
        base_mp4="",
        phase="",
        total=sum(c.total for c in counts),
        done=sum(c.done for c in counts),
        running=sum(c.running for c in counts),
        queued=sum(c.queued for c in counts),
        submitted=sum(c.submitted for c in counts),
        not_queued=sum(c.not_queued for c in counts),
        submitted_no_pid=sum(c.submitted_no_pid for c in counts),
        missing_prompt=sum(c.missing_prompt for c in counts),
    )

    print(f"experiments_root: {exp_root}")
    print(f"experiments_count: {len(counts)}")
    print(f"comfy_queue: pending={queue_pending} running={queue_running} total={queue_total}")
    print(f"comfy_queue_overlap_with_experiments: {in_queue_from_exps} prompt_ids")

    # Running job durations (best-effort) for prompt_ids we can map to experiments.
    now = time.time()
    if running:
        running_infos: List[Tuple[float, str, Dict[str, Any]]] = []
        not_mapped = 0
        for pid in sorted(running):
            info = pid_to_run.get(pid)
            if not isinstance(info, dict):
                not_mapped += 1
                continue
            # Prefer WS-derived active start, then WS exec start, then submitted time fallback.
            ts = info.get("active_started_ts")
            src = info.get("active_started_ts_source")
            if not isinstance(ts, (int, float)):
                ts = info.get("exec_started_ts")
                src = info.get("exec_started_ts_source")
            if not isinstance(ts, (int, float)):
                ts = info.get("submitted_ts")
                src = info.get("submitted_ts_source")
            age = (now - float(ts)) if isinstance(ts, (int, float)) else 0.0
            info2 = dict(info)
            info2["running_for_source"] = src
            running_infos.append((age, pid, info2))

        print("")
        print("running_jobs:")
        print(f"  total: {len(running)}")
        print(f"  mapped_to_experiments: {len(running_infos)}")
        print(f"  not_mapped_to_experiments: {not_mapped}")
        # Show top few by age.
        running_infos.sort(key=lambda t: t[0], reverse=True)
        for age, pid, info in running_infos[:5]:
            exp_id = info.get("exp_id")
            run_id = info.get("run_id")
            src = info.get("running_for_source")
            print(f"  - {exp_id}/{run_id}: running_for={_fmt_duration_s(age)} (prompt_id={pid}, since={src})")

    # Experiment-level summary: many experiments can have multiple statuses; we provide both
    # "has any X" counters and an exclusive phase distribution (priority order).
    exps_total = len(counts)
    exps_empty = sum(1 for c in counts if c.total == 0)
    exps_all_done = sum(1 for c in counts if c.total > 0 and c.done == c.total)
    exps_any_done = sum(1 for c in counts if c.done > 0)
    exps_any_running = sum(1 for c in counts if c.running > 0)
    exps_any_queued = sum(1 for c in counts if c.queued > 0)
    exps_any_not_queued = sum(1 for c in counts if c.not_queued > 0)
    exps_any_submitted = sum(1 for c in counts if c.submitted > 0 or c.submitted_no_pid > 0)

    phase = {
        "running": 0,
        "queued": 0,
        "not_queued": 0,
        "submitted": 0,
        "done": 0,
        "empty": 0,
    }
    for c in counts:
        if c.running > 0:
            phase["running"] += 1
        elif c.queued > 0:
            phase["queued"] += 1
        elif c.not_queued > 0:
            phase["not_queued"] += 1
        elif (c.submitted > 0) or (c.submitted_no_pid > 0):
            phase["submitted"] += 1
        elif c.total > 0 and c.done == c.total:
            phase["done"] += 1
        else:
            phase["empty"] += 1

    print("")
    print("experiment_summary_any:")
    print(f"  any_running:   {exps_any_running} / {exps_total}")
    print(f"  any_queued:    {exps_any_queued} / {exps_total}")
    print(f"  any_submitted: {exps_any_submitted} / {exps_total}")
    print(f"  any_not_queued:{exps_any_not_queued} / {exps_total}")
    print(f"  any_done:      {exps_any_done} / {exps_total}")
    print(f"  all_done:      {exps_all_done} / {exps_total}")
    print(f"  empty:         {exps_empty} / {exps_total}")
    print("")
    print("experiment_phase_exclusive (priority: running>queued>not_queued>submitted>done>empty):")
    for k in ["running", "queued", "not_queued", "submitted", "done", "empty"]:
        print(f"  {k}: {phase[k]}")

    if args.summary_only:
        return 0
    print("")

    headers = ["exp_id", "phase", "total", "done", "running", "queued", "submitted", "not_queued", "sub_no_pid", "no_prompt"]
    if args.show_base:
        headers += ["created_at", "base_mp4"]
    rows: List[List[str]] = []

    def add_row(c: ExpCounts) -> None:
        r = [
            c.exp_id,
            c.phase,
            str(c.total),
            str(c.done),
            str(c.running),
            str(c.queued),
            str(c.submitted),
            str(c.not_queued),
            str(c.submitted_no_pid),
            str(c.missing_prompt),
        ]
        if args.show_base:
            r += [c.created_at, c.base_mp4]
        rows.append(r)

    add_row(tot)
    for c in counts:
        add_row(c)

    widths = [len(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(cell))

    print(_fmt_row(headers, widths))
    print(_fmt_row(["-" * w for w in widths], widths))
    for r in rows:
        print(_fmt_row(r, widths))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

