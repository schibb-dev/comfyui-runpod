#!/usr/bin/env python3
"""
Sanity-check FB9-family ComfyUI litegraph workflows:
  - BasicScheduler / SDTurboScheduler: steps & denoise widget vs link sources (mxSlider etc.)
  - Primary video/image sources (VHS_LoadVideo*, LoadImage*, etc.) feeding Wan / CLIP / merge chains

Reads workflow JSON under workspace/comfyui_user/default/workflows/.

Discovery UI (experiments_ui) can edit API-prompt literals on VideoHelperSuite loaders
(skip_first_frames, frame_load_cap, force_rate) and VHS_VideoCombine (filename_prefix, frame_rate,
save_output, save_metadata); see VHS_QUICK_EDIT_INPUT_KEYS in DiscoveryComfyQuickEdits.tsx. Extending
this script to flag non-literal / linked VHS widgets helps keep graphs compatible with those edits.

Orphan litegraph nodes (no ``links`` endpoints) are listed via ``workflow_litegraph_health``; see also
``validate_workflow_orphans.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from workflow_litegraph_health import disconnected_litegraph_nodes, format_disconnected_report

WORKFLOW_DIR = Path(__file__).resolve().parents[1] / "comfyui_user" / "default" / "workflows"

# User-requested filenames (basename match).
TARGET_NAMES = [
    "FB9_GEX_DESPERATE_FACIAL.json",
    "FB9_GEX_FACIAL_SURPRISE.json",
    "FB9_GEX_FACIAL.json",
    "FB9_GEX_POSE.json",
    "FB9_GEX_REFERENCE.json",
    "FB9_GEX2_B.json",
    "FB9_GEX2_COLORv2.json",
    "FB9_GEX2_FACIAL.json",
    "FB9_GEX2_OVERHEAD_A.json",
    "FB9_GEX2_OVERHEAD_B.json",
    "FB9_GEX2_OVERHEAD_noFlorence_last8_v2_color.json",
    "FB9_GEX2_OVERHEAD_POV.json",
    "FB9_GEX2_OVERHEAD_v2_color.json",
    "FB9_GEX2_OVERHEAD_v2.json",
    "FB9_GEX2_OVERHEAD-cleaned.json",
    "FB9_GEX2_OVERHEAD.json",
    "FB9_GEX2_SHOCK.json",
    "FB9_GEX2_TIP.json",
    "FB9_GEX2_UNDRESS.json",
    "FB9_GEX2.json",
    "FB9_LoraEx.json",
    "FB9-FaceBlast.json",
    "FB9-handjob-start.json",
    "FB9-Intro-PS.json",
    "FB9-pose-start.json",
    "FEAR_FB9_GEX_2026-03-03_00023 (2).json",
]

SCHEDULER_TYPES = {"BasicScheduler", "SDTurboScheduler"}
VIDEO_LOADER_TYPES = (
    "VHS_LoadVideoPath",
    "VHS_LoadVideo",
    "LoadVideo",
    "VHS_LoadVideoFFmpeg",
)
IMAGE_LOADER_TYPES = (
    "LoadImage",
    "LoadImagePath",
    "LoadImagesFromDirectory",
    "ImageFromURL",
)
KEY_CONSUMER_TYPES = (
    "WanImageToVideo",
    "CLIPVisionEncode",
    "VHS_MergeImages",
    "SaveImage",
    "VHS_VideoCombine",
)


def build_link_index(links: list[list[Any]]) -> dict[int, dict[str, Any]]:
    """link_id -> {from_id, from_slot, to_id, to_slot, type}"""
    idx: dict[int, dict[str, Any]] = {}
    for row in links or []:
        if not isinstance(row, list) or len(row) < 6:
            continue
        lid, src, src_slot, dst, dst_slot, typ = row[0], row[1], row[2], row[3], row[4], row[5]
        idx[int(lid)] = {"from": src, "from_slot": src_slot, "to": dst, "to_slot": dst_slot, "type": typ}
    return idx


def input_link_id(node: dict[str, Any], name: str) -> int | None:
    for inp in node.get("inputs") or []:
        if inp.get("name") == name:
            v = inp.get("link")
            return int(v) if v is not None else None
    return None


def node_by_id(nodes: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(n["id"]): n for n in nodes if "id" in n}


def follow_link(
    link_idx: dict[int, dict[str, Any]],
    nodes: dict[int, dict[str, Any]],
    link_id: int | None,
) -> tuple[int | None, str | None]:
    if link_id is None or link_id not in link_idx:
        return None, None
    src = link_idx[link_id]["from"]
    n = nodes.get(int(src))
    if not n:
        return int(src), None
    return int(src), str(n.get("type") or "")


def widgets_values_summary(wv: Any, max_len: int = 120) -> str:
    if wv is None:
        return ""
    if isinstance(wv, dict):
        # VHS loaders often use dict with "video" path
        if "video" in wv:
            s = str(wv.get("video", ""))
        else:
            s = json.dumps(wv, ensure_ascii=False)[:max_len]
    elif isinstance(wv, list):
        s = json.dumps(wv, ensure_ascii=False)
    else:
        s = str(wv)
    return s[:max_len] + ("…" if len(s) > max_len else "")


def analyze_scheduler(nodes: dict[int, dict[str, Any]], link_idx: dict[int, dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for nid, n in sorted(nodes.items()):
        typ = str(n.get("type") or "")
        if typ not in SCHEDULER_TYPES:
            continue
        title = str(n.get("title") or typ)
        steps_lid = input_link_id(n, "steps")
        denoise_lid = input_link_id(n, "denoise")
        wv = n.get("widgets_values")
        src_steps_id, src_steps_type = follow_link(link_idx, nodes, steps_lid)
        src_den_id, src_den_type = follow_link(link_idx, nodes, denoise_lid)
        steps_src = f"link->{src_steps_type} n{src_steps_id}" if steps_lid else "literal/widget"
        den_src = f"link->{src_den_type} n{src_den_id}" if denoise_lid else "literal/widget"
        den_note = ""
        if denoise_lid and src_den_type == "mxSlider":
            den_note = " **denoise from mxSlider**"
        elif denoise_lid and src_den_type:
            den_note = f" (denoise linked from {src_den_type})"
        lines.append(
            f"  - [{nid}] {typ} \"{title}\": steps={steps_src}; denoise={den_src}{den_note} | widgets_values~= {widgets_values_summary(wv, 80)}"
        )
    return lines


def analyze_media_sources(nodes: dict[int, dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Returns (video_lines, image_lines) for loader-like nodes."""
    vlines: list[str] = []
    ilines: list[str] = []
    for nid, n in sorted(nodes.items()):
        typ = str(n.get("type") or "")
        title = str(n.get("title") or "")
        wv = n.get("widgets_values")
        if typ in VIDEO_LOADER_TYPES:
            vlines.append(f"  - [{nid}] {typ} \"{title}\": {widgets_values_summary(wv, 200)}")
        if typ in IMAGE_LOADER_TYPES:
            ilines.append(f"  - [{nid}] {typ} \"{title}\": {widgets_values_summary(wv, 200)}")
        # mxSlider titled like user-facing media knobs (informational)
        if typ in ("mxSlider", "mxSlider2D") and any(
            k in title.lower() for k in ("frame", "size", "video", "image", "load")
        ):
            ilines.append(f"  - [{nid}] {typ} \"{title}\" (numeric UI): {widgets_values_summary(wv, 80)}")
    return vlines, ilines


