#!/usr/bin/env python3
"""
Canonicalize/rename ComfyUI workflow node titles for template usage.

Why:
- If you want presets to survive node-id churn, you need stable, unique titles.
- ComfyUI's UI makes it easy to end up with inconsistent titles (whitespace, casing, etc.).

What this script does:
- Normalizes whitespace in titles.
- Renames common "control panel" titles to stable names:
    RUN_*      (knobs)
    PROMPT_*   (prompt text blocks)
    IN_*       (inputs)
    OUT_*      (savers/outputs)

Safety:
- Never touches node ids or wiring.
- Ensures resulting titles are unique (adds _<id> suffix if necessary).
"""

from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


def _ws(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())


# Title mapping based on common ComfyUI naming patterns (your workflows match these).
TITLE_MAP: Dict[str, str] = {
    # Knobs / sliders
    "Steps": "RUN_Steps",
    "CFG": "RUN_CFG",
    "Denoise": "RUN_Denoise",
    "Duration": "RUN_DurationSec",
    "Speed": "RUN_SpeedShift",
    "Size": "RUN_Size",
    "Tea cache": "RUN_TeaCache",
    "Upscale ratio": "RUN_UpscaleRatio",
    # Inputs / prompts
    "Load Image": "IN_Image",
    "Positive": "PROMPT_Positive",
    "Negative": "PROMPT_Negative",
    "Final prompt preview": "PROMPT_Preview",
    # Sampler-related
    "Noise": "RUN_Noise",
    "Sampler": "RUN_Sampler",
    "Scheduler": "RUN_Scheduler",
    # Outputs / saving
    "Output": "OUT_Main",
    "Save UPINT": "OUT_Upin",
    "Save Interpoled": "OUT_Interpolated",
    "Save Upscaled": "OUT_Upscaled",
    "Save UPINT": "OUT_Upin",
    "Save last frame": "OUT_LastFrame",
}


def _rename_title(node: Dict[str, Any], used: Set[str]) -> None:
    title = node.get("title")
    if not isinstance(title, str) or not title.strip():
        return
    t = _ws(title)
    new = TITLE_MAP.get(t, t)

    # Ensure uniqueness
    if new in used:
        nid = node.get("id")
        suffix = f"_{nid}" if isinstance(nid, int) else "_dup"
        new = f"{new}{suffix}"

    node["title"] = new
    used.add(new)


def canonicalize_titles(workflow: Dict[str, Any]) -> Dict[str, Any]:
    out = deepcopy(workflow)
    nodes = out.get("nodes")
    if not isinstance(nodes, list):
        return out

    used: Set[str] = set()
    for n in nodes:
        if not isinstance(n, dict):
            continue
        _rename_title(n, used)

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Canonicalize ComfyUI workflow node titles")
    ap.add_argument("input", help="Input workflow JSON")
    ap.add_argument("--out", required=True, help="Output workflow JSON")
    ap.add_argument("--indent", type=int, default=2)
    args = ap.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.out)
    if not in_path.exists():
        ap.error(f"Not found: {in_path}")

    obj = json.loads(in_path.read_text(encoding="utf-8"))
    out = canonicalize_titles(obj)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=args.indent, ensure_ascii=False), encoding="utf-8")
    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

