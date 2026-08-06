#!/usr/bin/env python3
"""
Queue a single shape-factory combo (generate job + submit to ComfyUI).

Used by POST /api/shape-factory/queue in experiments_ui_server.py.
"""

from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from shape_factory import (
    DEFAULT_DATA_ROOT,
    assert_workflows_not_quarantined,
    ffprobe_video_info,
    generate_job_for_picks,
    load_effective_quarantine_registry,
    load_yaml,
    requires_by_slot,
    submit_job_file,
)
from shape_factory_map import (
    _combo_key_from_slot_paths,
    normalize_combo_key,
    resolve_existing_path,
    resolve_shape_factory_data_root,
)
from shape_factory_prompt_recover import resolve_or_recover_prompt_profile_binding

# mxSlider node ids shared by FB9 GEX2 / GEX_FACIAL graphs
# (same ids documented in .data/shapes/dev-fast.yaml — do not load that profile here).
_ADHOC_PARAM_NODES = {
    "frames": "84",
    "steps": "82",
    "overlap": "387",
}


def _norm_media_path(path: str) -> str:
    return str(path or "").strip().replace("\\", "/").rstrip("/")


def _video_source_slot(shape: Dict[str, Any], bindings: Dict[str, str]) -> Optional[str]:
    """Prefer the shape's primary video require slot; fall back to binding name hints."""
    reqs = shape.get("requires") if isinstance(shape.get("requires"), list) else []
    for req in reqs:
        if not isinstance(req, dict):
            continue
        slot = str(req.get("slot") or "").strip()
        if not slot:
            continue
        media = str(req.get("media") or "").strip().lower()
        if media == "video" or "video" in slot.lower():
            return slot
    for slot in bindings:
        if "video" in str(slot).lower():
            return str(slot)
    return None


def _parent_frame_count(job: Optional[Dict[str, Any]]) -> Optional[int]:
    """Best-effort generation length from a prior job's captured workload / probes.

    For extend retries, prefer ``construction.frames_before`` (the parent clip length)
    over ``timings.workload.frames`` (often the *target* length of a failed extend).
    """
    if not isinstance(job, dict):
        return None
    construction = job.get("construction") if isinstance(job.get("construction"), dict) else {}
    if str(construction.get("derive_action") or construction.get("step") or "").strip().lower() == "extend":
        before = construction.get("frames_before")
        if isinstance(before, (int, float)) and int(before) > 0:
            return int(before)
    timings = job.get("timings") if isinstance(job.get("timings"), dict) else {}
    workload = timings.get("workload") if isinstance(timings.get("workload"), dict) else {}
    for key in ("frames", "output_frame_count"):
        raw = workload.get(key)
        if isinstance(raw, (int, float)) and int(raw) > 0:
            return int(raw)
    outputs = timings.get("outputs") if isinstance(timings.get("outputs"), dict) else {}
    probes = outputs.get("probes") if isinstance(outputs.get("probes"), list) else []
    for probe_row in probes:
        if not isinstance(probe_row, dict):
            continue
        probe = probe_row.get("probe") if isinstance(probe_row.get("probe"), dict) else {}
        fc = probe.get("frame_count")
        if isinstance(fc, (int, float)) and int(fc) > 0:
            return int(fc)
    # Last resort: construction frames_before even when step isn't stamped extend.
    before = construction.get("frames_before")
    if isinstance(before, (int, float)) and int(before) > 0:
        return int(before)
    return None


def _extend_source_path(
    job: Optional[Dict[str, Any]],
    *,
    output_abs: str,
    body: Dict[str, Any],
    bindings: Dict[str, str],
) -> str:
    """Resolve the clip to chain from when extending (including failed-extend retries)."""
    cand = str(output_abs or "").strip()
    if cand:
        return cand
    cand = str(body.get("output_path") or "").strip()
    if cand:
        return cand
    if isinstance(job, dict):
        cand = str(job.get("parent_output") or "").strip()
        if cand:
            return cand
        construction = job.get("construction") if isinstance(job.get("construction"), dict) else {}
        cand = str(construction.get("parent_output") or "").strip()
        if cand:
            return cand
    for slot in ("source_video", "source_video_ref", "video"):
        cand = str(bindings.get(slot) or "").strip()
        if cand:
            return cand
    return ""


