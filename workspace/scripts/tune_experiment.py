#!/usr/bin/env python3
"""
Systematic tuning experiments for ComfyUI workflows.

Two phases:
  1) generate: create an experiment directory with prompt/workflow variants
  2) run: validate and list runs (submission to ComfyUI is done by experiment_queue_manager.py)

We generate variants by taking a *base prompt* extracted from an MP4's embedded metadata
and overriding key "control panel" nodes (mxSlider / RandomNoise / VHS_VideoCombine filename_prefix, etc).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import itertools
import json
import math
import re
import shutil
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import comfy_meta_lib as cml
import clean_comfy_workflow as ccw


def _now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _slug(s: str) -> str:
    out = []
    for ch in s:
        if ch.isalnum() or ch in ("-", "_", "."):
            out.append(ch)
        else:
            out.append("_")
    return "".join(out).strip("_")


def _read_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


_WIN_ABS_PATH_RE = re.compile(r"^[a-zA-Z]:\\\\")
_WIN_UNC_PATH_RE = re.compile(r"^\\\\\\\\")
_PATHLIKE_EXTS = (
    ".safetensors",
    ".ckpt",
    ".gguf",
    ".pt",
    ".pth",
    ".bin",
    ".yaml",
    ".yml",
    ".json",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".mp4",
)


def _normalize_prompt_paths_for_linux(prompt_obj: Dict[str, Any]) -> None:
    """
    Normalize Windows-style backslashes in prompt inputs to forward slashes.

    This prevents common "value_not_in_list" validation errors for dropdowns
    (model names / subfolder paths) when prompts were authored on Windows.
    """

    def norm_str(s: str) -> str:
        if "\\" not in s:
            return s
        if _WIN_ABS_PATH_RE.match(s) or _WIN_UNC_PATH_RE.match(s):
            return s
        sl = s.lower()
        if any(ext in sl for ext in _PATHLIKE_EXTS):
            return s.replace("\\", "/")
        return s

    for _nid, node in prompt_obj.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for k, v in list(inputs.items()):
            if isinstance(v, str):
                nv = norm_str(v)
                if nv != v:
                    inputs[k] = nv


def _prune_dead_nodes(prompt_obj: Dict[str, Any]) -> int:
    """
    Remove nodes not connected to any sink node with inputs.

    Some workflows include optional UI/helper nodes that are not part of the
    executable graph; if those reference missing custom nodes, ComfyUI rejects
    the entire prompt. Pruning keeps only the dependency closure of likely
    output/sink nodes.
    """

    incoming: Dict[str, set] = {str(k): set() for k in prompt_obj.keys()}
    outgoing: Dict[str, set] = {str(k): set() for k in prompt_obj.keys()}

    for dst_id, node in prompt_obj.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for _k, v in inputs.items():
            if isinstance(v, list) and len(v) >= 2:
                src_id = str(v[0])
                dst_id_s = str(dst_id)
                if src_id in prompt_obj:
                    outgoing[src_id].add(dst_id_s)
                    incoming[dst_id_s].add(src_id)

    def has_any_inputs(nid: str) -> bool:
        node = prompt_obj.get(nid)
        if not isinstance(node, dict):
            return False
        inputs = node.get("inputs")
        return isinstance(inputs, dict) and len(inputs) > 0

    roots = [nid for nid in prompt_obj.keys() if len(outgoing[str(nid)]) == 0 and has_any_inputs(str(nid))]
    if not roots:
        return 0

    keep = set()
    stack = [str(r) for r in roots]
    while stack:
        cur = stack.pop()
        if cur in keep:
            continue
        keep.add(cur)
        for src in incoming.get(cur, set()):
            if src not in keep:
                stack.append(src)

    removed = 0
    for nid in list(prompt_obj.keys()):
        if str(nid) not in keep:
            prompt_obj.pop(nid, None)
            removed += 1
    return removed


def _write_json(p: Path, obj: Any, *, indent: int = 2) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=indent, ensure_ascii=False), encoding="utf-8")


def _utc_iso(ts: float) -> str:
    # ISO-ish UTC timestamp for stable logs/JSON.
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _read_json_dict(p: Path) -> Dict[str, Any]:
    """
    Best-effort read of a JSON object. Returns {} on missing/invalid.
    """
    try:
        obj = _read_json(p)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _merge_json_dict(p: Path, patch: Dict[str, Any], *, indent: int = 2) -> Dict[str, Any]:
    """
    Read JSON object (if present), shallow-merge patch, write back.
    Returns merged dict.
    """
    base = _read_json_dict(p) if p.exists() else {}
    merged = {**base, **patch}
    _write_json(p, merged, indent=indent)
    return merged


def _metrics_path(run_dir: Path) -> Path:
    return run_dir / "metrics.json"


def _extract_base_from_mp4(mp4: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    tags = cml.ffprobe_format_tags(mp4)
    prompt_obj, workflow_obj = cml.extract_prompt_workflow_from_tags(tags)
    if not isinstance(prompt_obj, dict):
        raise SystemExit("No embedded prompt JSON found in MP4 tags.")
    if not isinstance(workflow_obj, dict):
        raise SystemExit("No embedded workflow JSON found in MP4 tags.")
    return prompt_obj, workflow_obj


def _extract_base_from_png(png: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    chunks = cml.read_png_text_chunks(png)
    prompt_obj, workflow_obj = cml.extract_prompt_workflow_from_png_chunks(chunks)
    if not isinstance(prompt_obj, dict):
        raise SystemExit("No embedded prompt JSON found in PNG metadata.")
    if not isinstance(workflow_obj, dict):
        raise SystemExit("No embedded workflow JSON found in PNG metadata.")
    return prompt_obj, workflow_obj


def _extract_base_from_media(media_path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Extract prompt and workflow from MP4 (container tags) or PNG (tEXt/zTXt/iTXt chunks)."""
    suf = (media_path.suffix or "").lower()
    if suf == ".png":
        return _extract_base_from_png(media_path)
    return _extract_base_from_mp4(media_path)


def _extract_source_image_from_workflow_data(
    prompt_obj: Dict[str, Any],
    workflow_obj: Dict[str, Any],
    base_mp4: Path,
) -> Optional[str]:
    """
    Extract the source image path from the workflow data embedded in the MP4.
    Looks for LoadImage (or similar) node in the resolved prompt first, then workflow.
    Returns a resolved path string suitable for manifest.source_image, or None if not found.
    """
    # 1) Prefer prompt: it's the resolved execution graph with concrete inputs.
    for _nid, node in prompt_obj.items():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") != "LoadImage":
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        image_in = inputs.get("image")
        if image_in is None:
            continue
        path_str: Optional[str] = None
        if isinstance(image_in, str) and image_in.strip():
            path_str = image_in.strip().replace("\\", "/")
        elif isinstance(image_in, list) and len(image_in) >= 1:
            # ComfyUI folder-based: ["folder", "subpath"] or ["filename"]
            last = image_in[-1]
            if isinstance(last, str) and last.strip():
                path_str = last.strip().replace("\\", "/")
            elif len(image_in) >= 2 and isinstance(image_in[0], str) and isinstance(image_in[1], str):
                path_str = f"{image_in[0]}/{image_in[1]}".replace("\\", "/")
        if not path_str:
            continue
        # Resolve relative to the source video's directory.
        candidate = Path(path_str)
        if not candidate.is_absolute():
            candidate = (base_mp4.parent / path_str).resolve()
        return str(candidate)

    # 2) Fallback: workflow nodes with LoadImage and widgets_values (list: often [image_path]).
    nodes = workflow_obj.get("nodes") if isinstance(workflow_obj, dict) else None
    if not isinstance(nodes, list):
        return None
    for n in nodes:
        if not isinstance(n, dict):
            continue
        if n.get("type") != "LoadImage" and n.get("class_type") != "LoadImage":
            continue
        wv = n.get("widgets_values")
        if not isinstance(wv, list) or len(wv) < 1:
            continue
        first = wv[0]
        if not isinstance(first, str) or not first.strip():
            continue
        path_str = first.strip().replace("\\", "/")
        candidate = Path(path_str)
        if not candidate.is_absolute():
            candidate = (base_mp4.parent / path_str).resolve()
        return str(candidate)
    return None


_WIP_VARIANT_RE = re.compile(r"_(OG|UPIN)_(\d+)$", re.IGNORECASE)


