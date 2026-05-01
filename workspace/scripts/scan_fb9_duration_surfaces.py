#!/usr/bin/env python3
"""
Scan ComfyUI *litegraph* workflow JSON under workflows/ for FB9-related filenames
and summarize how generation length / duration appears (seconds vs frames, literal vs linked).
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / "comfyui_user" / "default" / "workflows"
NAME_RE = re.compile(r"fb9", re.I)

DURATIONISH_INPUT = re.compile(
    r"length|duration|seconds?|frames?|num_frames|frame_count|batch_size|video_length|\bsec\b",
    re.I,
)


def _input_link(inp: object) -> bool:
    if not isinstance(inp, dict):
        return False
    link = inp.get("link")
    return link is not None and link is not False


def _input_name(inp: object) -> str:
    if isinstance(inp, dict):
        n = inp.get("name") or inp.get("localized_name")
        return str(n or "")
    return ""


def analyze_file(data: dict) -> tuple[list[dict], list[dict]]:
    """Returns (durationish_rows, wan_i2v_summary_rows)."""
    nodes = data.get("nodes")
    if not isinstance(nodes, list):
        return [], []

    duration_rows: list[dict] = []
    wan_rows: list[dict] = []

    for n in nodes:
        if not isinstance(n, dict):
            continue
        typ = str(n.get("type") or "")
        nid = n.get("id")
        inputs = n.get("inputs")
        ins_list = inputs if isinstance(inputs, list) else []

        if typ == "WanImageToVideo":
            wv = n.get("widgets_values")
            wan_rows.append(
                {
                    "node_id": nid,
                    "widgets_values": wv if isinstance(wv, list) else None,
                    "inputs": [
                        {"name": _input_name(inp), "linked": _input_link(inp)} for inp in ins_list if _input_name(inp)
                    ],
                }
            )

        for inp in ins_list:
            nm = _input_name(inp)
            if not nm or not DURATIONISH_INPUT.search(nm):
                continue
            duration_rows.append(
                {
                    "node_id": nid,
                    "type": typ,
                    "input": nm,
                    "linked": _input_link(inp),
                }
            )

    return duration_rows, wan_rows


def main() -> None:
    if not WORKFLOWS.is_dir():
        raise SystemExit(f"missing {WORKFLOWS}")

    key_stats: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"n": 0, "linked": 0, "literal": 0})
    wan_widget_lens: list[int] = []
    files_with_wan = 0
    files_parse_err = 0

    json_files = sorted(p for p in WORKFLOWS.rglob("*.json") if NAME_RE.search(p.name))

    for p in json_files:
        try:
            data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            files_parse_err += 1
            continue
        drows, wanrows = analyze_file(data)
        if wanrows:
            files_with_wan += 1
        for w in wanrows:
            wv = w.get("widgets_values")
            # Litegraph order for WanImageToVideo is typically [width, height, length, batch_size].
            if isinstance(wv, list) and len(wv) >= 3:
                try:
                    wan_widget_lens.append(int(round(float(wv[2]))))
                except (TypeError, ValueError):
                    pass
        for r in drows:
            k = (r["type"], r["input"])
            key_stats[k]["n"] += 1
            if r["linked"]:
                key_stats[k]["linked"] += 1
            else:
                key_stats[k]["literal"] += 1

    print(f"Scanned {len(json_files)} JSON paths with 'fb9' in filename (under {WORKFLOWS})")
    print(f"Parse errors: {files_parse_err}")
    print(f"Files containing at least one WanImageToVideo node: {files_with_wan}\n")

    print("=== (class_type, input_name) on any node: occurrence count (literal = unlinked widget row) ===")
    for (typ, inp), st in sorted(key_stats.items(), key=lambda x: -x[1]["n"])[:40]:
        print(f"  {typ!r} / {inp!r}: total={st['n']} literal={st['literal']} linked={st['linked']}")
    if len(key_stats) > 40:
        print(f"  ... ({len(key_stats) - 40} more keys)\n")
    else:
        print()

    print("=== WanImageToVideo: widgets_values[2] (typical litegraph *length* column; may disagree with linked runtime) ===")
    if wan_widget_lens:
        wan_widget_lens.sort()
        print(f"  sample distinct values (widgets_values[2]): {sorted(set(wan_widget_lens))[:25]}")
        print(f"  min={wan_widget_lens[0]} max={wan_widget_lens[-1]} count={len(wan_widget_lens)}")
    else:
        print("  (no numeric widgets_values[0] collected)")

    # Deep sample: one file — list WanImageToVideo input names
    sample = next((p for p in json_files if "FB9_GEX2" in p.name and p.name.endswith(".json")), None)
    if sample:
        data = json.loads(sample.read_text(encoding="utf-8", errors="replace"))
        _, wanrows = analyze_file(data)
        print(f"\n=== Sample file {sample.name}: WanImageToVideo input linkage ===")
        for w in wanrows[:3]:
            print(f"  node {w['node_id']}: inputs={w['inputs']}")
            print(f"           widgets_values={w.get('widgets_values')}")


def trace_wan_length_sources() -> None:
    """For each file, find WanImageToVideo.length link → upstream node type + widget head."""
    json_files = sorted(p for p in WORKFLOWS.rglob("*.json") if NAME_RE.search(p.name))
    src_types: dict[str, int] = defaultdict(int)
    mx_modes: dict[str, int] = defaultdict(int)
    examples: list[str] = []

    for p in json_files:
        try:
            data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        nodes = data.get("nodes")
        links = data.get("links")
        if not isinstance(nodes, list) or not isinstance(links, list):
            continue
        by_id = {n["id"]: n for n in nodes if isinstance(n, dict) and "id" in n}
        link_by_id = {}
        for L in links:
            if isinstance(L, (list, tuple)) and len(L) >= 5:
                link_by_id[L[0]] = L  # id, from, from_slot, to, to_slot

        for n in nodes:
            if not isinstance(n, dict) or n.get("type") != "WanImageToVideo":
                continue
            wan_id = n["id"]
            length_link = None
            for inp in n.get("inputs") or []:
                if isinstance(inp, dict) and inp.get("name") == "length":
                    length_link = inp.get("link")
                    break
            if length_link is None:
                continue
            L = link_by_id.get(length_link)
            if not L:
                continue
            src_id, src_slot = L[1], L[2]
            src = by_id.get(src_id, {})
            styp = str(src.get("type") or "?")
            src_types[styp] += 1
            if styp in ("mxSlider", "mxSlider2D"):
                wv = src.get("widgets_values")
                if isinstance(wv, list) and len(wv) >= 3:
                    mode = "float" if float(wv[2] or 0) > 0 else "int"
                    mx_modes[mode] += 1
            if len(examples) < 8:
                wv = src.get("widgets_values")
                examples.append(
                    f"{p.name}: Wan {wan_id} length <- {styp} {src_id} slot{src_slot} widgets_values={wv!r}"
                )

    print("\n=== WanImageToVideo `length` input: upstream node type (litegraph link trace) ===")
    for k, v in sorted(src_types.items(), key=lambda x: -x[1]):
        print(f"  {k!r}: {v} graphs")
    print("\n=== mxSlider upstream: int vs float mode (widgets_values[2] isfloatX) ===")
    for k, v in sorted(mx_modes.items(), key=lambda x: -x[1]):
        print(f"  {k!r}: {v}")
    print("\n=== Examples ===")
    for e in examples:
        print(" ", e)


if __name__ == "__main__":
    main()
    trace_wan_length_sources()
