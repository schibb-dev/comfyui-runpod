#!/usr/bin/env python3
"""
Vision V1 — CPU frame sampler for time-slice captions.

Reads a list of asset relpaths (or absolute paths), probes duration with ffprobe,
emits fixed windows, extracts mid-frame JPEGs with ffmpeg, writes frames_manifest.json.

See docs/VISION_V1_TIME_SLICE_CAPTION_SPIKE.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

FRAMES_MANIFEST_NAME = "frames_manifest.json"
DEFAULT_WINDOW_SEC = 2.0
DEFAULT_MAX_WINDOWS = 30
SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_relpath(raw: str) -> str:
    s = str(raw or "").strip().replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    return s.lstrip("/")


def resolve_asset_path(raw: str, *, data_root: Optional[Path] = None) -> Tuple[str, Path]:
    """
    Return (asset_relpath_for_records, absolute_path).

    Absolute inputs keep basename-only relpath if under data_root/og, else the
    normalized path string as provided (relative form when possible).
    """
    text = str(raw or "").strip()
    if not text:
        raise ValueError("empty asset path")
    p = Path(text).expanduser()
    if p.is_absolute():
        abs_p = p.resolve()
        rel = normalize_relpath(text)
        if data_root is not None:
            try:
                rel = str(abs_p.relative_to(data_root.resolve())).replace("\\", "/")
            except ValueError:
                rel = abs_p.name
        return rel, abs_p
    rel = normalize_relpath(text)
    if data_root is None:
        raise ValueError(f"relative path requires --data-root: {rel}")
    return rel, (data_root / rel).resolve()


def plan_windows(
    duration_sec: float,
    *,
    window_sec: float = DEFAULT_WINDOW_SEC,
    max_windows: int = DEFAULT_MAX_WINDOWS,
) -> List[Tuple[float, float, float]]:
    """
    Fixed non-overlapping windows. Last window trimmed to EOF.
    Returns list of (t0, t1, frame_t) where frame_t is the mid-point.
    """
    if duration_sec <= 0:
        return []
    w = float(window_sec)
    if w <= 0:
        raise ValueError("window_sec must be > 0")
    cap = max(1, int(max_windows))
    out: List[Tuple[float, float, float]] = []
    t0 = 0.0
    while t0 < duration_sec - 1e-6 and len(out) < cap:
        t1 = min(t0 + w, duration_sec)
        if t1 - t0 < 1e-3:
            break
        frame_t = (t0 + t1) / 2.0
        out.append((round(t0, 6), round(t1, 6), round(frame_t, 6)))
        t0 = t1
    return out


def parse_inputs_file(path: Path) -> List[str]:
    lines: List[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        lines.append(s)
    return lines


@dataclass
class FrameItem:
    asset_relpath: str
    t0: float
    t1: float
    frame_t: float
    frame_relpath: str
    slice: str = "window"  # window | whole


def probe_duration_sec(video: Path, *, ffprobe: str = "ffprobe") -> float:
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
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=120)
    except FileNotFoundError as e:
        raise RuntimeError(f"ffprobe not found ({ffprobe})") from e
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {video}: {proc.stderr.strip() or proc.stdout.strip()}")
    try:
        return float((proc.stdout or "").strip())
    except ValueError as e:
        raise RuntimeError(f"ffprobe duration parse failed for {video}: {proc.stdout!r}") from e


def extract_frame_jpeg(
    video: Path,
    *,
    frame_t: float,
    out_jpeg: Path,
    ffmpeg: str = "ffmpeg",
) -> None:
    out_jpeg.parent.mkdir(parents=True, exist_ok=True)
    # -ss after -i is slower but more accurate for mid-window frames.
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{frame_t:.6f}",
        "-i",
        str(video),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(out_jpeg),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=120)
    except FileNotFoundError as e:
        raise RuntimeError(f"ffmpeg not found ({ffmpeg})") from e
    if proc.returncode != 0 or not out_jpeg.is_file():
        raise RuntimeError(f"ffmpeg extract failed for {video}@{frame_t}: {proc.stderr.strip()}")


def safe_stem(relpath: str) -> str:
    base = Path(relpath.replace("\\", "/")).name
    stem = Path(base).stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "asset"
    return stem[:80]


def sample_asset(
    *,
    asset_relpath: str,
    video: Path,
    work_dir: Path,
    window_sec: float,
    max_windows: int,
    include_whole: bool,
    ffprobe: str,
    ffmpeg: str,
    extract: bool = True,
) -> Dict[str, Any]:
    duration = probe_duration_sec(video, ffprobe=ffprobe)
    windows = plan_windows(duration, window_sec=window_sec, max_windows=max_windows)
    truncated = len(windows) >= max_windows and windows and windows[-1][1] < duration - 1e-3
    stem = safe_stem(asset_relpath)
    frames_dir = work_dir / "frames" / stem
    items: List[FrameItem] = []

    def add_item(t0: float, t1: float, frame_t: float, slice_kind: str, idx: int) -> None:
        name = f"{stem}_{idx:03d}_{slice_kind}_{frame_t:.3f}.jpg"
        rel = f"frames/{stem}/{name}".replace("\\", "/")
        abs_jpg = work_dir / rel
        if extract:
            extract_frame_jpeg(video, frame_t=frame_t, out_jpeg=abs_jpg, ffmpeg=ffmpeg)
        else:
            abs_jpg.parent.mkdir(parents=True, exist_ok=True)
            if not abs_jpg.is_file():
                abs_jpg.write_bytes(b"")
        items.append(
            FrameItem(
                asset_relpath=asset_relpath,
                t0=t0,
                t1=t1,
                frame_t=frame_t,
                frame_relpath=rel,
                slice=slice_kind,
            )
        )

    for i, (t0, t1, frame_t) in enumerate(windows):
        add_item(t0, t1, frame_t, "window", i)

    if include_whole and duration > 0:
        mid = round(duration / 2.0, 6)
        add_item(0.0, round(duration, 6), mid, "whole", len(windows))

    return {
        "asset_relpath": asset_relpath,
        "abs_path": str(video),
        "duration_sec": round(duration, 6),
        "window_sec": window_sec,
        "max_windows": max_windows,
        "truncated": truncated,
        "frame_count": len(items),
        "frames": [asdict(x) for x in items],
    }


def build_frames_manifest(
    asset_results: Sequence[Dict[str, Any]],
    *,
    work_dir: Path,
    window_sec: float,
    max_windows: int,
    data_root: Optional[Path],
) -> Dict[str, Any]:
    frames: List[Dict[str, Any]] = []
    assets: List[Dict[str, Any]] = []
    for ar in asset_results:
        assets.append(
            {
                "asset_relpath": ar["asset_relpath"],
                "duration_sec": ar["duration_sec"],
                "truncated": ar["truncated"],
                "frame_count": ar["frame_count"],
            }
        )
        frames.extend(ar["frames"])
    return {
        "schema": SCHEMA_VERSION,
        "created_utc": utc_now(),
        "work_dir": str(work_dir.resolve()),
        "data_root": str(data_root.resolve()) if data_root else None,
        "window_sec": window_sec,
        "max_windows": max_windows,
        "asset_count": len(assets),
        "frame_count": len(frames),
        "assets": assets,
        "frames": frames,
    }


def write_frames_manifest(doc: Dict[str, Any], work_dir: Path) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / FRAMES_MANIFEST_NAME
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def run_sample(
    inputs: Sequence[str],
    *,
    data_root: Optional[Path],
    work_dir: Path,
    window_sec: float = DEFAULT_WINDOW_SEC,
    max_windows: int = DEFAULT_MAX_WINDOWS,
    include_whole: bool = True,
    ffprobe: str = "ffprobe",
    ffmpeg: str = "ffmpeg",
    extract: bool = True,
) -> Dict[str, Any]:
    if shutil.which(ffprobe) is None and extract:
        raise RuntimeError(f"ffprobe not on PATH ({ffprobe})")
    if extract and shutil.which(ffmpeg) is None:
        raise RuntimeError(f"ffmpeg not on PATH ({ffmpeg})")

    work_dir = work_dir.expanduser().resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []

    for raw in inputs:
        try:
            rel, abs_p = resolve_asset_path(raw, data_root=data_root)
            if not abs_p.is_file():
                raise FileNotFoundError(f"missing video: {abs_p}")
            results.append(
                sample_asset(
                    asset_relpath=rel,
                    video=abs_p,
                    work_dir=work_dir,
                    window_sec=window_sec,
                    max_windows=max_windows,
                    include_whole=include_whole,
                    ffprobe=ffprobe,
                    ffmpeg=ffmpeg,
                    extract=extract,
                )
            )
        except Exception as e:
            errors.append({"input": raw, "error": str(e)})

    doc = build_frames_manifest(
        results,
        work_dir=work_dir,
        window_sec=window_sec,
        max_windows=max_windows,
        data_root=data_root,
    )
    if errors:
        doc["errors"] = errors
    path = write_frames_manifest(doc, work_dir)
    doc["_manifest_path"] = str(path)
    return doc


def _env_path(name: str) -> Optional[Path]:
    raw = (os.environ.get(name) or "").strip()
    return Path(raw).expanduser() if raw else None


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Vision V1: sample mid-frames for time-slice captions")
    ap.add_argument(
        "--inputs",
        type=Path,
        help="Text file: one asset_relpath or absolute path per line (# comments ok)",
    )
    ap.add_argument(
        "--asset",
        action="append",
        default=[],
        help="Asset path (repeatable). Combined with --inputs",
    )
    ap.add_argument(
        "--data-root",
        type=Path,
        default=_env_path("VISION_DATA_ROOT"),
        help="Root containing og/ (or VISION_DATA_ROOT)",
    )
    ap.add_argument(
        "--work-dir",
        type=Path,
        default=_env_path("VISION_WORK_DIR"),
        help="Staging dir for frames + frames_manifest.json (or VISION_WORK_DIR)",
    )
    ap.add_argument("--window-sec", type=float, default=DEFAULT_WINDOW_SEC)
    ap.add_argument("--max-windows", type=int, default=DEFAULT_MAX_WINDOWS)
    ap.add_argument("--no-whole", action="store_true", help="Skip whole-video mid frame")
    ap.add_argument("--ffprobe", default=os.environ.get("VISION_FFPROBE", "ffprobe"))
    ap.add_argument("--ffmpeg", default=os.environ.get("VISION_FFMPEG", "ffmpeg"))
    ap.add_argument(
        "--no-extract",
        action="store_true",
        help="Plan windows and write empty JPEG placeholders (tests / dry planning)",
    )
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.work_dir is None:
        print("error: --work-dir or VISION_WORK_DIR required", file=sys.stderr)
        return 2

    inputs: List[str] = list(args.asset or [])
    if args.inputs:
        inputs.extend(parse_inputs_file(Path(args.inputs)))
    if not inputs:
        print("error: provide --inputs and/or --asset", file=sys.stderr)
        return 2

    data_root = Path(args.data_root).expanduser().resolve() if args.data_root else None
    try:
        doc = run_sample(
            inputs,
            data_root=data_root,
            work_dir=Path(args.work_dir),
            window_sec=float(args.window_sec),
            max_windows=int(args.max_windows),
            include_whole=not bool(args.no_whole),
            ffprobe=str(args.ffprobe),
            ffmpeg=str(args.ffmpeg),
            extract=not bool(args.no_extract),
        )
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "manifest": doc.get("_manifest_path"),
                "asset_count": doc.get("asset_count"),
                "frame_count": doc.get("frame_count"),
                "error_count": len(doc.get("errors") or []),
            },
            indent=2,
        )
    )
    return 0 if not doc.get("errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())
