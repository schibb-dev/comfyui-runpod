#!/usr/bin/env python3
"""
Mark one or more experiments as stopped so they are no longer scheduled for running.

When an experiment is stopped:
- queue_incomplete_experiments (and ops) will skip it.
- tune_experiment.py run <exp_dir> will refuse to submit and exit with a message.

To stop: creates a sentinel file "experiment_stopped" in each experiment directory.
To resume: use --remove to delete the sentinel so the experiment can be queued again.

Usage:
  python stop_experiment.py <exp_dir> [<exp_dir> ...]
  python stop_experiment.py --experiments-root DIR --exp-id tune_xxx
  python stop_experiment.py --remove <exp_dir>   # allow scheduling again
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SENTINEL = "experiment_stopped"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Stop experiments from being scheduled (or --remove to allow again)."
    )
    ap.add_argument(
        "exp_dirs",
        nargs="*",
        help="Experiment directory path(s) (e.g. output/output/experiments/tune_xxx_20260222-123456)",
    )
    ap.add_argument(
        "--experiments-root",
        default="",
        help="Root containing experiments (used with --exp-id). Default: workspace/output/output/experiments",
    )
    ap.add_argument(
        "--exp-id",
        default="",
        help="Single experiment id (folder name) to stop under --experiments-root.",
    )
    ap.add_argument(
        "--remove",
        action="store_true",
        help="Remove the stopped marker so the experiment can be scheduled again.",
    )
    args = ap.parse_args()

    script_dir = Path(__file__).resolve().parent
    repo = script_dir.parent.parent
    workspace = repo / "workspace"
    default_root = workspace / "output" / "output" / "experiments"

    dirs: list[Path] = []
    for d in args.exp_dirs:
        p = Path(d)
        if not p.is_absolute():
            p = (repo / "workspace" / p) if (repo / "workspace" / p).exists() else p
        dirs.append(p.resolve())
    if args.exp_id:
        root = (
            (Path(args.experiments_root) if Path(args.experiments_root).is_absolute() else workspace / args.experiments_root)
            if args.experiments_root
            else default_root
        )
        dirs.append((root / args.exp_id).resolve())

    if not dirs:
        print("Give at least one experiment directory or --exp-id with --experiments-root.", file=sys.stderr)
        return 1

    for exp_dir in dirs:
        if not exp_dir.is_dir():
            print(f"Skip (not a directory): {exp_dir}", file=sys.stderr)
            continue
        sentinel = exp_dir / SENTINEL
        if args.remove:
            if sentinel.exists():
                sentinel.unlink()
                print(f"Resumed: {exp_dir.name} (removed {SENTINEL})")
            else:
                print(f"No change: {exp_dir.name} (not stopped)")
        else:
            if sentinel.exists():
                print(f"Already stopped: {exp_dir.name}")
            else:
                sentinel.write_text("", encoding="utf-8")
                print(f"Stopped: {exp_dir.name} (created {SENTINEL})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
