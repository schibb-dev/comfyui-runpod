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
    generate_job_for_picks,
    load_effective_quarantine_registry,
    load_yaml,
    requires_by_slot,
    submit_job_file,
)
from shape_factory_map import _combo_key_from_slot_paths, resolve_existing_path, resolve_shape_factory_data_root
from shape_factory_prompt_recover import resolve_or_recover_prompt_profile_binding

# mxSlider node ids shared by FB9 GEX2 / GEX_FACIAL graphs (see .data/shapes/dev-fast.yaml).
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
    """Best-effort generation length from a prior job's captured workload / probes."""
    if not isinstance(job, dict):
        return None
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
    return None


def _extend_length_parameters(
    job: Optional[Dict[str, Any]],
    *,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build parameter overrides so extend is a *longer* run, not a same-length re-render.

    Default: add another full parent-length chunk (≈ double). Override with
    ``SHAPE_FACTORY_EXTEND_EXTRA_FRAMES``.
    """
    params: Dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    if params.get("frames") not in (None, ""):
        # Caller already chose an explicit length.
        return params

    base = _parent_frame_count(job)
    if base is None or base <= 0:
        base = int(os.environ.get("SHAPE_FACTORY_EXTEND_DEFAULT_FRAMES", "80"))
    extra_raw = os.environ.get("SHAPE_FACTORY_EXTEND_EXTRA_FRAMES", "").strip()
    if extra_raw:
        extra = max(1, int(extra_raw))
    else:
        extra = max(1, base)
    new_frames = int(base) + int(extra)
    params["frames"] = new_frames
    # Keep VHS load cap from clipping the chained source when lengthening.
    if params.get("frame_load_cap") in (None, ""):
        params["frame_load_cap"] = new_frames
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
    Map UI parameter knobs onto dev-tuning structure.

    Uses dev-fast.yaml as a template when present; patches frames/steps/overlap/frame_load_cap.
    """
    if not isinstance(parameters, dict) or not parameters:
        return None

    dev_fast = data_root / "shapes" / "dev-fast.yaml"
    if dev_fast.is_file():
        tuning = copy.deepcopy(load_yaml(dev_fast))
    else:
        tuning = {"ui_nodes": {}, "api_nodes": {}}

    ui_nodes = tuning.setdefault("ui_nodes", {})
    api_nodes = tuning.setdefault("api_nodes", {})
    if not isinstance(ui_nodes, dict):
        ui_nodes = {}
        tuning["ui_nodes"] = ui_nodes
    if not isinstance(api_nodes, dict):
        api_nodes = {}
        tuning["api_nodes"] = api_nodes

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

    frame_cap = parameters.get("frame_load_cap")
    if frame_cap is not None and frame_cap != "":
        tuning["vhs_load_video_path"] = {"frame_load_cap": int(frame_cap)}
        touched = True

    if not touched:
        return None

    tuning["profile_id"] = "adhoc-ui"
    tuning["output_prefix_suffix"] = str(parameters.get("output_prefix_suffix") or "_adhoc")
    return tuning


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
    if combo_key and str(combo_key).strip() and str(combo_key).strip() != computed_combo:
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

    ``extend=true`` chains the job's output into a video source slot (v2v/i2v-from-video)
    *and* lengthens the run (more frames). A same-length re-render with the prior output
    as source is a zero-length extend and is rejected unless frames are explicitly set.
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
            raise ValueError("extend requires a resolvable output path")
        video_slot = _video_source_slot(shape, bindings)
        if video_slot is None:
            raise ValueError("extend_not_supported: shape has no video source slot")
        prev_source = _norm_media_path(bindings.get(video_slot) or "")
        next_source = _norm_media_path(output_abs)
        bindings[video_slot] = output_abs

        # Lengthen: disposition "extend" = chain + longer run (avoid zero-length re-render).
        params_in = overrides.get("parameters") if isinstance(overrides.get("parameters"), dict) else {}
        length_params = _extend_length_parameters(job, existing=params_in)
        base_frames = _parent_frame_count(job)
        new_frames = int(length_params.get("frames") or 0)
        source_unchanged = bool(prev_source) and prev_source == next_source
        if new_frames <= 0 or (base_frames is not None and new_frames <= int(base_frames)):
            raise ValueError(
                "extend_zero_length: refused same-length re-render; "
                "set overrides.parameters.frames higher than the parent run"
            )
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
            "frames_before": base_frames,
            "frames_after": new_frames,
            "replay_of_job_key": job_key or None,
        }

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
    return result


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
