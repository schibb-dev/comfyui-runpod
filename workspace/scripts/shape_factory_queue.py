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
from typing import Any, Dict, List, Optional, Tuple

from shape_factory import (
    DEFAULT_DATA_ROOT,
    assert_workflows_not_quarantined,
    ffprobe_video_info,
    generate_job_for_picks,
    load_effective_quarantine_registry,
    load_yaml,
    requires_by_slot,
    resolve_pool_members,
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


def _bindings_declared_by_shape(shape: Dict[str, Any], bindings: Dict[str, str]) -> Dict[str, str]:
    """Keep only slots the target shape declares (drop i2v ``source_still`` on v2v extend)."""
    known = set(requires_by_slot(shape))
    if not known:
        return dict(bindings)
    return {k: v for k, v in bindings.items() if k in known}


def _image_source_slots(shape: Dict[str, Any]) -> list[str]:
    """Required (non-optional) image / load_image slots — e.g. identity_anchor, source_still."""
    out: list[str] = []
    reqs = shape.get("requires") if isinstance(shape.get("requires"), list) else []
    for req in reqs:
        if not isinstance(req, dict) or req.get("optional"):
            continue
        slot = str(req.get("slot") or "").strip()
        if not slot:
            continue
        media = str(req.get("media") or "").strip().lower()
        btype = str((req.get("binding") or {}).get("type") or "").strip().lower() if isinstance(req.get("binding"), dict) else ""
        if media == "image" or btype == "load_image" or "still" in slot.lower() or "anchor" in slot.lower():
            if slot not in out:
                out.append(slot)
    return out


def _resolve_still_file(
    raw: str,
    *,
    workspace_root: Path,
    output_root: Path,
    data_root: Path,
) -> Optional[Path]:
    """Resolve a still path or basename to an existing file under known input roots."""
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        return resolve_existing_path(
            s,
            output_root=output_root,
            data_root=data_root,
            workspace_root=workspace_root,
        )
    except FileNotFoundError:
        pass
    bn = Path(s).name
    if not bn:
        return None
    roots = [
        Path("/home/yuji/comfyui-runpod-data/input"),
        Path(workspace_root).expanduser().resolve() / "input",
        Path(data_root).expanduser().resolve() / "input",
        Path(output_root).expanduser().resolve().parent / "input",
    ]
    for root in roots:
        cand = root / bn
        if cand.is_file():
            return cand.resolve()
        # Also accept input/<bn> style under root
        if (root / "input" / bn).is_file():
            return (root / "input" / bn).resolve()
    return None


def _infer_still_from_media(
    media_abs: str,
    *,
    workspace_root: Path,
    output_root: Path,
    data_root: Path,
) -> Optional[Tuple[str, str]]:
    """
    Recover a LoadImage still from an output's embedded prompt.

    Returns ``(abs_path, evidence)`` or None.
    """
    path = Path(str(media_abs or "")).expanduser()
    if not path.is_file():
        try:
            path = resolve_existing_path(
                str(media_abs),
                output_root=output_root,
                data_root=data_root,
                workspace_root=workspace_root,
            )
        except FileNotFoundError:
            return None
    try:
        from shape_factory_seed_sources import infer_source_still, source_still_relpath
    except ImportError:
        return None
    import shutil

    ffprobe = shutil.which("ffprobe")
    info = infer_source_still(path, ffprobe=ffprobe)
    if not info:
        return None
    bn = str(info.get("source_basename") or "").strip()
    rel = source_still_relpath(bn) if bn else ""
    resolved = _resolve_still_file(rel or bn, workspace_root=workspace_root, output_root=output_root, data_root=data_root)
    if resolved is None:
        return None
    evidence = str(info.get("evidence") or "embedded_load_image")
    return str(resolved), evidence


def _collect_identity_media_candidates(
    *,
    job: Optional[Dict[str, Any]],
    bindings: Dict[str, str],
    output_abs: str,
) -> list[str]:
    """Videos to probe for an embedded LoadImage still (nearest first)."""
    out: list[str] = []
    seen: set[str] = set()

    def add(p: str) -> None:
        n = _norm_media_path(p)
        if n and n not in seen:
            seen.add(n)
            out.append(n)

    add(output_abs)
    if isinstance(job, dict):
        add(str(job.get("parent_output") or ""))
        cons = job.get("construction") if isinstance(job.get("construction"), dict) else {}
        add(str(cons.get("parent_output") or ""))
        for slot in ("source_video", "source_video_ref", "video"):
            add(str(bindings.get(slot) or ""))
        # Prior job source before extend rebind is already in bindings when we call
        # this before/after rebind — also check deposit/submit outputs of parent chain.
    for slot in ("source_video", "source_video_ref", "video"):
        add(str(bindings.get(slot) or ""))
    return out


def _resolve_identity_still_for_shape(
    *,
    shape: Dict[str, Any],
    body: Dict[str, Any],
    job: Optional[Dict[str, Any]],
    bindings: Dict[str, str],
    output_abs: str,
    workspace_root: Path,
    output_root: Path,
    data_root: Path,
) -> Tuple[Dict[str, str], Optional[Dict[str, Any]]]:
    """
    Fill required image slots (identity_anchor / source_still) when missing.

    Ladder: explicit body path → existing still bindings (cross-slot) →
    embedded LoadImage on output/parent videos.
    """
    needed = _image_source_slots(shape)
    if not needed:
        return bindings, None

    next_bindings = dict(bindings)
    meta: Dict[str, Any] = {"slots": {}, "evidence": None}

    # Explicit operator overrides (aliases).
    explicit_raw = (
        body.get("identity_anchor")
        or body.get("source_still")
        or body.get("identity_still")
        or ""
    )
    explicit_path = ""
    if isinstance(explicit_raw, str) and explicit_raw.strip():
        explicit_path = explicit_raw.strip()
    elif isinstance(explicit_raw, dict):
        explicit_path = str(explicit_raw.get("path") or "").strip()
    body_bindings = body.get("bindings") if isinstance(body.get("bindings"), dict) else {}
    for alias in ("identity_anchor", "source_still"):
        if explicit_path:
            break
        spec = body_bindings.get(alias)
        if isinstance(spec, str) and spec.strip():
            explicit_path = spec.strip()
        elif isinstance(spec, dict):
            explicit_path = str(spec.get("path") or "").strip()

    resolved_explicit: Optional[Path] = None
    if explicit_path:
        resolved_explicit = _resolve_still_file(
            explicit_path,
            workspace_root=workspace_root,
            output_root=output_root,
            data_root=data_root,
        )
        if resolved_explicit is None:
            raise ValueError(f"identity_still_not_found: {explicit_path}")

    # Prefer any still already on the seed job.
    existing_still = ""
    for alias in ("identity_anchor", "source_still"):
        cand = str(next_bindings.get(alias) or "").strip()
        if cand:
            existing_still = cand
            break

    inferred: Optional[Tuple[str, str]] = None
    if resolved_explicit is None and not existing_still:
        for media in _collect_identity_media_candidates(
            job=job, bindings=next_bindings, output_abs=output_abs
        ):
            inferred = _infer_still_from_media(
                media,
                workspace_root=workspace_root,
                output_root=output_root,
                data_root=data_root,
            )
            if inferred:
                break
        # Walk parent_output one more hop via job_output_index when available.
        if inferred is None and output_abs:
            try:
                from shape_factory_job_output_index import (
                    default_job_output_index_path,
                    lookup_by_relpath,
                    open_job_output_index,
                )

                og_guess = Path(output_root) / "og"
                idx_path = default_job_output_index_path(og_guess if og_guess.is_dir() else Path(output_root))
                if idx_path.is_file():
                    con = open_job_output_index(idx_path)
                    try:
                        # Normalize to og/... relpath when possible
                        rel = str(output_abs).replace("\\", "/")
                        for prefix in (str(Path(output_root).resolve()) + "/", str(output_root) + "/"):
                            if rel.startswith(prefix):
                                rel = rel[len(prefix) :]
                                break
                        if not rel.startswith("og/") and "/og/" in rel:
                            rel = "og/" + rel.split("/og/", 1)[1]
                        row = lookup_by_relpath(con, rel, output_root=Path(output_root))
                        parent = str((row or {}).get("parent_output") or "").strip()
                        if parent:
                            inferred = _infer_still_from_media(
                                parent,
                                workspace_root=workspace_root,
                                output_root=output_root,
                                data_root=data_root,
                            )
                    finally:
                        con.close()
            except Exception:
                pass

    still_path = ""
    evidence = None
    if resolved_explicit is not None:
        still_path = str(resolved_explicit)
        evidence = "body"
    elif existing_still:
        got = _resolve_still_file(
            existing_still,
            workspace_root=workspace_root,
            output_root=output_root,
            data_root=data_root,
        )
        still_path = str(got) if got is not None else existing_still
        evidence = "job_binding"
    elif inferred:
        still_path, evidence = inferred

    for slot in needed:
        if str(next_bindings.get(slot) or "").strip():
            meta["slots"][slot] = {"path": next_bindings[slot], "evidence": "existing"}
            continue
        if not still_path:
            raise ValueError(
                f"missing_identity_still: shape requires {slot!r} but no still was provided "
                f"or recoverable from lineage (pass identity_anchor / source_still)"
            )
        next_bindings[slot] = still_path
        meta["slots"][slot] = {"path": still_path, "evidence": evidence}
    meta["evidence"] = evidence
    meta["path"] = still_path or None
    return next_bindings, meta


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
    VHS skip_first_frames / frame_load_cap, and/or ``seed`` / ``noise_seed``).
    Unmentioned knobs are left alone so the shape template / production graph
    keeps its defaults — do **not** inherit ``dev-fast.yaml`` (that profile is
    opt-in via ``--dev`` only).

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

    seed_raw = parameters.get("seed")
    if seed_raw is None or seed_raw == "":
        seed_raw = parameters.get("noise_seed")
    if seed_raw is not None and seed_raw != "":
        tuning["noise_seed"] = int(seed_raw)
        touched = True

    if not touched:
        return None

    tuning["profile_id"] = "adhoc-ui"
    tuning["output_prefix_suffix"] = str(parameters.get("output_prefix_suffix") or "_adhoc")
    return tuning


