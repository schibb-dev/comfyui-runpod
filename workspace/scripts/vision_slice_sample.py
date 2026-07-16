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
DEFAULT_EXCERPT_SEC = 0.0  # 0 = use full video
DEFAULT_EXCERPT_COUNT = 2
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


def plan_excerpt_span(
    duration_sec: float,
    *,
    excerpt_sec: float,
    mode: str = "mid",
) -> Tuple[float, float]:
    """
    Choose a contiguous [t0, t1] span inside the source video.

    If excerpt_sec <= 0 or >= duration, returns the full video (0, duration).
    mode: mid | start | end
    """
    dur = float(duration_sec)
    if dur <= 0:
        return (0.0, 0.0)
    ex = float(excerpt_sec)
    if ex <= 0 or ex >= dur - 1e-6:
        return (0.0, round(dur, 6))
    m = (mode or "mid").strip().lower()
    if m == "start":
        t0 = 0.0
    elif m == "end":
        t0 = max(0.0, dur - ex)
    else:
        # mid (default)
        t0 = max(0.0, (dur - ex) / 2.0)
    t1 = min(dur, t0 + ex)
    return (round(t0, 6), round(t1, 6))


def plan_excerpt_spans(
    duration_sec: float,
    *,
    excerpt_sec: float,
    count: int = DEFAULT_EXCERPT_COUNT,
    mode: str = "spread",
) -> List[Tuple[float, float]]:
    """
    Pick ``count`` consistent excerpt spans for one source video.

    Default ``mode=spread`` places spans evenly (for count=2: centered near 1/3 and
    2/3 of the timeline). Deterministic for a given duration — not content-aware.
    ``mid`` / ``start`` / ``end`` always return a single span (count ignored).
    """
    dur = float(duration_sec)
    ex = float(excerpt_sec)
    n = max(1, int(count))
    m = (mode or "spread").strip().lower()
    if dur <= 0:
        return []
    if ex <= 0:
        return [(0.0, round(dur, 6))]
    if m in {"mid", "start", "end"}:
        return [plan_excerpt_span(dur, excerpt_sec=ex, mode=m)]
    if ex >= dur - 1e-6:
        return [(0.0, round(dur, 6))]

    max_fit = max(1, int(dur // ex))
    n = min(n, max_fit)
    if n == 1:
        return [plan_excerpt_span(dur, excerpt_sec=ex, mode="mid")]

    spans: List[Tuple[float, float]] = []
    for i in range(n):
        center = dur * float(i + 1) / float(n + 1)
        t0 = center - ex / 2.0
        t1 = t0 + ex
        if t0 < 0:
            t0, t1 = 0.0, ex
        if t1 > dur:
            t0, t1 = dur - ex, dur
        spans.append((round(t0, 6), round(t1, 6)))

    fixed: List[Tuple[float, float]] = []
    cursor = 0.0
    for t0, t1 in spans:
        if t0 < cursor:
            t0 = cursor
            t1 = t0 + ex
        if t1 > dur + 1e-9:
            t1 = dur
            t0 = max(0.0, t1 - ex)
        if t1 - t0 < 1e-3:
            continue
        fixed.append((round(t0, 6), round(t1, 6)))
        cursor = t1
    return fixed or [plan_excerpt_span(dur, excerpt_sec=ex, mode="mid")]


def plan_windows(
    duration_sec: float,
    *,
    window_sec: float = DEFAULT_WINDOW_SEC,
    max_windows: int = DEFAULT_MAX_WINDOWS,
    offset_sec: float = 0.0,
) -> List[Tuple[float, float, float]]:
    """
    Fixed non-overlapping windows over ``duration_sec`` of content starting at
    ``offset_sec`` in the source timeline. Last window trimmed to the content end.
    Returns list of (t0, t1, frame_t) in **source** time.
    """
    if duration_sec <= 0:
        return []
    w = float(window_sec)
    if w <= 0:
        raise ValueError("window_sec must be > 0")
    offset = max(0.0, float(offset_sec))
    end = offset + float(duration_sec)
    cap = max(1, int(max_windows))
    out: List[Tuple[float, float, float]] = []
    t0 = offset
    while t0 < end - 1e-6 and len(out) < cap:
        t1 = min(t0 + w, end)
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
    excerpt_index: Optional[int] = None
    excerpt_video_relpath: Optional[str] = None
    excerpt_local_t: Optional[float] = None  # frame_t relative to excerpt start


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
    duration_sec: Optional[float] = None,
) -> None:
    out_jpeg.parent.mkdir(parents=True, exist_ok=True)
    t = max(0.0, float(frame_t))
    if duration_sec is not None and float(duration_sec) > 0:
        # Stay inside the stream — end-of-file seeks fail on short OG clips.
        t = min(t, max(0.0, float(duration_sec) - 0.05))
    # -ss after -i is slower but more accurate for mid-window frames.
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
        "-q:v",
        "2",
        str(out_jpeg),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=120)
    except FileNotFoundError as e:
        raise RuntimeError(f"ffmpeg not found ({ffmpeg})") from e
    if proc.returncode != 0 or not out_jpeg.is_file():
        raise RuntimeError(f"ffmpeg extract failed for {video}@{t}: {proc.stderr.strip()}")


