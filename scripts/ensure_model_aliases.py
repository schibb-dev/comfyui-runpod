#!/usr/bin/env python3
"""
Ensure local "alias" model files exist (by copying from known sources).

This is used to satisfy workflows that reference a file that the repo already contains
somewhere else (e.g. 4xLSDIR.pth shipped with the WAN template checkout).

Reads `aliases:` from scripts/model_download_manifest.yaml.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import yaml


def _load_manifest(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("Manifest must be a mapping")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy known model alias files into the ComfyUI models dir.")
    parser.add_argument(
        "--models-dir",
        default=os.getenv("COMFYUI_MODELS_DIR_IN_CONTAINER", "/ComfyUI/models"),
        help="ComfyUI models directory in the container (default: /ComfyUI/models).",
    )
    parser.add_argument(
        "--manifest",
        default="/workspace/scripts/model_download_manifest.yaml",
        help="Path to manifest YAML (default: /workspace/scripts/model_download_manifest.yaml).",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite dest even if it exists.")
    args = parser.parse_args()

    models_dir = Path(args.models_dir)
    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"WARNING: manifest not found: {manifest_path}")
        return 0

    manifest = _load_manifest(manifest_path)
    aliases = manifest.get("aliases") or []
    if not isinstance(aliases, list) or not aliases:
        print("No aliases to apply.")
        return 0

    copied = 0
    skipped = 0
    failed = 0

    print("Ensuring model aliases")
    print(f"  models_dir: {models_dir}")

    for a in aliases:
        if not isinstance(a, dict):
            continue
        src = a.get("src")
        src_candidates = a.get("src_candidates")
        dest_rel = a.get("dest")
        if (not src and not src_candidates) or not dest_rel:
            continue

        candidate_paths: list[Path] = []
        if src:
            candidate_paths.append(Path(str(src)))
        if isinstance(src_candidates, list):
            candidate_paths.extend(Path(str(p)) for p in src_candidates)

        src_path = None
        for c in candidate_paths:
            if c.exists():
                src_path = c
                break

        dest_path = models_dir / str(dest_rel)

        if dest_path.exists() and not args.force:
            skipped += 1
            continue

        try:
            if src_path is None:
                shown = ", ".join(str(p) for p in candidate_paths) if candidate_paths else "<none>"
                print(f"WARNING: alias source missing (tried: {shown})")
                failed += 1
                continue

            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(src_path.read_bytes())
            print(f"OK: copied {src_path} -> {dest_path}")
            copied += 1
        except Exception as e:
            print(f"ERROR: failed copying {src_path} -> {dest_path}: {e}")
            failed += 1

    print("Summary")
    print(f"  copied:  {copied}")
    print(f"  skipped: {skipped}")
    print(f"  failed:  {failed}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

