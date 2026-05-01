#!/usr/bin/env python3
"""
Clean a ComfyUI workflow JSON for git-friendly "template" usage.

Goal: round-trip friendly
- You edit workflows normally in the ComfyUI UI.
- Then you run this cleaner to strip volatile / localized details so diffs are meaningful.
- The cleaned output should still be loadable in ComfyUI.

What gets cleaned (heuristics)
- Remove UI/session noise: `extra` (zoom/pan), previews, and other volatile fields.
- Remove video preview blobs under VHS nodes (e.g. `videopreview`).
- De-localize common timestamped `filename_prefix` patterns (convert resolved timestamps back to %date tokens).
- Replace localized content that shouldn't live in templates:
  - `LoadImage` filename -> "" (keeps placeholder structure)
  - `PrimitiveStringMultiline` prompt text -> "" (keeps placeholder structure)
  - `RandomNoise` seed -> 0 (keeps "randomize" flag if present)

This is intentionally conservative: it avoids touching graph wiring / node ids.
"""

from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


def _delocalize_filename_prefix(s: str) -> str:
    # Normalize slashes for matching, but keep original separators mostly intact.
    out = s

    # Convert folder date segments like /2026-01-18/ or \2026-01-18\
    out = re.sub(r"([/\\\\])\d{4}-\d{2}-\d{2}([/\\\\])", r"\1%date:yyyy-MM-dd%\2", out)

    # Convert patterns like -2026-01-18-003159_ into -%date:yyyy-MM-dd%-%date:hhmmss%_
    out = re.sub(
        r"-(\d{4}-\d{2}-\d{2})-(\d{6})(?=_)",
        r"-%date:yyyy-MM-dd%-%date:hhmmss%",
        out,
    )

    # Convert trailing -003159_ into -%date:hhmmss%_
    out = re.sub(r"-(\d{6})(?=_)", r"-%date:hhmmss%", out)

    return out


def _canonicalize_title(s: str) -> str:
    # Keep it UI-friendly: just strip and collapse whitespace.
    return re.sub(r"\s+", " ", s.strip())


_TITLE_TYPES: Set[str] = {
    # "Control panel" / patch-target nodes
    "mxSlider",
    "mxSlider2D",
    "RandomNoise",
    "PrimitiveStringMultiline",
    "LoadImage",
    "BasicScheduler",
    "CFGGuider",
    "KSamplerSelect",
    "VHS_VideoCombine",
    "RIFE VFI",
}


def _clean_vhs_widgets(wv: Dict[str, Any]) -> Dict[str, Any]:
    wv = dict(wv)
    # Always drop preview blob; it's volatile and often contains absolute paths.
    wv.pop("videopreview", None)
    # De-localize filename_prefix if present.
    fp = wv.get("filename_prefix")
    if isinstance(fp, str):
        wv["filename_prefix"] = _delocalize_filename_prefix(fp)
    return wv


def _clean_node(node: Dict[str, Any], *, canonicalize_titles: bool) -> Dict[str, Any]:
    node = deepcopy(node)
    ntype = node.get("type")

    # Strip per-node volatile UI fields if present.
    node.pop("selected", None)

    # Canonicalize titles for patch-target nodes (optional).
    if canonicalize_titles and isinstance(ntype, str) and ntype in _TITLE_TYPES:
        title = node.get("title")
        if isinstance(title, str) and title.strip():
            node["title"] = _canonicalize_title(title)

    # Clean widgets
    wv = node.get("widgets_values")
    if isinstance(wv, dict):
        # VHS + similar nodes store widget state as dict
        node["widgets_values"] = _clean_vhs_widgets(wv)
    elif isinstance(wv, list):
        # Localized inputs
        if ntype == "LoadImage":
            # Typically: [<filename>, "image"] or similar.
            if wv:
                wv2 = list(wv)
                wv2[0] = ""
                node["widgets_values"] = wv2
        elif ntype == "PrimitiveStringMultiline":
            # Keep shape but remove the actual prompt/negative text.
            if wv:
                node["widgets_values"] = [""]
        elif ntype == "RandomNoise":
            # Keep randomize flag (often second element), but remove fixed seed.
            wv2 = list(wv)
            if len(wv2) >= 1 and isinstance(wv2[0], (int, float, str)):
                wv2[0] = 0
            node["widgets_values"] = wv2

    # Remove some volatile properties that can differ per install/version.
    props = node.get("properties")
    if isinstance(props, dict):
        # These are handy in UI but noisy in diffs. Keep Node name for S&R, drop version pins.
        props2 = dict(props)
        props2.pop("ver", None)
        node["properties"] = props2

    return node


def clean_workflow(obj: Dict[str, Any], *, canonicalize_titles: bool) -> Dict[str, Any]:
    out = deepcopy(obj)

    # Top-level session/UI noise: safe to drop for templates.
    out.pop("extra", None)

    # Keep core graph fields; clean nodes.
    nodes = out.get("nodes")
    if isinstance(nodes, list):
        out["nodes"] = [
            _clean_node(n, canonicalize_titles=canonicalize_titles) if isinstance(n, dict) else n for n in nodes
        ]

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Clean ComfyUI workflow JSON for template usage")
    ap.add_argument("input", help="Path to a ComfyUI workflow .json")
    ap.add_argument(
        "--canonicalize-titles",
        action="store_true",
        help="Normalize titles (strip/collapse whitespace) for common patch-target nodes.",
    )
    ap.add_argument(
        "--out",
        default="",
        help="Output path (default: print to stdout). If omitted, prints JSON.",
    )
    ap.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indent level for output (default: 2)",
    )
    args = ap.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        ap.error(f"Not found: {in_path}")

    obj = json.loads(in_path.read_text(encoding="utf-8"))
    cleaned = clean_workflow(obj, canonicalize_titles=args.canonicalize_titles)
    payload = json.dumps(cleaned, indent=args.indent, ensure_ascii=False)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload, encoding="utf-8")
        print(str(out_path))
    else:
        print(payload)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

