#!/usr/bin/env python3
"""
Generalized roundtrip verification for a wip folder.

For each *.mp4 in a directory, we verify:
1) Embedded metadata matches the sidecars we wrote (workflow/preset/template hashes)
2) Roundtrip is possible: apply preset -> template produces a workflow where all preset
   values are correctly set in the corresponding nodes' widgets_values.

This is intentionally a "sanity check" tool, not a unit test.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import comfy_meta_lib as cml
import apply_comfy_preset as acp


def _stable_hash(obj: Any) -> Optional[str]:
    return cml.stable_json_sha256(obj) if obj is not None else None


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _ensure_list(v: Any) -> List[Any]:
    return v if isinstance(v, list) else []


def _ensure_dict(v: Any) -> Dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _expected_widgets_for_preset(entry: Dict[str, Any]) -> Tuple[str, Any]:
    """
    Return a (kind, expected) where kind is 'list' or 'dict' and expected is the shape we assert.
    Mirrors apply_comfy_preset.py behavior.
    """
    ctype = entry.get("class_type")
    inputs = entry.get("inputs") if isinstance(entry.get("inputs"), dict) else {}

    if ctype == "PrimitiveStringMultiline":
        return "list", [inputs.get("value", "")]
    if ctype == "LoadImage":
        # Only assert filename at index 0; index 1 is mode and left as-is.
        return "list_prefix", [inputs.get("image", "")]
    if ctype == "RandomNoise":
        return "list_prefix", [inputs.get("noise_seed")]
    if ctype == "mxSlider":
        xi, xf, isfloat = inputs.get("Xi"), inputs.get("Xf"), inputs.get("isfloatX")
        if isinstance(xi, int) and isinstance(isfloat, int) and isinstance(xf, (int, float)):
            return "list", [xi, float(xf) if isfloat else int(xf), isfloat]
        return "skip", None
    if ctype == "mxSlider2D":
        xi, xf, yi, yf = inputs.get("Xi"), inputs.get("Xf"), inputs.get("Yi"), inputs.get("Yf")
        isfx, isfy = inputs.get("isfloatX"), inputs.get("isfloatY")
        if all(isinstance(v, int) for v in (xi, yi, isfx, isfy)) and all(isinstance(v, (int, float)) for v in (xf, yf)):
            return "list", [
                int(xi),
                float(xf) if isfx else int(xf),
                int(yi),
                float(yf) if isfy else int(yf),
                int(isfx),
                int(isfy),
            ]
        return "skip", None
    if ctype == "CFGGuider":
        cfg = inputs.get("cfg")
        if isinstance(cfg, (int, float)):
            return "list_prefix", [float(cfg)]
        return "skip", None
    if ctype == "BasicScheduler":
        # We'll assert only the fields present in inputs
        return "scheduler", inputs
    if ctype == "KSamplerSelect":
        return "list_prefix", [inputs.get("sampler_name")]
    if ctype == "VHS_VideoCombine":
        return "dict_subset", inputs
    if ctype == "RIFE VFI":
        # We only write a few indices; assert those
        return "rife", inputs
    return "skip", None


def _assert_preset_applied(node: Dict[str, Any], entry: Dict[str, Any]) -> Optional[str]:
    kind, expected = _expected_widgets_for_preset(entry)
    if kind == "skip":
        return None

    wv = node.get("widgets_values")
    if kind == "list":
        if _ensure_list(wv) != expected:
            return f"widgets_values mismatch: got={wv!r} expected={expected!r}"
        return None
    if kind == "list_prefix":
        got = _ensure_list(wv)
        exp0 = expected[0]
        if not got:
            return f"widgets_values empty; expected prefix={expected!r}"
        if got[0] != exp0:
            return f"widgets_values[0] mismatch: got={got[0]!r} expected={exp0!r}"
        return None
    if kind == "scheduler":
        got = _ensure_list(wv)
        if len(got) < 3:
            return f"BasicScheduler widgets_values too short: {got!r}"
        inp: Dict[str, Any] = expected
        if "scheduler" in inp and got[0] != inp["scheduler"]:
            return f"BasicScheduler scheduler mismatch: got={got[0]!r} expected={inp['scheduler']!r}"
        if "steps" in inp and got[1] != inp["steps"]:
            return f"BasicScheduler steps mismatch: got={got[1]!r} expected={inp['steps']!r}"
        if "denoise" in inp and float(got[2]) != float(inp["denoise"]):
            return f"BasicScheduler denoise mismatch: got={got[2]!r} expected={inp['denoise']!r}"
        return None
    if kind == "dict_subset":
        got = _ensure_dict(wv)
        if "videopreview" in got:
            return "VHS_VideoCombine should not contain videopreview"
        for k, v in expected.items():
            if k not in got:
                return f"VHS_VideoCombine missing key {k!r}"
            if got[k] != v:
                return f"VHS_VideoCombine[{k}] mismatch: got={got[k]!r} expected={v!r}"
        return None
    if kind == "rife":
        got = _ensure_list(wv)
        if len(got) < 6:
            return f"RIFE widgets_values too short: {got!r}"
        inp: Dict[str, Any] = expected
        if "ckpt_name" in inp and got[0] != inp["ckpt_name"]:
            return f"RIFE ckpt_name mismatch: got={got[0]!r} expected={inp['ckpt_name']!r}"
        if "multiplier" in inp and got[2] != inp["multiplier"]:
            return f"RIFE multiplier mismatch: got={got[2]!r} expected={inp['multiplier']!r}"
        if "fast_mode" in inp and got[3] != inp["fast_mode"]:
            return f"RIFE fast_mode mismatch: got={got[3]!r} expected={inp['fast_mode']!r}"
        if "ensemble" in inp and got[4] != inp["ensemble"]:
            return f"RIFE ensemble mismatch: got={got[4]!r} expected={inp['ensemble']!r}"
        return None

    return f"Unknown assertion kind: {kind}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify ComfyUI roundtrip (mp4 metadata -> preset/template -> applied workflow)")
    ap.add_argument("dir", help="Directory containing *.mp4 and sidecars")
    ap.add_argument("--limit", type=int, default=0, help="Only check first N mp4s (0 = all)")
    ap.add_argument(
        "--no-verify-embedded",
        action="store_true",
        help="Skip verifying embedded MP4 prompt/workflow hashes against sidecars (faster).",
    )
    args = ap.parse_args()

    d = Path(args.dir)
    mp4s = sorted([p for p in d.iterdir() if p.is_file() and p.suffix.lower() == ".mp4"])
    if args.limit and args.limit > 0:
        mp4s = mp4s[: args.limit]

    failures: Dict[str, List[str]] = {}
    checked = 0

    for mp4 in mp4s:
        checked += 1
        errs: List[str] = []

        preset_path = mp4.with_suffix(".preset.json")
        template_path = mp4.with_suffix(".template.cleaned.json")
        workflow_path = mp4.with_suffix(".workflow.json")
        meta_path = mp4.with_suffix(".metadata.json")

        for p in (preset_path, template_path, workflow_path, meta_path):
            if not p.exists():
                errs.append(f"missing sidecar: {p.name}")

        if errs:
            failures[mp4.name] = errs
            continue

        preset_obj = _load_json(preset_path)
        template_obj = _load_json(template_path)
        workflow_obj_sidecar = _load_json(workflow_path)
        meta_obj = _load_json(meta_path)

        # Verify embedded MP4 metadata matches sidecars (optional).
        if not args.no_verify_embedded:
            tags = cml.ffprobe_format_tags(mp4)
            prompt_obj_embed, workflow_obj_embed = cml.extract_prompt_workflow_from_tags(tags)

            embed_workflow_hash = _stable_hash(workflow_obj_embed)
            side_workflow_hash = _stable_hash(workflow_obj_sidecar)
            if embed_workflow_hash != side_workflow_hash:
                errs.append("workflow.json hash mismatch vs embedded MP4 workflow")

            embed_preset = cml.extract_preset(prompt_obj_embed)
            embed_preset_hash = _stable_hash(embed_preset)
            side_preset_hash = _stable_hash(preset_obj)
            if embed_preset_hash != side_preset_hash:
                errs.append("preset.json hash mismatch vs embedded MP4 prompt-derived preset")

            # Compare to metadata.json fields if present
            if meta_obj.get("workflow_sha256") and meta_obj["workflow_sha256"] != side_workflow_hash:
                errs.append("metadata.json workflow_sha256 mismatch vs workflow.json")
            if meta_obj.get("preset_sha256") and meta_obj["preset_sha256"] != side_preset_hash:
                errs.append("metadata.json preset_sha256 mismatch vs preset.json")

        # Roundtrip: apply preset -> template
        rebuilt, stats = acp.apply_preset(template_obj, preset_obj)
        if stats.get("missing_nodes"):
            errs.append(f"apply_preset missing_nodes={stats.get('missing_nodes')}")
        if stats.get("skipped"):
            errs.append(f"apply_preset skipped={stats.get('skipped')}")

        # Validate each preset entry applied.
        nodes = {n.get("id"): n for n in rebuilt.get("nodes", []) if isinstance(n, dict) and isinstance(n.get("id"), int)}
        pnodes = preset_obj.get("nodes") if isinstance(preset_obj.get("nodes"), dict) else {}
        for key, entry in pnodes.items():
            if not isinstance(key, str) or not isinstance(entry, dict):
                continue
            nid, _title = acp._parse_preset_key(key)
            if nid is None:
                continue
            node = nodes.get(nid)
            if node is None:
                # could be title-fallback; we don't have direct mapping here.
                continue
            msg = _assert_preset_applied(node, entry)
            if msg:
                errs.append(f"{key}: {msg}")

        if errs:
            failures[mp4.name] = errs

    out = {
        "dir": str(d),
        "checked_mp4s": checked,
        "failures": len(failures),
        "ok": checked - len(failures),
    }
    print(json.dumps(out, indent=2))
    if failures:
        # Print a short failure sample
        sample = list(failures.items())[:10]
        print("## Failure sample (first 10)")
        for fname, errs in sample:
            print(f"- {fname}")
            for e in errs[:10]:
                print(f"  - {e}")
            if len(errs) > 10:
                print(f"  ... and {len(errs) - 10} more")
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())

