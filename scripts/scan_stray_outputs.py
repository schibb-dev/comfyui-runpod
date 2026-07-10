#!/usr/bin/env python3
"""
Scan for recent media written to nested / legacy output locations.

Typical stray path: <bind>/output/og/... when canonical flat layout is <bind>/og/...
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO_ROOT / "workspace" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from output_path_lib import scan_stray_outputs  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--repo-root",
        type=Path,
        default=_REPO_ROOT,
        help="Repository root (default: parent of scripts/)",
    )
    ap.add_argument(
        "--since-hours",
        type=float,
        default=48.0,
        help="Only report media newer than this many hours (default: 48)",
    )
    ap.add_argument("--json", action="store_true", help="Emit JSON report on stdout")
    ap.add_argument("--max-files", type=int, default=200)
    args = ap.parse_args(argv)

    report = scan_stray_outputs(
        args.repo_root.expanduser().resolve(),
        since_hours=args.since_hours,
        max_files=args.max_files,
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"canonical_output: {report.canonical_output or '(unset)'}")
        print(f"since_hours: {report.since_hours}")
        print(f"roots_scanned: {len(report.roots_scanned)}")
        for line in report.roots_scanned:
            print(f"  - {line}")
        print(f"stray_media_files: {report.total_files} ({report.total_bytes} bytes)")
        for f in report.files:
            print(f"  [{f.mtime_iso}] {f.root_label}/{f.relpath} ({f.size} B)")
        if report.total_files:
            print()
            print("Recover: python3 scripts/flatten_output_nest.py --bind-root <canonical_output> --apply")

    return 1 if report.total_files else 0


if __name__ == "__main__":
    raise SystemExit(main())
