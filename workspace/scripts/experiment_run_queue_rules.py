#!/usr/bin/env python3
"""
Pluggable rules for the Experiment Run Queue: ordering and (future) dispositions.

Reference implementation: flat FIFO — sort by (experiment mtime, exp_id, run_id).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List


def order_runs(entries: List[Dict[str, Any]], context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Return the same run descriptors, reordered for submission. No filtering.

    Flat FIFO: sort by (experiment_created_or_mtime, exp_id, run_id).
    Each entry must have at least exp_id, run_id, and run_dir (Path or str).
    """
    if not entries:
        return []

    def exp_mtime(entry: Dict[str, Any]) -> float:
        run_dir = entry.get("run_dir")
        if run_dir is None:
            return 0.0
        try:
            p = Path(run_dir) if isinstance(run_dir, str) else run_dir
            # exp_dir is parent of "runs", so run_dir -> runs -> exp_id
            exp_dir = p.parent.parent if p.name else p.parent
            if exp_dir.is_dir():
                return float(exp_dir.stat().st_mtime)
        except (OSError, TypeError):
            pass
        return 0.0

    def sort_key(entry: Dict[str, Any]) -> tuple:
        return (
            exp_mtime(entry),
            str(entry.get("exp_id", "")),
            str(entry.get("run_id", "")),
        )

    return sorted(entries, key=sort_key)
