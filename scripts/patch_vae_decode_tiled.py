#!/usr/bin/env python3
"""Patch LiteGraph WAN graphs: VAEDecode → VAEDecodeTiled (spatial + temporal)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

# ComfyUI VAEDecodeTiled (video VAEs): tile_size, overlap, temporal_size, temporal_overlap.
TILED_WIDGETS = [256, 64, 8, 4]


def _widget_input(name: str, typ: str) -> dict[str, Any]:
    return {
        "link": None,
        "localized_name": name,
        "name": name,
        "type": typ,
        "widget": {"name": name},
    }


def patch_vae_decode_tiled(workflow: dict[str, Any], widgets: list[int] | None = None) -> int:
    """Return count of VAEDecode nodes converted."""
    values = list(widgets or TILED_WIDGETS)
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        return 0
    n = 0
    extra = [_widget_input("tile_size", "INT"), _widget_input("overlap", "INT"),
             _widget_input("temporal_size", "INT"), _widget_input("temporal_overlap", "INT")]
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "VAEDecode":
            continue
        node["type"] = "VAEDecodeTiled"
        props = node.get("properties")
        if not isinstance(props, dict):
            props = {}
            node["properties"] = props
        props["Node name for S&R"] = "VAEDecodeTiled"
        inputs = node.get("inputs")
        if not isinstance(inputs, list):
            inputs = []
            node["inputs"] = inputs
        have = {str(i.get("name")) for i in inputs if isinstance(i, dict)}
        for spec in extra:
            if spec["name"] not in have:
                inputs.append(spec)
        node["widgets_values"] = values
        title = str(node.get("title") or "")
        if "DECODE" in title and "tiled" not in title.lower():
            node["title"] = title + " (tiled)"
        n += 1
    return n


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("workflow", type=Path)
    args = p.parse_args()
    path = args.workflow.expanduser().resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    n = patch_vae_decode_tiled(data)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"patched {n} VAEDecode → VAEDecodeTiled in {path}")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
