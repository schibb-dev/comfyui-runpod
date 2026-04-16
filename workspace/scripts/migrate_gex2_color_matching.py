"""
Upgrade legacy FB9 GEX2 workflows (Florence2 + direct CLIP image) to the
COLORv2 graph: overlap / last-N / ColorMatch(reinhard) -> CLIP vision.

Run from repo root: python workspace/scripts/migrate_gex2_color_matching.py
"""
from __future__ import annotations

import copy
import json
import re
import uuid
from pathlib import Path

WF_DIR = Path(__file__).resolve().parents[1] / "comfyui_user" / "default" / "workflows"

# Nodes whose wiring is defined by the COLORv2 template; do not copy from legacy.
_DENY_OVERLAY = frozenset({130, 376, 382, 383, 384, 385, 387, 388, 389, 390, 391})


def _stem_output_prefix(stem: str) -> str:
    return f"output/og/%date:yyyy-MM-dd%/{stem}_%date:yyyy-MM-dd%"


def _set_output_prefix(nodes: list, prefix: str) -> None:
    for n in nodes:
        if n.get("id") == 80 and n.get("type") == "VHS_VideoCombine":
            w = n.get("widgets_values")
            if isinstance(w, dict):
                w["filename_prefix"] = prefix
            return


def _normalize_color_match(nodes: list) -> None:
    for n in nodes:
        if n.get("type") == "ColorMatch":
            n["widgets_values"] = ["reinhard", 1.0, True]
            n["title"] = "Match last-N to last frame (CLIP vision)"


def migrate_legacy_to_color(
    *,
    template: dict,
    legacy_path: Path,
    dest_path: Path | None = None,
    output_stem: str | None = None,
) -> dict:
    legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    new = copy.deepcopy(template)
    new["id"] = legacy.get("id") or str(uuid.uuid4())

    old_map = {n["id"]: n for n in legacy["nodes"]}
    stem = output_stem or legacy_path.stem

    for n in new["nodes"]:
        oid = n["id"]
        if oid in _DENY_OVERLAY:
            continue
        o = old_map.get(oid)
        if not o or o.get("type") != n.get("type"):
            continue
        if "widgets_values" in o:
            n["widgets_values"] = copy.deepcopy(o["widgets_values"])

    _set_output_prefix(new["nodes"], _stem_output_prefix(stem))
    _normalize_color_match(new["nodes"])

    text = json.dumps(new, separators=(",", ":"))
    (dest_path or legacy_path).write_text(text, encoding="utf-8")
    return new


def fix_color_workflow_prefix(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    _set_output_prefix(data["nodes"], _stem_output_prefix(path.stem))
    _normalize_color_match(data["nodes"])
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")


def restore_overhead_v2_from_color(path_v2: Path, path_v2_color: Path) -> None:
    data = json.loads(path_v2_color.read_text(encoding="utf-8"))
    data["id"] = str(uuid.uuid4())
    _set_output_prefix(data["nodes"], _stem_output_prefix(path_v2.stem))
    _normalize_color_match(data["nodes"])
    path_v2.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")


def main() -> None:
    template_path = WF_DIR / "FB9_GEX2_COLORv2.json"
    template = json.loads(template_path.read_text(encoding="utf-8"))

    legacy_names = [
        "FB9_GEX2.json",
        "FB9-GEX2-Overhead.json",
        "FB9_GEX2_FACIAL.json",
        "FB9_GEX2_OVERHEAD-cleaned.json",
        "FB9_GEX2_OVERHEAD.json",
        "FB9_GEX2_SHOCK.json",
        "FB9_GEX2_TIP.json",
    ]
    for name in legacy_names:
        p = WF_DIR / name
        if not p.exists():
            continue
        migrate_legacy_to_color(template=template, legacy_path=p)
        print("migrated", name)

    # Existing color graphs: align output prefix + ColorMatch defaults.
    for name in [
        "FB9_GEX2_COLORv2.json",
        "FB9_GEX2_OVERHEAD_noFlorence_last8_v2_color.json",
        "FB9_GEX2_OVERHEAD_v2_color.json",
    ]:
        p = WF_DIR / name
        if p.exists():
            fix_color_workflow_prefix(p)
            print("fixed", name)

    v2 = WF_DIR / "FB9_GEX2_OVERHEAD_v2.json"
    v2c = WF_DIR / "FB9_GEX2_OVERHEAD_v2_color.json"
    if v2c.exists():
        restore_overhead_v2_from_color(v2, v2c)
        print("restored", v2.name, "from", v2c.name)


if __name__ == "__main__":
    main()
