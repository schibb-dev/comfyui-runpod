"""Apply shape-level postprocess policy to catalog / job workflows."""

from __future__ import annotations

from typing import Any, Optional

POSTPROCESS_KEYS = ("upscale", "interpolate", "color_match", "merge_frames")

POSTPROCESS_NODE_TYPES: dict[str, str] = {
    "ImageUpscaleWithModel": "upscale",
    "RIFE VFI": "interpolate",
    "ColorMatch": "color_match",
    "VHS_MergeImages": "merge_frames",
}

BYPASSER_MATCH_TITLE: dict[str, str] = {
    "Upscaler": "upscale",
    "Interpolation": "interpolate",
}

DEFAULT_POSTPROCESS: dict[str, bool] = {
    "upscale": False,
    "interpolate": False,
    "color_match": False,
    "merge_frames": False,
}

# ComfyUI LiteGraph node modes
MODE_ACTIVE = 0
MODE_BYPASS = 2


def shape_postprocess_raw(shape: dict[str, Any]) -> dict[str, Any]:
    raw = shape.get("postprocess")
    return raw if isinstance(raw, dict) else {}


def resolve_postprocess(
    shape: dict[str, Any],
    job: Optional[dict[str, Any]] = None,
) -> dict[str, bool]:
    """Merge shape defaults with optional per-job adhoc overrides."""
    out = dict(DEFAULT_POSTPROCESS)
    raw = shape_postprocess_raw(shape)
    for key in POSTPROCESS_KEYS:
        if key in raw:
            out[key] = bool(raw[key])
    if job:
        adhoc = job.get("adhoc_overrides")
        if isinstance(adhoc, dict):
            pp = adhoc.get("postprocess")
            if isinstance(pp, dict):
                for key in POSTPROCESS_KEYS:
                    if key in pp:
                        out[key] = bool(pp[key])
    return out


def infer_postprocess_from_workflow(workflow: dict[str, Any]) -> dict[str, bool]:
    """Read effective postprocess state from node modes (for tests / inventory)."""
    out = dict(DEFAULT_POSTPROCESS)
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        ntype = str(node.get("type") or "")
        key = POSTPROCESS_NODE_TYPES.get(ntype)
        if not key:
            continue
        mode = int(node.get("mode") or 0)
        if mode == MODE_ACTIVE:
            out[key] = True
    return out


def _set_node_mode(node: dict[str, Any], enabled: bool) -> bool:
    target = MODE_ACTIVE if enabled else MODE_BYPASS
    current = int(node.get("mode") or 0)
    if current == target:
        return False
    node["mode"] = target
    return True


def apply_shape_postprocess_ui(
    workflow: dict[str, Any],
    shape: dict[str, Any],
    job: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Set postprocess node bypass state from shape policy.

    Returns a change map (empty when shape has no postprocess block and no job override).
    """
    raw = shape_postprocess_raw(shape)
    job_pp = None
    if job and isinstance(job.get("adhoc_overrides"), dict):
        job_pp = job["adhoc_overrides"].get("postprocess")
    if not raw and not isinstance(job_pp, dict):
        return {}

    policy = resolve_postprocess(shape, job)
    changes: dict[str, Any] = {"profile_id": raw.get("profile_id"), "applied": {}}

    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        ntype = str(node.get("type") or "")
        key = POSTPROCESS_NODE_TYPES.get(ntype)
        if key is not None:
            if _set_node_mode(node, policy[key]):
                changes["applied"][f"node:{node.get('id')}:{key}"] = {
                    "enabled": policy[key],
                    "mode": MODE_ACTIVE if policy[key] else MODE_BYPASS,
                }
            continue

        if ntype == "Fast Groups Bypasser (rgthree)":
            props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
            match_title = str(props.get("matchTitle") or "")
            bypass_key = BYPASSER_MATCH_TITLE.get(match_title)
            if bypass_key is None:
                continue
            # Bypasser node mode 2 = bypasser itself inactive; group policy still driven by
            # direct postprocess nodes when present. For GEX graphs (no upscale/RIFE nodes),
            # leave bypassers unchanged — they only affect optional empty groups.
            if bypass_key not in policy:
                continue

    if not changes["applied"]:
        changes.pop("applied", None)
    if changes.get("profile_id") is None:
        changes.pop("profile_id", None)
    return changes


def apply_shape_postprocess_api(
    prompt: dict[str, Any],
    shape: dict[str, Any],
    job: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    API prompts are built from UI workflows after postprocess apply.

    No-op placeholder for symmetry with ui_defaults; convert excludes bypassed nodes.
    """
    _ = (prompt, shape, job)
    return {}
