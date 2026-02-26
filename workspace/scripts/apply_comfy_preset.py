#!/usr/bin/env python3
"""
Apply a compact preset JSON onto a ComfyUI workflow template JSON.

This is the missing roundtrip piece:
- You keep a cleaned/template workflow in git (stable structure).
- You keep per-run presets as standalone JSON (knobs only).
- This tool applies preset values back onto the template to produce a runnable workflow JSON.

Preset format (current):
{
  "nodes": {
    "73:Noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": 123}},
    "408:Positive": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "..." }},
    ...
  }
}

Heuristics:
- We match nodes by numeric ID parsed from the preset key prefix (before ':').
- We patch node `widgets_values` (or VHS dict widgets) without changing wiring.
"""

from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _parse_preset_key(key: str) -> Tuple[Optional[int], Optional[str]]:
    # keys like "73:Noise", "398:Output"
    m = re.match(r"^\s*(\d+)\s*:", key)
    if not m:
        return None, None
    try:
        nid = int(m.group(1))
    except Exception:
        return None, None
    # Title part is everything after first colon
    parts = key.split(":", 1)
    title = parts[1].strip() if len(parts) == 2 else None
    return nid, (title if title else None)


def _find_node_by_id(workflow: Dict[str, Any], node_id: int) -> Optional[Dict[str, Any]]:
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        return None
    for n in nodes:
        if isinstance(n, dict) and n.get("id") == node_id:
            return n
    return None


def _find_nodes_by_title_and_type(
    workflow: Dict[str, Any], *, title: str, node_type: Optional[str]
) -> List[Dict[str, Any]]:
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        return []
    hits: List[Dict[str, Any]] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        if not isinstance(n.get("title"), str):
            continue
        if n.get("title") != title:
            continue
        if node_type and n.get("type") != node_type:
            continue
        hits.append(n)
    return hits


def _ensure_list_size(lst: list, n: int, fill: Any = None) -> list:
    while len(lst) < n:
        lst.append(fill)
    return lst


def _apply_to_node(node: Dict[str, Any], preset_entry: Dict[str, Any]) -> None:
    ctype = preset_entry.get("class_type")
    inputs = preset_entry.get("inputs") if isinstance(preset_entry.get("inputs"), dict) else {}

    wv = node.get("widgets_values")

    # PrimitiveStringMultiline: widgets_values = [text]
    if ctype == "PrimitiveStringMultiline":
        text = inputs.get("value", "")
        if not isinstance(text, str):
            return
        if not isinstance(wv, list):
            wv = []
        wv = list(wv)
        _ensure_list_size(wv, 1, "")
        wv[0] = text
        node["widgets_values"] = wv
        return

    # LoadImage: widgets_values typically [filename, "image"]
    if ctype == "LoadImage":
        img = inputs.get("image", "")
        if not isinstance(img, str):
            return
        if not isinstance(wv, list):
            wv = []
        wv = list(wv)
        _ensure_list_size(wv, 2, "")
        wv[0] = img
        # keep mode in [1] as-is (often "image")
        node["widgets_values"] = wv
        return

    # RandomNoise: widgets_values typically [seed, "randomize"]
    if ctype == "RandomNoise":
        seed = inputs.get("noise_seed")
        if not isinstance(seed, int):
            return
        if not isinstance(wv, list):
            wv = []
        wv = list(wv)
        _ensure_list_size(wv, 1, 0)
        wv[0] = seed
        node["widgets_values"] = wv
        return

    # mxSlider: widgets_values [Xi, Xf, isfloatX]
    if ctype == "mxSlider":
        xi = inputs.get("Xi")
        xf = inputs.get("Xf")
        isfloat = inputs.get("isfloatX")
        if not isinstance(xi, int) or not isinstance(isfloat, int) or not isinstance(xf, (int, float)):
            return
        node["widgets_values"] = [xi, float(xf) if isfloat else int(xf), isfloat]
        return

    # mxSlider2D: widgets_values [Xi, Xf, Yi, Yf, isfloatX, isfloatY]
    if ctype == "mxSlider2D":
        xi = inputs.get("Xi")
        xf = inputs.get("Xf")
        yi = inputs.get("Yi")
        yf = inputs.get("Yf")
        isfloatx = inputs.get("isfloatX")
        isfloaty = inputs.get("isfloatY")
        if not all(isinstance(v, int) for v in (xi, yi, isfloatx, isfloaty)) or not all(
            isinstance(v, (int, float)) for v in (xf, yf)
        ):
            return
        node["widgets_values"] = [
            int(xi),
            float(xf) if isfloatx else int(xf),
            int(yi),
            float(yf) if isfloaty else int(yf),
            int(isfloatx),
            int(isfloaty),
        ]
        return

    # CFGGuider: widgets_values [cfg]
    if ctype == "CFGGuider":
        cfg = inputs.get("cfg")
        if not isinstance(cfg, (int, float)):
            return
        if not isinstance(wv, list):
            wv = []
        wv = list(wv)
        _ensure_list_size(wv, 1, 0)
        wv[0] = float(cfg)
        node["widgets_values"] = wv
        return

    # BasicScheduler: widgets_values [scheduler, steps, denoise]
    if ctype == "BasicScheduler":
        scheduler = inputs.get("scheduler")
        steps = inputs.get("steps")
        denoise = inputs.get("denoise")
        if scheduler is not None and not isinstance(scheduler, str):
            return
        if steps is not None and not isinstance(steps, int):
            return
        if denoise is not None and not isinstance(denoise, (int, float)):
            return
        if not isinstance(wv, list):
            wv = []
        wv = list(wv)
        _ensure_list_size(wv, 3, None)
        if isinstance(scheduler, str):
            wv[0] = scheduler
        if isinstance(steps, int):
            wv[1] = steps
        if isinstance(denoise, (int, float)):
            wv[2] = float(denoise)
        node["widgets_values"] = wv
        return

    # KSamplerSelect: widgets_values [sampler_name]
    if ctype == "KSamplerSelect":
        sampler = inputs.get("sampler_name")
        if not isinstance(sampler, str):
            return
        if not isinstance(wv, list):
            wv = []
        wv = list(wv)
        _ensure_list_size(wv, 1, "")
        wv[0] = sampler
        node["widgets_values"] = wv
        return

    # VHS_VideoCombine: widgets_values is a dict of settings
    if ctype == "VHS_VideoCombine":
        if not isinstance(wv, dict):
            wv = {}
        wv = dict(wv)
        for k in ("frame_rate", "filename_prefix", "format", "crf", "pix_fmt", "save_metadata"):
            if k in inputs:
                wv[k] = inputs[k]
        # Never write videopreview from presets.
        wv.pop("videopreview", None)
        node["widgets_values"] = wv
        return

    # RIFE VFI: widgets_values list is typically:
    # [ckpt_name, clear_cache_after_n_frames, multiplier, fast_mode, ensemble, scale_factor]
    if ctype == "RIFE VFI":
        if not isinstance(wv, list):
            wv = []
        wv = list(wv)
        _ensure_list_size(wv, 6, None)
        if isinstance(inputs.get("ckpt_name"), str):
            wv[0] = inputs["ckpt_name"]
        if isinstance(inputs.get("multiplier"), int):
            wv[2] = inputs["multiplier"]
        if isinstance(inputs.get("fast_mode"), bool):
            wv[3] = inputs["fast_mode"]
        if isinstance(inputs.get("ensemble"), bool):
            wv[4] = inputs["ensemble"]
        node["widgets_values"] = wv
        return


