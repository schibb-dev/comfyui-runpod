#!/usr/bin/env python3
"""
Backfill manifest.source_image for experiments that were created before
source_image was populated from workflow data.

For each experiment under --experiments-root that has manifest.json with
base_mp4 but missing or empty source_image:
  1. Resolve base_mp4 path (absolute or relative to --output-root).
  2. If the MP4 exists, extract prompt/workflow from its metadata (ffprobe).
  3. Extract source image path from the workflow (LoadImage node).
  4. Write source_image into manifest.json.

Run from repo root: python scripts/backfill_manifest_source_image.py
Or with explicit paths: python scripts/backfill_manifest_source_image.py --experiments-root /path/to/experiments --output-root /path/to/output
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure we can import from scripts/
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import comfy_meta_lib as cml
from tune_experiment import _extract_source_image_from_workflow_data


def _iter_experiments(experiments_root: Path):
    if not experiments_root.is_dir():
        return
    for child in sorted(experiments_root.iterdir(), key=lambda p: p.name):
        if child.is_dir() and (child / "manifest.json").is_file():
            yield child


def _read_manifest(exp_dir: Path) -> dict | None:
    path = exp_dir / "manifest.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_manifest(exp_dir: Path, manifest: dict) -> None:
    path = exp_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _resolve_base_mp4_path(base_mp4_str: str, output_root: Path) -> Path | None:
    s = (base_mp4_str or "").strip().replace("\\", "/")
    if not s:
        return None
    p = Path(s)
    if p.is_absolute():
        return p if p.is_file() else None
    # Relative to output_root
    full = (output_root / s).resolve()
    return full if full.is_file() else None


def backfill_one(
    exp_dir: Path,
    output_root: Path,
    *,
    force: bool,
    dry_run: bool,
) -> tuple[str, bool, str]:
    """
    Backfill source_image for one experiment. Returns (exp_id, updated, message).
    """
    manifest = _read_manifest(exp_dir)
    if not manifest:
        return exp_dir.name, False, "no manifest or invalid JSON"
    exp_id = manifest.get("exp_id") or exp_dir.name
    existing = manifest.get("source_image")
    if isinstance(existing, str) and existing.strip() and not force:
        return exp_id, False, "already has source_image"
    base_mp4_str = manifest.get("base_mp4")
    if not isinstance(base_mp4_str, str) or not base_mp4_str.strip():
        return exp_id, False, "no base_mp4"
    base_mp4_path = _resolve_base_mp4_path(base_mp4_str, output_root)
    if not base_mp4_path:
        return exp_id, False, "base_mp4 file not found"
    try:
        tags = cml.ffprobe_format_tags(base_mp4_path)
        prompt_obj, workflow_obj = cml.extract_prompt_workflow_from_tags(tags)
    except Exception as e:
        return exp_id, False, f"extract metadata: {e}"
    if not isinstance(prompt_obj, dict) or not isinstance(workflow_obj, dict):
        return exp_id, False, "no prompt/workflow in MP4"
    source_image = _extract_source_image_from_workflow_data(
        prompt_obj, workflow_obj, base_mp4_path
    )
    if not source_image:
        return exp_id, False, "no LoadImage in workflow"
    manifest["source_image"] = source_image
    if not dry_run:
        _write_manifest(exp_dir, manifest)
    return exp_id, True, source_image


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Backfill manifest.source_image from base_mp4 workflow metadata."
    )
    ap.add_argument(
        "--experiments-root",
        default="",
        help="Root folder containing experiment dirs (default: workspace/output/output/experiments)",
    )
    ap.add_argument(
        "--output-root",
        default="",
        help="Root for resolving relative base_mp4 paths (default: workspace/output)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Re-derive and overwrite source_image even when already set",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written but do not modify manifests",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max experiments to process (0=all)",
    )
    ap.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print reason for each skipped experiment",
    )
    args = ap.parse_args()

    repo_root = _SCRIPT_DIR.parent
    experiments_root = (
        Path(args.experiments_root)
        if args.experiments_root
        else (repo_root / "workspace" / "output" / "output" / "experiments")
    )
    output_root = (
        Path(args.output_root)
        if args.output_root
        else (repo_root / "workspace" / "output")
    )
    experiments_root = experiments_root.resolve()
    output_root = output_root.resolve()

    if not experiments_root.is_dir():
        print(f"ERROR: experiments root not found: {experiments_root}", file=sys.stderr)
        return 2
    if not output_root.is_dir():
        print(f"ERROR: output root not found: {output_root}", file=sys.stderr)
        return 2

    exp_dirs = list(_iter_experiments(experiments_root))
    if args.limit and args.limit > 0:
        exp_dirs = exp_dirs[: args.limit]

    updated = 0
    skipped = 0
    for exp_dir in exp_dirs:
        exp_id, changed, msg = backfill_one(
            exp_dir, output_root, force=args.force, dry_run=args.dry_run
        )
        if changed:
            updated += 1
            action = "(dry-run would write)" if args.dry_run else "updated"
            print(f"{exp_id}: {action} source_image={msg}")
        else:
            skipped += 1
            if args.verbose:
                print(f"  skip {exp_id}: {msg}", file=sys.stderr)

    print(f"experiments_root: {experiments_root}")
    print(f"processed: {len(exp_dirs)}, updated: {updated}, skipped: {skipped}")
    if args.dry_run and updated:
        print("Run without --dry-run to write manifests.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
