#!/usr/bin/env python3
"""
Canonicalize XMP sidecar filenames to use upper-case extension: .XMP

Why:
- Some tools (e.g., FileBrowser Pro) generate uppercase .XMP and you want
  consistent casing for diffs / tooling.

Behavior:
- Renames only files whose suffix is case-insensitively ".xmp" to ".XMP".
- Uses a safe two-step rename for case-only changes on Windows.
- By default, scans non-recursively; use --recursive to walk subdirs.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def _rename_case_safe(src: Path, dst: Path) -> None:
    if src == dst:
        return
    # Case-only rename on Windows can be flaky; do temp hop if needed.
    tmp = dst.with_name(dst.name + ".tmp_rename")
    if tmp.exists():
        tmp.unlink()
    src.rename(tmp)
    tmp.rename(dst)


def canonicalize_dir(dir_path: Path, *, recursive: bool) -> dict:
    if not dir_path.exists() or not dir_path.is_dir():
        raise ValueError(f"Not a directory: {dir_path}")

    it = dir_path.rglob("*") if recursive else dir_path.iterdir()
    changed = 0
    skipped = 0
    errors: list[str] = []

    for p in it:
        if not p.is_file():
            continue
        if p.suffix.lower() != ".xmp":
            continue
        target = p.with_suffix(".XMP")
        try:
            if p.name == target.name:
                skipped += 1
                continue
            if target.exists() and target != p:
                # Avoid overwriting if both variants exist somehow.
                errors.append(f"{p.name}: target already exists ({target.name})")
                continue
            _rename_case_safe(p, target)
            changed += 1
        except Exception as e:
            errors.append(f"{p.name}: {e}")

    return {"dir": str(dir_path), "recursive": recursive, "changed": changed, "skipped": skipped, "errors": errors}


def main() -> int:
    ap = argparse.ArgumentParser(description="Rename *.xmp to *.XMP (case canonicalization)")
    ap.add_argument("dir", help="Directory to process")
    ap.add_argument("--recursive", action="store_true", help="Recurse into subdirectories")
    args = ap.parse_args()

    out = canonicalize_dir(Path(args.dir), recursive=args.recursive)
    print(out)
    return 0 if not out["errors"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