def apply_preset(workflow: Dict[str, Any], preset: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    workflow = deepcopy(workflow)
    nodes_map = preset.get("nodes")
    if not isinstance(nodes_map, dict):
        return workflow, {"applied": 0, "skipped": 0, "missing_nodes": 0}

    stats = {"applied": 0, "skipped": 0, "missing_nodes": 0, "ambiguous_title_matches": 0, "title_fallback_used": 0}
    for key, entry in nodes_map.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            stats["skipped"] += 1
            continue
        nid, title = _parse_preset_key(key)
        if nid is None:
            stats["skipped"] += 1
            continue
        node = _find_node_by_id(workflow, nid)
        if node is None and title:
            # Fallback: if IDs changed but titles stayed canonical.
            preset_type = entry.get("class_type")
            node_type = preset_type if isinstance(preset_type, str) else None
            hits = _find_nodes_by_title_and_type(workflow, title=title, node_type=node_type)
            if len(hits) == 1:
                node = hits[0]
                stats["title_fallback_used"] += 1
            elif len(hits) > 1:
                stats["ambiguous_title_matches"] += 1
                continue
        if node is None:
            stats["missing_nodes"] += 1
            continue
        _apply_to_node(node, entry)
        stats["applied"] += 1

    return workflow, stats


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply preset JSON to a ComfyUI workflow template")
    ap.add_argument("template", help="Path to template workflow JSON")
    ap.add_argument("preset", help="Path to preset JSON (from extract_comfy_preset.py)")
    ap.add_argument("--out", required=True, help="Output workflow JSON path")
    ap.add_argument("--indent", type=int, default=2)
    args = ap.parse_args()

    template_path = Path(args.template)
    preset_path = Path(args.preset)
    out_path = Path(args.out)

    workflow = json.loads(template_path.read_text(encoding="utf-8"))
    preset = json.loads(preset_path.read_text(encoding="utf-8"))

    merged, stats = apply_preset(workflow, preset)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(merged, indent=args.indent, ensure_ascii=False), encoding="utf-8")

    print(json.dumps({"out": str(out_path), "stats": stats}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

