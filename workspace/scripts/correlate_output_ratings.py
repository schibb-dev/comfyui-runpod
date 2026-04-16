#!/usr/bin/env python3
"""
Correlate sidecar XMP star ratings with ComfyUI generation settings.

Workflow JSON is read from embedded metadata in this order:
  1) Companion PNG (same stem as the .XMP) — PIL Image.info['prompt']  (preferred)
  2) Companion MP4 — ffprobe format.tags.comment → JSON with string "prompt" field

Requires: Pillow (pip install pillow), ffprobe on PATH (for MP4 fallback).

Example:
  python workspace/scripts/correlate_output_ratings.py \\
    --root workspace/output/output/og \\
    --name-glob "X-Kneel*.XMP" \\
    --days 30
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import statistics
import subprocess
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

RATING_RE = re.compile(r'xmp:Rating="(\d+)"')


def parse_xmp_rating(xmp_path: Path) -> Optional[int]:
    try:
        txt = xmp_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = RATING_RE.search(txt)
    return int(m.group(1)) if m else None


def _mx_slider_value(node: Dict[str, Any]) -> Optional[float]:
    if not isinstance(node, dict) or node.get("class_type") != "mxSlider":
        return None
    inp = node.get("inputs") or {}
    if inp.get("isfloatX"):
        return float(inp.get("Xf", 0))
    return float(inp.get("Xi", 0))


def extract_prompt_png(png_path: Path) -> Optional[Dict[str, Any]]:
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        im = Image.open(png_path)
        im.load()
        raw = im.info.get("prompt")
        if isinstance(raw, str) and raw.strip():
            return json.loads(raw)
    except Exception:
        return None
    return None


def extract_prompt_mp4(mp4_path: Path, *, ffprobe: str) -> Optional[Dict[str, Any]]:
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                str(mp4_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            return None
        fmt = json.loads(proc.stdout).get("format") or {}
        tags = fmt.get("tags") or {}
        comment = tags.get("comment")
        if not isinstance(comment, str) or not comment.strip():
            return None
        outer = json.loads(comment)
        pr = outer.get("prompt")
        if isinstance(pr, str):
            return json.loads(pr)
        if isinstance(pr, dict):
            return pr
    except Exception:
        return None
    return None


def extract_prompt_media(
    stem_dir: Path,
    stem_name: str,
    *,
    ffprobe: Optional[str],
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Return (prompt_dict, source_label)."""
    png = stem_dir / f"{stem_name}.png"
    if png.is_file():
        pr = extract_prompt_png(png)
        if pr:
            return pr, f"png:{png.name}"
    mp4 = stem_dir / f"{stem_name}.mp4"
    if mp4.is_file() and ffprobe:
        pr = extract_prompt_mp4(mp4, ffprobe=ffprobe)
        if pr:
            return pr, f"mp4:{mp4.name}"
    return None, ""


@dataclass
class Row:
    xmp: str
    rating: Optional[int]
    mtime_iso: str
    prompt_source: str
    steps: Optional[float]
    cfg: Optional[float]
    denoise: Optional[float]
    tea_rel1: Optional[float]
    speed_Xf: Optional[float]
    error: str


