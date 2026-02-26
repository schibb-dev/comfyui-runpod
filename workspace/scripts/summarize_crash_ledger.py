#!/usr/bin/env python3
"""
Summarize the watch_queue crash ledger.

Reads JSONL events from:
  <experiments_root>/_crashes/crash_ledger.jsonl

Outputs counts grouped by (workflow_sha256, exp_id, kind).
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


def _iter_events(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            yield obj


def main() -> int:
    ap = argparse.ArgumentParser(description="Summarize workflow crashes from crash_ledger.jsonl")
    ap.add_argument(
        "--experiments-root",
        default="output/output/experiments",
        help="Experiments root directory (default: output/output/experiments)",
    )
    ap.add_argument("--limit", type=int, default=50, help="Max rows to print (default: 50)")
    args = ap.parse_args()

    exp_root = Path(args.experiments_root)
    ledger = exp_root / "_crashes" / "crash_ledger.jsonl"

    counts: Counter[Tuple[str, str, str]] = Counter()
    last_seen: Dict[Tuple[str, str, str], str] = {}

    for e in _iter_events(ledger):
        kind = str(e.get("kind") or "unknown")
        exp_id = str(e.get("exp_id") or "")
        wf = e.get("workflow_sha256")
        wf_sha = str(wf) if isinstance(wf, str) and wf else "unknown_workflow"
        k = (wf_sha, exp_id, kind)
        counts[k] += 1
        at = e.get("at")
        if isinstance(at, str):
            last_seen[k] = at

    rows: List[Tuple[int, Tuple[str, str, str]]] = sorted([(n, k) for k, n in counts.items()], reverse=True)
    if not rows:
        print(f"No events found at: {ledger}")
        return 0

    print(f"Ledger: {ledger}")
    print("count\tlast_seen\tkind\texp_id\tworkflow_sha256")
    for n, (wf_sha, exp_id, kind) in rows[: int(args.limit)]:
        print(f"{n}\t{last_seen.get((wf_sha, exp_id, kind), '')}\t{kind}\t{exp_id}\t{wf_sha}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

