#!/usr/bin/env python3
"""
Vision V1 — caption frames_manifest.json into NDJSON (run-anywhere).

Providers (via ``vision_slice_runner.make_runner``):

- ``--dry-run`` / ``--provider dry-run`` — placeholders, no GPU
- ``--provider comfy`` — ComfyUI Florence2 over HTTP (local Docker or RunPod :8188)
- ``--provider transformers`` — in-process Florence (optional torch deps)

See docs/VISION_V1_TIME_SLICE_CAPTION_SPIKE.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from vision_slice_runner import (
    DEFAULT_COMFY_MODEL,
    CaptionRequest,
    make_runner,
)

SCHEMA_VERSION = 1
DEFAULT_MODEL_PIN = DEFAULT_COMFY_MODEL
CAPTIONS_NDJSON = "vision_slice_captions.ndjson"
SLICE_MANIFEST = "vision_slice_manifest.json"


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


def normalize_tag(raw: str) -> str:
    t = str(raw or "").strip().lower()
    t = re.sub(r"[^a-z0-9_]+", "", t)
    return t


def tags_from_caption(caption: str, *, max_tags: int = 16) -> List[str]:
    text = caption or ""
    if text.startswith("[dry-run]"):
        return []
    words = re.findall(r"[a-z][a-z0-9]{2,}", text.lower())
    out: List[str] = []
    seen = set()
    for w in words:
        t = normalize_tag(w)
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= max_tags:
            break
    return out


def dry_run_caption(frame: Dict[str, Any]) -> str:
    rel = frame.get("asset_relpath") or "asset"
    slice_kind = frame.get("slice") or "window"
    t0 = frame.get("t0")
    t1 = frame.get("t1")
    return f"[dry-run] {slice_kind} {t0}-{t1}s of {rel}"


def resolve_frame_path(frame: Dict[str, Any], *, work_dir: Path) -> Path:
    rel = str(frame.get("frame_relpath") or "").replace("\\", "/")
    if not rel:
        raise ValueError("frame missing frame_relpath")
    return (work_dir / rel).resolve()


def build_row(
    frame: Dict[str, Any],
    *,
    caption: str,
    provider: str,
    model_pin: str,
    run_id: str,
    runner: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "asset_relpath": frame.get("asset_relpath"),
        "content_id": frame.get("content_id"),
        "t0": frame.get("t0"),
        "t1": frame.get("t1"),
        "frame_t": frame.get("frame_t"),
        "caption": caption,
        "tags": tags_from_caption(caption),
        "provider": provider,
        "model_pin": model_pin,
        "run_id": run_id,
        "runner": runner,
        "frame_relpath": frame.get("frame_relpath"),
    }
    if frame.get("slice"):
        row["slice"] = frame.get("slice")
    if extra:
        row["runner_raw"] = extra
    return row


def default_status_dir(data_root: Optional[Path]) -> Optional[Path]:
    """Prefer ``_status`` beside ``og/`` when data_root is the output bind."""
    if data_root is None:
        return None
    root = data_root.expanduser().resolve()
    candidates = [
        root / "_status",
        root / "output" / "_status",
    ]
    for cand in candidates:
        if cand.is_dir():
            return cand
    if (root / "og").is_dir():
        return root / "_status"
    return root / "output" / "_status"


def run_caption(
    frames_manifest: Path,
    *,
    status_dir: Path,
    work_dir: Optional[Path] = None,
    run_id: str,
    runner: str,
    model_pin: str = DEFAULT_MODEL_PIN,
    device: str = "cuda",
    dry_run: bool = False,
    append: bool = False,
    provider: str = "comfy",
    comfy_server: str = "http://127.0.0.1:8188",
    image_mode: str = "upload",
    comfy_input_root: Optional[Path] = None,
) -> Dict[str, Any]:
    doc = load_frames_manifest(frames_manifest)
    wd = Path(work_dir or doc.get("work_dir") or frames_manifest.parent).expanduser().resolve()
    status_dir = status_dir.expanduser().resolve()
    status_dir.mkdir(parents=True, exist_ok=True)

    ndjson_path = status_dir / CAPTIONS_NDJSON
    if not append and ndjson_path.exists():
        ndjson_path.unlink()

    cap_runner = make_runner(
        provider=provider,
        runner_label=runner,
        comfy_server=comfy_server,
        model_pin=model_pin,
        device=device,
        image_mode=image_mode,
        comfy_input_root=comfy_input_root,
        dry_run=dry_run,
    )

    rows: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    started = utc_now()
    used_provider = "dry-run" if dry_run else provider
    used_pin = "dry-run" if dry_run else model_pin

    try:
        with ndjson_path.open("a", encoding="utf-8") as fh:
            for frame in doc.get("frames") or []:
                if not isinstance(frame, dict):
                    continue
                try:
                    img = resolve_frame_path(frame, work_dir=wd)
                    result = cap_runner.caption(
                        CaptionRequest(
                            image_path=img,
                            asset_relpath=str(frame.get("asset_relpath") or ""),
                            frame_relpath=str(frame.get("frame_relpath") or ""),
                            meta={
                                "slice": frame.get("slice"),
                                "t0": frame.get("t0"),
                                "t1": frame.get("t1"),
                                "frame_t": frame.get("frame_t"),
                            },
                        )
                    )
                    used_provider = result.provider
                    used_pin = result.model_pin
                    row = build_row(
                        frame,
                        caption=result.caption,
                        provider=result.provider,
                        model_pin=result.model_pin,
                        run_id=run_id,
                        runner=result.runner or runner,
                        extra=result.raw or None,
                    )
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    rows.append(row)
                except Exception as e:
                    errors.append(
                        {
                            "asset_relpath": str(frame.get("asset_relpath") or ""),
                            "frame_relpath": str(frame.get("frame_relpath") or ""),
                            "error": str(e),
                        }
                    )
    finally:
        try:
            cap_runner.close()
        except Exception:
            pass

    finished = utc_now()
    manifest = {
        "schema": SCHEMA_VERSION,
        "run_id": run_id,
        "runner": runner,
        "provider": used_provider,
        "model_pin": used_pin,
        "comfy_server": comfy_server if provider in ("comfy", "runpod", "comfyui") else None,
        "image_mode": image_mode if provider in ("comfy", "runpod", "comfyui") else None,
        "device": device if provider in ("transformers", "florence2") else None,
        "dry_run": dry_run,
        "started_utc": started,
        "finished_utc": finished,
        "frames_manifest": str(frames_manifest.resolve()),
        "work_dir": str(wd),
        "status_dir": str(status_dir),
        "window_sec": doc.get("window_sec"),
        "asset_count": doc.get("asset_count"),
        "frame_count": doc.get("frame_count"),
        "caption_count": len(rows),
        "error_count": len(errors),
        "errors": errors,
        "ndjson": str(ndjson_path),
    }
    manifest_path = status_dir / SLICE_MANIFEST
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest["_manifest_path"] = str(manifest_path)
    return manifest


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Vision V1: caption sampled frames to NDJSON")
    ap.add_argument(
        "--frames-manifest",
        type=Path,
        required=True,
        help="frames_manifest.json from vision_slice_sample.py",
    )
    ap.add_argument(
        "--status-dir",
        type=Path,
        default=_env_path("VISION_STATUS_DIR"),
        help="Output dir for NDJSON + vision_slice_manifest.json (or VISION_STATUS_DIR)",
    )
    ap.add_argument(
        "--work-dir",
        type=Path,
        default=_env_path("VISION_WORK_DIR"),
        help="Override work dir containing frames/ (default: from frames manifest)",
    )
    ap.add_argument(
        "--data-root",
        type=Path,
        default=_env_path("VISION_DATA_ROOT"),
        help="Used only to default status-dir when --status-dir unset",
    )
    ap.add_argument("--run-id", required=True, help="e.g. vision_v1_20260714")
    ap.add_argument(
        "--runner",
        default=os.environ.get("VISION_RUNNER", "local"),
        help="Label recorded in outputs: local | docker | runpod | comfy | …",
    )
    ap.add_argument(
        "--provider",
        default=os.environ.get("VISION_PROVIDER", "comfy"),
        choices=["comfy", "runpod", "comfyui", "transformers", "florence2", "dry-run"],
        help="Caption backend (comfy/runpod share HTTP API; transformers = in-process)",
    )
    ap.add_argument(
        "--comfy-server",
        default=os.environ.get("VISION_COMFY_SERVER", "http://127.0.0.1:8188"),
        help="ComfyUI base URL (local or RunPod-mapped :8188)",
    )
    ap.add_argument(
        "--image-mode",
        default=os.environ.get("VISION_COMFY_IMAGE_MODE", "upload"),
        choices=["upload", "input_copy"],
        help="How frames reach Comfy LoadImage (upload preferred for RunPod)",
    )
    ap.add_argument(
        "--comfy-input-root",
        type=Path,
        default=_env_path("VISION_COMFY_INPUT_ROOT"),
        help="Host path to Comfy input/ for image_mode=input_copy",
    )
    ap.add_argument(
        "--model-pin",
        default=os.environ.get("VISION_MODEL_PIN", DEFAULT_MODEL_PIN),
        help="Florence weights id (Comfy DownloadAndLoadFlorence2Model.model or HF id)",
    )
    ap.add_argument(
        "--device",
        default=os.environ.get("VISION_DEVICE", "cuda"),
        help="Device for --provider transformers only",
    )
    ap.add_argument("--dry-run", action="store_true", help="Placeholder captions; no model / Comfy")
    ap.add_argument("--append", action="store_true", help="Append to existing NDJSON instead of replace")
    args = ap.parse_args(list(argv) if argv is not None else None)

    status_dir = args.status_dir
    if status_dir is None:
        dr = Path(args.data_root).expanduser().resolve() if args.data_root else None
        status_dir = default_status_dir(dr)
    if status_dir is None:
        print("error: --status-dir or VISION_STATUS_DIR (or --data-root) required", file=sys.stderr)
        return 2

    provider = "dry-run" if args.dry_run else str(args.provider)
    try:
        manifest = run_caption(
            Path(args.frames_manifest),
            status_dir=Path(status_dir),
            work_dir=Path(args.work_dir) if args.work_dir else None,
            run_id=str(args.run_id),
            runner=str(args.runner),
            model_pin=str(args.model_pin),
            device=str(args.device),
            dry_run=bool(args.dry_run),
            append=bool(args.append),
            provider=provider,
            comfy_server=str(args.comfy_server),
            image_mode=str(args.image_mode),
            comfy_input_root=Path(args.comfy_input_root) if args.comfy_input_root else None,
        )
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "manifest": manifest.get("_manifest_path"),
                "ndjson": manifest.get("ndjson"),
                "caption_count": manifest.get("caption_count"),
                "error_count": manifest.get("error_count"),
                "provider": manifest.get("provider"),
                "dry_run": manifest.get("dry_run"),
            },
            indent=2,
        )
    )
    return 0 if not manifest.get("error_count") else 1


if __name__ == "__main__":
    raise SystemExit(main())