def collect_settings(prompt: Dict[str, Any]) -> Tuple[Optional[float], ...]:
    """Pull common WAN / mxSlider ids from X-Kneel-FB9-style graphs."""
    ids = {
        "steps": "82",
        "cfg": "468",
        "denoise": "470",
        "tea": "126",
        "speed": "157",
    }
    out: Dict[str, Optional[float]] = {k: None for k in ids}
    for key, nid in ids.items():
        node = prompt.get(nid)
        if isinstance(node, dict):
            out[key] = _mx_slider_value(node)
    return (
        out["steps"],
        out["cfg"],
        out["denoise"],
        out["tea"],
        out["speed"],
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--root",
        type=Path,
        default=Path("workspace/output/output/og"),
        help="Directory tree to scan (default: workspace/output/output/og)",
    )
    ap.add_argument(
        "--name-glob",
        default="X-Kneel*.XMP",
        help="Glob for XMP filenames (default: X-Kneel*.XMP)",
    )
    ap.add_argument(
        "--days",
        type=int,
        default=0,
        help="Only include XMPs modified in the last N days (0 = all)",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Write CSV/JSON here (default: <root>/_status)",
    )
    ap.add_argument(
        "--ffprobe",
        default=None,
        help="Path to ffprobe executable (default: search PATH)",
    )
    args = ap.parse_args()

    root: Path = args.root.resolve()
    if not root.is_dir():
        print(f"ERROR: root not found: {root}", file=sys.stderr)
        return 2

    ffprobe = args.ffprobe or shutil.which("ffprobe")
    if not ffprobe:
        print("WARNING: ffprobe not found; MP4 fallback disabled.", file=sys.stderr)

    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("ERROR: Install Pillow: pip install pillow", file=sys.stderr)
        return 2

    out_dir = (args.out_dir or (root / "_status")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    cut = None
    if args.days and args.days > 0:
        cut = datetime.now() - timedelta(days=args.days)

    rows: List[Row] = []
    for xmp in sorted(root.glob(f"**/{args.name_glob}")):
        try:
            mtime = datetime.fromtimestamp(xmp.stat().st_mtime)
        except OSError:
            continue
        if cut and mtime < cut:
            continue
        rating = parse_xmp_rating(xmp)
        stem_name = xmp.stem
        stem_dir = xmp.parent

        pr: Optional[Dict[str, Any]] = None
        src = ""
        err = ""
        pr, src = extract_prompt_media(stem_dir, stem_name, ffprobe=ffprobe)
        if pr is None:
            if not ffprobe and not (stem_dir / f"{stem_name}.png").is_file():
                err = "no_png_and_no_ffprobe"
            elif not (stem_dir / f"{stem_name}.png").is_file() and not (
                stem_dir / f"{stem_name}.mp4"
            ).is_file():
                err = "missing_png_and_mp4"
            else:
                err = "metadata_parse_failed"

        steps = cfg = denoise = tea = speed = None
        if pr is not None:
            steps, cfg, denoise, tea, speed = collect_settings(pr)

        try:
            xmp_rel = str(xmp.relative_to(root))
        except ValueError:
            xmp_rel = str(xmp)
        rows.append(
            Row(
                xmp=xmp_rel,
                rating=rating,
                mtime_iso=mtime.strftime("%Y-%m-%dT%H:%M:%S"),
                prompt_source=src,
                steps=steps,
                cfg=cfg,
                denoise=denoise,
                tea_rel1=tea,
                speed_Xf=speed,
                error=err,
            )
        )

    csv_path = out_dir / "rating_settings_correlation.csv"
    fieldnames = [f.name for f in fields(Row)]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(asdict(r))

    rated = [r for r in rows if r.rating is not None and not r.error]
    with_settings = [r for r in rated if r.steps is not None]

    # Group by exact (steps, cfg, denoise) tuple
    groups: Dict[Tuple[float, float, float], List[int]] = defaultdict(list)
    for r in with_settings:
        key = (float(r.steps), float(r.cfg), float(r.denoise))
        groups[key].append(r.rating)

    group_stats: List[Dict[str, Any]] = []
    for key, ratings in sorted(groups.items(), key=lambda kv: (-statistics.mean(kv[1]), -len(kv[1]))):
        group_stats.append(
            {
                "steps": key[0],
                "cfg": key[1],
                "denoise": key[2],
                "n": len(ratings),
                "mean_rating": round(statistics.mean(ratings), 4),
                "median_rating": statistics.median(ratings),
            }
        )

    stable = [g for g in group_stats if g["n"] >= 2]
    stable.sort(key=lambda g: (-g["mean_rating"], -g["n"]))

    summary = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "root": str(root),
        "name_glob": args.name_glob,
        "days": args.days,
        "total_xmp": len(rows),
        "rated": len([r for r in rows if r.rating is not None]),
        "with_prompt": len([r for r in rows if r.prompt_source]),
        "errors": len([r for r in rows if r.error]),
        "mean_rating_all_rated": round(
            statistics.mean([r.rating for r in rated]), 4
        )
        if rated
        else None,
        "by_steps_cfg_denoise": group_stats[:40],
        "sweet_spot_candidates": group_stats[:5],
        "sweet_spot_min_n2": stable[:10],
    }

    json_path = out_dir / "rating_settings_summary.json"
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"Wrote {csv_path}")
    print(f"Wrote {json_path}")
    print(f"rows={len(rated)} with rating, {len(with_settings)} with steps/cfg/denoise from prompt")
    if group_stats:
        print("Top combo by mean_rating (min n=1):")
        for g in group_stats[:8]:
            print(
                f"  steps={g['steps']:.0f} cfg={g['cfg']:.2f} denoise={g['denoise']:.3f} "
                f"n={g['n']} mean={g['mean_rating']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
