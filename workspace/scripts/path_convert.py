#!/usr/bin/env python3
"""
Convert paths between Unix-style (forward slashes) and Windows-style (backslashes).

FB9 workflows store paths in Unix-style form (relative, forward slashes).
This utility converts both ways so automation and ComfyUI can resolve paths
correctly on any OS.

Uses only Python stdlib (pathlib). For WSL-style (/mnt/c/...) conversion
see the optional PyPI package 'wslPath'.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import PureWindowsPath


def to_unix_path(path: str) -> str:
    """
    Convert a path to Unix-style (forward slashes).

    - On Windows: C:\\foo\\bar -> C:/foo/bar (or foo/bar if relative).
    - Handles mixed slashes and normalizes.
    - Relative paths stay relative; absolute Windows paths keep drive letter
      as a single segment (C:) so they remain portable.
    """
    if not path or not path.strip():
        return path
    try:
        return PureWindowsPath(path.strip()).as_posix()
    except Exception:
        return path.strip().replace("\\", "/")


def to_windows_path(path: str) -> str:
    """
    Convert a path to Windows-style (backslashes).

    - Forward slashes become backslashes.
    - A leading single letter + colon (e.g. C:) is treated as drive.
    - Safe to call on Unix; produces paths that work on Windows.
    """
    if not path or not path.strip():
        return path
    return path.strip().replace("/", "\\")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Convert paths between Unix-style and Windows-style (both directions)."
    )
    ap.add_argument(
        "direction",
        choices=["to_unix", "to_win", "to_windows"],
        help="Conversion direction: to_unix or to_win(to_windows)",
    )
    ap.add_argument(
        "paths",
        nargs="+",
        help="Path(s) to convert",
    )
    args = ap.parse_args()

    to_win = args.direction in ("to_win", "to_windows")
    for path in args.paths:
        out = to_windows_path(path) if to_win else to_unix_path(path)
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
