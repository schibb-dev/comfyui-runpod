#!/usr/bin/env python3
"""Build the delivery postprocess catalog workflow (Phase 2).

Separate from all generation templates. One graph with optional components
(toggled at apply time via shape ``delivery:`` block):

  VHS_LoadVideoPath → ColorMatch → ImageUpscaleWithModel → RIFE VFI → VHS_VideoCombine
                        (opt)         (opt)                    (opt)

Usage:
  python3 build_delivery_catalogs.py --out-dir /path/to/catalog
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

CATALOG_DEFAULT = Path(
    "/home/yuji/comfyui-runpod-data/comfyui_user/default/workflows/generated/catalog"
)

DELIVERY_STEM = "wan-delivery-postprocess"
MODE_BYPASS = 2


def _link(link_id: int, src: int, src_slot: int, dst: int, dst_slot: int, typ: str) -> list[Any]:
    return [link_id, src, src_slot, dst, dst_slot, typ]


def build_delivery_catalog(*, prefix: str) -> dict[str, Any]:
    load = {
        "id": 10,
        "type": "VHS_LoadVideoPath",
        "pos": [0, 120],
        "size": [340, 286],
        "flags": {},
        "order": 0,
        "mode": 0,
        "title": "INPUT: source video",
        "properties": {"Node name for S&R": "VHS_LoadVideoPath"},
        "inputs": [
            {"name": "meta_batch", "type": "VHS_BatchManager", "link": None},
            {"name": "vae", "type": "VAE", "link": None},
            {"name": "video", "type": "STRING", "widget": {"name": "video"}, "link": None},
            {"name": "force_rate", "type": "FLOAT", "widget": {"name": "force_rate"}, "link": None},
            {"name": "custom_width", "type": "INT", "widget": {"name": "custom_width"}, "link": None},
            {"name": "custom_height", "type": "INT", "widget": {"name": "custom_height"}, "link": None},
            {"name": "frame_load_cap", "type": "INT", "widget": {"name": "frame_load_cap"}, "link": None},
            {"name": "skip_first_frames", "type": "INT", "widget": {"name": "skip_first_frames"}, "link": None},
            {"name": "select_every_nth", "type": "INT", "widget": {"name": "select_every_nth"}, "link": None},
            {"name": "format", "type": "COMBO", "widget": {"name": "format"}, "link": None},
        ],
        "outputs": [
            {"name": "IMAGE", "type": "IMAGE", "links": [1, 2], "slot_index": 0},
            {"name": "frame_count", "type": "INT", "links": None},
            {"name": "audio", "type": "AUDIO", "links": None},
            {"name": "video_info", "type": "VHS_VIDEOINFO", "links": None},
        ],
        "widgets_values": {
            "video": "input/placeholder.mp4",
            "force_rate": 0,
            "custom_width": 0,
            "custom_height": 0,
            "frame_load_cap": 0,
            "skip_first_frames": 0,
            "select_every_nth": 1,
            "format": "AnimateDiff",
        },
        "color": "#432",
        "bgcolor": "#653",
    }
    color_match = {
        "id": 15,
        "type": "ColorMatch",
        "pos": [380, 120],
        "size": [210, 54],
        "flags": {},
        "order": 1,
        "mode": MODE_BYPASS,
        "title": "DELIVERY: ColorMatch (optional)",
        "properties": {"Node name for S&R": "ColorMatch", "cnr_id": "comfyui-kjnodes"},
        "inputs": [
            {"name": "image_ref", "type": "IMAGE", "link": 1},
            {"name": "image_target", "type": "IMAGE", "link": 2},
            {"name": "method", "type": "COMBO", "widget": {"name": "method"}, "link": None},
            {"name": "strength", "type": "FLOAT", "widget": {"name": "strength"}, "link": None},
            {"name": "multithread", "type": "BOOLEAN", "widget": {"name": "multithread"}, "link": None},
        ],
        "outputs": [{"name": "image", "type": "IMAGE", "links": [3], "slot_index": 0}],
        "widgets_values": ["mkl", 1, True],
        "color": "#222",
        "bgcolor": "#000",
    }
    loader = {
        "id": 11,
        "type": "UpscaleModelLoader",
        "pos": [620, 40],
        "size": [320, 58],
        "flags": {},
        "order": 2,
        "mode": MODE_BYPASS,
        "title": "DELIVERY: upscale model (optional)",
        "properties": {"Node name for S&R": "UpscaleModelLoader", "cnr_id": "comfy-core"},
        "inputs": [{"name": "model_name", "type": "COMBO", "widget": {"name": "model_name"}, "link": None}],
        "outputs": [{"name": "UPSCALE_MODEL", "type": "UPSCALE_MODEL", "links": [4]}],
        "widgets_values": ["RealESRGAN_x4plus.pth"],
        "color": "#223",
        "bgcolor": "#335",
    }
    upscale = {
        "id": 20,
        "type": "ImageUpscaleWithModel",
        "pos": [620, 120],
        "size": [240, 46],
        "flags": {},
        "order": 3,
        "mode": MODE_BYPASS,
        "title": "DELIVERY: 4x upscale (optional)",
        "properties": {"Node name for S&R": "ImageUpscaleWithModel", "cnr_id": "comfy-core"},
        "inputs": [
            {"name": "upscale_model", "type": "UPSCALE_MODEL", "link": 4},
            {"name": "image", "type": "IMAGE", "link": 3},
        ],
        "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [5], "slot_index": 0}],
        "widgets_values": [],
        "color": "#222",
        "bgcolor": "#000",
    }
    rife = {
        "id": 30,
        "type": "RIFE VFI",
        "pos": [900, 120],
        "size": [280, 190],
        "flags": {},
        "order": 4,
        "mode": MODE_BYPASS,
        "title": "DELIVERY: RIFE interpolation (optional)",
        "properties": {
            "Node name for S&R": "RIFE VFI",
            "cnr_id": "comfyui-frame-interpolation",
        },
        "inputs": [
            {"name": "frames", "type": "IMAGE", "link": 5},
            {"name": "optional_interpolation_states", "type": "INTERPOLATION_STATES", "link": None},
            {"name": "ckpt_name", "type": "COMBO", "widget": {"name": "ckpt_name"}, "link": None},
            {"name": "clear_cache_after_n_frames", "type": "INT", "widget": {"name": "clear_cache_after_n_frames"}, "link": None},
            {"name": "multiplier", "type": "INT", "widget": {"name": "multiplier"}, "link": None},
            {"name": "fast_mode", "type": "BOOLEAN", "widget": {"name": "fast_mode"}, "link": None},
            {"name": "ensemble", "type": "BOOLEAN", "widget": {"name": "ensemble"}, "link": None},
            {"name": "scale_factor", "type": "COMBO", "widget": {"name": "scale_factor"}, "link": None},
        ],
        "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [6], "slot_index": 0}],
        "widgets_values": ["rife47.pth", 10, 2, True, True, 1],
        "color": "#222",
        "bgcolor": "#000",
    }
    combine = {
        "id": 40,
        "type": "VHS_VideoCombine",
        "pos": [1220, 120],
        "size": [400, 334],
        "flags": {},
        "order": 5,
        "mode": 0,
        "title": "OUTPUT: delivered MP4",
        "properties": {"Node name for S&R": "VHS_VideoCombine"},
        "inputs": [
            {"name": "images", "type": "IMAGE", "link": 6},
            {"name": "audio", "type": "AUDIO", "link": None},
            {"name": "meta_batch", "type": "VHS_BatchManager", "link": None},
            {"name": "vae", "type": "VAE", "link": None},
            {"name": "frame_rate", "type": "FLOAT", "widget": {"name": "frame_rate"}, "link": None},
            {"name": "loop_count", "type": "INT", "widget": {"name": "loop_count"}, "link": None},
            {"name": "filename_prefix", "type": "STRING", "widget": {"name": "filename_prefix"}, "link": None},
            {"name": "format", "type": "COMBO", "widget": {"name": "format"}, "link": None},
            {"name": "pingpong", "type": "BOOLEAN", "widget": {"name": "pingpong"}, "link": None},
            {"name": "save_output", "type": "BOOLEAN", "widget": {"name": "save_output"}, "link": None},
        ],
        "outputs": [{"name": "Filenames", "type": "VHS_FILENAMES", "links": None}],
        "widgets_values": {
            "frame_rate": 16,
            "loop_count": 0,
            "filename_prefix": prefix,
            "format": "video/h264-mp4",
            "pingpong": False,
            "save_output": True,
            "pix_fmt": "yuv420p",
            "crf": 19,
            "save_metadata": True,
            "trim_to_audio": False,
        },
        "color": "#233",
        "bgcolor": "#355",
    }
    wf = {
        "last_node_id": 40,
        "last_link_id": 6,
        "nodes": [load, color_match, loader, upscale, rife, combine],
        "links": [
            _link(1, 10, 0, 15, 0, "IMAGE"),
            _link(2, 10, 0, 15, 1, "IMAGE"),
            _link(3, 15, 0, 20, 1, "IMAGE"),
            _link(4, 11, 0, 20, 0, "UPSCALE_MODEL"),
            _link(5, 20, 0, 30, 0, "IMAGE"),
            _link(6, 30, 0, 40, 0, "IMAGE"),
        ],
        "groups": [],
        "config": {},
        "extra": {},
        "version": 0.4,
    }
    validate_delivery_catalog(wf)
    return wf


def validate_delivery_catalog(workflow: dict[str, Any]) -> None:
    types = {str(n.get("type") or "") for n in workflow.get("nodes") or []}
    required = {
        "VHS_LoadVideoPath",
        "ColorMatch",
        "UpscaleModelLoader",
        "ImageUpscaleWithModel",
        "RIFE VFI",
        "VHS_VideoCombine",
    }
    missing = sorted(required - types)
    if missing:
        raise ValueError(f"delivery catalog missing node types: {missing}")


def graph_hash(workflow: dict[str, Any]) -> str:
    from shape_factory_vocab import graph_fingerprint_topology

    return graph_fingerprint_topology(workflow, aliases=False)


def write_catalog(out_dir: Path, workflow: dict[str, Any]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{DELIVERY_STEM}-readable.json"
    path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="Build delivery postprocess catalog workflow")
    ap.add_argument("--out-dir", type=Path, default=CATALOG_DEFAULT)
    args = ap.parse_args()
    out_dir = args.out_dir.expanduser().resolve()

    wf = build_delivery_catalog(prefix=f"og/%date:yyyy-MM-dd%/{DELIVERY_STEM}/FINAL")
    path = write_catalog(out_dir, wf)
    print(json.dumps({"path": str(path), "graph_hash": graph_hash(wf), "nodes": len(wf["nodes"])}))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