def extract_excerpt_mp4(
    video: Path,
    *,
    t0: float,
    t1: float,
    out_mp4: Path,
    ffmpeg: str = "ffmpeg",
    force: bool = False,
) -> bool:
    """
    Write a short contiguous clip [t0, t1] to ``out_mp4`` for the review UI.

    Idempotent: if ``out_mp4`` already exists and is non-empty, skip ffmpeg
    unless ``force=True``. Prefer stream-copy for speed; fall back to a light
    re-encode if copy fails (odd codecs / non-keyframes).

    Returns True when ffmpeg ran, False when an existing file was reused.
    """
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    if not force and out_mp4.is_file() and out_mp4.stat().st_size > 0:
        return False
    dur = max(0.05, float(t1) - float(t0))
    common = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{float(t0):.6f}",
        "-i",
        str(video),
        "-t",
        f"{dur:.6f}",
    ]
    copy_cmd = common + ["-c", "copy", "-movflags", "+faststart", str(out_mp4)]
    try:
        proc = subprocess.run(copy_cmd, capture_output=True, text=True, check=False, timeout=300)
    except FileNotFoundError as e:
        raise RuntimeError(f"ffmpeg not found ({ffmpeg})") from e
    if proc.returncode == 0 and out_mp4.is_file() and out_mp4.stat().st_size > 0:
        return True
    # Re-encode fallback (more reliable than stream copy across codecs).
    re_cmd = common + [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(out_mp4),
    ]
    proc2 = subprocess.run(re_cmd, capture_output=True, text=True, check=False, timeout=600)
    if proc2.returncode != 0 or not out_mp4.is_file() or out_mp4.stat().st_size <= 0:
        raise RuntimeError(
            f"ffmpeg excerpt failed for {video} [{t0},{t1}]: "
            f"{(proc2.stderr or proc.stderr or '').strip()}"
        )
    return True


def safe_stem(relpath: str) -> str:
    base = Path(relpath.replace("\\", "/")).name
    stem = Path(base).stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "asset"
    return stem[:80]