def _copy_base_media_variants(*, base_mp4: Path, exp_dir: Path) -> List[str]:
    """
    Copy the base input media (OG/UPIN MP4 + companion PNGs, if present) into the experiment directory.

    This is a convenience feature so each experiment folder is self-contained for quick inspection.
    Copies are stored under: <exp_dir>/inputs/

    Returns list of copied relative paths (posix-ish) under exp_dir.
    """
    inputs_dir = exp_dir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    src_dir = base_mp4.parent
    stem = base_mp4.stem

    m = _WIP_VARIANT_RE.search(stem)
    # Always include the exact base file + companion extension (MP4<->PNG).
    if (base_mp4.suffix or "").lower() == ".png":
        candidates: List[Path] = [base_mp4, base_mp4.with_suffix(".mp4")]
    else:
        candidates = [base_mp4, base_mp4.with_suffix(".png")]

    if m:
        group = stem[: m.start()]
        idx = m.group(2)

        groups = {group}
        if group.startswith("Test_"):
            groups.add(group[len("Test_") :])
        else:
            groups.add("Test_" + group)

        for g in sorted(groups):
            for var in ("OG", "UPIN"):
                s = src_dir / f"{g}_{var}_{idx}.mp4"
                candidates.append(s)
                candidates.append(s.with_suffix(".png"))

    copied: List[str] = []
    for src in candidates:
        try:
            if not src.exists() or not src.is_file():
                continue
            dst = inputs_dir / src.name
            if dst.exists():
                continue
            shutil.copy2(src, dst)
            copied.append(str(dst.relative_to(exp_dir)).replace("\\", "/"))
        except Exception:
            # best-effort; don't fail experiment generation if a copy fails
            continue

    # Also extract embedded prompt/workflow JSON from copied inputs into sidecar files.
    # Prefer companion PNG (usually most faithful) over container tags.
    def _write_if_missing(path: Path, obj: Any) -> None:
        try:
            if path.exists():
                return
            if obj is None:
                return
            if not isinstance(obj, dict):
                return
            _write_json(path, obj, indent=2)
        except Exception:
            return

    # First pass: PNGs
    try:
        for p in sorted(inputs_dir.glob("*.png"), key=lambda x: x.name):
            try:
                chunks = cml.read_png_text_chunks(p)
                prompt_obj, workflow_obj = cml.extract_prompt_workflow_from_png_chunks(chunks)
                _write_if_missing(inputs_dir / f"{p.stem}.prompt.json", prompt_obj)
                _write_if_missing(inputs_dir / f"{p.stem}.workflow.json", workflow_obj)
            except Exception:
                continue
    except Exception:
        pass

    # Second pass: videos (only if sidecars not already present)
    try:
        for p in sorted(list(inputs_dir.glob("*.mp4")) + list(inputs_dir.glob("*.mov")) + list(inputs_dir.glob("*.mkv")) + list(inputs_dir.glob("*.webm")), key=lambda x: x.name):
            try:
                prompt_path = inputs_dir / f"{p.stem}.prompt.json"
                workflow_path = inputs_dir / f"{p.stem}.workflow.json"
                if prompt_path.exists() and workflow_path.exists():
                    continue
                tags = cml.ffprobe_format_tags(p)
                prompt_obj, workflow_obj = cml.extract_prompt_workflow_from_tags(tags)
                _write_if_missing(prompt_path, prompt_obj)
                _write_if_missing(workflow_path, workflow_obj)
            except Exception:
                continue
    except Exception:
        pass

    return copied


def _is_prompt_graph_ref(v: Any) -> bool:
    # Prompt JSON sometimes uses graph references like ["123", 0]
    return isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and isinstance(v[1], int)


def _exp_base_stem(*, exp_dir: Path, manifest: Optional[Dict[str, Any]] = None, base_mp4: Optional[Path] = None) -> str:
    """
    Best-effort stem for naming exported workflows.

    Preference order:
    - base_mp4 (Path) if provided
    - manifest.base_mp4 filename
    - manifest.base_mp4_fallback filename
    - exp_dir.name
    """
    try:
        if isinstance(base_mp4, Path):
            return base_mp4.stem
    except Exception:
        pass
    if isinstance(manifest, dict):
        for k in ("base_mp4", "base_mp4_fallback"):
            v = manifest.get(k)
            if isinstance(v, str) and v.strip():
                try:
                    return Path(v).name.rsplit(".", 1)[0]
                except Exception:
                    continue
    return Path(exp_dir).name


def _workflow_short_name_from_stem(stem: str) -> str:
    """
    Derive a stable workflow name used in production wip prefixes.
    Example stems:
      - FB8VA5L-2026-02-01-003706_UPIN_00001  -> FB8VA5L
      - Test_FB8VA5L-2026-02-03-001817_UPIN_00001 -> FB8VA5L
    """
    s = (stem or "").strip()
    if s.startswith("Test_"):
        s = s[len("Test_") :]
    # take token before first '-' if present
    if "-" in s:
        s = s.split("-", 1)[0]
    return s or "workflow"


def _force_production_wip_prefixes(prompt: Dict[str, Any], *, workflow_short: str) -> int:
    """
    Force production (wip) output prefixes for final OG/UPIN videos.

    This is used for *candidate/tuned workflows* that you open in ComfyUI.
    It intentionally does NOT affect the experiment submission `prompt.json`
    which may isolate outputs under output/experiments/.
    """
    n = 0
    for _, node in prompt.items():
        if not isinstance(node, dict) or node.get("class_type") != "VHS_VideoCombine":
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        fp = inputs.get("filename_prefix")
        if not isinstance(fp, str) or not fp.strip():
            continue

        # Only override the final OG/UPIN outputs (these are the ones that typically get experiment isolation).
        tag = None
        u = fp.upper()
        if "_UPIN" in u:
            tag = "UPIN"
        elif "_OG" in u:
            tag = "OG"
        else:
            # Leave other outputs alone (e.g. intermediate IN, stills, etc.)
            continue

        inputs["filename_prefix"] = (
            f"output/wip/%date:yyyy-MM-dd%/{workflow_short}-%date:yyyy-MM-dd%-%date:hhmmss%_{tag}"
        )
        n += 1
    return n


def _workflow_widget_name_order(node: Dict[str, Any]) -> List[str]:
    """
    Best-effort mapping from workflow node inputs -> widgets_values index order.

    ComfyUI workflow JSON stores widget state in `widgets_values` (list or dict).
    For list widgets, the order generally corresponds to the order of `inputs[]`
    entries that have a `widget` and are not wired (`link` is null).
    """
    ins = node.get("inputs")
    if not isinstance(ins, list):
        return []
    out: List[str] = []
    for entry in ins:
        if not isinstance(entry, dict):
            continue
        # Only patch non-wired widget inputs.
        if entry.get("link") is not None:
            continue
        w = entry.get("widget")
        if not isinstance(w, dict):
            continue
        # Prefer widget.name if present, else fall back to input.name
        wname = w.get("name")
        if not isinstance(wname, str) or not wname.strip():
            wname = entry.get("name")
        if isinstance(wname, str) and wname.strip():
            out.append(wname)
    return out


