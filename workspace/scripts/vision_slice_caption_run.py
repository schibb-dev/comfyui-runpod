#!/usr/bin/env python3
"""
Vision V1 — caption frames_manifest.json into NDJSON (run-anywhere).

Default provider for real GPU runs: Florence-2 via transformers (optional dep).
``--dry-run`` writes placeholder captions without loading a model.

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

SCHEMA_VERSION = 1
DEFAULT_MODEL_PIN = "florence-community/Florence-2-base"
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
    words = re.findall(r"[a-z][a-z0-9]{2,}", (caption or "").lower())
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


class CaptionProvider:
    def caption_image(self, image_path: Path) -> str:
        raise NotImplementedError


class DryRunProvider(CaptionProvider):
    def __init__(self, frame: Optional[Dict[str, Any]] = None) -> None:
        self._frame = frame

    def caption_image(self, image_path: Path) -> str:
        if self._frame is not None:
            return dry_run_caption(self._frame)
        return f"[dry-run] {image_path.name}"


class Florence2Provider(CaptionProvider):
    """Lazy Florence-2 captioner (transformers)."""

    def __init__(self, *, model_pin: str, device: str) -> None:
        self.model_pin = model_pin
        self.device = device
        self._model = None
        self._processor = None

    def _ensure(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            from PIL import Image  # noqa: F401
            from transformers import AutoModelForCausalLM, AutoProcessor
        except ImportError as e:
            raise RuntimeError(
                "Florence captioning requires torch, transformers, and Pillow. "
                "Use --dry-run without a GPU stack, or install those deps on the runner."
            ) from e

        self._processor = AutoProcessor.from_pretrained(self.model_pin, trust_remote_code=True)
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_pin,
            trust_remote_code=True,
            torch_dtype="auto",
        )
        if self.device.startswith("cuda"):
            self._model = self._model.to(self.device)
        self._model.eval()
        self._torch = torch
        from PIL import Image

        self._Image = Image

    def caption_image(self, image_path: Path) -> str:
        self._ensure()
        assert self._model is not None and self._processor is not None
        image = self._Image.open(image_path).convert("RGB")
        task = "<CAPTION>"
        inputs = self._processor(text=task, images=image, return_tensors="pt")
        if self.device.startswith("cuda"):
            inputs = {k: v.to(self.device) if hasattr(v, "to") else v for k, v in inputs.items()}
        with self._torch.no_grad():
            generated = self._model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs.get("pixel_values"),
                max_new_tokens=64,
                num_beams=1,
                do_sample=False,
            )
        text = self._processor.batch_decode(generated, skip_special_tokens=False)[0]
        parsed = self._processor.post_process_generation(
            text, task=task, image_size=(image.width, image.height)
        )
        if isinstance(parsed, dict):
            cap = parsed.get(task) or parsed.get("<CAPTION>") or next(iter(parsed.values()), "")
            return str(cap).strip()
        return str(parsed).strip()


def build_row(
    frame: Dict[str, Any],
    *,
    caption: str,
    provider: str,
    model_pin: str,
    run_id: str,
    runner: str,
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
    return row


def default_status_dir(data_root: Optional[Path]) -> Optional[Path]:
    if data_root is None:
        return None
    # Prefer existing _status; else default under output/
    for cand in (data_root / "output" / "_status", data_root / "_status"):
        if cand.is_dir():
            return cand
    return data_root / "output" / "_status"


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
) -> Dict[str, Any]:
    doc = load_frames_manifest(frames_manifest)
    wd = Path(work_dir or doc.get("work_dir") or frames_manifest.parent).expanduser().resolve()
    status_dir = status_dir.expanduser().resolve()
    status_dir.mkdir(parents=True, exist_ok=True)

    ndjson_path = status_dir / CAPTIONS_NDJSON
    if not append and ndjson_path.exists():
        ndjson_path.unlink()

    provider_name = "dry-run" if dry_run else "florence2"
    florence: Optional[Florence2Provider] = None
    if not dry_run:
        florence = Florence2Provider(model_pin=model_pin, device=device)

    rows: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    started = utc_now()

    with ndjson_path.open("a", encoding="utf-8") as fh:
        for frame in doc.get("frames") or []:
            if not isinstance(frame, dict):
                continue
            try:
                img = resolve_frame_path(frame, work_dir=wd)
                if dry_run:
                    caption = dry_run_caption(frame)
                else:
                    assert florence is not None
                    if not img.is_file() or img.stat().st_size == 0:
                        raise FileNotFoundError(f"missing/empty frame: {img}")
                    caption = florence.caption_image(img)
                row = build_row(
                    frame,
                    caption=caption,
                    provider=provider_name,
                    model_pin=model_pin if not dry_run else "dry-run",
                    run_id=run_id,
                    runner=runner,
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

    finished = utc_now()
    manifest = {
        "schema": SCHEMA_VERSION,
        "run_id": run_id,
        "runner": runner,
        "provider": provider_name,
        "model_pin": model_pin if not dry_run else "dry-run",
        "device": device if not dry_run else "cpu",
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
        help="local | docker | runpod | other (recorded in outputs)",
    )
    ap.add_argument(
        "--model-pin",
        default=os.environ.get("VISION_MODEL_PIN", DEFAULT_MODEL_PIN),
    )
    ap.add_argument(
        "--device",
        default=os.environ.get("VISION_DEVICE", "cuda"),
        help="cuda | cuda:0 | cpu (cpu only sensible with --dry-run)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Placeholder captions; no model load")
    ap.add_argument("--append", action="store_true", help="Append to existing NDJSON instead of replace")
    args = ap.parse_args(list(argv) if argv is not None else None)

    status_dir = args.status_dir
    if status_dir is None:
        dr = Path(args.data_root).expanduser().resolve() if args.data_root else None
        status_dir = default_status_dir(dr)
    if status_dir is None:
        print("error: --status-dir or VISION_STATUS_DIR (or --data-root) required", file=sys.stderr)
        return 2

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
                "dry_run": manifest.get("dry_run"),
            },
            indent=2,
        )
    )
    return 0 if not manifest.get("error_count") else 1


if __name__ == "__main__":
    raise SystemExit(main())
