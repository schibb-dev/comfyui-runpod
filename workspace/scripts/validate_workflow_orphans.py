#!/usr/bin/env python3
"""
List **litegraph** workflow nodes that have no link endpoints (fully disconnected in ``links``).

Cheap O(nodes + links). Use in CI with ``--strict`` to fail on disconnections that look like real
compute nodes (KSampler, RandomNoise, etc.).

Examples::

  python workspace/scripts/validate_workflow_orphans.py workspace/comfyui_user/default/workflows/FB9-pose-start.json
  python workspace/scripts/validate_workflow_orphans.py --strict path/to/a.json path/to/b.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from workflow_litegraph_health import (
    classify_disconnected_nodes,
    disconnected_litegraph_nodes,
    format_disconnected_report,
    load_workflow_json,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Warn about Comfy litegraph nodes with no link connections")
    ap.add_argument("paths", nargs="+", type=Path, help="Workflow JSON files (litegraph format)")
    ap.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if any disconnected node is a warn-class type (KSampler, RandomNoise, ...)",
    )
    args = ap.parse_args()

    exit_code = 0
    for path in args.paths:
        if not path.is_file():
            print(f"{path}: NOT FOUND", file=sys.stderr)
            exit_code = 1
            continue
        try:
            data = load_workflow_json(path)
        except (OSError, json.JSONDecodeError) as e:
            print(f"{path}: {e}", file=sys.stderr)
            exit_code = 1
            continue
        rows = disconnected_litegraph_nodes(data)
        print(f"\n## {path.as_posix()}")
        if not rows:
            print("  (no fully disconnected nodes)")
            continue
        warn, _low, _other = classify_disconnected_nodes(rows)
        for line in format_disconnected_report(rows, indent="  "):
            print(line)
        if args.strict and warn:
            print(f"  **strict: {len(warn)} warn-class orphan(s)**", file=sys.stderr)
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