def resolve_excerpt_media_dir(
    *,
    work_dir: Path,
    data_root: Optional[Path],
    excerpt_media_dir: Optional[Path] = None,
) -> Path:
    """Prefer durable status-adjacent dir so Experiments UI can /files serve clips."""
    if excerpt_media_dir is not None:
        return excerpt_media_dir.expanduser().resolve()
    env = (os.environ.get("VISION_EXCERPT_MEDIA_DIR") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if data_root is not None:
        root = data_root.expanduser().resolve()
        # Prefer output/_status (Experiments /files root) over a sibling _status.
        candidates = [
            root / "output" / "_status",
            root / "_status",
        ]
        # If data_root already *is* the output tree, use its _status.
        if root.name == "output" or (root / "og").is_dir():
            candidates.insert(0, root / "_status")
        for status in candidates:
            try:
                if status.is_dir():
                    return (status / "vision_slice_excerpts").resolve()
            except OSError:
                continue
        # Create under output/_status when possible so /files can serve clips.
        for status in candidates:
            try:
                status.mkdir(parents=True, exist_ok=True)
                return (status / "vision_slice_excerpts").resolve()
            except OSError:
                continue
    return (work_dir / "excerpts").resolve()


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
    excerpt_sec: float = DEFAULT_EXCERPT_SEC,
    excerpt_mode: str = "spread",
    excerpt_count: int = DEFAULT_EXCERPT_COUNT,
    excerpt_media_dir: Optional[Path] = None,
    data_root: Optional[Path] = None,
    force_excerpts: bool = False,
) -> Dict[str, Any]:
    source_duration = probe_duration_sec(video, ffprobe=ffprobe)
    if float(excerpt_sec) > 0:
        spans = plan_excerpt_spans(
            source_duration,
            excerpt_sec=float(excerpt_sec),
            count=int(excerpt_count),
            mode=excerpt_mode,
        )
    else:
        spans = [(0.0, round(source_duration, 6))] if source_duration > 0 else []

    stem = safe_stem(asset_relpath)
    media_dir = resolve_excerpt_media_dir(
        work_dir=work_dir, data_root=data_root, excerpt_media_dir=excerpt_media_dir
    )
    items: List[FrameItem] = []
    frame_idx = 0
    truncated = bool(float(excerpt_sec) > 0 and source_duration > float(excerpt_sec) + 1e-3)
    excerpt_metas: List[Dict[str, Any]] = []

    def add_item(
        t0: float,
        t1: float,
        frame_t: float,
        slice_kind: str,
        *,
        excerpt_index: Optional[int],
        excerpt_t0: float,
        excerpt_video_relpath: Optional[str],
    ) -> None:
        nonlocal frame_idx
        name = f"{stem}_{frame_idx:03d}_{slice_kind}_{frame_t:.3f}.jpg"
        rel = f"frames/{stem}/{name}".replace("\\", "/")
        abs_jpg = work_dir / rel
        if extract:
            extract_frame_jpeg(
                video,
                frame_t=frame_t,
                out_jpeg=abs_jpg,
                ffmpeg=ffmpeg,
                duration_sec=source_duration,
            )
        else:
            abs_jpg.parent.mkdir(parents=True, exist_ok=True)
            if not abs_jpg.is_file():
                abs_jpg.write_bytes(b"")
        local_t = round(float(frame_t) - float(excerpt_t0), 6)
        items.append(
            FrameItem(
                asset_relpath=asset_relpath,
                t0=t0,
                t1=t1,
                frame_t=frame_t,
                frame_relpath=rel,
                slice=slice_kind,
                excerpt_index=excerpt_index,
                excerpt_video_relpath=excerpt_video_relpath,
                excerpt_local_t=local_t if excerpt_video_relpath else None,
            )
        )
        frame_idx += 1

    for ei, (excerpt_t0, excerpt_t1) in enumerate(spans):
        span = max(0.0, excerpt_t1 - excerpt_t0)
        excerpt_video_relpath: Optional[str] = None
        if float(excerpt_sec) > 0:
            out_mp4 = media_dir / stem / f"ex{ei:02d}.mp4"
            if extract:
                extract_excerpt_mp4(
                    video,
                    t0=excerpt_t0,
                    t1=excerpt_t1,
                    out_mp4=out_mp4,
                    ffmpeg=ffmpeg,
                    force=force_excerpts,
                )
            else:
                out_mp4.parent.mkdir(parents=True, exist_ok=True)
                if not out_mp4.is_file():
                    out_mp4.write_bytes(b"")
            excerpt_video_relpath = None
            roots = []
            if data_root is not None:
                # Prefer output/ (Experiments /files root) before workspace root.
                roots.extend([(data_root / "output").resolve(), data_root.resolve()])
            for root in roots:
                try:
                    excerpt_video_relpath = str(out_mp4.resolve().relative_to(root)).replace("\\", "/")
                    break
                except ValueError:
                    continue
            if excerpt_video_relpath is None:
                excerpt_video_relpath = out_mp4.name
            excerpt_metas.append(
                {
                    "index": ei,
                    "t0": excerpt_t0,
                    "t1": excerpt_t1,
                    "video_relpath": excerpt_video_relpath,
                    "video_abs": str(out_mp4.resolve()),
                }
            )

        windows = plan_windows(
            span,
            window_sec=window_sec,
            max_windows=max_windows,
            offset_sec=excerpt_t0,
        )
        if len(windows) >= max_windows and windows and windows[-1][1] < excerpt_t1 - 1e-3:
            truncated = True
        for t0, t1, frame_t in windows:
            add_item(
                t0,
                t1,
                frame_t,
                "window",
                excerpt_index=ei if float(excerpt_sec) > 0 else None,
                excerpt_t0=excerpt_t0,
                excerpt_video_relpath=excerpt_video_relpath,
            )
        if include_whole and span > 0:
            mid = round((excerpt_t0 + excerpt_t1) / 2.0, 6)
            add_item(
                excerpt_t0,
                excerpt_t1,
                mid,
                "whole",
                excerpt_index=ei if float(excerpt_sec) > 0 else None,
                excerpt_t0=excerpt_t0,
                excerpt_video_relpath=excerpt_video_relpath,
            )

    out: Dict[str, Any] = {
        "asset_relpath": asset_relpath,
        "abs_path": str(video),
        "duration_sec": round(source_duration, 6),
        "window_sec": window_sec,
        "max_windows": max_windows,
        "truncated": truncated,
        "frame_count": len(items),
        "frames": [asdict(x) for x in items],
    }
    if float(excerpt_sec) > 0:
        out["excerpt_sec"] = float(excerpt_sec)
        out["excerpt_mode"] = excerpt_mode
        out["excerpt_count"] = len(spans)
        out["excerpts"] = excerpt_metas
        out["excerpt_media_dir"] = str(media_dir)
    return out