def _coerce_int_seed(raw: Any) -> Optional[int]:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return int(raw)
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def extract_job_noise_seed(job: Optional[Dict[str, Any]], job_path: Optional[Path] = None) -> Optional[int]:
    """Best-effort Comfy noise seed from a prior job's prompt / construction."""
    if isinstance(job, dict):
        for key in ("noise_seed", "seed", "used_seed"):
            coerced = _coerce_int_seed(job.get(key))
            if coerced is not None:
                return coerced
        construction = job.get("construction") if isinstance(job.get("construction"), dict) else {}
        for key in ("noise_seed", "seed", "used_seed"):
            coerced = _coerce_int_seed(construction.get(key))
            if coerced is not None:
                return coerced
        adhoc = job.get("adhoc_overrides") if isinstance(job.get("adhoc_overrides"), dict) else {}
        params = adhoc.get("parameters") if isinstance(adhoc.get("parameters"), dict) else {}
        for key in ("seed", "noise_seed"):
            coerced = _coerce_int_seed(params.get(key))
            if coerced is not None:
                return coerced
        dev = job.get("dev_tuning") if isinstance(job.get("dev_tuning"), dict) else {}
        spec = dev.get("spec") if isinstance(dev.get("spec"), dict) else {}
        coerced = _coerce_int_seed(dev.get("noise_seed") or spec.get("noise_seed"))
        if coerced is not None:
            return coerced

    prompt_obj: Optional[Dict[str, Any]] = None
    if isinstance(job, dict):
        for key in ("prompt", "api_prompt"):
            raw = job.get(key)
            if isinstance(raw, dict) and raw:
                prompt_obj = raw
                break
    if prompt_obj is None and job_path is not None:
        sibling = job_path.with_name(job_path.name.replace(".job.json", ".prompt.json"))
        if sibling.is_file():
            try:
                doc = json.loads(sibling.read_text(encoding="utf-8"))
            except Exception:
                doc = None
            if isinstance(doc, dict):
                # Sibling may be {prompt: {...}} or the prompt map itself.
                inner = doc.get("prompt") if isinstance(doc.get("prompt"), dict) else doc
                if isinstance(inner, dict):
                    prompt_obj = inner

    if isinstance(prompt_obj, dict):
        try:
            from comfy_meta_lib import collect_seeds_from_prompt  # type: ignore

            info = collect_seeds_from_prompt(prompt_obj)
            used = info.get("used_seed") if isinstance(info, dict) else None
            coerced = _coerce_int_seed(used)
            if coerced is not None:
                return coerced
        except Exception:
            pass
        # Inline fallback if comfy_meta_lib is unavailable.
        for _nid, node in prompt_obj.items():
            if not isinstance(node, dict):
                continue
            ctype = node.get("class_type")
            inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
            if ctype == "RandomNoise":
                coerced = _coerce_int_seed(inputs.get("noise_seed"))
                if coerced is not None:
                    return coerced
        for _nid, node in prompt_obj.items():
            if not isinstance(node, dict):
                continue
            if node.get("class_type") in ("KSampler", "KSamplerAdvanced"):
                inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
                coerced = _coerce_int_seed(inputs.get("seed"))
                if coerced is not None:
                    return coerced
    return None