def materialize_workflow_from_prompt(workflow_template: Dict[str, Any], prompt_obj: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Create a loadable workflow JSON by patching `widgets_values` from a resolved `prompt` JSON.

    - Does NOT change graph wiring (links), node ids, or node structure.
    - Only patches scalar widget inputs (int/float/bool/str/None).
    - Skips prompt graph references like ["123", 0].
    """
    # Deep copy (keep it simple and JSON-safe)
    workflow = json.loads(json.dumps(workflow_template))

    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        return workflow, {"patched_nodes": 0, "patched_values": 0, "missing_nodes": 0}

    # Build id->node and widget-name->index maps
    wf_nodes_by_id: Dict[int, Dict[str, Any]] = {}
    wf_widget_index: Dict[int, Dict[str, int]] = {}
    for n in nodes:
        if not isinstance(n, dict):
            continue
        nid = n.get("id")
        if not isinstance(nid, int):
            continue
        wf_nodes_by_id[nid] = n
        order = _workflow_widget_name_order(n)
        wf_widget_index[nid] = {name: idx for idx, name in enumerate(order)}

    stats = {"patched_nodes": 0, "patched_values": 0, "missing_nodes": 0}
    touched_nodes: set[int] = set()

    for node_id_str, pnode in prompt_obj.items():
        if not isinstance(node_id_str, str) or not isinstance(pnode, dict):
            continue
        try:
            node_id = int(node_id_str)
        except Exception:
            continue
        wf_node = wf_nodes_by_id.get(node_id)
        if wf_node is None:
            stats["missing_nodes"] += 1
            continue

        pinputs = pnode.get("inputs")
        if not isinstance(pinputs, dict):
            continue

        wv = wf_node.get("widgets_values")
        if not isinstance(wv, (list, dict)):
            continue

        patched_any = False
        if isinstance(wv, dict):
            wv2 = dict(wv)
            for k, v in pinputs.items():
                if not isinstance(k, str) or _is_prompt_graph_ref(v):
                    continue
                if isinstance(v, (int, float, bool)) or v is None or isinstance(v, str):
                    # For dict widgets, patch by key name.
                    wv2[k] = v
                    stats["patched_values"] += 1
                    patched_any = True
            if patched_any:
                wf_node["widgets_values"] = wv2
        else:
            wv2 = list(wv)
            idx_map = wf_widget_index.get(node_id, {})
            for k, v in pinputs.items():
                if not isinstance(k, str) or _is_prompt_graph_ref(v):
                    continue
                if not (isinstance(v, (int, float, bool)) or v is None or isinstance(v, str)):
                    continue
                idx = idx_map.get(k)
                if idx is None:
                    continue
                # Ensure list is long enough
                while len(wv2) <= idx:
                    wv2.append(None)
                wv2[idx] = v
                stats["patched_values"] += 1
                patched_any = True
            if patched_any:
                wf_node["widgets_values"] = wv2

        if patched_any:
            touched_nodes.add(node_id)

    stats["patched_nodes"] = len(touched_nodes)
    return workflow, stats


def _find_prompt_nodes_by_title(prompt: Dict[str, Any], title: str) -> List[str]:
    hits: List[str] = []
    for nid, node in prompt.items():
        if not isinstance(nid, str) or not isinstance(node, dict):
            continue
        meta = node.get("_meta")
        if not isinstance(meta, dict):
            continue
        t = meta.get("title")
        if isinstance(t, str) and t == title:
            hits.append(nid)
    return hits


def _set_unique_prompt_input(prompt: Dict[str, Any], *, title: str, class_type: str, updates: Dict[str, Any]) -> None:
    hits = _find_prompt_nodes_by_title(prompt, title)
    hits = [nid for nid in hits if isinstance(prompt.get(nid), dict) and prompt[nid].get("class_type") == class_type]
    if len(hits) != 1:
        raise RuntimeError(f"Expected exactly 1 node titled {title!r} with class_type {class_type!r}, found {len(hits)}")
    nid = hits[0]
    node = prompt[nid]
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        inputs = {}
        node["inputs"] = inputs
    inputs.update(updates)


def _set_prompt_input_all(prompt: Dict[str, Any], *, title: str, class_type: str, updates: Dict[str, Any]) -> int:
    """
    Update inputs for ALL nodes matching (title, class_type).

    Some real-world workflows contain multiple mxSlider nodes with the same title
    (e.g. multiple "CFG" sliders). For tuning sweeps, it's usually safer to update
    all matching nodes than to crash.
    """
    hits = _find_prompt_nodes_by_title(prompt, title)
    hits = [nid for nid in hits if isinstance(prompt.get(nid), dict) and prompt[nid].get("class_type") == class_type]
    if not hits:
        raise RuntimeError(f"Expected at least 1 node titled {title!r} with class_type {class_type!r}, found 0")
    n = 0
    for nid in hits:
        node = prompt[nid]
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            inputs = {}
            node["inputs"] = inputs
        inputs.update(updates)
        n += 1
    return n


def _update_all_inputs(prompt: Dict[str, Any], *, class_type: str, updates: Dict[str, Any]) -> int:
    """
    Apply scalar input updates to all nodes of a given class_type.
    Returns number of nodes updated.
    """
    n = 0
    for _, node in prompt.items():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") != class_type:
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            inputs = {}
            node["inputs"] = inputs
        inputs.update(updates)
        n += 1
    return n


def _set_first_noise_seed(prompt: Dict[str, Any], seed: int) -> None:
    # Prefer the RandomNoise node that drives your pipeline.
    for _, node in prompt.items():
        if isinstance(node, dict) and node.get("class_type") == "RandomNoise":
            inputs = node.get("inputs")
            if isinstance(inputs, dict):
                inputs["noise_seed"] = int(seed)
                return
    raise RuntimeError("No RandomNoise node found in prompt.")


def _force_fixed_seed_everywhere(prompt: Dict[str, Any], seed: int) -> int:
    """
    Force deterministic repeatability across reruns by overriding seed-like inputs.

    Motivation: some workflows embed seeds in multiple places (e.g. `RandomNoise.noise_seed`
    AND `KSampler.seed`). Also, some nodes have "after generate" behaviors (increment/randomize).
    We only touch keys that already exist to avoid introducing unknown inputs.

    Returns number of nodes updated.
    """
    n = 0
    for _, node in prompt.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue

        changed = False

        # Common seed inputs across ComfyUI/custom nodes.
        if "seed" in inputs and isinstance(inputs.get("seed"), int):
            inputs["seed"] = int(seed)
            changed = True
        if "noise_seed" in inputs and isinstance(inputs.get("noise_seed"), int):
            inputs["noise_seed"] = int(seed)
            changed = True

        # Common "after generate" behaviors for seed. Only set if key exists.
        # (Avoid adding unknown keys; some nodes validate inputs.)
        for k in ("control_after_generate", "seed_after_generate", "seed_after_run"):
            v = inputs.get(k)
            if isinstance(v, str):
                # Typical choices: "fixed", "randomize", "increment"
                if v.lower() != "fixed":
                    inputs[k] = "fixed"
                    changed = True
            elif isinstance(v, int):
                # Some custom nodes use 0/1 toggles.
                if v != 0:
                    inputs[k] = 0
                    changed = True
            elif isinstance(v, bool):
                if v is True:
                    inputs[k] = False
                    changed = True

        if changed:
            n += 1
    return n


def _rewrite_filename_prefix(prefix: str, *, exp_id: str, run_id: str) -> str:
    # Keep the basename (everything after last slash/backslash), but move into output/experiments/<exp_id>/
    parts = prefix.replace("\\", "/").split("/")
    base = parts[-1] if parts else prefix
    # Preserve date token folder pattern if present; otherwise just use exp dir.
    # (Your typical prefix already contains %date:yyyy-MM-dd% in a folder segment.)
    if any("%date:yyyy-MM-dd%" in p for p in parts):
        return f"output/experiments/{exp_id}/%date:yyyy-MM-dd%/{run_id}_{base}"
    return f"output/experiments/{exp_id}/{run_id}_{base}"


def _label_outputs(prompt: Dict[str, Any], *, exp_id: str, run_id: str) -> None:
    for _, node in prompt.items():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") != "VHS_VideoCombine":
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        fp = inputs.get("filename_prefix")
        if isinstance(fp, str) and fp.strip():
            inputs["filename_prefix"] = _rewrite_filename_prefix(fp, exp_id=exp_id, run_id=run_id)


def _set_slider(prompt: Dict[str, Any], *, title: str, value: float, is_int: bool) -> None:
    """
    For mxSlider nodes in this workflow, we set:
      Xi=int(value) (or value if int)
      Xf=value
      isfloatX=0/1
    """
    if is_int:
        updates = {"Xi": int(value), "Xf": int(value), "isfloatX": 0}
    else:
        updates = {"Xi": int(value), "Xf": float(value), "isfloatX": 1}
    _set_prompt_input_all(prompt, title=title, class_type="mxSlider", updates=updates)


def _set_slider_any_title(prompt: Dict[str, Any], *, titles: List[str], value: float, is_int: bool) -> None:
    last_err: Optional[Exception] = None
    for t in titles:
        try:
            _set_slider(prompt, title=t, value=value, is_int=is_int)
            return
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"Could not set mxSlider for any of titles={titles!r}. Last error: {last_err}")


def _grid(values: Optional[List[float]]) -> List[Optional[float]]:
    return [None] if not values else values


def _grid_i(values: Optional[List[int]]) -> List[Optional[int]]:
    return [None] if not values else values


def _resolve_sweep(arg: Optional[list], defaults: list) -> Optional[list]:
    """
    Sweep argument semantics:
    - None  => do not sweep (keep base value)
    - []    => use defaults
    - [..]  => use provided values
    """
    if arg is None:
        return None
    if isinstance(arg, list) and len(arg) == 0:
        return list(defaults)
    return arg


DEFAULT_SWEEPS = {
    # Core quality knobs
    # Speed (RUN_SpeedShift): lower = smoother/slower motion; higher = faster/frenetic. Keep defaults low for smooth output.
    "speed": [2.0, 3.0],
    "cfg": [4.5, 6.0],
    "denoise": [0.80, 0.85],
    "steps": [28, 32],
    "teacache": [0.10, 0.15],
    # Post / encode knobs (use only if you opt in)
    "crf": [17, 19],
    "pix_fmt": ["yuv420p"],
    # Skip layer (small nudge around baseline)
    "skip_blocks": ["10"],
    "skip_start": [0.2, 0.3],
    "skip_end": [1.0],
    # Temporal attention multipliers (keep small by default: 2x2=4 runs)
    # If you want a larger sweep, pass explicit values.
    "ta_self_temporal": [0.95, 1.05],
    "ta_cross_temporal": [0.95, 1.05],
}

DEFAULT_GROUPS: Dict[str, List[str]] = {
    # Keep these intentionally small so defaults are usable.
    "core": ["cfg", "denoise", "steps"],
    "motion": ["speed", "teacache"],
    "encode": ["crf", "pix_fmt"],
    "skip": ["skip_blocks", "skip_start", "skip_end"],
    "temporal": ["ta_self_temporal", "ta_cross_temporal"],
}


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _uniq(xs: List[Any]) -> List[Any]:
    out: List[Any] = []
    for x in xs:
        if x not in out:
            out.append(x)
    return out


def _prompt_mxslider_value(prompt: Dict[str, Any], *, title: str) -> Optional[float]:
    """
    Read current value of an mxSlider from the embedded prompt JSON.
    """
    hits = _find_prompt_nodes_by_title(prompt, title)
    for nid in hits:
        node = prompt.get(nid)
        if not isinstance(node, dict) or node.get("class_type") != "mxSlider":
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        xf = inputs.get("Xf")
        xi = inputs.get("Xi")
        isf = inputs.get("isfloatX")
        if isinstance(xf, (int, float)):
            return float(xf)
        if isinstance(isf, int) and isf == 0 and isinstance(xi, int):
            return float(xi)
    return None


def _prompt_first_vhs_value(prompt: Dict[str, Any], key: str) -> Any:
    for _, node in prompt.items():
        if not isinstance(node, dict) or node.get("class_type") != "VHS_VideoCombine":
            continue
        inputs = node.get("inputs")
        if isinstance(inputs, dict) and key in inputs:
            return inputs.get(key)
    return None


def _prompt_first_inputs_value(prompt: Dict[str, Any], class_type: str, key: str) -> Any:
    for _, node in prompt.items():
        if not isinstance(node, dict) or node.get("class_type") != class_type:
            continue
        inputs = node.get("inputs")
        if isinstance(inputs, dict) and key in inputs:
            return inputs.get(key)
    return None


def _heuristic_group_values(*, group: str, prompt_base: Dict[str, Any], strength: int = 1) -> Dict[str, Any]:
    """
    Produce small (usually 2-point) sweeps centered on the base video's current values.
    Goal: keep run counts modest (e.g. core=8, motion=4, encode=2).
    """
    out: Dict[str, Any] = {}
    if strength not in (0, 1, 2, 3):
        raise ValueError(f"heuristic strength must be 0..3, got {strength}")

    cfg_up = [0.5, 1.5, 2.5, 3.5][strength]
    denoise_down = [0.05, 0.10, 0.15, 0.20][strength]
    steps_up = [4, 8, 12, 16][strength]
    speed_down = [1.0, 2.0, 3.0, 4.0][strength]
    teacache_mul = [0.9, 0.75, 0.6, 0.5][strength]
    crf_down = [1, 2, 3, 4][strength]
    skip_start_up = [0.05, 0.10, 0.15, 0.20][strength]
    temporal_pct = [0.02, 0.05, 0.08, 0.12][strength]

    def mx_any(titles: List[str]) -> Optional[float]:
        for t in titles:
            v = _prompt_mxslider_value(prompt_base, title=t)
            if v is not None:
                return v
        return None

    if group == "core":
        cfg0 = mx_any(["RUN_CFG", "CFG"])
        den0 = mx_any(["RUN_Denoise", "Denoise"])
        st0 = mx_any(["RUN_Steps", "Steps"])
        if cfg0 is not None:
            out["cfg"] = _uniq([float(cfg0), float(_clamp(cfg0 + cfg_up, 0.0, 10.0))])
        if den0 is not None:
            # bias toward lower denoise to reduce temporal crawling
            out["denoise"] = _uniq([float(den0), float(_clamp(den0 - denoise_down, 0.01, 1.0))])
        if st0 is not None:
            base = int(round(st0))
            out["steps"] = _uniq([int(_clamp(base, 1, 100)), int(_clamp(base + steps_up, 1, 100))])
        return out

    if group == "motion":
        sp0 = mx_any(["RUN_SpeedShift", "Speed"])
        tc0 = mx_any(["RUN_TeaCache", "Tea cache", "Tea Cache"])
        if sp0 is not None:
            out["speed"] = _uniq([float(sp0), float(_clamp(sp0 - speed_down, 0.0, 10.0))])
        if tc0 is not None:
            out["teacache"] = _uniq([float(tc0), float(_clamp(tc0 * teacache_mul, 0.01, 0.5))])
        return out

    if group == "encode":
        crf0 = _prompt_first_vhs_value(prompt_base, "crf")
        pix0 = _prompt_first_vhs_value(prompt_base, "pix_fmt")
        if isinstance(crf0, int):
            out["crf"] = _uniq([int(crf0), max(1, int(crf0) - crf_down)])
        if isinstance(pix0, str):
            out["pix_fmt"] = [pix0]
        return out

    if group == "skip":
        b0 = _prompt_first_inputs_value(prompt_base, "SkipLayerGuidanceWanVideo", "blocks")
        s0 = _prompt_first_inputs_value(prompt_base, "SkipLayerGuidanceWanVideo", "start_percent")
        e0 = _prompt_first_inputs_value(prompt_base, "SkipLayerGuidanceWanVideo", "end_percent")
        if isinstance(b0, str):
            out["skip_blocks"] = [b0]
        if isinstance(e0, (int, float)):
            out["skip_end"] = [float(e0)]
        if isinstance(s0, (int, float)):
            out["skip_start"] = _uniq([float(s0), float(_clamp(float(s0) + skip_start_up, 0.0, 1.0))])
        return out

    if group == "temporal":
        st0 = _prompt_first_inputs_value(prompt_base, "UNetTemporalAttentionMultiply", "self_temporal")
        ct0 = _prompt_first_inputs_value(prompt_base, "UNetTemporalAttentionMultiply", "cross_temporal")
        st = 1.0 if not isinstance(st0, (int, float)) else float(st0)
        ct = 1.0 if not isinstance(ct0, (int, float)) else float(ct0)
        out["ta_self_temporal"] = _uniq(
            [
                float(_clamp(st * (1.0 - temporal_pct), 0.0, 2.0)),
                float(_clamp(st * (1.0 + temporal_pct), 0.0, 2.0)),
            ]
        )
        out["ta_cross_temporal"] = _uniq(
            [
                float(_clamp(ct * (1.0 - temporal_pct), 0.0, 2.0)),
                float(_clamp(ct * (1.0 + temporal_pct), 0.0, 2.0)),
            ]
        )
        return out

    raise ValueError(f"Unknown group: {group}")


def _apply_default_groups(
    *,
    selected_groups: List[str],
    speed: Optional[List[float]],
    cfg: Optional[List[float]],
    denoise: Optional[List[float]],
    steps: Optional[List[int]],
    teacache: Optional[List[float]],
    crf: Optional[List[int]],
    pix_fmt: Optional[List[str]],
    skip_blocks: Optional[List[str]],
    skip_start: Optional[List[float]],
    skip_end: Optional[List[float]],
    ta_self_temporal: Optional[List[float]],
    ta_cross_temporal: Optional[List[float]],
) -> Dict[str, Any]:
    """
    If a parameter is None (not specified) and is included in a selected group,
    set it to [] to trigger default-set sweeping via _resolve_sweep().
    """
    want: set[str] = set()
    for g in selected_groups:
        want.update(DEFAULT_GROUPS.get(g, []))

    def use_default_if_selected(name: str, val: Any) -> Any:
        if val is None and name in want:
            return []
        return val

    return {
        "speed": use_default_if_selected("speed", speed),
        "cfg": use_default_if_selected("cfg", cfg),
        "denoise": use_default_if_selected("denoise", denoise),
        "steps": use_default_if_selected("steps", steps),
        "teacache": use_default_if_selected("teacache", teacache),
        "crf": use_default_if_selected("crf", crf),
        "pix_fmt": use_default_if_selected("pix_fmt", pix_fmt),
        "skip_blocks": use_default_if_selected("skip_blocks", skip_blocks),
        "skip_start": use_default_if_selected("skip_start", skip_start),
        "skip_end": use_default_if_selected("skip_end", skip_end),
        "ta_self_temporal": use_default_if_selected("ta_self_temporal", ta_self_temporal),
        "ta_cross_temporal": use_default_if_selected("ta_cross_temporal", ta_cross_temporal),
    }


def _estimate_run_count(
    *,
    speeds: Optional[List[float]],
    cfgs: Optional[List[float]],
    denoises: Optional[List[float]],
    steps: Optional[List[int]],
    teacache: Optional[List[float]],
    crf: Optional[List[int]],
    pix_fmt: Optional[List[str]],
    skip_blocks: Optional[List[str]],
    skip_start: Optional[List[float]],
    skip_end: Optional[List[float]],
    ta_self_temporal: Optional[List[float]],
    ta_cross_temporal: Optional[List[float]],
) -> int:
    def n(x: Optional[List[Any]]) -> int:
        return 1 if x is None or len(x) == 0 else len(x)

    # note: pix_fmt/skip_blocks are string lists; still count length.
    return math.prod(
        [
            n(speeds),
            n(cfgs),
            n(denoises),
            n(steps),
            n(teacache),
            n(crf),
            n(pix_fmt),
            n(skip_blocks),
            n(skip_start),
            n(skip_end),
            n(ta_self_temporal),
            n(ta_cross_temporal),
        ]
    )


def generate_experiment(
    *,
    base_mp4: Path,
    out_root: Path,
    exp_id: str,
    seed: int,
    duration_sec: float,
    baseline_first: bool,
    speeds: Optional[List[float]],
    cfgs: Optional[List[float]],
    denoises: Optional[List[float]],
    steps: Optional[List[int]],
    teacache: Optional[List[float]],
    crf: Optional[List[int]],
    pix_fmt: Optional[List[str]],
    skip_blocks: Optional[List[str]],
    skip_start: Optional[List[float]],
    skip_end: Optional[List[float]],
    ta_self_temporal: Optional[List[float]],
    ta_cross_temporal: Optional[List[float]],
    max_runs: int,
    min_runs: Optional[int] = None,
    indent: int = 2,
) -> Path:
    prompt_base, workflow_base = _extract_base_from_media(base_mp4)

    def _has_slider_any(titles: List[str]) -> bool:
        """
        Best-effort detection for whether this prompt has an mxSlider with any of the given titles.
        """
        for t in titles:
            try:
                if _prompt_mxslider_value(prompt_base, title=t) is not None:
                    return True
            except Exception:
                continue
        return False

    has_duration = _has_slider_any(["RUN_DurationSec", "Duration"])
    has_speed = _has_slider_any(["RUN_SpeedShift", "Speed"])
    has_cfg = _has_slider_any(["RUN_CFG", "CFG"])
    has_denoise = _has_slider_any(["RUN_Denoise", "Denoise"])
    has_steps = _has_slider_any(["RUN_Steps", "Steps"])
    has_teacache = _has_slider_any(["RUN_TeaCache", "Tea cache", "Tea Cache"])

    exp_dir = out_root / exp_id
    runs_dir = exp_dir / "runs"
    base_dir = exp_dir / "base"
    exp_dir.mkdir(parents=True, exist_ok=True)

    # Save base artifacts for reference
    _write_json(base_dir / "base.prompt.json", prompt_base, indent=indent)
    _write_json(base_dir / "base.workflow.json", workflow_base, indent=indent)
    workflow_template_cleaned = ccw.clean_workflow(workflow_base, canonicalize_titles=True)
    _write_json(base_dir / "base.template.cleaned.json", workflow_template_cleaned, indent=indent)

    # Convenience: copy OG/UPIN + PNG variants next to the experiment.
    base_media_copies = _copy_base_media_variants(base_mp4=base_mp4, exp_dir=exp_dir)

    # Source image from workflow data (for grouping in Experiments UI).
    source_image = _extract_source_image_from_workflow_data(prompt_base, workflow_base, base_mp4)

    # Prepare sweep values (None=no sweep; [] uses defaults)
    speeds = _resolve_sweep(speeds, DEFAULT_SWEEPS["speed"])
    cfgs = _resolve_sweep(cfgs, DEFAULT_SWEEPS["cfg"])
    denoises = _resolve_sweep(denoises, DEFAULT_SWEEPS["denoise"])
    steps = _resolve_sweep(steps, DEFAULT_SWEEPS["steps"])
    teacache = _resolve_sweep(teacache, DEFAULT_SWEEPS["teacache"])
    crf = _resolve_sweep(crf, DEFAULT_SWEEPS["crf"])
    pix_fmt = _resolve_sweep(pix_fmt, DEFAULT_SWEEPS["pix_fmt"])
    skip_blocks = _resolve_sweep(skip_blocks, DEFAULT_SWEEPS["skip_blocks"])
    skip_start = _resolve_sweep(skip_start, DEFAULT_SWEEPS["skip_start"])
    skip_end = _resolve_sweep(skip_end, DEFAULT_SWEEPS["skip_end"])
    ta_self_temporal = _resolve_sweep(ta_self_temporal, DEFAULT_SWEEPS["ta_self_temporal"])
    ta_cross_temporal = _resolve_sweep(ta_cross_temporal, DEFAULT_SWEEPS["ta_cross_temporal"])

    est = _estimate_run_count(
        speeds=speeds,
        cfgs=cfgs,
        denoises=denoises,
        steps=steps,
        teacache=teacache,
        crf=crf,
        pix_fmt=pix_fmt,
        skip_blocks=skip_blocks,
        skip_start=skip_start,
        skip_end=skip_end,
        ta_self_temporal=ta_self_temporal,
        ta_cross_temporal=ta_cross_temporal,
    )
    baseline_added = bool(baseline_first) and est > 1
    est_total = est + (1 if baseline_added else 0)

    # If --min-runs N and sweep yields fewer than N runs, expand one dimension at a time (steps, then cfg, then denoise).
    if min_runs is not None and est_total < min_runs:
        for _ in range(20):  # cap iterations
            if est_total >= min_runs:
                break
            # Prefer expanding steps (int), then cfg, then denoise.
            expanded = False
            if steps and len(steps) >= 2:
                lo, hi = int(steps[0]), int(steps[1])
                mid = (lo + hi) // 2
                if mid not in steps:
                    steps = sorted(set(steps) | {mid})
                    expanded = True
            if not expanded and cfgs and len(cfgs) >= 2:
                lo, hi = float(cfgs[0]), float(cfgs[1])
                mid = round((lo + hi) / 2, 2)
                if mid not in cfgs:
                    cfgs = sorted(set(cfgs) | {mid})
                    expanded = True
            if not expanded and denoises and len(denoises) >= 2:
                lo, hi = float(denoises[0]), float(denoises[1])
                mid = round((lo + hi) / 2, 2)
                if mid not in denoises:
                    denoises = sorted(set(denoises) | {mid})
                    expanded = True
            if not expanded:
                raise SystemExit(
                    f"Cannot reach --min-runs {min_runs} (current {est_total} runs). Add more sweep values manually (e.g. --steps 28 30 32)."
                )
            est = _estimate_run_count(
                speeds=speeds,
                cfgs=cfgs,
                denoises=denoises,
                steps=steps,
                teacache=teacache,
                crf=crf,
                pix_fmt=pix_fmt,
                skip_blocks=skip_blocks,
                skip_start=skip_start,
                skip_end=skip_end,
                ta_self_temporal=ta_self_temporal,
                ta_cross_temporal=ta_cross_temporal,
            )
            est_total = est + (1 if baseline_added else 0)
            if max_runs and est_total > max_runs:
                raise SystemExit(
                    f"Reaching --min-runs {min_runs} would produce {est_total} runs which exceeds --max-runs {max_runs}. Increase --max-runs or lower --min-runs."
                )

    if max_runs and est_total > max_runs:
        raise SystemExit(
            f"Sweep expands to {est_total} runs which exceeds --max-runs {max_runs}. Reduce sweep sizes."
        )

    combos = list(
        itertools.product(
            _grid(speeds),
            _grid(cfgs),
            _grid(denoises),
            _grid_i([int(x) for x in steps] if steps is not None else None),
            _grid(teacache),
            _grid_i([int(x) for x in crf] if crf is not None else None),
            [None] if pix_fmt is None else pix_fmt,
            [None] if skip_blocks is None else skip_blocks,
            _grid(skip_start),
            _grid(skip_end),
            _grid(ta_self_temporal),
            _grid(ta_cross_temporal),
        )
    )
    if baseline_added:
        # Baseline run: keep base prompt values for all sweepable knobs (but still use fixed seed + duration + output labeling).
        combos = [(None, None, None, None, None, None, None, None, None, None, None, None)] + combos

    manifest: Dict[str, Any] = {
        "exp_id": exp_id,
        "created_at": _now_stamp(),
        "base_mp4": str(base_mp4),
        "source_image": source_image,
        "base_media_copies": base_media_copies,
        "baseline_first": bool(baseline_added),
        "baseline_run_id": "run_001" if baseline_added else None,
        "server_default": "http://127.0.0.1:8188",
        "fixed_seed": int(seed),
        "fixed_duration_sec": float(duration_sec),
        "sweep": {
            "speed": speeds or [],
            "cfg": cfgs or [],
            "denoise": denoises or [],
            "steps": steps or [],
            "teacache": teacache or [],
            "crf": crf or [],
            "pix_fmt": pix_fmt or [],
            "skip_blocks": skip_blocks or [],
            "skip_start": skip_start or [],
            "skip_end": skip_end or [],
            "ta_self_temporal": ta_self_temporal or [],
            "ta_cross_temporal": ta_cross_temporal or [],
        },
        "runs": [],
    }

    for i, (
        speed,
        cfg,
        denoise,
        steps_v,
        teac,
        crf_v,
        pix_fmt_v,
        skip_blocks_v,
        skip_start_v,
        skip_end_v,
        ta_self_t_v,
        ta_cross_t_v,
    ) in enumerate(combos, start=1):
        run_id = f"run_{i:03d}"
        params = {
            "seed": int(seed),
            "duration_sec": float(duration_sec),
            "speed": speed,
            "cfg": cfg,
            "denoise": denoise,
            "steps": steps_v,
            "teacache": teac,
            "crf": crf_v,
            "pix_fmt": pix_fmt_v,
            "skip_blocks": skip_blocks_v,
            "skip_start": skip_start_v,
            "skip_end": skip_end_v,
            "ta_self_temporal": ta_self_t_v,
            "ta_cross_temporal": ta_cross_t_v,
        }
        is_baseline = baseline_added and i == 1

        # Clone prompt
        prompt = json.loads(json.dumps(prompt_base))

        # Fixed controls
        _set_first_noise_seed(prompt, int(seed))
        _force_fixed_seed_everywhere(prompt, int(seed))
        if has_duration:
            _set_slider_any_title(prompt, titles=["RUN_DurationSec", "Duration"], value=float(duration_sec), is_int=False)

        # Sweep controls (only if specified)
        if speed is not None and has_speed:
            _set_slider_any_title(prompt, titles=["RUN_SpeedShift", "Speed"], value=float(speed), is_int=False)
        if cfg is not None and has_cfg:
            _set_slider_any_title(prompt, titles=["RUN_CFG", "CFG"], value=float(cfg), is_int=False)
        if denoise is not None and has_denoise:
            _set_slider_any_title(prompt, titles=["RUN_Denoise", "Denoise"], value=float(denoise), is_int=False)
        if steps_v is not None and has_steps:
            _set_slider_any_title(prompt, titles=["RUN_Steps", "Steps"], value=float(steps_v), is_int=True)
        if teac is not None and has_teacache:
            _set_slider_any_title(
                prompt, titles=["RUN_TeaCache", "Tea cache", "Tea Cache"], value=float(teac), is_int=False
            )

        # Skip layer tweaks (direct node inputs; not through slider)
        skip_updates: Dict[str, Any] = {}
        if skip_blocks_v is not None:
            skip_updates["blocks"] = str(skip_blocks_v)
        if skip_start_v is not None:
            skip_updates["start_percent"] = float(skip_start_v)
        if skip_end_v is not None:
            skip_updates["end_percent"] = float(skip_end_v)
        if skip_updates:
            n = _update_all_inputs(prompt, class_type="SkipLayerGuidanceWanVideo", updates=skip_updates)
            if n == 0:
                # Some workflows don't include this node; skip if absent.
                pass

        # Temporal attention multipliers
        ta_updates: Dict[str, Any] = {}
        if ta_self_t_v is not None:
            ta_updates["self_temporal"] = float(ta_self_t_v)
        if ta_cross_t_v is not None:
            ta_updates["cross_temporal"] = float(ta_cross_t_v)
        if ta_updates:
            n = _update_all_inputs(prompt, class_type="UNetTemporalAttentionMultiply", updates=ta_updates)
            if n == 0:
                pass

        # Encode knobs (apply to all VHS_VideoCombine outputs)
        enc_updates: Dict[str, Any] = {}
        if crf_v is not None:
            enc_updates["crf"] = int(crf_v)
        if pix_fmt_v is not None:
            enc_updates["pix_fmt"] = str(pix_fmt_v)
        if enc_updates:
            n = _update_all_inputs(prompt, class_type="VHS_VideoCombine", updates=enc_updates)
            if n == 0:
                pass

        # Ensure outputs are labeled into a dedicated subdir.
        _label_outputs(prompt, exp_id=exp_id, run_id=run_id)

        run_dir = runs_dir / run_id
        _write_json(run_dir / "prompt.json", prompt, indent=indent)
        _write_json(run_dir / "params.json", params, indent=indent)
        # Emit per-run loadable workflow JSONs for direct use in ComfyUI UI.
        #
        # IMPORTANT: these are intended to be usable as “final tuned workflows”, so they should NOT
        # include experiment bookkeeping like output isolation. Output isolation is kept in prompt.json
        # (used for submissions) via _label_outputs().
        candidate_prompt = json.loads(json.dumps(prompt_base))
        _apply_run_params_to_prompt(candidate_prompt, params=params, include_duration=False)
        _force_production_wip_prefixes(
            candidate_prompt,
            workflow_short=_workflow_short_name_from_stem(_exp_base_stem(exp_dir=exp_dir, manifest=manifest, base_mp4=base_mp4)),
        )
        wf_run, _wf_stats = materialize_workflow_from_prompt(workflow_base, candidate_prompt)
        wf_run_cleaned, _wf_clean_stats = materialize_workflow_from_prompt(workflow_template_cleaned, candidate_prompt)
        stem = _exp_base_stem(exp_dir=exp_dir, manifest=manifest, base_mp4=base_mp4)
        wf_path = run_dir / f"{stem}.workflow.{run_id}.json"
        wf_clean_path = run_dir / f"{stem}.workflow.{run_id}.cleaned.json"
        _write_json(wf_path, wf_run, indent=indent)
        _write_json(wf_clean_path, wf_run_cleaned, indent=indent)

        manifest["runs"].append(
            {
                "run_id": run_id,
                "dir": str(run_dir),
                "params": params,
                "baseline": bool(is_baseline),
                "workflow_path": str(wf_path),
                "workflow_cleaned_path": str(wf_clean_path),
            }
        )

    _write_json(exp_dir / "manifest.json", manifest, indent=indent)
    return exp_dir


def _http_json(method: str, url: str, payload: Optional[Dict[str, Any]] = None, timeout_s: int = 30) -> Any:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def _read_prompt_id_from_submit(submit_path: Path) -> Optional[str]:
    try:
        obj = _read_json(submit_path)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    pid = obj.get("prompt_id")
    return pid if isinstance(pid, str) and pid.strip() else None


def _poll_history(*, server: str, prompt_id: str, poll_s: float, timeout_s: int) -> Any:
    start = time.time()
    hist = None
    while True:
        if time.time() - start > timeout_s:
            raise TimeoutError(f"timed out waiting for history prompt_id={prompt_id}")
        try:
            hist = _http_json("GET", f"{server}/history/{prompt_id}", None, timeout_s=30)
        except Exception:
            hist = None
        if hist:
            return hist, float(time.time() - start)
        time.sleep(poll_s)


# Sentinel file: if present in an experiment dir, do not schedule/queue any more runs (see stop_experiment.py).
STOPPED_SENTINEL = "experiment_stopped"


def run_experiment(
    *,
    exp_dir: Path,
    server: str,
    poll_s: float,
    timeout_s: int,
    limit: int = 0,
    submit_all: bool = False,
    no_wait: bool = False,
) -> None:
    if (exp_dir / STOPPED_SENTINEL).exists():
        raise SystemExit(
            f"Experiment is stopped ({STOPPED_SENTINEL} present). Remove that file to allow scheduling again."
        )
    manifest = _read_json(exp_dir / "manifest.json")
    runs = manifest.get("runs") if isinstance(manifest, dict) else None
    if not isinstance(runs, list):
        raise SystemExit("Bad manifest.json (missing runs).")

    server = server.rstrip("/")

    # Build a normalized list of runnable runs.
    selected: List[Tuple[str, Path, Path]] = []
    n = 0
    for run in runs:
        if limit and n >= limit:
            break
        if not isinstance(run, dict):
            continue
        run_id = run.get("run_id")
        run_dir = Path(run.get("dir")) if isinstance(run.get("dir"), str) else None
        if not isinstance(run_id, str) or run_dir is None:
            continue
        prompt_path = run_dir / "prompt.json"
        if not prompt_path.exists():
            continue
        selected.append((run_id, run_dir, prompt_path))
        n += 1

    # Submission is handled by the experiment queue manager (experiment_queue_manager.py).
    # run_experiment only validates and lists eligible runs; it does not POST to ComfyUI.
    # Use: python workspace/scripts/experiment_queue_manager.py [--experiments-root ...] [--server ...]


def materialize_experiment_workflows(*, exp_dir: Path, overwrite: bool, limit: int = 0, indent: int = 2) -> Dict[str, Any]:
    """
    Retroactively write per-run workflow JSON files for an existing experiment directory.

    Reads:
      - <exp_dir>/base/base.workflow.json
      - <exp_dir>/base/base.template.cleaned.json (if missing, derived from base.workflow.json)
      - <exp_dir>/base/base.prompt.json
      - <exp_dir>/runs/<run_id>/params.json

    Writes:
      - <exp_dir>/runs/<run_id>/<stem>.workflow.<run_id>.json
      - <exp_dir>/runs/<run_id>/<stem>.workflow.<run_id>.cleaned.json

    Also updates manifest.json run entries with workflow paths (best-effort).
    """
    exp_dir = Path(exp_dir)
    manifest_path = exp_dir / "manifest.json"
    manifest = _read_json_dict(manifest_path) if manifest_path.exists() else {}

    base_dir = exp_dir / "base"
    prompt_base_path = base_dir / "base.prompt.json"
    if not prompt_base_path.exists():
        raise SystemExit(f"Missing base prompt: {prompt_base_path}")
    prompt_base = _read_json(prompt_base_path)
    if not isinstance(prompt_base, dict):
        raise SystemExit(f"Bad base prompt JSON (expected object): {prompt_base_path}")

    workflow_base_path = base_dir / "base.workflow.json"
    if not workflow_base_path.exists():
        raise SystemExit(f"Missing base workflow: {workflow_base_path}")
    workflow_base = _read_json(workflow_base_path)
    if not isinstance(workflow_base, dict):
        raise SystemExit(f"Bad base workflow JSON (expected object): {workflow_base_path}")

    workflow_clean_path = base_dir / "base.template.cleaned.json"
    if workflow_clean_path.exists():
        workflow_template_cleaned = _read_json(workflow_clean_path)
        if not isinstance(workflow_template_cleaned, dict):
            workflow_template_cleaned = ccw.clean_workflow(workflow_base, canonicalize_titles=True)
    else:
        workflow_template_cleaned = ccw.clean_workflow(workflow_base, canonicalize_titles=True)

    runs_dir = exp_dir / "runs"
    if not runs_dir.exists():
        raise SystemExit(f"Missing runs directory: {runs_dir}")

    # Prefer manifest ordering if present; otherwise scan the runs directory.
    run_entries: List[Dict[str, Any]] = []
    runs = manifest.get("runs")
    if isinstance(runs, list) and runs:
        for r in runs:
            if isinstance(r, dict):
                run_entries.append(r)
    else:
        for d in sorted([p for p in runs_dir.iterdir() if p.is_dir()], key=lambda p: p.name):
            run_entries.append({"run_id": d.name, "dir": str(d)})

    stats: Dict[str, Any] = {
        "processed": 0,
        "written": 0,
        "skipped_exists": 0,
        "missing_params": 0,
        "errors": 0,
    }

    n = 0
    for r in run_entries:
        if limit and n >= limit:
            break
        run_id = r.get("run_id") if isinstance(r.get("run_id"), str) else None
        run_dir = Path(r.get("dir")) if isinstance(r.get("dir"), str) else None
        if not run_id or run_dir is None:
            continue

        params_path = run_dir / "params.json"
        if not params_path.exists():
            stats["missing_params"] += 1
            continue

        stem = _exp_base_stem(exp_dir=exp_dir, manifest=manifest, base_mp4=None)
        wf_path = run_dir / f"{stem}.workflow.{run_id}.json"
        wf_cleaned_path = run_dir / f"{stem}.workflow.{run_id}.cleaned.json"
        if not overwrite and wf_path.exists() and wf_cleaned_path.exists():
            stats["skipped_exists"] += 1
            n += 1
            continue

        try:
            params = _read_json_dict(params_path)
            candidate_prompt = json.loads(json.dumps(prompt_base))
            _apply_run_params_to_prompt(candidate_prompt, params=params, include_duration=False)
            _force_production_wip_prefixes(
                candidate_prompt,
                workflow_short=_workflow_short_name_from_stem(_exp_base_stem(exp_dir=exp_dir, manifest=manifest, base_mp4=None)),
            )

            wf_run, _ = materialize_workflow_from_prompt(workflow_base, candidate_prompt)
            wf_run_cleaned, _ = materialize_workflow_from_prompt(workflow_template_cleaned, candidate_prompt)
            _write_json(wf_path, wf_run, indent=indent)
            _write_json(wf_cleaned_path, wf_run_cleaned, indent=indent)

            r["workflow_path"] = str(wf_path)
            r["workflow_cleaned_path"] = str(wf_cleaned_path)

            stats["written"] += 1
        except Exception:
            stats["errors"] += 1

        stats["processed"] += 1
        n += 1

    # Best-effort persist manifest updates.
    if stats["written"] > 0:
        if not isinstance(manifest.get("runs"), list):
            manifest["runs"] = run_entries
        _write_json(manifest_path, manifest, indent=indent)

    return stats


def _path_from_manifest(exp_dir: Path, p: str) -> Path:
    """
    Manifest paths are often stored as strings; sometimes absolute, sometimes relative.
    Interpret relative paths as relative to exp_dir.
    """
    pp = Path(p)
    return pp if pp.is_absolute() else (exp_dir / pp)


def _apply_run_params_to_prompt(
    prompt: Dict[str, Any],
    *,
    params: Dict[str, Any],
    include_duration: bool,
) -> Dict[str, Any]:
    """
    Apply tuning params onto a prompt dict WITHOUT experiment bookkeeping.

    Intentionally does NOT:
    - force seeds
    - label outputs into experiment subdirs

    This is meant for exporting “production” workflows derived from your original workflow.
    """

    def _has_slider_any(titles: List[str]) -> bool:
        for t in titles:
            try:
                if _prompt_mxslider_value(prompt, title=t) is not None:
                    return True
            except Exception:
                continue
        return False

    stats: Dict[str, Any] = {"updated": 0, "skipped_missing": 0}

    # Duration (optional)
    if include_duration:
        dur = params.get("duration_sec")
        if isinstance(dur, (int, float)) and _has_slider_any(["RUN_DurationSec", "Duration"]):
            try:
                _set_slider_any_title(prompt, titles=["RUN_DurationSec", "Duration"], value=float(dur), is_int=False)
                stats["updated"] += 1
            except Exception:
                stats["skipped_missing"] += 1

    # Core knobs (mxSlider)
    speed = params.get("speed")
    if isinstance(speed, (int, float)) and _has_slider_any(["RUN_SpeedShift", "Speed"]):
        try:
            _set_slider_any_title(prompt, titles=["RUN_SpeedShift", "Speed"], value=float(speed), is_int=False)
            stats["updated"] += 1
        except Exception:
            stats["skipped_missing"] += 1

    cfg = params.get("cfg")
    if isinstance(cfg, (int, float)) and _has_slider_any(["RUN_CFG", "CFG"]):
        try:
            _set_slider_any_title(prompt, titles=["RUN_CFG", "CFG"], value=float(cfg), is_int=False)
            stats["updated"] += 1
        except Exception:
            stats["skipped_missing"] += 1

    denoise = params.get("denoise")
    if isinstance(denoise, (int, float)) and _has_slider_any(["RUN_Denoise", "Denoise"]):
        try:
            _set_slider_any_title(prompt, titles=["RUN_Denoise", "Denoise"], value=float(denoise), is_int=False)
            stats["updated"] += 1
        except Exception:
            stats["skipped_missing"] += 1

    steps_v = params.get("steps")
    if isinstance(steps_v, int) and _has_slider_any(["RUN_Steps", "Steps"]):
        try:
            _set_slider_any_title(prompt, titles=["RUN_Steps", "Steps"], value=float(steps_v), is_int=True)
            stats["updated"] += 1
        except Exception:
            stats["skipped_missing"] += 1

    teac = params.get("teacache")
    if isinstance(teac, (int, float)) and _has_slider_any(["RUN_TeaCache", "Tea cache", "Tea Cache"]):
        try:
            _set_slider_any_title(prompt, titles=["RUN_TeaCache", "Tea cache", "Tea Cache"], value=float(teac), is_int=False)
            stats["updated"] += 1
        except Exception:
            stats["skipped_missing"] += 1

    # Skip layer tweaks (direct node inputs; not through slider)
    skip_updates: Dict[str, Any] = {}
    if params.get("skip_blocks") is not None:
        skip_updates["blocks"] = str(params["skip_blocks"])
    if isinstance(params.get("skip_start"), (int, float)):
        skip_updates["start_percent"] = float(params["skip_start"])
    if isinstance(params.get("skip_end"), (int, float)):
        skip_updates["end_percent"] = float(params["skip_end"])
    if skip_updates:
        n = _update_all_inputs(prompt, class_type="SkipLayerGuidanceWanVideo", updates=skip_updates)
        if n > 0:
            stats["updated"] += 1
        else:
            stats["skipped_missing"] += 1

    # Temporal attention multipliers
    ta_updates: Dict[str, Any] = {}
    if isinstance(params.get("ta_self_temporal"), (int, float)):
        ta_updates["self_temporal"] = float(params["ta_self_temporal"])
    if isinstance(params.get("ta_cross_temporal"), (int, float)):
        ta_updates["cross_temporal"] = float(params["ta_cross_temporal"])
    if ta_updates:
        n = _update_all_inputs(prompt, class_type="UNetTemporalAttentionMultiply", updates=ta_updates)
        if n > 0:
            stats["updated"] += 1
        else:
            stats["skipped_missing"] += 1

    # Encode knobs (apply to all VHS_VideoCombine outputs)
    enc_updates: Dict[str, Any] = {}
    if isinstance(params.get("crf"), int):
        enc_updates["crf"] = int(params["crf"])
    if isinstance(params.get("pix_fmt"), str):
        enc_updates["pix_fmt"] = str(params["pix_fmt"])
    if enc_updates:
        n = _update_all_inputs(prompt, class_type="VHS_VideoCombine", updates=enc_updates)
        if n > 0:
            stats["updated"] += 1
        else:
            stats["skipped_missing"] += 1

    return stats


def export_tuned_workflows(
    *,
    exp_dir: Path,
    workflow_template_path: Optional[Path],
    out_dir: Optional[Path],
    run_ids: Optional[List[str]],
    include_duration: bool,
    overwrite: bool,
    limit: int = 0,
    indent: int = 2,
) -> Dict[str, Any]:
    """
    Apply per-run tuning params onto the *original/base* prompt and materialize a tuned workflow.

    Unlike `materialize`, this uses `params.json` (the tuning deltas) and keeps everything else from
    your workflow template intact (e.g. output naming/location, seed behavior), unless you opt in
    to include duration.
    """
    exp_dir = Path(exp_dir)
    manifest_path = exp_dir / "manifest.json"
    manifest = _read_json_dict(manifest_path) if manifest_path.exists() else {}

    base_dir = exp_dir / "base"
    prompt_base_path = base_dir / "base.prompt.json"
    if not prompt_base_path.exists():
        raise SystemExit(f"Missing base prompt: {prompt_base_path}")
    prompt_base = _read_json(prompt_base_path)
    if not isinstance(prompt_base, dict):
        raise SystemExit(f"Bad base prompt JSON (expected object): {prompt_base_path}")

    # Workflow template: user-specified, else experiment base workflow.
    if workflow_template_path is None:
        workflow_template_path = base_dir / "base.workflow.json"
    if not workflow_template_path.exists():
        raise SystemExit(f"Missing workflow template: {workflow_template_path}")
    workflow_template = _read_json(workflow_template_path)
    if not isinstance(workflow_template, dict):
        raise SystemExit(f"Bad workflow template JSON (expected object): {workflow_template_path}")

    cleaned_template = ccw.clean_workflow(workflow_template, canonicalize_titles=True)

    runs_dir = exp_dir / "runs"
    if not runs_dir.exists():
        raise SystemExit(f"Missing runs directory: {runs_dir}")

    if out_dir is None:
        out_dir = exp_dir / "tuned_workflows"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Run list from manifest, fallback to directory scan.
    run_entries: List[Dict[str, Any]] = []
    runs = manifest.get("runs")
    if isinstance(runs, list) and runs:
        for r in runs:
            if isinstance(r, dict):
                run_entries.append(r)
    else:
        for d in sorted([p for p in runs_dir.iterdir() if p.is_dir()], key=lambda p: p.name):
            run_entries.append({"run_id": d.name, "dir": str(d)})

    want: Optional[set[str]] = set(run_ids) if run_ids else None
    stats: Dict[str, Any] = {
        "processed": 0,
        "written": 0,
        "skipped_exists": 0,
        "missing_params": 0,
        "errors": 0,
    }

    n = 0
    for r in run_entries:
        if limit and n >= limit:
            break
        run_id = r.get("run_id") if isinstance(r.get("run_id"), str) else None
        run_dir_s = r.get("dir") if isinstance(r.get("dir"), str) else None
        if not run_id or not run_dir_s:
            continue
        if want is not None and run_id not in want:
            continue

        run_dir = _path_from_manifest(exp_dir, run_dir_s)
        params_path = run_dir / "params.json"
        if not params_path.exists():
            stats["missing_params"] += 1
            continue

        stem = _exp_base_stem(exp_dir=exp_dir, manifest=manifest, base_mp4=None)
        out_path = out_dir / f"{stem}.workflow.{run_id}.json"
        out_clean_path = out_dir / f"{stem}.workflow.{run_id}.cleaned.json"
        if not overwrite and out_path.exists() and out_clean_path.exists():
            stats["skipped_exists"] += 1
            n += 1
            continue

        try:
            params = _read_json_dict(params_path)
            # Clone base prompt and apply ONLY tuning knobs.
            prompt = json.loads(json.dumps(prompt_base))
            _apply_run_params_to_prompt(prompt, params=params, include_duration=include_duration)
            _force_production_wip_prefixes(
                prompt,
                workflow_short=_workflow_short_name_from_stem(_exp_base_stem(exp_dir=exp_dir, manifest=manifest, base_mp4=None)),
            )

            wf_run, _ = materialize_workflow_from_prompt(workflow_template, prompt)
            wf_run_clean, _ = materialize_workflow_from_prompt(cleaned_template, prompt)
            _write_json(out_path, wf_run, indent=indent)
            _write_json(out_clean_path, wf_run_clean, indent=indent)

            stats["written"] += 1
        except Exception:
            stats["errors"] += 1

        stats["processed"] += 1
        n += 1

    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate and/or run systematic ComfyUI tuning experiments")
    sub = ap.add_subparsers(dest="cmd", required=True)

    gen = sub.add_parser("generate", help="Generate an experiment sweep directory (no execution)")
    gen.add_argument("base_mp4", help="MP4 or PNG with embedded prompt/workflow metadata")
    gen.add_argument(
        "--out-root",
        default="output/output/experiments",
        help="Where to create experiment directories (default: output/output/experiments)",
    )
    gen.add_argument("--exp-id", default="", help="Experiment id (default: derived from mp4 + timestamp)")
    gen.add_argument("--seed", type=int, required=True, help="Fixed seed (RandomNoise.noise_seed)")
    gen.add_argument(
        "--duration",
        type=float,
        default=2.0,
        help="Duration in seconds for each run. Match the original video's length for similar motion pacing.",
    )
    gen.add_argument(
        "--baseline-first",
        action="store_true",
        default=True,
        help="Insert a baseline run first (no parameter overrides) when sweeping (default: true).",
    )
    gen.add_argument(
        "--no-baseline-first",
        action="store_false",
        dest="baseline_first",
        help="Disable inserting a baseline run first.",
    )
    gen.add_argument(
        "--defaults",
        action="append",
        choices=sorted(DEFAULT_GROUPS.keys()),
        default=[],
        help=(
            "Enable a bundled default sweep set. Repeatable. "
            "Example: --defaults core (sweeps cfg/denoise/steps)."
        ),
    )
    gen.add_argument(
        "--heuristic",
        action="append",
        choices=sorted(DEFAULT_GROUPS.keys()),
        default=[],
        help="Enable heuristic sweep sets centered around the base video's values. Repeatable. Example: --heuristic core",
    )
    gen.add_argument(
        "--heuristic-strength",
        type=int,
        choices=[0, 1, 2, 3],
        default=1,
        help="Heuristic aggressiveness 0..3 (0=smallest deltas, 3=largest). Default: 1.",
    )
    gen.add_argument(
        "--speed",
        type=float,
        nargs="*",
        default=None,
        help="Sweep values for Speed / RUN_SpeedShift. Lower = smoother motion; omit to keep base prompt value.",
    )
    gen.add_argument("--cfg", type=float, nargs="*", default=None, help="Sweep values for CFG (empty => defaults)")
    gen.add_argument("--denoise", type=float, nargs="*", default=None, help="Sweep values for Denoise (empty => defaults)")
    gen.add_argument("--steps", type=int, nargs="*", default=None, help="Sweep values for Steps (empty => defaults)")
    gen.add_argument("--teacache", type=float, nargs="*", default=None, help="Sweep values for Tea cache (empty => defaults)")
    gen.add_argument("--crf", type=int, nargs="*", default=None, help="Sweep values for H.264 CRF (empty => defaults)")
    gen.add_argument("--pix-fmt", dest="pix_fmt", nargs="*", default=None, help="Sweep values for pix_fmt (empty => defaults)")
    gen.add_argument("--skip-blocks", nargs="*", default=None, help="Sweep values for Skip Layer blocks (empty => defaults)")
    gen.add_argument("--skip-start", type=float, nargs="*", default=None, help="Sweep values for Skip Layer start_percent")
    gen.add_argument("--skip-end", type=float, nargs="*", default=None, help="Sweep values for Skip Layer end_percent")
    gen.add_argument("--ta-self-temporal", type=float, nargs="*", default=None, help="Sweep values for TemporalAttention self_temporal")
    gen.add_argument("--ta-cross-temporal", type=float, nargs="*", default=None, help="Sweep values for TemporalAttention cross_temporal")
    gen.add_argument("--max-runs", type=int, default=200, help="Fail if sweep expands beyond this many runs (default: 200)")
    gen.add_argument("--min-runs", type=int, default=None, metavar="N", help="If sweep yields fewer than N runs, expand steps (then cfg/denoise) until >= N (e.g. 12)")
    gen.add_argument("--indent", type=int, default=2)

    run = sub.add_parser("run", help="Run an existing experiment sweep via ComfyUI HTTP API")
    run.add_argument("exp_dir", help="Experiment directory produced by generate")
    run.add_argument("--server", default="http://127.0.0.1:8188", help="ComfyUI server base URL")
    run.add_argument("--poll", type=float, default=2.0, help="Poll interval seconds for /history")
    run.add_argument("--timeout", type=int, default=1800, help="Per-run timeout seconds")
    run.add_argument("--limit", type=int, default=0, help="Only run first N runs (0=all)")
    run.add_argument(
        "--submit-all",
        action="store_true",
        help="Submit all runs first (queue them), then poll for completion (unless --no-wait).",
    )
    run.add_argument(
        "--no-wait",
        action="store_true",
        help="Queue runs and exit immediately (implies --submit-all). Re-run later without --no-wait to write history.json.",
    )

    mat = sub.add_parser(
        "materialize",
        help="Retroactively write per-run candidate workflows from params.json (usable like tuned workflows).",
    )
    mat.add_argument("exp_dir", help="Experiment directory produced by generate")
    mat.add_argument("--overwrite", action="store_true", help="Overwrite existing workflow.json files")
    mat.add_argument("--limit", type=int, default=0, help="Only process first N runs (0=all)")
    mat.add_argument("--indent", type=int, default=2)

    app = sub.add_parser(
        "apply",
        help="Apply tuning params onto a workflow template and export TUNED workflows (no output bookkeeping).",
    )
    app.add_argument("exp_dir", help="Experiment directory produced by generate")
    app.add_argument(
        "--workflow-template",
        default="",
        help="Workflow JSON to apply tuning onto (default: <exp_dir>/base/base.workflow.json).",
    )
    app.add_argument(
        "--out-dir",
        default="",
        help="Where to write tuned workflows (default: <exp_dir>/tuned_workflows).",
    )
    app.add_argument("--run-id", action="append", default=[], help="Only export specific run_id(s). Repeatable.")
    app.add_argument("--include-duration", action="store_true", help="Also apply duration_sec if present in params.json.")
    app.add_argument("--overwrite", action="store_true", help="Overwrite existing tuned workflow files")
    app.add_argument("--limit", type=int, default=0, help="Only process first N runs (0=all)")
    app.add_argument("--indent", type=int, default=2)

    args = ap.parse_args()

    if args.cmd == "generate":
        mp4 = Path(args.base_mp4)
        out_root = Path(args.out_root)
        exp_id = args.exp_id.strip() or _slug(f"tune_{mp4.stem}_{_now_stamp()}")

        heur: Dict[str, Any] = {}
        if args.heuristic:
            prompt_base, _workflow_base = _extract_base_from_media(mp4)
            for g in (args.heuristic or []):
                heur.update(_heuristic_group_values(group=g, prompt_base=prompt_base, strength=int(args.heuristic_strength)))

        resolved = _apply_default_groups(
            selected_groups=list(args.defaults or []),
            # precedence: explicit CLI values > heuristic > defaults
            speed=args.speed if args.speed is not None else heur.get("speed"),
            cfg=args.cfg if args.cfg is not None else heur.get("cfg"),
            denoise=args.denoise if args.denoise is not None else heur.get("denoise"),
            steps=args.steps if args.steps is not None else heur.get("steps"),
            teacache=args.teacache if args.teacache is not None else heur.get("teacache"),
            crf=args.crf if args.crf is not None else heur.get("crf"),
            pix_fmt=args.pix_fmt if args.pix_fmt is not None else heur.get("pix_fmt"),
            skip_blocks=args.skip_blocks if args.skip_blocks is not None else heur.get("skip_blocks"),
            skip_start=args.skip_start if args.skip_start is not None else heur.get("skip_start"),
            skip_end=args.skip_end if args.skip_end is not None else heur.get("skip_end"),
            ta_self_temporal=args.ta_self_temporal if args.ta_self_temporal is not None else heur.get("ta_self_temporal"),
            ta_cross_temporal=args.ta_cross_temporal if args.ta_cross_temporal is not None else heur.get("ta_cross_temporal"),
        )

        exp_dir = generate_experiment(
            base_mp4=mp4,
            out_root=out_root,
            exp_id=exp_id,
            seed=args.seed,
            duration_sec=args.duration,
            baseline_first=bool(args.baseline_first),
            speeds=resolved["speed"],
            cfgs=resolved["cfg"],
            denoises=resolved["denoise"],
            steps=resolved["steps"],
            teacache=resolved["teacache"],
            crf=resolved["crf"],
            pix_fmt=resolved["pix_fmt"],
            skip_blocks=resolved["skip_blocks"],
            skip_start=resolved["skip_start"],
            skip_end=resolved["skip_end"],
            ta_self_temporal=resolved["ta_self_temporal"],
            ta_cross_temporal=resolved["ta_cross_temporal"],
            max_runs=args.max_runs,
            min_runs=args.min_runs,
            indent=args.indent,
        )
        print(str(exp_dir))
        return 0

    if args.cmd == "run":
        run_experiment(
            exp_dir=Path(args.exp_dir),
            server=args.server,
            poll_s=args.poll,
            timeout_s=args.timeout,
            limit=args.limit,
            submit_all=bool(args.submit_all or args.no_wait),
            no_wait=bool(args.no_wait),
        )
        print("OK")
        return 0

    if args.cmd == "materialize":
        stats = materialize_experiment_workflows(
            exp_dir=Path(args.exp_dir),
            overwrite=bool(args.overwrite),
            limit=int(args.limit),
            indent=int(args.indent),
        )
        print(json.dumps({"exp_dir": str(args.exp_dir), "stats": stats}, indent=2))
        return 0

    if args.cmd == "apply":
        exp_dir = Path(args.exp_dir)
        wf_path = Path(args.workflow_template) if isinstance(args.workflow_template, str) and args.workflow_template.strip() else None
        if wf_path is not None and not wf_path.is_absolute():
            # Interpret relative workflow paths as relative to CWD, same as other commands.
            wf_path = Path(args.workflow_template)
        out_dir = Path(args.out_dir) if isinstance(args.out_dir, str) and args.out_dir.strip() else None
        run_ids = list(args.run_id or [])
        stats = export_tuned_workflows(
            exp_dir=exp_dir,
            workflow_template_path=wf_path,
            out_dir=out_dir,
            run_ids=run_ids if run_ids else None,
            include_duration=bool(args.include_duration),
            overwrite=bool(args.overwrite),
            limit=int(args.limit),
            indent=int(args.indent),
        )
        resolved_out_dir = out_dir or (exp_dir / "tuned_workflows")
        print(json.dumps({"exp_dir": str(exp_dir), "stats": stats, "out_dir": str(resolved_out_dir)}, indent=2))
        return 0

    raise SystemExit("unknown subcommand")


if __name__ == "__main__":
    raise SystemExit(main())

