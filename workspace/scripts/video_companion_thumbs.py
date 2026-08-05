#!/usr/bin/env python3
"""
Ensure same-stem companion PNG thumbnails for videos that lack one.

Writes ``video.mp4`` → ``video.png`` (mid-frame via ffmpeg) so Rating / Discovery
filmstrips and ``companion_image_for_video`` all agree.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from snowflake_inventory import (
    VIDEO_EXTS,
    companion_image_for_video,
    iter_orphan_videos,
)


def probe_duration_sec(video: Path, *, ffprobe: str = "ffprobe") -> Optional[float]:
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=60)
    except FileNotFoundError as e:
        raise RuntimeError(f"ffprobe not found ({ffprobe})") from e
    if proc.returncode != 0:
        return None
    try:
        return float((proc.stdout or "").strip())
    except ValueError:
        return None


def extract_frame_png(
    video: Path,
    out_png: Path,
    *,
    frame_t: float,
    ffmpeg: str = "ffmpeg",
    duration_sec: Optional[float] = None,
) -> None:
    """Extract a single PNG frame at ``frame_t`` seconds into ``out_png``."""
    out_png.parent.mkdir(parents=True, exist_ok=True)
    t = max(0.0, float(frame_t))
    if duration_sec is not None and float(duration_sec) > 0:
        t = min(t, max(0.0, float(duration_sec) - 0.05))
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{t:.6f}",
        "-i",
        str(video),
        "-frames:v",
        "1",
        str(out_png),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=120)
    except FileNotFoundError as e:
        raise RuntimeError(f"ffmpeg not found ({ffmpeg})") from e
    if proc.returncode != 0 or not out_png.is_file() or out_png.stat().st_size <= 0:
        raise RuntimeError(f"ffmpeg extract failed for {video}@{t}: {(proc.stderr or '').strip()}")


def ensure_companion_thumb(
    video: Path,
    *,
    force: bool = False,
    at_frac: float = 0.5,
    dry_run: bool = False,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
    extract_fn=None,
    probe_fn=None,
) -> Dict[str, Any]:
    """
    Ensure a same-stem ``.png`` companion exists next to ``video``.

    Returns ``{ok, path, created, skipped, error?, reason?}``.
    """
    video = Path(video)
    out: Dict[str, Any] = {
        "ok": False,
        "path": None,
        "created": False,
        "skipped": False,
        "error": None,
        "reason": None,
    }
    if not video.is_file():
        out["error"] = "video_missing"
        return out
    if video.suffix.lower() not in VIDEO_EXTS:
        out["error"] = "not_a_video"
        return out

    existing = companion_image_for_video(video)
    target = video.with_suffix(".png")
    out["path"] = str(target)

    if existing is not None and not force:
        out["ok"] = True
        out["skipped"] = True
        out["path"] = str(existing)
        out["reason"] = "companion_exists"
        return out

    if dry_run:
        out["ok"] = True
        out["skipped"] = True
        out["reason"] = "dry_run"
        return out

    try:
        if probe_fn is not None:
            duration = probe_fn(video)
        else:
            duration = probe_duration_sec(video, ffprobe=ffprobe)
        frac = min(0.95, max(0.0, float(at_frac)))
        frame_t = (float(duration) * frac) if duration and duration > 0 else 0.0
        if extract_fn is not None:
            extract_fn(video, target, frame_t=frame_t, duration_sec=duration)
        else:
            extract_frame_png(
                video,
                target,
                frame_t=frame_t,
                ffmpeg=ffmpeg,
                duration_sec=duration,
            )
    except Exception as e:
        out["error"] = str(e)
        return out

    if not target.is_file() or target.stat().st_size <= 0:
        out["error"] = "thumb_not_written"
        return out

    out["ok"] = True
    out["created"] = True
    out["reason"] = "created"
    return out


def backfill_orphan_thumbs(
    roots: Sequence[Path],
    *,
    limit: Optional[int] = 200,
    dry_run: bool = False,
    force: bool = False,
    at_frac: float = 0.5,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
) -> Dict[str, Any]:
    """Scan roots for orphan videos and ensure companion PNGs."""
    root_list = [Path(r).expanduser().resolve() for r in roots]
    orphans = list(iter_orphan_videos(root_list, limit, reverse_chronological=True))
    results: List[Dict[str, Any]] = []
    created = skipped = failed = 0
    for video in orphans:
        row = ensure_companion_thumb(
            video,
            force=force,
            at_frac=at_frac,
            dry_run=dry_run,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
        )
        row["video"] = str(video)
        results.append(row)
        if row.get("created"):
            created += 1
        elif row.get("skipped"):
            skipped += 1
        else:
            failed += 1
    return {
        "ok": failed == 0,
        "roots": [str(r) for r in root_list],
        "scanned": len(orphans),
        "created": created,
        "skipped": skipped,
        "failed": failed,
        "dry_run": dry_run,
        "force": force,
        "results": results,
        "note": "Discovery picks up new sidecars on the next index refresh (?refresh=1).",
    }


def cmd_backfill(args: argparse.Namespace) -> int:
    roots = [Path(r) for r in (args.root or [])]
    if not roots:
        print("error: pass at least one --root", file=sys.stderr)
        return 2
    summary = backfill_orphan_thumbs(
        roots,
        limit=None if args.limit <= 0 else int(args.limit),
        dry_run=bool(args.dry_run),
        force=bool(args.force),
        at_frac=float(args.at_frac),
        ffmpeg=str(args.ffmpeg),
        ffprobe=str(args.ffprobe),
    )
    print(
        f"scanned={summary['scanned']} created={summary['created']} "
        f"skipped={summary['skipped']} failed={summary['failed']}"
        + (" (dry-run)" if summary["dry_run"] else "")
    )
    if args.json:
        print(json.dumps(summary, indent=2))
    elif summary["failed"]:
        for row in summary["results"]:
            if not row.get("ok"):
                print(f"FAIL {row.get('video')}: {row.get('error')}", file=sys.stderr)
    print(summary.get("note") or "")
    return 0 if summary["ok"] else 1


def cmd_ensure(args: argparse.Namespace) -> int:
    video = Path(args.video).expanduser().resolve()
    row = ensure_companion_thumb(
        video,
        force=bool(args.force),
        at_frac=float(args.at_frac),
        dry_run=bool(args.dry_run),
        ffmpeg=str(args.ffmpeg),
        ffprobe=str(args.ffprobe),
    )
    print(json.dumps(row, indent=2))
    return 0 if row.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Generate missing same-stem PNG thumbnails for videos")
    sub = p.add_subparsers(dest="cmd", required=True)

    backfill = sub.add_parser("backfill", help="Scan roots for orphan videos and write companion PNGs")
    backfill.add_argument(
        "--root",
        action="append",
        default=[],
        help="Root to scan (repeatable). Example: /home/yuji/comfyui-runpod-data/output/og",
    )
    backfill.add_argument("--limit", type=int, default=200, help="Max orphans (0 = no limit)")
    backfill.add_argument("--dry-run", action="store_true")
    backfill.add_argument("--force", action="store_true", help="Overwrite even when a companion exists")
    backfill.add_argument("--at-frac", type=float, default=0.5, help="Frame position as fraction of duration")
    backfill.add_argument("--ffmpeg", default="ffmpeg")
    backfill.add_argument("--ffprobe", default="ffprobe")
    backfill.add_argument("--json", action="store_true", help="Print full JSON summary")
    backfill.set_defaults(func=cmd_backfill)

    ensure = sub.add_parser("ensure", help="Ensure a companion PNG for one video")
    ensure.add_argument("video", help="Path to video file")
    ensure.add_argument("--dry-run", action="store_true")
    ensure.add_argument("--force", action="store_true")
    ensure.add_argument("--at-frac", type=float, default=0.5)
    ensure.add_argument("--ffmpeg", default="ffmpeg")
    ensure.add_argument("--ffprobe", default="ffprobe")
    ensure.set_defaults(func=cmd_ensure)

    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