def wan_clip_image_inputs(nodes: dict[int, dict[str, Any]], link_idx: dict[int, dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for nid, n in sorted(nodes.items()):
        typ = str(n.get("type") or "")
        if typ == "WanImageToVideo":
            for name in ("start_image", "positive", "negative", "clip_vision_output"):
                lid = input_link_id(n, name)
                sid, st = follow_link(link_idx, nodes, lid)
                out.append(f"  - WanImageToVideo [{nid}].{name}: -> n{sid} ({st or '?'})")
        if typ == "CLIPVisionEncode":
            lid = input_link_id(n, "image")
            sid, st = follow_link(link_idx, nodes, lid)
            out.append(f"  - CLIPVisionEncode [{nid}].image: -> n{sid} ({st or '?'})")
    return out


def main() -> int:
    base = WORKFLOW_DIR
    if not base.is_dir():
        print(f"ERROR: workflow dir not found: {base}", file=sys.stderr)
        return 2

    missing: list[str] = []
    reports: list[str] = []

    for name in TARGET_NAMES:
        path = base / name
        if not path.is_file():
            missing.append(name)
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            reports.append(f"\n## {name}\n**INVALID JSON**: {e}\n")
            continue
        nodes_list = data.get("nodes") or []
        links = data.get("links") or []
        if not nodes_list:
            reports.append(f"\n## {name}\n(no nodes)\n")
            continue
        link_idx = build_link_index(links)
        nodes = node_by_id(nodes_list)

        mx_count = sum(1 for n in nodes.values() if str(n.get("type")) in ("mxSlider", "mxSlider2D"))
        sched_lines = analyze_scheduler(nodes, link_idx)
        vlines, ilines = analyze_media_sources(nodes)
        wan_lines = wan_clip_image_inputs(nodes, link_idx)

        reports.append(f"\n## {name}\n")
        reports.append(f"- Path: `{path.as_posix()}`\n")
        reports.append(f"- Nodes: {len(nodes)} | mxSlider/mxSlider2D: {mx_count}\n")
        if sched_lines:
            reports.append("### Schedulers (steps / denoise wiring)\n")
            reports.extend(l + "\n" for l in sched_lines)
        else:
            reports.append("### Schedulers\n(none BasicScheduler/SDTurboScheduler)\n")
        reports.append("### Video loaders\n")
        reports.extend((l + "\n" for l in vlines) if vlines else ["(none detected)\n"])
        reports.append("### Image loaders / notable image UI\n")
        reports.extend((l + "\n" for l in ilines) if ilines else ["(none detected)\n"])
        reports.append("### Wan / CLIP vision image inputs (immediate upstream type)\n")
        reports.extend((l + "\n" for l in wan_lines) if wan_lines else ["(no WanImageToVideo / CLIPVisionEncode)\n"])

        disc = disconnected_litegraph_nodes(data)
        if disc:
            reports.append("### Orphan litegraph nodes (no link endpoints)\n")
            for line in format_disconnected_report(disc, indent="  "):
                reports.append(line + "\n")
        else:
            reports.append("### Orphan litegraph nodes (no link endpoints)\n(none)\n")

    out = "".join(reports)
    print("# FB9 workflow sanity report\n")
    print(out)
    if missing:
        print("\n## Missing files (not in workflows dir)\n")
        for m in missing:
            print(f"- {m}")
    print(
        "\n---\n**Interpretation**: `denoise=link->mxSlider` means the scheduler's denoise input is driven by an mxToolkit slider (API: edit that node's `Xi`/`Xf`). "
        "`literal/widget` means denoise is set on the scheduler node itself. "
        "Video paths usually live under `VHS_LoadVideoPath` / `widgets_values.video`. "
        "**Orphan nodes** never appear as `links` from/to ids; a disconnected `KSampler` often has a misleading seed widget vs the real `RandomNoise` path."
    )
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
