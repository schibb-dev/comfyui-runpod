#!/usr/bin/env python3
"""
Optional: flag generated outputs that may contain "intruders" (multiple people in frame).

Run on a folder of frames or a video; writes a small report (e.g. which files have
person_count > 1) so you can filter or review those without watching every clip.

Requires: pip install ultralytics  (or use OpenCV DNN / another detector of your choice).
Usage:
  python scripts/check_output_intruders.py path/to/frames_or_video --out report.json
  python scripts/check_output_intruders.py path/to/video.mp4
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def _try_yolo_detect(image_paths: List[Path], confidence: float) -> List[Dict[str, Any]]:
    try:
        from ultralytics import YOLO
    except ImportError:
        return []  # Caller will report "install ultralytics"

    model = YOLO("yolov8n.pt")  # person class is 0 in COCO
    results = []
    for p in image_paths:
        if not p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            continue
        preds = model.predict(str(p), conf=confidence, verbose=False)
        count = 0
        for r in preds:
            if r.boxes is None:
                continue
            for cls in r.boxes.cls:
                if int(cls) == 0:  # COCO person
                    count += 1
        results.append({"path": str(p), "person_count": count})
    return results


def _frames_from_video(video_path: Path, sample_every_n: int) -> List[Path]:
    """Return list of extracted frame paths (requires opencv)."""
    try:
        import cv2
    except ImportError:
        return []
    out_dir = video_path.parent / f"_frames_{video_path.stem}"
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video_path))
    paths = []
    idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if idx % sample_every_n == 0:
            fp = out_dir / f"frame_{idx:06d}.jpg"
            cv2.imwrite(str(fp), frame)
            paths.append(fp)
        idx += 1
    cap.release()
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description="Flag outputs that may have multiple people (intruders)")
    ap.add_argument("path", type=Path, help="Folder of images, or a single video file")
    ap.add_argument("--out", type=Path, default=None, help="Output JSON report path")
    ap.add_argument("--confidence", type=float, default=0.4, help="Detection confidence threshold")
    ap.add_argument("--sample-every", type=int, default=16, help="For video: sample every N frames")
    ap.add_argument("--max-frames", type=int, default=100, help="Max frames to sample from video")
    args = ap.parse_args()

    path = args.path
    if not path.exists():
        print(f"Not found: {path}")
        return 1

    image_paths: List[Path] = []
    if path.is_file():
        suf = path.suffix.lower()
        if suf in (".mp4", ".webm", ".avi", ".mkv"):
            image_paths = _frames_from_video(path, args.sample_every)[: args.max_frames]
        elif suf in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            image_paths = [path]
    else:
        for suf in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
            image_paths.extend(path.glob(suf))
        image_paths = sorted(image_paths)[: args.max_frames]

    if not image_paths:
        print("No images or video frames to process.")
        return 0

    try:
        from ultralytics import YOLO
    except ImportError:
        report = {
            "error": "ultralytics not installed",
            "hint": "pip install ultralytics",
            "would_process": len(image_paths),
        }
        if args.out:
            args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        else:
            print(json.dumps(report, indent=2))
        return 0

    results = _try_yolo_detect(image_paths, args.confidence)
    max_count = max((r["person_count"] for r in results), default=0)
    flagged = [r for r in results if r["person_count"] > 1]
    report = {
        "source": str(path),
        "frames_checked": len(results),
        "max_person_count": max_count,
        "flagged_frames": len(flagged),
        "possible_intruders": [r for r in results if r["person_count"] > 1],
        "per_frame": results,
    }

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote {args.out} (flagged {len(flagged)} frames with >1 person)")
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
