#!/usr/bin/env python3
"""
Vision V1 — pick ~N diverse og/ videos into an inputs list for the slice spike.

Scans ``<data-root>/og/**/*.mp4``, scores by recency (path date / mtime) and
spreads across date folders so the ~12-clip set is not one afternoon dump.

See docs/VISION_V1_TIME_SLICE_CAPTION_SPIKE.md.
"""

from __future__ import annotations

import argparse
import os
import random
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

DATE_DIR_RE = re.compile(r"^(20\d{2}-\d{2}-\d{2})$")
DEFAULT_LIMIT = 12
DEFAULT_OUT_NAME = "vision_v1_inputs.txt"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _env_path(name: str) -> Optional[Path]:
    raw = (os.environ.get(name) or "").strip()
    return Path(raw).expanduser() if raw else None


def discover_og_root(data_root: Path) -> Path:
    root = data_root.expanduser().resolve()
    for cand in (root / "og", root / "output" / "og"):
        if cand.is_dir():
            return cand
    raise FileNotFoundError(f"no og/ under {root} (tried og/ and output/og/)")


def relpath_from_data_root(video: Path, data_root: Path) -> str:
    return str(video.resolve().relative_to(data_root.resolve())).replace("\\", "/")


def date_bucket(video: Path, og_root: Path) -> str:
    """Prefer YYYY-MM-DD folder under og/; else mtime date."""
    try:
        rel = video.resolve().relative_to(og_root.resolve())
        parts = rel.parts
        if parts and DATE_DIR_RE.match(parts[0]):
            return parts[0]
    except ValueError:
        pass
    ts = video.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def list_og_mp4s(og_root: Path, *, max_scan: int = 50000) -> List[Path]:
    out: List[Path] = []
    for p in og_root.rglob("*.mp4"):
        if p.is_file():
            out.append(p)
            if len(out) >= max_scan:
                break
    return out


def pick_diverse(
    videos: Sequence[Path],
    *,
    og_root: Path,
    limit: int = DEFAULT_LIMIT,
    seed: int = 0,
    prefer_hourly: bool = True,
) -> List[Path]:
    """
    Round-robin across date buckets (newest buckets first), with optional
    preference for paths containing 'hourly'.
    """
    if limit <= 0 or not videos:
        return []
    rng = random.Random(int(seed))
    by_bucket: Dict[str, List[Path]] = defaultdict(list)
    for v in videos:
        by_bucket[date_bucket(v, og_root)].append(v)

    def sort_key(p: Path) -> Tuple[int, float]:
        hourly = 1 if prefer_hourly and "hourly" in str(p).replace("\\", "/").lower() else 0
        return (hourly, p.stat().st_mtime)

    for b in by_bucket:
        by_bucket[b].sort(key=sort_key, reverse=True)
        # light shuffle within same score band for variety
        rng.shuffle(by_bucket[b])
        by_bucket[b].sort(key=sort_key, reverse=True)

    buckets_newest = sorted(by_bucket.keys(), reverse=True)
    picked: List[Path] = []
    seen = set()
    # Round-robin until limit
    while len(picked) < limit:
        progressed = False
        for b in buckets_newest:
            if len(picked) >= limit:
                break
            pile = by_bucket[b]
            while pile:
                cand = pile.pop(0)
                key = str(cand.resolve())
                if key in seen:
                    continue
                seen.add(key)
                picked.append(cand)
                progressed = True
                break
        if not progressed:
            break
    return picked


def write_inputs(paths: Sequence[Path], *, data_root: Path, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# vision V1 inputs — generated {utc_now()}",
        f"# data_root={data_root.resolve()}",
        f"# count={len(paths)}",
    ]
    for p in paths:
        lines.append(relpath_from_data_root(p, data_root))
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def run_pick(
    *,
    data_root: Path,
    limit: int = DEFAULT_LIMIT,
    seed: int = 0,
    out_path: Optional[Path] = None,
    prefer_hourly: bool = True,
) -> Dict[str, object]:
    data_root = data_root.expanduser().resolve()
    og_root = discover_og_root(data_root)
    # Relpaths should be under data_root (og/...); if og is data_root/output/og, keep that.
    videos = list_og_mp4s(og_root)
    picked = pick_diverse(
        videos, og_root=og_root, limit=limit, seed=seed, prefer_hourly=prefer_hourly
    )
    if out_path is None:
        status = data_root / "_status"
        if not (data_root / "og").is_dir() and (data_root / "output" / "og").is_dir():
            status = data_root / "output" / "_status"
        out_path = status / DEFAULT_OUT_NAME
    path = write_inputs(picked, data_root=data_root, out_path=out_path)
    return {
        "ok": True,
        "og_root": str(og_root),
        "scanned": len(videos),
        "picked": len(picked),
        "out": str(path),
        "relpaths": [relpath_from_data_root(p, data_root) for p in picked],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Vision V1: pick ~N og videos into inputs list")
    ap.add_argument(
        "--data-root",
        type=Path,
        default=_env_path("VISION_DATA_ROOT"),
        help="Root containing og/ (usually the Comfy output bind)",
    )
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"Output inputs file (default: <data-root>/_status/{DEFAULT_OUT_NAME})",
    )
    ap.add_argument("--no-prefer-hourly", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.data_root is None:
        print("error: --data-root or VISION_DATA_ROOT required", file=sys.stderr)
        return 2
    try:
        result = run_pick(
            data_root=Path(args.data_root),
            limit=int(args.limit),
            seed=int(args.seed),
            out_path=Path(args.out) if args.out else None,
            prefer_hourly=not bool(args.no_prefer_hourly),
        )
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    import json

    print(json.dumps(result, indent=2))
    return 0 if result.get("picked") else 1


if __name__ == "__main__":
    raise SystemExit(main())
