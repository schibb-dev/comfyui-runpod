#!/usr/bin/env python3
"""
Vision V1 — classical frame quality on frames_manifest.json (run-anywhere).

Writes ``vision_slice_quality.ndjson`` + ``vision_slice_manifest__quality.json``.
Uses Pillow when available; otherwise ffmpeg raw gray decode.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from vision_slice_quality_metrics import Gray, rollup_quality_rows, score_frame

SCHEMA_VERSION = 1
QUALITY_NDJSON = "vision_slice_quality.ndjson"
QUALITY_MANIFEST = "vision_slice_manifest__quality.json"
QUALITY_VARIANT = "quality"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _env_path(name: str) -> Optional[Path]:
    raw = (os.environ.get(name) or "").strip()
    return Path(raw).expanduser() if raw else None


def load_frames_manifest(path: Path) -> Dict[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("frames_manifest must be a JSON object")
    frames = doc.get("frames")
    if not isinstance(frames, list):
        raise ValueError("frames_manifest.frames must be a list")
    return doc


def resolve_frame_path(frame: Dict[str, Any], *, work_dir: Path) -> Path:
    rel = str(frame.get("frame_relpath") or "").replace("\\", "/")
    if not rel:
        raise ValueError("frame missing frame_relpath")
    return (work_dir / rel).resolve()


def _gray_from_pil(path: Path, *, max_side: int = 384) -> Gray:
    from PIL import Image  # type: ignore

    with Image.open(path) as im:
        im = im.convert("L")
        w, h = im.size
        scale = min(1.0, float(max_side) / float(max(w, h, 1)))
        if scale < 1.0:
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.BILINEAR)
        pixels = list(im.getdata())
        ww, hh = im.size
    out: Gray = []
    for y in range(hh):
        row = pixels[y * ww : (y + 1) * ww]
        out.append([p / 255.0 for p in row])
    return out


def _gray_from_ffmpeg(path: Path, *, ffmpeg: str = "ffmpeg", max_side: int = 384) -> Gray:
    """Decode JPEG/PNG to downscaled gray via ffmpeg (no Pillow required)."""
    ffprobe = "ffprobe"
    if ffmpeg.endswith("ffmpeg"):
        ffprobe = ffmpeg[: -len("ffmpeg")] + "ffprobe"
    probe = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    w = h = 0
    if probe.returncode == 0 and "x" in (probe.stdout or ""):
        try:
            w_s, h_s = probe.stdout.strip().split("x", 1)
            w, h = int(w_s), int(h_s)
        except ValueError:
            w = h = 0
    if w <= 0 or h <= 0:
        w = h = max_side
    scale = min(1.0, float(max_side) / float(max(w, h, 1)))
    ow = max(1, int(w * scale))
    oh = max(1, int(h * scale))
    # Keep even dims for some codecs; gray raw doesn't care but be safe.
    ow -= ow % 2
    oh -= oh % 2
    ow = max(2, ow)
    oh = max(2, oh)
    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vf",
            f"scale={ow}:{oh},format=gray",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
        check=False,
        timeout=60,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f"ffmpeg gray decode failed for {path}: {proc.stderr!r}")
    data = proc.stdout
    expect = ow * oh
    if len(data) < expect:
        raise RuntimeError(f"ffmpeg gray decode short for {path}: {len(data)} < {expect}")
    out: Gray = []
    for y in range(oh):
        base = y * ow
        out.append([data[base + x] / 255.0 for x in range(ow)])
    return out


def load_gray(path: Path, *, ffmpeg: str = "ffmpeg", max_side: int = 384) -> Gray:
    try:
        return _gray_from_pil(path, max_side=max_side)
    except Exception:
        return _gray_from_ffmpeg(path, ffmpeg=ffmpeg, max_side=max_side)


def run_quality(
    frames_manifest: Path,
    *,
    status_dir: Path,
    work_dir: Optional[Path] = None,
    run_id: str = "",
    ffmpeg: str = "ffmpeg",
    max_side: int = 384,
) -> Dict[str, Any]:
    doc = load_frames_manifest(frames_manifest)
    wd = Path(work_dir or doc.get("work_dir") or frames_manifest.parent).expanduser().resolve()
    status_dir = status_dir.expanduser().resolve()
    status_dir.mkdir(parents=True, exist_ok=True)
    rid = run_id or f"vision_quality_{utc_now().replace(':', '')}"

    frames = [f for f in (doc.get("frames") or []) if isinstance(f, dict)]
    # Process in asset order then time so convergence uses previous sample.
    frames.sort(
        key=lambda f: (
            str(f.get("asset_relpath") or ""),
            float(f.get("frame_t") or f.get("t0") or 0.0),
            str(f.get("slice") or ""),
        )
    )

    nd_path = status_dir / QUALITY_NDJSON
    man_path = status_dir / QUALITY_MANIFEST
    started = utc_now()
    wall0 = time.perf_counter()

    progress = {
        "schema": SCHEMA_VERSION,
        "run_id": rid,
        "variant": QUALITY_VARIANT,
        "status": "running",
        "started_utc": started,
        "finished_utc": None,
        "frames_manifest": str(frames_manifest.resolve()),
        "work_dir": str(wd),
        "status_dir": str(status_dir),
        "asset_count": doc.get("asset_count"),
        "frame_count": len(frames),
        "caption_count": 0,
        "error_count": 0,
        "ndjson": str(nd_path),
        "provider": "classical_cv",
        "model_pin": "classical/laplacian_blockiness_v1",
        "task": "frame_quality",
    }
    man_path.write_text(json.dumps(progress, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    rows: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    prev_by_asset: Dict[str, Gray] = {}

    with nd_path.open("w", encoding="utf-8") as fh:
        for frame in frames:
            asset = str(frame.get("asset_relpath") or "")
            try:
                img_path = resolve_frame_path(frame, work_dir=wd)
                if not img_path.is_file():
                    raise FileNotFoundError(str(img_path))
                gray = load_gray(img_path, ffmpeg=ffmpeg, max_side=max_side)
                prev = prev_by_asset.get(asset)
                quality = score_frame(gray, prev=prev)
                prev_by_asset[asset] = gray
                row = {
                    "schema": SCHEMA_VERSION,
                    "asset_relpath": asset,
                    "content_id": frame.get("content_id"),
                    "t0": frame.get("t0"),
                    "t1": frame.get("t1"),
                    "frame_t": frame.get("frame_t"),
                    "slice": frame.get("slice") or "window",
                    "frame_relpath": frame.get("frame_relpath"),
                    "excerpt_index": frame.get("excerpt_index"),
                    "quality": quality,
                    "provider": "classical_cv",
                    "model_pin": "classical/laplacian_blockiness_v1",
                    "run_id": rid,
                    "task": "frame_quality",
                }
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                rows.append(row)
            except Exception as e:
                errors.append(
                    {
                        "asset_relpath": asset,
                        "frame_relpath": str(frame.get("frame_relpath") or ""),
                        "error": str(e),
                    }
                )

    finished = utc_now()
    wall_s = time.perf_counter() - wall0

    # Asset rollups for the manifest (UI can also compute from NDJSON).
    by_asset: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_asset.setdefault(str(r.get("asset_relpath") or ""), []).append(r)
    asset_quality = {a: rollup_quality_rows(rs) for a, rs in by_asset.items()}

    manifest = {
        "schema": SCHEMA_VERSION,
        "run_id": rid,
        "variant": QUALITY_VARIANT,
        "status": "complete",
        "provider": "classical_cv",
        "model_pin": "classical/laplacian_blockiness_v1",
        "task": "frame_quality",
        "started_utc": started,
        "finished_utc": finished,
        "frames_manifest": str(frames_manifest.resolve()),
        "work_dir": str(wd),
        "status_dir": str(status_dir),
        "asset_count": len(by_asset),
        "frame_count": len(frames),
        "caption_count": len(rows),
        "error_count": len(errors),
        "errors": errors[:50],
        "ndjson": str(nd_path),
        "timing": {"wall_s": round(wall_s, 3)},
        "asset_quality": asset_quality,
    }
    man_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest["_manifest_path"] = str(man_path)
    return manifest


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Vision V1: classical frame quality → NDJSON")
    ap.add_argument("--frames-manifest", type=Path, required=True)
    ap.add_argument(
        "--status-dir",
        type=Path,
        default=None,
        help="Output dir for NDJSON + quality manifest (or VISION_STATUS_DIR)",
    )
    ap.add_argument("--work-dir", type=Path, default=None)
    ap.add_argument("--run-id", default="")
    ap.add_argument("--ffmpeg", default=os.environ.get("FFMPEG", "ffmpeg"))
    ap.add_argument("--max-side", type=int, default=384)
    args = ap.parse_args(list(argv) if argv is not None else None)

    status = args.status_dir or _env_path("VISION_STATUS_DIR")
    if status is None:
        print("error: --status-dir or VISION_STATUS_DIR required", file=sys.stderr)
        return 2

    man = run_quality(
        Path(args.frames_manifest),
        status_dir=Path(status),
        work_dir=Path(args.work_dir) if args.work_dir else None,
        run_id=str(args.run_id or ""),
        ffmpeg=str(args.ffmpeg),
        max_side=int(args.max_side),
    )
    print(
        json.dumps(
            {
                "ok": True,
                "manifest": man.get("_manifest_path"),
                "ndjson": man.get("ndjson"),
                "frame_count": man.get("caption_count"),
                "error_count": man.get("error_count"),
                "wall_s": (man.get("timing") or {}).get("wall_s"),
            },
            indent=2,
        )
    )
    return 0 if not man.get("error_count") else 1


if __name__ == "__main__":
    raise SystemExit(main())