def resolve_queue_seed_parameter(
    body: Dict[str, Any],
    *,
    job: Optional[Dict[str, Any]] = None,
    job_path: Optional[Path] = None,
) -> tuple[Optional[int], Optional[str]]:
    """
    Resolve noise-seed policy for factory queue / replay.

    Precedence:
    1. Explicit ``overrides.parameters.seed`` / ``noise_seed``
    2. ``seed_mode=same`` → hold seed from ``job`` / prompt (if recoverable)
    3. Default / ``seed_mode=new`` → fresh random draw

    Seed-surfing (walk nearby seeds) is intentionally not implemented yet.
    Returns ``(seed_or_none, mode_applied)``.
    """
    import random

    overrides = body.get("overrides") if isinstance(body.get("overrides"), dict) else {}
    params = overrides.get("parameters") if isinstance(overrides.get("parameters"), dict) else {}
    explicit = _coerce_int_seed(
        params.get("seed") if params.get("seed") not in (None, "") else params.get("noise_seed")
    )
    if explicit is not None:
        return explicit, "explicit"

    mode = str(body.get("seed_mode") or "new").strip().lower() or "new"
    if mode in ("hold", "keep"):
        mode = "same"
    if mode in ("random", "fresh", "default"):
        mode = "new"
    if mode not in ("same", "new"):
        mode = "new"

    if mode == "new":
        return int(random.randint(0, 2**31 - 1)), "new"

    held = extract_job_noise_seed(job, job_path)
    if held is not None:
        return held, "same"
    # Same requested but unknown — fall back to a new draw so we never silently
    # reuse a fixed template seed and produce lookalike outputs.
    return int(random.randint(0, 2**31 - 1)), "same_missing_new"


# Backward-compatible name used by replay callers / tests.
resolve_replay_seed_parameter = resolve_queue_seed_parameter


def apply_seed_policy_to_overrides(
    overrides: Optional[Dict[str, Any]],
    *,
    seed_mode: Optional[str] = None,
    job: Optional[Dict[str, Any]] = None,
    job_path: Optional[Path] = None,
) -> tuple[Dict[str, Any], Optional[int], Optional[str]]:
    """Ensure overrides carry a seed per default-new policy; return updated overrides."""
    base = dict(overrides) if isinstance(overrides, dict) else {}
    seed_value, mode = resolve_queue_seed_parameter(
        {"overrides": base, "seed_mode": seed_mode},
        job=job,
        job_path=job_path,
    )
    if seed_value is None:
        return base, None, mode
    params = dict(base.get("parameters") if isinstance(base.get("parameters"), dict) else {})
    params["seed"] = int(seed_value)
    base["parameters"] = params
    return base, int(seed_value), mode


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


def hostify_media_abs(media_abs: Optional[Path]) -> Optional[Path]:
    """Resolve container ``/workspace/...`` paths to host-visible files when needed."""
    if media_abs is None:
        return None
    p = Path(media_abs).expanduser()
    if p.is_file():
        return p.resolve()
    try:
        from shape_factory import hostify_repo_path

        mapped = hostify_repo_path(p)
        if mapped.is_file():
            return mapped.resolve()
    except Exception:
        pass
    # Common bind aliases when API runs on host.
    text = str(p).replace("\\", "/")
    aliases = (
        ("/workspace/output/", "/home/yuji/comfyui-runpod-data/output/"),
        ("/workspace/input/", "/home/yuji/comfyui-runpod-data/input/"),
    )
    for src, dst in aliases:
        if text.startswith(src):
            cand = Path(dst + text[len(src) :])
            if cand.is_file():
                return cand.resolve()
    return p