def _extend_length_parameters(
    job: Optional[Dict[str, Any]],
    *,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Parameter overrides for an Extend pass.

    Extend chains the prior clip into the video source slot. The Frames slider is
    this pass's generation budget — FB9 templates are tuned for ~80 (sometimes 88),
    already near the VRAM ceiling. Reusing that same budget is a normal extend
    chunk, not a "zero-length" no-op. Do **not** inflate Frames to parent+extra
    (the old ``SHAPE_FACTORY_EXTEND_EXTRA_FRAMES`` bump); leave Frames unset so the
    shape template applies unless the caller set an explicit ``frames`` (UI / OOM
    soft-retry).
    """
    params: Dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    # Explicit frames from the caller win; otherwise do not patch Frames.
    # Never invent frame_load_cap from a frames budget — VHS load window is
    # independent (trim UI / template uncapped load).
    overlap = None
    if isinstance(job, dict):
        timings = job.get("timings") if isinstance(job.get("timings"), dict) else {}
        workload = timings.get("workload") if isinstance(timings.get("workload"), dict) else {}
        if isinstance(workload.get("overlap"), (int, float)):
            overlap = int(workload["overlap"])
    if overlap is not None and params.get("overlap") in (None, ""):
        params["overlap"] = overlap
    params["output_prefix_suffix"] = params.get("output_prefix_suffix") or "_extend"
    return params


def _comfy_data_root(*, workspace_root: Path, output_root: Optional[Path] = None) -> Path:
    """Comfy-facing bind root (input/ + output/ siblings).

    Prefer the live output bind (``output_root`` / ``COMFYUI_BIND_OUTPUT_DIR`` /
    ``DEFAULT_DATA_ROOT``) over an empty repo ``workspace/output`` trap directory.
    """
    candidates: list[Path] = []

    def add(p: Optional[Path]) -> None:
        if p is None:
            return
        resolved = p.expanduser().resolve()
        if resolved.name == "output":
            candidates.append(resolved.parent)
        elif (resolved / "output").is_dir():
            candidates.append(resolved)
        elif resolved.is_dir():
            candidates.append(resolved)

    add(output_root)
    env_out = os.environ.get("COMFYUI_BIND_OUTPUT_DIR", "").strip()
    if env_out:
        add(Path(env_out))
    add(DEFAULT_DATA_ROOT)
    add(workspace_root)

    seen: set[str] = set()
    for root in candidates:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        out = root / "output"
        if not out.is_dir():
            continue
        # Skip empty/trap workspace/output when a richer bind exists later in the list
        # only if this is the workspace root and DEFAULT_DATA_ROOT also qualifies.
        if root == workspace_root.expanduser().resolve():
            default = DEFAULT_DATA_ROOT.expanduser().resolve()
            if default != root and (default / "output").is_dir():
                continue
        return root

    ws = workspace_root.expanduser().resolve()
    if (ws / "output").is_dir():
        return ws
    return DEFAULT_DATA_ROOT.expanduser().resolve()


def _resolve_shape_path(shape_path: Path, *, data_root: Path, family_slug: str) -> Path:
    if shape_path.is_file():
        return shape_path.resolve()
    fallback = data_root / "shapes" / f"{family_slug}.shape.yaml"
    if fallback.is_file():
        return fallback.resolve()
    raise FileNotFoundError(f"shape not found for family {family_slug!r}")


def _safe_prompt_profile_path(data_root: Path, raw: str, *, workspace_root: Path, output_root: Path) -> Path:
    """Resolve a prompt profile JSON path under data_root/pools/.../prompts/."""
    if not raw.strip():
        raise ValueError("prompt profile path is empty")
    resolved = resolve_existing_path(
        raw.strip(),
        output_root=output_root,
        data_root=data_root,
        workspace_root=workspace_root,
    )
    norm = str(resolved).replace("\\", "/")
    data_base = str(data_root.resolve()).replace("\\", "/").rstrip("/")
    if not norm.startswith(data_base + "/"):
        raise ValueError("prompt profile path must be under shape-factory data root")
    if "/prompts/" not in norm:
        raise ValueError("prompt profile path must be under a prompts/ directory")
    if resolved.suffix.lower() != ".json":
        raise ValueError("prompt profile must be a .json file")
    return resolved


def read_prompt_profile(
    *,
    path: str,
    data_root: Path,
    workspace_root: Path,
    output_root: Path,
) -> Dict[str, Any]:
    profile_path = _safe_prompt_profile_path(
        data_root,
        path,
        workspace_root=workspace_root,
        output_root=output_root,
    )
    obj = json.loads(profile_path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"prompt profile is not a JSON object: {profile_path}")
    return {
        "ok": True,
        "path": str(profile_path),
        "basename": profile_path.name,
        "label": obj.get("label"),
        "positive": obj.get("positive"),
        "negative": obj.get("negative"),
        "profile": obj,
    }


def _merge_prompt_profile(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key in ("label", "positive", "negative"):
        if key in override and override[key] is not None:
            out[key] = override[key]
    return out


def write_scratch_prompt_profile(
    data_root: Path,
    *,
    family: str,
    base: dict[str, Any],
    override: dict[str, Any],
    source_path: Path,
) -> Path:
    merged = _merge_prompt_profile(base, override)
    scratch_dir = data_root / "shape_factory" / "jobs" / "_scratch" / family
    scratch_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    stem = slugify_stem(source_path.stem)
    out = scratch_dir / f"{stem}__draft_{ts}.json"
    out.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def slugify_stem(value: str) -> str:
    import re

    out = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_")
    return (out or "prompt")[:80]


def build_adhoc_dev_tuning(parameters: Dict[str, Any], *, data_root: Path) -> Optional[Dict[str, Any]]:
    """
    Map UI parameter knobs onto a sparse dev-tuning patch.

    Only keys present in ``parameters`` are patched (frames/steps/overlap and/or
    VHS skip_first_frames / frame_load_cap). Unmentioned knobs are left alone so
    the shape template / production graph keeps its defaults — do **not** inherit
    ``dev-fast.yaml`` (that profile is opt-in via ``--dev`` only).

    ``data_root`` is accepted for call-site compatibility; the patch is built from
    ``parameters`` alone.
    """
    if not isinstance(parameters, dict) or not parameters:
        return None

    _ = data_root  # reserved; patch is parameter-sparse by design
    tuning: Dict[str, Any] = {"ui_nodes": {}, "api_nodes": {}}
    ui_nodes = tuning["ui_nodes"]
    api_nodes = tuning["api_nodes"]

    touched = False
    for param_key, node_id in _ADHOC_PARAM_NODES.items():
        raw = parameters.get(param_key)
        if raw is None or raw == "":
            continue
        val = int(raw)
        ui_nodes[int(node_id)] = {
            "type": "mxSlider",
            "widgets_values": [val, val, 0],
        }
        api_nodes[str(node_id)] = {"inputs": {"Xi": val, "Xf": val}}
        touched = True

    vhs_patch: Dict[str, Any] = {}
    frame_cap = parameters.get("frame_load_cap")
    if frame_cap is not None and frame_cap != "":
        vhs_patch["frame_load_cap"] = int(frame_cap)
    skip_first = parameters.get("skip_first_frames")
    if skip_first is not None and skip_first != "":
        vhs_patch["skip_first_frames"] = int(skip_first)
    if vhs_patch:
        tuning["vhs_load_video_path"] = vhs_patch
        touched = True

    if not touched:
        return None

    tuning["profile_id"] = "adhoc-ui"
    tuning["output_prefix_suffix"] = str(parameters.get("output_prefix_suffix") or "_adhoc")
    return tuning


def clamp_vhs_load_window(
    *,
    skip_first_frames: int,
    frame_load_cap: int,
    frame_count: int,
) -> tuple[int, int, bool]:
    """
    Clamp VHS skip/cap into ``[0, frame_count)`` so the loader never gets an empty window.

    Returns ``(skip, cap, clamped)`` where ``cap==0`` means uncapped (load remainder).
    """
    fc = max(0, int(frame_count))
    req_skip = max(0, int(skip_first_frames))
    req_cap = max(0, int(frame_load_cap))
    if fc <= 0:
        return 0, 0, (req_skip != 0 or req_cap != 0)
    skip = min(req_skip, max(0, fc - 1))
    remaining = fc - skip
    if req_cap <= 0:
        cap = 0
    else:
        cap = min(req_cap, remaining)
        if cap <= 0:
            # Collapse to uncapped remainder rather than an empty load.
            cap = 0
            skip = min(skip, max(0, fc - 1))
    clamped = skip != req_skip or cap != req_cap
    return skip, cap, clamped


def parse_avg_frame_rate(raw: Any, *, default: float = 18.0) -> float:
    """Parse ffprobe-style ``avg_frame_rate`` (``18/1``) or a plain number into fps."""
    if isinstance(raw, (int, float)) and float(raw) > 0:
        return float(raw)
    text = str(raw or "").strip()
    if not text:
        return float(default)
    if "/" in text:
        num_s, den_s = text.split("/", 1)
        try:
            num = float(num_s)
            den = float(den_s)
            if den != 0 and num / den > 0:
                return num / den
        except ValueError:
            return float(default)
        return float(default)
    try:
        val = float(text)
        return val if val > 0 else float(default)
    except ValueError:
        return float(default)


def trim_seconds_to_vhs_window(
    *,
    mark_in: Optional[float],
    mark_out: Optional[float],
    duration_s: float,
    fps: float,
    frame_count: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Convert playback trim marks (seconds) into VHS skip/cap, clamping to the clip.

    ``frame_load_cap==0`` means load through end of file after skip.
    """
    fps_f = float(fps) if float(fps) > 0 else 18.0
    dur = float(duration_s) if float(duration_s) > 0 else 0.0
    if frame_count is None or int(frame_count) <= 0:
        fc = max(1, int(round(dur * fps_f))) if dur > 0 else 0
    else:
        fc = int(frame_count)
    raw_in = max(0.0, float(mark_in if mark_in is not None else 0.0))
    raw_out = float(mark_out) if mark_out is not None else (dur if dur > 0 else raw_in)
    if dur > 0:
        raw_out = min(dur, raw_out)
    if raw_out < raw_in:
        raw_out = raw_in
    req_skip = max(0, int(round(raw_in * fps_f)))
    if mark_out is None or (dur > 0 and raw_out >= dur - 1e-3):
        req_cap = 0
    else:
        req_cap = max(0, int(round((raw_out - raw_in) * fps_f)))
    skip, cap, clamped = clamp_vhs_load_window(
        skip_first_frames=req_skip,
        frame_load_cap=req_cap,
        frame_count=fc,
    )
    return {
        "skip_first_frames": skip,
        "frame_load_cap": cap,
        "frame_count": fc,
        "fps": fps_f,
        "requested_skip_first_frames": req_skip,
        "requested_frame_load_cap": req_cap,
        "clamped": clamped,
    }


WORK_PRODUCTS_TRIM_CONTEXT = "work-products"


def read_vhs_loader_defaults_from_template(
    template_path: Path,
    *,
    node_id: Optional[int] = None,
) -> Dict[str, int]:
    """Read ``skip_first_frames`` / ``frame_load_cap`` from a LiteGraph VHS_LoadVideoPath node."""
    out = {"skip_first_frames": 0, "frame_load_cap": 0}
    try:
        doc = json.loads(Path(template_path).read_text(encoding="utf-8"))
    except Exception:
        return out
    nodes = doc.get("nodes") if isinstance(doc, dict) else None
    if not isinstance(nodes, list):
        return out
    chosen: Optional[dict[str, Any]] = None
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "VHS_LoadVideoPath":
            continue
        if node_id is not None and int(node.get("id") or -1) != int(node_id):
            continue
        chosen = node
        break
    if chosen is None:
        for node in nodes:
            if isinstance(node, dict) and node.get("type") == "VHS_LoadVideoPath":
                chosen = node
                break
    if not isinstance(chosen, dict):
        return out
    widgets = chosen.get("widgets_values")
    if not isinstance(widgets, dict):
        return out
    try:
        if widgets.get("skip_first_frames") is not None:
            out["skip_first_frames"] = max(0, int(widgets["skip_first_frames"]))
    except (TypeError, ValueError):
        pass
    try:
        if widgets.get("frame_load_cap") is not None:
            out["frame_load_cap"] = max(0, int(widgets["frame_load_cap"]))
    except (TypeError, ValueError):
        pass
    return out


def vhs_loader_defaults_for_shape(
    shape: Dict[str, Any],
    *,
    data_root: Path,
    workspace_root: Path,
    output_root: Path,
) -> Dict[str, int]:
    """Resolve shape template + video-slot binding node → VHS skip/cap defaults."""
    node_id: Optional[int] = None
    for req in shape.get("requires") or []:
        if not isinstance(req, dict):
            continue
        binding = req.get("binding") if isinstance(req.get("binding"), dict) else {}
        if str(binding.get("type") or "") != "vhs_load_video_path":
            continue
        try:
            node_id = int(binding.get("node_id"))
        except (TypeError, ValueError):
            node_id = None
        break
    template_raw = str(shape.get("template") or "").strip()
    if not template_raw:
        return {"skip_first_frames": 0, "frame_load_cap": 0}
    try:
        template_path = resolve_existing_path(
            template_raw,
            output_root=output_root,
            data_root=data_root,
            workspace_root=workspace_root,
        )
    except Exception:
        return {"skip_first_frames": 0, "frame_load_cap": 0}
    return read_vhs_loader_defaults_from_template(template_path, node_id=node_id)


def _load_work_products_trim_seconds(media_abs: Path) -> Optional[Tuple[float, float]]:
    sidecar = media_abs.with_suffix(".trims.json")
    if not sidecar.is_file():
        return None
    try:
        doc = json.loads(sidecar.read_text(encoding="utf-8"))
    except Exception:
        return None
    contexts = doc.get("contexts") if isinstance(doc, dict) else None
    if not isinstance(contexts, dict):
        return None
    ctx = contexts.get(WORK_PRODUCTS_TRIM_CONTEXT)
    if not isinstance(ctx, dict):
        return None
    presets = ctx.get("presets") if isinstance(ctx.get("presets"), list) else []
    active_id = str(ctx.get("active_preset_id") or "").strip()
    chosen: Optional[dict[str, Any]] = None
    for row in presets:
        if not isinstance(row, dict):
            continue
        if active_id and str(row.get("id") or "") == active_id:
            chosen = row
            break
        if chosen is None:
            chosen = row
    if not isinstance(chosen, dict):
        return None
    try:
        tin = float(chosen["in"])
        tout = float(chosen["out"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (tin >= 0 and tout > tin):
        return None
    return tin, tout


def _probe_media_frame_meta(media_abs: Path) -> Dict[str, Any]:
    info = ffprobe_video_info(media_abs) if media_abs.is_file() else {}
    fps = parse_avg_frame_rate(info.get("avg_frame_rate"), default=18.0)
    fc = info.get("frame_count")
    try:
        frame_count = int(fc) if fc is not None else 0
    except (TypeError, ValueError):
        frame_count = 0
    duration = 0.0
    try:
        if info.get("duration") is not None:
            duration = float(info["duration"])
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0 and frame_count > 0 and fps > 0:
        duration = frame_count / fps
    if frame_count <= 0 and duration > 0 and fps > 0:
        frame_count = max(1, int(round(duration * fps)))
    return {"fps": fps, "frame_count": frame_count, "duration": duration, "probe": info}


def resolve_vhs_window_overrides(
    *,
    parameters: Optional[Dict[str, Any]],
    media_abs: Optional[Path],
    template_defaults: Optional[Dict[str, int]] = None,
    read_sidecar: bool = True,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """
    Build skip/cap parameter patch + optional ``trim_clamped`` metadata.

    Prefer explicit skip/cap parameters, else work-products trim sidecar, else
    template defaults when those are out of range for the media (policy 2).

    Only writes ``skip_first_frames`` / ``frame_load_cap`` when those keys were
    explicitly provided or introduced by sidecar / template clamp — so an
    extend-only ``frame_load_cap`` lengthen value does not zero out template skip.
    """
    params_in = dict(parameters) if isinstance(parameters, dict) else {}
    out_params = dict(params_in)
    meta: Optional[Dict[str, Any]] = None
    media_meta = (
        _probe_media_frame_meta(media_abs)
        if media_abs is not None
        else {"fps": 18.0, "frame_count": 0, "duration": 0.0}
    )
    fps = float(media_meta["fps"])
    frame_count = int(media_meta["frame_count"] or 0)
    duration = float(media_meta["duration"] or 0.0)

    explicit_skip = params_in.get("skip_first_frames") not in (None, "")
    explicit_cap = params_in.get("frame_load_cap") not in (None, "")
    # Trim intent: both skip and cap from UI/sidecar, or skip alone.
    # Extend lengthen often sets frame_load_cap alone — that is not a trim window.
    trim_intent = explicit_skip or (explicit_skip and explicit_cap)
    source: Optional[str] = "overrides" if explicit_skip else None

    if source is None and read_sidecar and media_abs is not None:
        marks = _load_work_products_trim_seconds(media_abs)
        if marks is not None:
            win = trim_seconds_to_vhs_window(
                mark_in=marks[0],
                mark_out=marks[1],
                duration_s=duration,
                fps=fps,
                frame_count=frame_count or None,
            )
            out_params["skip_first_frames"] = int(win["skip_first_frames"])
            out_params["frame_load_cap"] = int(win["frame_load_cap"])
            explicit_skip = True
            explicit_cap = True
            trim_intent = True
            source = "sidecar"
            if win.get("clamped"):
                meta = {
                    "source": source,
                    "requested_skip_first_frames": win["requested_skip_first_frames"],
                    "requested_frame_load_cap": win["requested_frame_load_cap"],
                    "skip_first_frames": win["skip_first_frames"],
                    "frame_load_cap": win["frame_load_cap"],
                    "frame_count": win["frame_count"],
                    "message": (
                        f"trim skip {win['requested_skip_first_frames']} → {win['skip_first_frames']}"
                        f" for this clip ({win['frame_count']} frames)"
                    ),
                }

    if source is None and template_defaults and frame_count > 0:
        req_skip = int(template_defaults.get("skip_first_frames") or 0)
        req_cap = int(template_defaults.get("frame_load_cap") or 0)
        skip, cap, clamped = clamp_vhs_load_window(
            skip_first_frames=req_skip,
            frame_load_cap=req_cap,
            frame_count=frame_count,
        )
        if clamped:
            out_params["skip_first_frames"] = skip
            out_params["frame_load_cap"] = cap
            explicit_skip = True
            explicit_cap = True
            trim_intent = True
            source = "template_clamped"
            meta = {
                "source": source,
                "requested_skip_first_frames": req_skip,
                "requested_frame_load_cap": req_cap,
                "skip_first_frames": skip,
                "frame_load_cap": cap,
                "frame_count": frame_count,
                "message": f"template skip {req_skip} → {skip} for this clip ({frame_count} frames)",
            }

    if trim_intent and frame_count > 0:
        try:
            req_skip = int(out_params.get("skip_first_frames") or 0)
        except (TypeError, ValueError):
            req_skip = 0
        try:
            req_cap = (
                int(out_params["frame_load_cap"])
                if out_params.get("frame_load_cap") not in (None, "")
                else 0
            )
        except (TypeError, ValueError):
            req_cap = 0
        skip, cap, clamped = clamp_vhs_load_window(
            skip_first_frames=req_skip,
            frame_load_cap=req_cap,
            frame_count=frame_count,
        )
        out_params["skip_first_frames"] = skip
        if explicit_cap:
            out_params["frame_load_cap"] = cap
        if clamped and meta is None:
            meta = {
                "source": source or "overrides",
                "requested_skip_first_frames": req_skip,
                "requested_frame_load_cap": req_cap,
                "skip_first_frames": skip,
                "frame_load_cap": cap if explicit_cap else req_cap,
                "frame_count": frame_count,
                "message": f"skip {req_skip} → {skip} for this clip ({frame_count} frames)",
            }
    elif explicit_cap and frame_count > 0 and not explicit_skip:
        # Lengthen-only cap: clamp to media length without inventing skip=0.
        try:
            req_cap = int(out_params["frame_load_cap"])
        except (TypeError, ValueError):
            req_cap = 0
        if req_cap > frame_count:
            out_params["frame_load_cap"] = frame_count
            meta = {
                "source": "overrides",
                "requested_frame_load_cap": req_cap,
                "frame_load_cap": frame_count,
                "frame_count": frame_count,
                "message": f"frame_load_cap {req_cap} → {frame_count} for this clip",
            }

    return out_params, meta


def _parse_overrides(body: Dict[str, Any]) -> Dict[str, Any]:
    overrides = body.get("overrides")
    return overrides if isinstance(overrides, dict) else {}


def _apply_binding_overrides(
    picks: Dict[str, Path],
    overrides: Dict[str, Any],
    *,
    data_root: Path,
    family: str,
    workspace_root: Path,
    output_root: Path,
) -> tuple[Dict[str, Path], Dict[str, Any]]:
    """Return updated picks and adhoc metadata for prompt/parameter overrides."""
    adhoc: Dict[str, Any] = {}
    prompt_override = overrides.get("prompt_profile")
    if isinstance(prompt_override, dict) and "prompt_profile" in picks:
        source_path = picks["prompt_profile"]
        base = json.loads(source_path.read_text(encoding="utf-8"))
        if not isinstance(base, dict):
            raise ValueError(f"prompt profile is not a JSON object: {source_path}")
        scratch = write_scratch_prompt_profile(
            data_root,
            family=family,
            base=base,
            override=prompt_override,
            source_path=source_path,
        )
        picks = dict(picks)
        picks["prompt_profile"] = scratch
        adhoc["prompt_profile"] = {
            "source_path": str(source_path),
            "scratch_path": str(scratch),
            "override": prompt_override,
        }

    parameters = overrides.get("parameters")
    if isinstance(parameters, dict) and parameters:
        adhoc["parameters"] = parameters

    return picks, adhoc


def queue_shape_factory_combo(
    *,
    family_slug: str,
    bindings: Dict[str, str],
    combo_key: Optional[str] = None,
    data_root: Path,
    workspace_root: Path,
    output_root: Path,
    comfy_server: str,
    front: bool = False,
    dry_run: bool = False,
    dev: bool = False,
    force: bool = False,
    overrides: Optional[Dict[str, Any]] = None,
    pick_mode: str = "product",
    parent_output: Optional[str] = None,
    construction: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Generate + submit one explicit slot binding combo.

    ``bindings`` maps slot name -> absolute filesystem path (as returned by the map API).
    ``overrides`` may include ``prompt_profile`` (inline edits) and ``parameters`` (frames/steps/…).
    """
    family = str(family_slug or "").strip()
    if not family:
        raise ValueError("family_slug is required")

    if not isinstance(bindings, dict) or not bindings:
        raise ValueError("bindings must be a non-empty object")

    data_root = data_root.expanduser().resolve()
    workspace_root = workspace_root.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    comfy_data_root = _comfy_data_root(workspace_root=workspace_root, output_root=output_root)
    overrides = overrides if isinstance(overrides, dict) else {}

    shape_path = _resolve_shape_path(data_root / "shapes" / f"{family}.shape.yaml", data_root=data_root, family_slug=family)
    pools_path = data_root / "pools" / family / "pools.yaml"
    if not pools_path.is_file():
        raise FileNotFoundError(f"pools.yaml not found: {pools_path}")

    shape = load_yaml(shape_path)
    req_by_slot = requires_by_slot(shape)

    picks: Dict[str, Path] = {}
    slot_paths: Dict[str, str] = {}
    for slot, raw_path in bindings.items():
        slot_name = str(slot or "").strip()
        path_str = str(raw_path or "").strip()
        if not slot_name or not path_str:
            continue
        resolved = resolve_existing_path(
            path_str,
            output_root=output_root,
            data_root=data_root,
            workspace_root=workspace_root,
        )
        picks[slot_name] = resolved
        slot_paths[slot_name] = str(resolved)

    missing = [s for s, req in req_by_slot.items() if s not in picks and not req.get("optional")]
    if missing:
        raise ValueError(f"missing required bindings: {missing}")

    computed_combo = _combo_key_from_slot_paths(slot_paths)
    if (
        combo_key
        and str(combo_key).strip()
        and normalize_combo_key(combo_key) != normalize_combo_key(computed_combo)
    ):
        raise ValueError(f"combo_key mismatch (expected {computed_combo!r}, got {combo_key!r})")

    picks, adhoc_meta = _apply_binding_overrides(
        picks,
        overrides,
        data_root=data_root,
        family=family,
        workspace_root=workspace_root,
        output_root=output_root,
    )

    template_path = resolve_existing_path(
        str(shape.get("template") or ""),
        output_root=output_root,
        data_root=data_root,
        workspace_root=workspace_root,
    )

    quarantine_path = data_root / "shape_factory" / "quarantine.json"
    registry, _effective = load_effective_quarantine_registry(
        data_root=data_root,
        quarantine_path=quarantine_path,
    )
    assert_workflows_not_quarantined(registry, [template_path], ignore=False)

    resolved_shape_path = resolve_existing_path(
        str(shape_path),
        output_root=output_root,
        data_root=data_root,
        workspace_root=workspace_root,
    )

    job_dir = data_root / "shape_factory" / "jobs"
    workflow_dir = workspace_root / "comfyui_user" / "default" / "workflows" / "generated" / "shape_factory"
    job_suffix = f"_ui{int(time.time())}"

    dev_tuning_override = None
    params = overrides.get("parameters") if isinstance(overrides.get("parameters"), dict) else {}
    if params:
        dev_tuning_override = build_adhoc_dev_tuning(params, data_root=data_root)

    mode = str(pick_mode or "product").strip() or "product"
    gen = generate_job_for_picks(
        picks=picks,
        shape=shape,
        shape_path=resolved_shape_path,
        pools_path=pools_path,
        template_path=template_path,
        data_root=comfy_data_root,
        workflow_dir=workflow_dir,
        job_dir=job_dir,
        pick_index=0,
        pick_mode=mode,
        job_suffix=job_suffix,
        dev=dev,
        dev_tuning_override=dev_tuning_override,
        adhoc_overrides=adhoc_meta or None,
        parent_output=parent_output,
        construction=construction,
    )

    submit: Dict[str, Any]
    if dry_run:
        submit = submit_job_file(
            gen["job_path"],
            server=str(comfy_server).rstrip("/"),
            data_root=comfy_data_root,
            dry_run=True,
            quarantine_path=quarantine_path,
        )
    else:
        submit = submit_job_file(
            gen["job_path"],
            server=str(comfy_server).rstrip("/"),
            data_root=comfy_data_root,
            quarantine_path=quarantine_path,
            front=front,
            force=force,
            client_id="factory-map-ui",
        )

    return {
        "ok": True,
        "family_slug": family,
        "combo_key": computed_combo,
        "job_key": gen["job_key"],
        "job_path": str(gen["job_path"]),
        "workflow_path": str(gen["workflow_path"]),
        "prompt_id": submit.get("prompt_id"),
        "dry_run": bool(dry_run),
        "skipped": bool(submit.get("skipped")),
        "overrides_applied": adhoc_meta or None,
        "pick_mode": mode,
        "parent_output": parent_output,
        "submit": submit,
    }


def queue_from_request_body(
    body: Dict[str, Any],
    *,
    repo_root: Path,
    workspace_root: Path,
    output_root: Path,
    comfy_server: str,
) -> Dict[str, Any]:
    family_slug = str(body.get("family_slug") or body.get("family") or "").strip()
    combo_key = body.get("combo_key")
    if combo_key is not None:
        combo_key = str(combo_key).strip() or None

    bindings_raw = body.get("bindings")
    bindings: Dict[str, str] = {}
    if isinstance(bindings_raw, dict):
        for slot, spec in bindings_raw.items():
            if isinstance(spec, str):
                bindings[str(slot)] = spec
            elif isinstance(spec, dict):
                path = str(spec.get("path") or "").strip()
                if path:
                    bindings[str(slot)] = path

    data_root = resolve_shape_factory_data_root(repo_root=repo_root)
    return queue_shape_factory_combo(
        family_slug=family_slug,
        bindings=bindings,
        combo_key=combo_key,
        data_root=data_root,
        workspace_root=workspace_root,
        output_root=output_root,
        comfy_server=comfy_server,
        front=bool(body.get("front") or False),
        dry_run=bool(body.get("dry_run") or False),
        dev=bool(body.get("dev") or False),
        force=bool(body.get("force") or False),
        overrides=_parse_overrides(body),
    )


def _find_job_doc(data_root: Path, job_key: str) -> Optional[Tuple[Dict[str, Any], Path]]:
    jobs_root = data_root / "shape_factory" / "jobs"
    if not jobs_root.is_dir():
        return None
    for p in jobs_root.glob(f"**/{job_key}.job.json"):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(doc, dict):
            return doc, p
    return None


def replay_from_request_body(
    body: Dict[str, Any],
    *,
    repo_root: Path,
    workspace_root: Path,
    output_root: Path,
    comfy_server: str,
) -> Dict[str, Any]:
    """
    Re-run a prior job (by ``job_key``) or an explicit binding set.

    ``extend=true`` chains the job's output into a video source slot (v2v/i2v-from-video).
    Frames stays on the shape template (~80/88) unless ``overrides.parameters.frames``
    is set — that budget is one extend chunk, not parent-length + extra.
    """
    data_root = resolve_shape_factory_data_root(repo_root=repo_root)
    job_key = str(body.get("job_key") or "").strip()
    family_slug = str(body.get("family_slug") or body.get("family") or "").strip()
    bindings: Dict[str, str] = {}
    output_abs = ""

    if job_key:
        found = _find_job_doc(data_root, job_key)
        if not found:
            raise ValueError(f"job not found: {job_key}")
        job, _job_path = found
        family_slug = family_slug or str(job.get("family_slug") or "").strip()
        job_bindings = job.get("bindings") if isinstance(job.get("bindings"), dict) else {}
        for slot, spec in job_bindings.items():
            if isinstance(spec, dict):
                path = str(spec.get("path") or "").strip()
                if path:
                    bindings[str(slot)] = path
        submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
        sub_outs = submit.get("outputs") if isinstance(submit.get("outputs"), list) else []
        job_outs = job.get("outputs") if isinstance(job.get("outputs"), list) else []
        dep = job.get("deposit") if isinstance(job.get("deposit"), dict) else {}
        dep_vids = dep.get("videos") if isinstance(dep.get("videos"), list) else []
        output_abs = str(
            (sub_outs[0] if sub_outs else (job_outs[0] if job_outs else (dep_vids[-1] if dep_vids else "")))
            or ""
        )
        # Failed/interrupted extends have no outputs — fall back to parent clip / body.
        if not output_abs and bool(body.get("extend")):
            output_abs = _extend_source_path(job, output_abs="", body=body, bindings=bindings)
    else:
        job = None
        raw = body.get("bindings")
        if isinstance(raw, dict):
            for slot, spec in raw.items():
                if isinstance(spec, str) and spec.strip():
                    bindings[str(slot)] = spec.strip()
                elif isinstance(spec, dict):
                    path = str(spec.get("path") or "").strip()
                    if path:
                        bindings[str(slot)] = path
        output_abs = str(body.get("output_path") or "").strip()

    if not family_slug:
        raise ValueError("family_slug is required")
    if not bindings:
        raise ValueError("no bindings to replay")

    shape_path = _resolve_shape_path(
        data_root / "shapes" / f"{family_slug}.shape.yaml",
        data_root=data_root,
        family_slug=family_slug,
    )
    shape = load_yaml(shape_path)
    recovered_prompt: Optional[str] = None
    if "prompt_profile" in bindings or any(
        isinstance(r, dict) and str((r.get("binding") or {}).get("type") or "") == "prompt_bundle"
        for r in (shape.get("requires") or [])
    ):
        bindings, recovered_prompt = resolve_or_recover_prompt_profile_binding(
            bindings,
            job=job,
            shape=shape,
            data_root=data_root,
            family=family_slug,
        )

    overrides = _parse_overrides(body)
    extend = bool(body.get("extend"))
    pick_mode = "replay"
    parent_output: Optional[str] = None
    construction: Optional[Dict[str, Any]] = None

    if extend:
        if not output_abs:
            output_abs = _extend_source_path(job, output_abs="", body=body, bindings=bindings)
        if not output_abs:
            raise ValueError("extend requires a resolvable output path")
        video_slot = _video_source_slot(shape, bindings)
        if video_slot is None:
            raise ValueError("extend_not_supported: shape has no video source slot")
        prev_source = _norm_media_path(bindings.get(video_slot) or "")
        next_source = _norm_media_path(output_abs)
        bindings[video_slot] = output_abs

        # Chain prior output as source; keep template Frames budget (~80/88).
        params_in = overrides.get("parameters") if isinstance(overrides.get("parameters"), dict) else {}
        length_params = _extend_length_parameters(job, existing=params_in)
        base_frames = _parent_frame_count(job)
        raw_budget = length_params.get("frames")
        if isinstance(raw_budget, (int, float)) and int(raw_budget) > 0:
            budget_frames: Optional[int] = int(raw_budget)
        else:
            # Unset → template Frames applies; stamp parent chunk for observability.
            budget_frames = int(base_frames) if isinstance(base_frames, int) and base_frames > 0 else None
        source_unchanged = bool(prev_source) and prev_source == next_source
        overrides = dict(overrides)
        overrides["parameters"] = length_params
        pick_mode = "extend"
        parent_output = output_abs
        construction = {
            "step": "extend",
            "derive_action": "extend",
            "pick_mode": "extend",
            "parent_output": output_abs,
            "source_slot": video_slot,
            "source_unchanged": source_unchanged,
            # Generation budget for this pass (same as parent chunk is normal).
            "frames_before": base_frames,
            "frames_after": budget_frames,
            "replay_of_job_key": job_key or None,
            "retry_of_failed_extend": bool(
                isinstance(job, dict)
                and str((job.get("submit") or {}).get("status") or "").lower()
                in {"error", "interrupted", "abandoned"}
            ),
        }

    # Resolve VHS input window (explicit overrides → sidecar → OOR template clamp).
    video_slot_for_trim = _video_source_slot(shape, bindings)
    media_for_trim: Optional[Path] = None
    if video_slot_for_trim:
        raw_media = str(bindings.get(video_slot_for_trim) or "").strip()
        if raw_media:
            media_for_trim = Path(raw_media).expanduser()
    template_defaults = vhs_loader_defaults_for_shape(
        shape,
        data_root=data_root,
        workspace_root=workspace_root,
        output_root=output_root,
    )
    params_for_trim = overrides.get("parameters") if isinstance(overrides.get("parameters"), dict) else {}
    resolved_params, trim_clamped = resolve_vhs_window_overrides(
        parameters=params_for_trim,
        media_abs=media_for_trim if media_for_trim and media_for_trim.is_file() else None,
        template_defaults=template_defaults,
        read_sidecar=True,
    )
    if resolved_params != params_for_trim:
        overrides = dict(overrides)
        overrides["parameters"] = resolved_params

    result = queue_shape_factory_combo(
        family_slug=family_slug,
        bindings=bindings,
        combo_key=None,
        data_root=data_root,
        workspace_root=workspace_root,
        output_root=output_root,
        comfy_server=comfy_server,
        front=bool(body.get("front") or False),
        dry_run=bool(body.get("dry_run") or False),
        dev=bool(body.get("dev") or False),
        force=bool(body.get("force") or False),
        overrides=overrides,
        pick_mode=pick_mode,
        parent_output=parent_output,
        construction=construction,
    )
    if isinstance(result, dict):
        result.setdefault("replay_of_job_key", job_key or None)
        result.setdefault("extend", extend)
        if construction:
            result["construction"] = construction
        if recovered_prompt:
            result["prompt_profile_recovered"] = recovered_prompt
        if trim_clamped:
            result["trim_clamped"] = trim_clamped
    return result


def is_oom_error_message(text: Any) -> bool:
    msg = str(text or "").lower()
    return any(
        n in msg
        for n in (
            "out of memory",
            "exceed allowed memory",
            "cuda out of memory",
            "cudaerror_out_of_memory",
        )
    )


def oom_extend_auto_retry_enabled() -> bool:
    raw = os.environ.get("SHAPE_FACTORY_OOM_EXTEND_AUTO_RETRY", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def oom_extend_max_retries() -> int:
    raw = os.environ.get("SHAPE_FACTORY_OOM_EXTEND_MAX_RETRIES", "1").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 1


def _job_is_extend(job: Dict[str, Any]) -> bool:
    if str(job.get("pick_mode") or "").strip().lower() == "extend":
        return True
    construction = job.get("construction") if isinstance(job.get("construction"), dict) else {}
    step = str(construction.get("derive_action") or construction.get("step") or "").strip().lower()
    return step == "extend"


def compute_oom_retry_frame_target(job: Dict[str, Any]) -> Optional[Tuple[int, int, int]]:
    """
    Pick a shorter generation budget after an extend OOM.

    Returns ``(frames_before, new_budget, reduced_by)`` or None when we cannot shrink.
    Halves the failed pass's Frames budget (not parent+extra).
    """
    before = _parent_frame_count(job)
    if before is None or before <= 0:
        before = int(os.environ.get("SHAPE_FACTORY_EXTEND_DEFAULT_FRAMES", "80"))
    construction = job.get("construction") if isinstance(job.get("construction"), dict) else {}
    after_raw = construction.get("frames_after")
    if isinstance(after_raw, (int, float)) and int(after_raw) > 0:
        current = int(after_raw)
    else:
        current = int(before)
    new_budget = max(8, current // 2)
    if new_budget >= current:
        return None
    return int(before), int(new_budget), int(current) - int(new_budget)


def maybe_auto_retry_oom_extend(
    job: Dict[str, Any],
    job_path: Path,
    *,
    repo_root: Path,
    workspace_root: Path,
    output_root: Path,
    comfy_server: str,
    persist: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    If ``job`` just failed with a Comfy OOM on an extend, spawn one shorter extend replay.

    Idempotent: stamps ``submit.oom_auto_retry`` on the failed job so reconcile polls
    do not spawn duplicates. Disabled with ``SHAPE_FACTORY_OOM_EXTEND_AUTO_RETRY=0``.
    """
    if not oom_extend_auto_retry_enabled():
        return None
    submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    if str(submit.get("status") or "").strip().lower() != "error":
        return None
    if not is_oom_error_message(submit.get("error")):
        return None
    if not _job_is_extend(job):
        return None
    if isinstance(submit.get("oom_auto_retry"), dict) and submit["oom_auto_retry"].get("spawned_job_key"):
        return None

    construction = job.get("construction") if isinstance(job.get("construction"), dict) else {}
    prior_tries = 0
    for raw in (submit.get("oom_retries"), construction.get("oom_retries")):
        if isinstance(raw, int) and raw >= 0:
            prior_tries = max(prior_tries, raw)
        elif isinstance(raw, str) and raw.strip().isdigit():
            prior_tries = max(prior_tries, int(raw.strip()))
    if prior_tries >= oom_extend_max_retries():
        return None

    target = compute_oom_retry_frame_target(job)
    if target is None:
        return None
    frames_before, frames_after, extra = target

    job_key = str(job.get("job_key") or job_path.stem.replace(".job", "")).strip()
    if not job_key:
        return None

    body: Dict[str, Any] = {
        "job_key": job_key,
        "extend": True,
        "overrides": {
            "parameters": {
                # Soft budget only — do not couple VHS frame_load_cap to sampler Frames.
                "frames": frames_after,
            }
        },
    }
    family = str(job.get("family_slug") or "").strip()
    if family:
        body["family_slug"] = family

    result = replay_from_request_body(
        body,
        repo_root=repo_root,
        workspace_root=workspace_root,
        output_root=output_root,
        comfy_server=comfy_server,
    )
    if not isinstance(result, dict) or not result.get("ok"):
        return result if isinstance(result, dict) else {"ok": False, "error": "oom_retry_failed"}

    spawned_key = str(result.get("job_key") or "").strip()
    attempt = prior_tries + 1
    submit["oom_retries"] = attempt
    submit["oom_auto_retry"] = {
        "attempt": attempt,
        "spawned_job_key": spawned_key or None,
        "spawned_prompt_id": result.get("prompt_id"),
        "frames_before": frames_before,
        "frames_after": frames_after,
        "extra_frames": extra,  # amount trimmed from the failed budget
        "spawned_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        .isoformat(timespec="seconds"),
        "reason": "comfy_oom",
    }
    construction = dict(construction)
    construction["oom_retries"] = attempt
    job["construction"] = construction
    job["submit"] = submit
    if persist:
        try:
            from shape_factory import atomic_write_json  # type: ignore

            atomic_write_json(job_path, job)
        except Exception:
            pass

    out = dict(result)
    out["oom_auto_retry"] = True
    out["oom_retry_of_job_key"] = job_key
    out["frames_before"] = frames_before
    out["frames_after"] = frames_after
    return out


def prompt_profile_from_request(
    q: Dict[str, Any],
    *,
    repo_root: Path,
    workspace_root: Path,
    output_root: Path,
) -> Dict[str, Any]:
    path = str(q.get("path") or q.get("relpath") or "").strip()
    if not path:
        raise ValueError("path query parameter is required")
    data_root = resolve_shape_factory_data_root(repo_root=repo_root)
    return read_prompt_profile(
        path=path,
        data_root=data_root,
        workspace_root=workspace_root,
        output_root=output_root,
    )