def build_frames_manifest(
    asset_results: Sequence[Dict[str, Any]],
    *,
    work_dir: Path,
    window_sec: float,
    max_windows: int,
    data_root: Optional[Path],
    excerpt_sec: float = DEFAULT_EXCERPT_SEC,
    excerpt_mode: str = "spread",
    excerpt_count: int = DEFAULT_EXCERPT_COUNT,
) -> Dict[str, Any]:
    frames: List[Dict[str, Any]] = []
    assets: List[Dict[str, Any]] = []
    for ar in asset_results:
        assets.append(
            {
                "asset_relpath": ar["asset_relpath"],
                "duration_sec": ar["duration_sec"],
                "excerpts": ar.get("excerpts"),
                "truncated": ar["truncated"],
                "frame_count": ar["frame_count"],
            }
        )
        frames.extend(ar["frames"])
    doc: Dict[str, Any] = {
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
    if float(excerpt_sec) > 0:
        doc["excerpt_sec"] = float(excerpt_sec)
        doc["excerpt_mode"] = excerpt_mode
        doc["excerpt_count"] = int(excerpt_count)
    return doc



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
    excerpt_sec: float = DEFAULT_EXCERPT_SEC,
    excerpt_mode: str = "spread",
    excerpt_count: int = DEFAULT_EXCERPT_COUNT,
    excerpt_media_dir: Optional[Path] = None,
    force_excerpts: bool = False,
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
                    excerpt_sec=excerpt_sec,
                    excerpt_mode=excerpt_mode,
                    excerpt_count=excerpt_count,
                    excerpt_media_dir=excerpt_media_dir,
                    data_root=data_root,
                    force_excerpts=force_excerpts,
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
        excerpt_sec=excerpt_sec,
        excerpt_mode=excerpt_mode,
        excerpt_count=excerpt_count,
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
    ap.add_argument(
        "--excerpt-sec",
        type=float,
        default=float(os.environ.get("VISION_EXCERPT_SEC") or DEFAULT_EXCERPT_SEC),
        help="If >0, sample this many seconds per excerpt instead of the full video",
    )
    ap.add_argument(
        "--excerpt-count",
        type=int,
        default=int(os.environ.get("VISION_EXCERPT_COUNT") or DEFAULT_EXCERPT_COUNT),
        help="How many consistent excerpts per video (default 2; uses --excerpt-mode spread)",
    )
    ap.add_argument(
        "--excerpt-mode",
        choices=["spread", "mid", "start", "end"],
        default=os.environ.get("VISION_EXCERPT_MODE", "spread"),
        help="spread=evenly spaced excerpts (default); mid/start/end=single span",
    )
    ap.add_argument(
        "--excerpt-media-dir",
        type=Path,
        default=_env_path("VISION_EXCERPT_MEDIA_DIR"),
        help="Where to write excerpt MP4s (default: <status>/vision_slice_excerpts under data-root)",
    )
    ap.add_argument(
        "--force-excerpts",
        action="store_true",
        help="Re-cut excerpt MP4s even when non-empty files already exist (default: reuse)",
    )
    ap.add_argument("--no-whole", action="store_true", help="Skip whole-excerpt mid frame")
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
            excerpt_sec=float(args.excerpt_sec),
            excerpt_mode=str(args.excerpt_mode),
            excerpt_count=int(args.excerpt_count),
            excerpt_media_dir=Path(args.excerpt_media_dir) if args.excerpt_media_dir else None,
            force_excerpts=bool(args.force_excerpts),
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
                "excerpt_sec": doc.get("excerpt_sec"),
                "excerpt_mode": doc.get("excerpt_mode"),
                "excerpt_count": doc.get("excerpt_count"),
                "error_count": len(doc.get("errors") or []),
            },
            indent=2,
        )
    )
    return 0 if not doc.get("errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())