def _probe_media_frame_meta(media_abs: Path) -> Dict[str, Any]:
    media = hostify_media_abs(media_abs) or Path(media_abs)
    info = ffprobe_video_info(media) if media.is_file() else {}
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

    Prefer ``mark_in``/``mark_out`` (seconds), then non-zero skip/cap parameters,
    else work-products trim sidecar. Bare ``skip=0,cap=0`` without marks does
    **not** block the sidecar (UI race when media duration was unknown).
    Catalog template skip/cap are **ignored** (fossilized on authoring media) —
    rebound sources seed from clips / full file via ``shape_factory_clips``.

    Only writes ``skip_first_frames`` / ``frame_load_cap`` when those keys were
    explicitly provided or introduced by sidecar — so an extend-only
    ``frame_load_cap`` lengthen value does not invent skip=0.
    """
    params_in = dict(parameters) if isinstance(parameters, dict) else {}
    out_params = dict(params_in)
    meta: Optional[Dict[str, Any]] = None
    media_resolved = hostify_media_abs(media_abs) if media_abs is not None else None
    media_meta = (
        _probe_media_frame_meta(media_resolved)
        if media_resolved is not None
        else {"fps": 18.0, "frame_count": 0, "duration": 0.0}
    )
    fps = float(media_meta["fps"])
    frame_count = int(media_meta["frame_count"] or 0)
    duration = float(media_meta["duration"] or 0.0)

    explicit_skip = params_in.get("skip_first_frames") not in (None, "")
    explicit_cap = params_in.get("frame_load_cap") not in (None, "")
    has_marks = params_in.get("mark_in") not in (None, "") or params_in.get("mark_out") not in (None, "")
    try:
        skip_v = int(params_in.get("skip_first_frames") or 0) if explicit_skip else None
    except (TypeError, ValueError):
        skip_v = 0 if explicit_skip else None
    try:
        cap_v = int(params_in.get("frame_load_cap") or 0) if explicit_cap else None
    except (TypeError, ValueError):
        cap_v = 0 if explicit_cap else None
    # Bare skip=0,cap=0 without marks is often a UI race (duration unknown → zeros),
    # not an intentional full-file override. Allow the work-products sidecar to win.
    weak_full_file_zeros = (
        not has_marks
        and explicit_skip
        and int(skip_v or 0) == 0
        and (not explicit_cap or int(cap_v or 0) == 0)
    )
    # Trim intent: both skip and cap from UI/sidecar, or skip alone.
    # Extend lengthen often sets frame_load_cap alone — that is not a trim window.
    trim_intent = bool(explicit_skip and not weak_full_file_zeros) or has_marks
    source: Optional[str] = "overrides" if (explicit_skip and not weak_full_file_zeros) or has_marks else None

    if has_marks:
        try:
            tin = float(params_in["mark_in"]) if params_in.get("mark_in") not in (None, "") else 0.0
        except (TypeError, ValueError):
            tin = 0.0
        try:
            tout = (
                float(params_in["mark_out"])
                if params_in.get("mark_out") not in (None, "")
                else (duration if duration > 0 else tin)
            )
        except (TypeError, ValueError):
            tout = duration if duration > 0 else tin
        win = trim_seconds_to_vhs_window(
            mark_in=tin,
            mark_out=tout,
            duration_s=duration,
            fps=fps,
            frame_count=frame_count or None,
        )
        out_params["skip_first_frames"] = int(win["skip_first_frames"])
        out_params["frame_load_cap"] = int(win["frame_load_cap"])
        explicit_skip = True
        explicit_cap = True
        trim_intent = True
        source = "overrides"
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

    if source is None and read_sidecar and media_resolved is not None and media_resolved.is_file():
        marks = _load_work_products_trim_seconds(media_resolved)
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

    # template_defaults retained in signature for callers, but never applied as policy.
    _ = template_defaults

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

    clip_id = overrides.get("source_clip_id") or overrides.get("clip_id")
    if clip_id not in (None, ""):
        adhoc["source_clip_id"] = str(clip_id).strip()

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
    seed_mode: Optional[str] = None,
    seed_job: Optional[Dict[str, Any]] = None,
    seed_job_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Generate + submit one explicit slot binding combo.

    ``bindings`` maps slot name -> absolute filesystem path (as returned by the map API).
    ``overrides`` may include ``prompt_profile`` (inline edits) and ``parameters`` (frames/steps/…).

    Noise seed defaults to a **new** random draw unless ``overrides.parameters.seed`` is set
    or ``seed_mode="same"`` (hold ``seed_job``'s seed).
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
    overrides, seed_value, seed_mode_applied = apply_seed_policy_to_overrides(
        overrides,
        seed_mode=seed_mode,
        job=seed_job,
        job_path=seed_job_path,
    )
    if seed_value is not None or seed_mode_applied:
        construction = dict(construction) if isinstance(construction, dict) else {}
        if seed_value is not None:
            construction["noise_seed"] = int(seed_value)
        if seed_mode_applied:
            construction["seed_mode"] = seed_mode_applied
        if not construction.get("step"):
            construction["step"] = str(pick_mode or "product")
        if not construction.get("pick_mode"):
            construction["pick_mode"] = str(pick_mode or "product")

    shape_path = _resolve_shape_path(data_root / "shapes" / f"{family}.shape.yaml", data_root=data_root, family_slug=family)
    pools_path = data_root / "pools" / family / "pools.yaml"
    if not pools_path.is_file():
        raise FileNotFoundError(f"pools.yaml not found: {pools_path}")

    shape = load_yaml(shape_path)
    pools_doc = load_yaml(pools_path)
    req_by_slot = requires_by_slot(shape)
    bindings = _bindings_declared_by_shape(shape, bindings)

    picks: Dict[str, Path] = {}
    slot_paths: Dict[str, str] = {}
    for slot, raw_path in bindings.items():
        slot_name = str(slot or "").strip()
        path_str = str(raw_path or "").strip()
        if not slot_name or not path_str:
            continue
        if slot_name not in req_by_slot:
            continue
        resolved: Optional[Path] = None
        try:
            resolved = resolve_existing_path(
                path_str,
                output_root=output_root,
                data_root=data_root,
                workspace_root=workspace_root,
            )
        except FileNotFoundError:
            if slot_name in {"source_still", "identity_anchor", "identity_still", "source_image"}:
                resolved = _resolve_still_file(
                    path_str,
                    workspace_root=workspace_root,
                    output_root=output_root,
                    data_root=data_root,
                )
            if resolved is None:
                raise
        picks[slot_name] = resolved
        slot_paths[slot_name] = str(resolved)

    # Fill missing required still / prompt slots from the family's pools (same as map zip).
    for slot_name, req in req_by_slot.items():
        if slot_name in picks or (isinstance(req, dict) and req.get("optional")):
            continue
        if slot_name not in {
            "source_still",
            "identity_anchor",
            "identity_still",
            "source_image",
            "prompt_profile",
        }:
            continue
        fallback = _first_pool_member_for_slot(pools_doc, slot_name)
        if fallback is not None:
            picks[slot_name] = fallback
            slot_paths[slot_name] = str(fallback)

    source_req = req_by_slot.get("source_still") if isinstance(req_by_slot, dict) else None
    if isinstance(source_req, dict) and not source_req.get("optional") and "source_still" not in picks:
        fallback = _first_pool_member_for_slot(pools_doc, "source_still")
        if fallback is not None:
            picks["source_still"] = fallback
            slot_paths["source_still"] = str(fallback)

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
        "noise_seed": int(seed_value) if seed_value is not None else None,
        "seed_mode": seed_mode_applied,
        "construction": construction,
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


def _first_pool_member_for_slot(pools_doc: Dict[str, Any], slot: str) -> Optional[Path]:
    members = _all_pool_members_for_slot(pools_doc, slot)
    return members[0] if members else None


def _all_pool_members_for_slot(pools_doc: Dict[str, Any], slot: str) -> list[Path]:
    pools = pools_doc.get("pools") if isinstance(pools_doc.get("pools"), dict) else {}
    pool_def = pools.get(slot)
    if not isinstance(pool_def, dict):
        for _name, cand in pools.items():
            if isinstance(cand, dict) and str(cand.get("slot") or "").strip() == slot:
                pool_def = cand
                break
    if not isinstance(pool_def, dict):
        return []
    members = resolve_pool_members(pool_def)
    if slot != "source_still":
        return members
    shape_raw = pools_doc.get("shape")
    family_slug = ""
    if isinstance(shape_raw, str) and shape_raw.strip():
        family_slug = Path(shape_raw).stem
    if not family_slug:
        family_slug = str(pools_doc.get("family_slug") or "").strip()
    if not family_slug:
        return members
    try:
        from shape_factory_input_curation import merged_source_stills  # type: ignore
    except Exception:
        return members
    try:
        merged = merged_source_stills(
            family_slug=family_slug,
            base_members=members,
            data_root=DEFAULT_DATA_ROOT,
        )
        return list(merged.get("members") or members)
    except Exception:
        return members


def _prompt_label_from_profile(path: Path) -> str:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(raw, dict):
        return ""
    return str(raw.get("label") or "").strip()


def _prompt_profile_path_from_spec(spec: Any) -> str:
    if isinstance(spec, str):
        return spec.strip()
    if isinstance(spec, dict):
        return str(spec.get("path") or spec.get("relpath") or "").strip()
    return ""


def _explicit_prompt_profile_from_body(body: Dict[str, Any]) -> str:
    """Operator-chosen prompt_profile from Submit / run-step (bindings or top-level)."""
    raw = body.get("bindings") if isinstance(body.get("bindings"), dict) else {}
    if isinstance(raw, dict) and "prompt_profile" in raw:
        path = _prompt_profile_path_from_spec(raw.get("prompt_profile"))
        if path:
            return path
    return str(body.get("prompt_profile") or "").strip()


def _pool_def_for_slot(pools_doc: Dict[str, Any], slot: str) -> Optional[Dict[str, Any]]:
    pools = pools_doc.get("pools") if isinstance(pools_doc.get("pools"), dict) else {}
    pool_def = pools.get(slot)
    if isinstance(pool_def, dict):
        return pool_def
    for _name, cand in pools.items():
        if isinstance(cand, dict) and str(cand.get("slot") or "").strip() == slot:
            return cand
    return None


def _hostify_pool_dir(dir_text: str, *, data_root: Path) -> Path:
    raw = str(dir_text or "").strip().replace("\\", "/")
    if raw.startswith("/workspace/.data/"):
        rel = raw[len("/workspace/.data/") :]
        return data_root / rel
    if raw.startswith("/workspace/output/"):
        # Prompt pools are expected under data_root; keep this as a best-effort
        # remap for uncommon layouts.
        return data_root / raw[len("/workspace/output/") :]
    return Path(raw).expanduser()


def _remap_prompt_profile_binding_for_family(
    bindings: Dict[str, str],
    *,
    data_root: Path,
    family_slug: str,
) -> Tuple[Dict[str, str], Optional[Dict[str, str]]]:
    """
    Re-home prompt_profile to a target family's pool when a cross-family rewire
    carried a parent-family prompt path.
    """
    current = str(bindings.get("prompt_profile") or "").strip()
    if not current:
        return dict(bindings), None

    pools_path = data_root / "pools" / family_slug / "pools.yaml"
    if not pools_path.is_file():
        return dict(bindings), None
    try:
        pools_doc = load_yaml(pools_path)
    except Exception:
        return dict(bindings), None
    candidates = _all_pool_members_for_slot(pools_doc, "prompt_profile")
    if not candidates:
        pool_def = _pool_def_for_slot(pools_doc, "prompt_profile")
        members = pool_def.get("members") if isinstance(pool_def, dict) and isinstance(pool_def.get("members"), list) else []
        for member in members:
            if isinstance(member, dict):
                dir_raw = str(member.get("dir") or "").strip()
                if dir_raw:
                    host_dir = _hostify_pool_dir(dir_raw, data_root=data_root)
                    if host_dir.is_dir():
                        candidates.extend(sorted(host_dir.glob("*.json")))
    if not candidates:
        prompts_dir = data_root / "pools" / family_slug / "prompts"
        if prompts_dir.is_dir():
            candidates = sorted(prompts_dir.glob("*.json"))
    if not candidates:
        return dict(bindings), None

    current_norm = str(Path(current).expanduser()).replace("\\", "/")
    candidate_norms = {str(Path(c).expanduser()).replace("\\", "/") for c in candidates}
    if current_norm in candidate_norms:
        return dict(bindings), None

    source = Path(current).expanduser()
    source_label = _prompt_label_from_profile(source) if source.is_file() else ""
    source_name = source.name

    chosen: Optional[Path] = None
    if source_label:
        for cand in candidates:
            if _prompt_label_from_profile(cand) == source_label:
                chosen = cand
                break
    if chosen is None and source_name:
        for cand in candidates:
            if cand.name == source_name:
                chosen = cand
                break
    if chosen is None:
        chosen = candidates[0]

    next_bindings = dict(bindings)
    next_bindings["prompt_profile"] = str(chosen.resolve())
    return next_bindings, {
        "from": current,
        "to": str(chosen.resolve()),
        "reason": "cross_family_prompt_profile_remap",
    }


def queue_from_source_media(
    *,
    media_abs: Path,
    family_slug: str,
    body: Dict[str, Any],
    repo_root: Path,
    workspace_root: Path,
    output_root: Path,
    comfy_server: str,
) -> Dict[str, Any]:
    """
    Fresh combo when there is no parent factory job: bind this media as source_video,
    pick the first pool prompt_profile, resolve identity from body, apply clip overrides.
    """
    family = str(family_slug or "").strip()
    if not family:
        raise ValueError("family_slug is required")
    media = hostify_media_abs(Path(media_abs).expanduser()) or Path(media_abs).expanduser()
    if not media.is_file():
        raise FileNotFoundError(f"source media not found: {media}")

    data_root = resolve_shape_factory_data_root(repo_root=repo_root)
    shape_path = _resolve_shape_path(
        data_root / "shapes" / f"{family}.shape.yaml",
        data_root=data_root,
        family_slug=family,
    )
    shape = load_yaml(shape_path)
    pools_path = data_root / "pools" / family / "pools.yaml"
    if not pools_path.is_file():
        raise FileNotFoundError(f"pools.yaml not found: {pools_path}")
    pools_doc = load_yaml(pools_path)

    bindings: Dict[str, str] = {}
    video_slot = _video_source_slot(shape, {}) or "source_video"
    bindings[video_slot] = str(media.resolve())

    explicit_prompt = _explicit_prompt_profile_from_body(body)
    if explicit_prompt:
        bindings["prompt_profile"] = explicit_prompt
    else:
        prompt_path = _first_pool_member_for_slot(pools_doc, "prompt_profile")
        if prompt_path is None:
            raise ValueError(f"no prompt_profile pool members for family {family!r}")
        bindings["prompt_profile"] = str(prompt_path.resolve())

    bindings, identity_meta = _resolve_identity_still_for_shape(
        shape=shape,
        body=body,
        job=None,
        bindings=bindings,
        output_abs=str(media.resolve()),
        workspace_root=workspace_root,
        output_root=output_root,
        data_root=data_root,
    )

    overrides = _parse_overrides(body)
    params_for_trim = overrides.get("parameters") if isinstance(overrides.get("parameters"), dict) else {}
    template_defaults = vhs_loader_defaults_for_shape(
        shape,
        data_root=data_root,
        workspace_root=workspace_root,
        output_root=output_root,
    )
    resolved_params, trim_clamped = resolve_vhs_window_overrides(
        parameters=params_for_trim,
        media_abs=media if media.is_file() else None,
        template_defaults=template_defaults,
        read_sidecar=True,
    )
    if resolved_params != params_for_trim:
        overrides = dict(overrides)
        overrides["parameters"] = resolved_params

    result = queue_shape_factory_combo(
        family_slug=family,
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
        pick_mode="adhoc",
        parent_output=str(media.resolve()),
        construction={
            "source": "queue_from_source_media",
            "media": str(media.resolve()),
        },
    )
    if isinstance(result, dict):
        result.setdefault("fresh_combo", True)
        result.setdefault("extend", False)
        if identity_meta:
            result["identity_anchor"] = identity_meta
        if trim_clamped:
            result["trim_clamped"] = trim_clamped
    return result


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

    request_bindings = body.get("bindings") if isinstance(body.get("bindings"), dict) else {}
    explicit_prompt = _explicit_prompt_profile_from_body(body)
    explicit_prompt_binding = bool(explicit_prompt)
    prompt_remap_meta: Optional[Dict[str, str]] = None

    if job_key:
        found = _find_job_doc(data_root, job_key)
        if not found:
            raise ValueError(f"job not found: {job_key}")
        job, job_path = found
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
        if explicit_prompt:
            bindings["prompt_profile"] = explicit_prompt
    else:
        job = None
        job_path = None
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
    source_family = str(job.get("family_slug") or "").strip() if isinstance(job, dict) else ""
    is_cross_family = bool(source_family and source_family != family_slug)
    if is_cross_family and not explicit_prompt_binding:
        bindings, prompt_remap_meta = _remap_prompt_profile_binding_for_family(
            bindings,
            data_root=data_root,
            family_slug=family_slug,
        )

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
    seed_mode = str(body.get("seed_mode") or "").strip() or None

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

    # Bind identity_anchor / source_still when the target shape requires an image slot.
    bindings, identity_meta = _resolve_identity_still_for_shape(
        shape=shape,
        body=body,
        job=job if isinstance(job, dict) else None,
        bindings=bindings,
        output_abs=output_abs,
        workspace_root=workspace_root,
        output_root=output_root,
        data_root=data_root,
    )
    if identity_meta and construction is not None:
        construction = dict(construction)
        construction["identity_anchor"] = identity_meta.get("path")
        construction["identity_evidence"] = identity_meta.get("evidence")

    bindings = _bindings_declared_by_shape(shape, bindings)

    # Resolve VHS input window (explicit overrides → sidecar; never template skip).
    video_slot_for_trim = _video_source_slot(shape, bindings)
    media_for_trim: Optional[Path] = None
    if video_slot_for_trim:
        raw_media = str(bindings.get(video_slot_for_trim) or "").strip()
        if raw_media:
            media_for_trim = hostify_media_abs(Path(raw_media).expanduser())
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
        seed_mode=seed_mode,
        seed_job=job if isinstance(job, dict) else None,
        seed_job_path=job_path,
    )
    if isinstance(result, dict):
        result.setdefault("replay_of_job_key", job_key or None)
        result.setdefault("extend", extend)
        if identity_meta:
            result["identity_anchor"] = identity_meta
        if recovered_prompt:
            result["prompt_profile_recovered"] = recovered_prompt
        if prompt_remap_meta:
            result["prompt_profile_remapped"] = prompt_remap_meta
        if trim_clamped:
            result["trim_clamped"] = trim_clamped
    return result


def _job_submit_status(job: Optional[Dict[str, Any]]) -> str:
    if not isinstance(job, dict):
        return ""
    submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    return str(submit.get("status") or job.get("status") or "").strip().lower()


# Only these may be retired by a family swap. Complete / deposited / failed jobs stay on disk.
_SWAP_FAMILY_WAITING_STATUSES = frozenset({"queued", "pending", "editing", "submitted"})


def _job_has_produced_output(job: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(job, dict):
        return False
    submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    for bucket in (submit.get("outputs"), job.get("outputs")):
        if isinstance(bucket, list) and any(str(x or "").strip() for x in bucket):
            return True
    deposit = job.get("deposit") if isinstance(job.get("deposit"), dict) else {}
    videos = deposit.get("videos") if isinstance(deposit.get("videos"), list) else []
    return any(str(x or "").strip() for x in videos)


def _job_prompt_id(job: Optional[Dict[str, Any]]) -> str:
    if not isinstance(job, dict):
        return ""
    submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    return str(submit.get("prompt_id") or "").strip()


def swap_family_from_request_body(
    body: Dict[str, Any],
    *,
    repo_root: Path,
    workspace_root: Path,
    output_root: Path,
    comfy_server: str,
) -> Dict[str, Any]:
    """
    Replay one or more jobs as another family, then retire the originals.

    Queued Comfy prompts are unqueued first so the old family does not start.
    The source job is then discarded (expunged) so Workbench is not left with
    a pending leftover. Running jobs and finished jobs (complete / deposited /
    failed, or any job that already produced output) are skipped — swap never
    deletes a done work product.
    """
    from shape_factory_creation_control import mutate_job
    from shape_factory_map import resolve_shape_factory_data_root

    target = str(body.get("family_slug") or body.get("to_family") or "").strip()
    if not target:
        raise ValueError("family_slug is required")
    keys: List[str] = []
    raw_keys = body.get("job_keys")
    if isinstance(raw_keys, list):
        keys.extend(str(k).strip() for k in raw_keys if str(k or "").strip())
    one = str(body.get("job_key") or "").strip()
    if one and one not in keys:
        keys.append(one)
    if not keys:
        raise ValueError("job_key is required")

    replace = True if "replace" not in body else bool(body.get("replace"))
    front = bool(body.get("front"))
    seed_mode = str(body.get("seed_mode") or "same").strip() or "same"
    data_root = resolve_shape_factory_data_root(repo_root=repo_root)

    items: List[Dict[str, Any]] = []
    for job_key in keys:
        found = _find_job_doc(data_root, job_key)
        if not found:
            items.append({"ok": False, "job_key": job_key, "error": "not_found"})
            continue
        job, job_path = found
        source_family = str(job.get("family_slug") or "").strip()
        if source_family == target:
            items.append(
                {
                    "ok": False,
                    "job_key": job_key,
                    "family_slug": source_family,
                    "error": "same_family",
                }
            )
            continue
        status = _job_submit_status(job)
        has_output = _job_has_produced_output(job)
        waiting = status in _SWAP_FAMILY_WAITING_STATUSES or (not status and not has_output)
        if status == "running":
            items.append(
                {
                    "ok": False,
                    "job_key": job_key,
                    "family_slug": source_family,
                    "error": "running",
                    "detail": "Interrupt the running job before swapping its family",
                }
            )
            continue
        if not waiting or has_output:
            items.append(
                {
                    "ok": False,
                    "job_key": job_key,
                    "family_slug": source_family,
                    "error": "not_waiting",
                    "detail": (
                        "Swap only retires waiting jobs; finished jobs are kept. "
                        "Use Re-run to copy this recipe as another family."
                    ),
                }
            )
            continue
        try:
            replay_body: Dict[str, Any] = {
                "job_key": job_key,
                "family_slug": target,
                "extend": False,
                "front": front,
                "seed_mode": seed_mode,
                "force": True,
            }
            overrides = body.get("overrides")
            if isinstance(overrides, dict) and overrides:
                replay_body["overrides"] = overrides
            replayed = replay_from_request_body(
                replay_body,
                repo_root=repo_root,
                workspace_root=workspace_root,
                output_root=output_root,
                comfy_server=comfy_server,
            )
        except Exception as e:
            items.append(
                {
                    "ok": False,
                    "job_key": job_key,
                    "family_slug": source_family,
                    "error": "replay_failed",
                    "detail": str(e),
                }
            )
            continue
        if not isinstance(replayed, dict) or not replayed.get("ok", True):
            items.append(
                {
                    "ok": False,
                    "job_key": job_key,
                    "family_slug": source_family,
                    "error": "replay_failed",
                    "detail": (replayed or {}).get("error") or (replayed or {}).get("detail"),
                    "replay": replayed,
                }
            )
            continue

        row: Dict[str, Any] = {
            "ok": True,
            "job_key": job_key,
            "family_slug": source_family,
            "to_family": target,
            "replay": replayed,
            "replaced": False,
        }
        if replace:
            pid = _job_prompt_id(job)
            retired: Dict[str, Any] = {}
            if pid:
                retired["unqueue"] = mutate_job(
                    action="unqueue_to_pending",
                    prompt_id=pid,
                    server=str(comfy_server),
                    data_root=data_root,
                    job_key=job_key,
                    job_path=job_path,
                    reason="family_swap",
                    actor=str(body.get("actor") or "operator"),
                    source_surface=str(body.get("source_surface") or "workbench"),
                )
            retired["discard"] = mutate_job(
                action="discard",
                data_root=data_root,
                server=str(comfy_server),
                job_key=job_key,
                job_path=job_path,
                expunge=True,
                reason="family_swap",
                actor=str(body.get("actor") or "operator"),
                source_surface=str(body.get("source_surface") or "workbench"),
            )
            row["replaced"] = bool((retired.get("discard") or {}).get("ok"))
            row["retire"] = retired
        items.append(row)

    swapped = sum(1 for it in items if it.get("ok"))
    failed = len(items) - swapped
    return {
        "ok": failed == 0 and swapped > 0,
        "to_family": target,
        "swapped": swapped,
        "failed": failed,
        "items": items,
    }


def derive_from_request_body(
    body: Dict[str, Any],
    *,
    repo_root: Path,
    workspace_root: Path,
    output_root: Path,
    comfy_server: str,
) -> Dict[str, Any]:
    """
    Rewire a prior job into a new combo (Workbench Derive / ``pick_mode: derive``).

    Uses the same ``_derive_rewire`` path as hourly appetite derive: same source + alt
    prompt, same prompt + alt source, or extend (facet=both) when that is the best rewire.
    """
    import random

    from shape_factory_hourly import (
        _derive_rewire,
        _load_appetite_index,
        _load_heuristics_index,
        _load_ratings_index,
        _load_source_facets_doc,
        _picks_from_job,
        _recipe_appetite,
        _recipe_from_picks,
        _recent_combo_keys,
        collect_pool_source_videos,
        collect_replay_recipes,
    )
    from shape_factory_ratings import normalize_appetite_facet

    data_root = resolve_shape_factory_data_root(repo_root=repo_root)
    job_key = str(body.get("job_key") or "").strip()
    if not job_key:
        raise ValueError("job_key is required")

    found = _find_job_doc(data_root, job_key)
    if not found:
        raise ValueError(f"job not found: {job_key}")
    job, _job_path = found
    family_slug = str(body.get("family_slug") or body.get("family") or job.get("family_slug") or "").strip()
    if not family_slug:
        raise ValueError("family_slug is required")

    shape_path = _resolve_shape_path(
        data_root / "shapes" / f"{family_slug}.shape.yaml",
        data_root=data_root,
        family_slug=family_slug,
    )
    shape = load_yaml(shape_path)
    picks = _picks_from_job(job, shape=shape, data_root=data_root)
    if not picks:
        raise ValueError("cannot recover picks from job for derive")

    submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    sub_outs = submit.get("outputs") if isinstance(submit.get("outputs"), list) else []
    job_outs = job.get("outputs") if isinstance(job.get("outputs"), list) else []
    dep = job.get("deposit") if isinstance(job.get("deposit"), dict) else {}
    dep_vids = dep.get("videos") if isinstance(dep.get("videos"), list) else []
    output_abs = str(
        body.get("output_path")
        or (sub_outs[0] if sub_outs else (job_outs[0] if job_outs else (dep_vids[-1] if dep_vids else "")))
        or ""
    ).strip()

    seed = _recipe_from_picks(
        family=family_slug,
        picks=picks,
        source=job_key,
        output_path=output_abs or None,
    )

    recipes = collect_replay_recipes(family_slug, data_root=data_root)
    pool_sources = [str(p) for p in collect_pool_source_videos(family_slug, data_root=data_root)]
    recent = _recent_combo_keys(data_root=data_root, family=family_slug, limit=12)
    facets_doc = _load_source_facets_doc(data_root)

    facet_raw = str(body.get("facet") or "").strip().lower()
    if facet_raw not in {"source", "processing", "both"}:
        ratings_doc = _load_ratings_index(data_root)
        heuristics_doc = _load_heuristics_index(data_root)
        appetite_doc = _load_appetite_index(data_root)
        info = _recipe_appetite(
            seed,
            shape=shape,
            ratings_doc=ratings_doc,
            heuristics_doc=heuristics_doc,
            appetite_doc=appetite_doc,
        )
        facet_raw = normalize_appetite_facet(info.get("facet") or "both")
    facet = facet_raw if facet_raw in {"source", "processing", "both"} else "both"

    rng = random.Random(hash(job_key) ^ 0xD3E17E)
    rewired, action, hold_meta = _derive_rewire(
        seed,
        facet=facet,
        family=family_slug,
        pool=recipes,
        rng=rng,
        recent=recent,
        cursor=int(time.time()) % 10_000,
        facets_doc=facets_doc,
        extra_sources=pool_sources,
    )
    if rewired is None or not isinstance(rewired.get("picks"), dict):
        raise ValueError("derive_no_distinct_combo: no alternate prompt/source available")

    bindings = {str(slot): str(path) for slot, path in rewired["picks"].items() if str(path).strip()}
    if not bindings:
        raise ValueError("derive produced empty bindings")
    source_family = str(job.get("family_slug") or "").strip()
    prompt_remap_meta: Optional[Dict[str, str]] = None
    explicit_prompt = _explicit_prompt_profile_from_body(body)
    if explicit_prompt:
        bindings["prompt_profile"] = explicit_prompt
    elif source_family and source_family != family_slug:
        bindings, prompt_remap_meta = _remap_prompt_profile_binding_for_family(
            bindings,
            data_root=data_root,
            family_slug=family_slug,
        )

    pick_mode = str(action or "derive").strip() or "derive"
    if pick_mode not in {"derive", "extend"}:
        pick_mode = "derive"

    parent_output = output_abs or None
    construction: Dict[str, Any] = {
        "step": "derive" if pick_mode == "derive" else "extend",
        "derive_action": pick_mode,
        "pick_mode": pick_mode,
        "parent_output": parent_output,
        "replay_of_job_key": job_key,
        "appetite_facet": facet,
        "combo_key": rewired.get("combo_key"),
        "source": rewired.get("source"),
        **(hold_meta if isinstance(hold_meta, dict) else {}),
    }

    overrides = _parse_overrides(body)
    video_slot_for_trim = _video_source_slot(shape, bindings)
    media_for_trim: Optional[Path] = None
    if video_slot_for_trim:
        raw_media = str(bindings.get(video_slot_for_trim) or "").strip()
        if raw_media:
            media_for_trim = hostify_media_abs(Path(raw_media).expanduser())
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

    if pick_mode == "extend":
        params_in = overrides.get("parameters") if isinstance(overrides.get("parameters"), dict) else {}
        length_params = _extend_length_parameters(job, existing=params_in)
        overrides = dict(overrides)
        overrides["parameters"] = length_params
        construction["frames_before"] = _parent_frame_count(job)
        raw_budget = length_params.get("frames")
        if isinstance(raw_budget, (int, float)) and int(raw_budget) > 0:
            construction["frames_after"] = int(raw_budget)

    result = queue_shape_factory_combo(
        family_slug=family_slug,
        bindings=bindings,
        combo_key=str(rewired.get("combo_key") or "") or None,
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
        seed_mode=str(body.get("seed_mode") or "").strip() or None,
        seed_job=job,
        seed_job_path=_job_path,
    )
    if isinstance(result, dict):
        result.setdefault("ok", True)
        result["derive_of_job_key"] = job_key
        result["derive_action"] = pick_mode
        result["appetite_facet"] = facet
        result["construction"] = construction
        if prompt_remap_meta:
            result["prompt_profile_remapped"] = prompt_remap_meta
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
