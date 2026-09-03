"""Apply shape-level generation editorial policy (ColorMatch, VHS_MergeImages)."""

from __future__ import annotations

from typing import Any, Optional

EDITORIAL_KEYS = ("color_match", "merge_frames")

EDITORIAL_NODE_TYPES: dict[str, str] = {
    "ColorMatch": "color_match",
    "VHS_MergeImages": "merge_frames",
}

DEFAULT_EDITORIAL: dict[str, bool] = {
    "color_match": False,
    "merge_frames": False,
}

MODE_ACTIVE = 0
MODE_BYPASS = 2


def shape_editorial_raw(shape: dict[str, Any]) -> dict[str, Any]:
    raw = shape.get("postprocess")
    return raw if isinstance(raw, dict) else {}


def resolve_editorial(
    shape: dict[str, Any],
    job: Optional[dict[str, Any]] = None,
) -> dict[str, bool]:
    out = dict(DEFAULT_EDITORIAL)
    raw = shape_editorial_raw(shape)
    for key in EDITORIAL_KEYS:
        if key in raw:
            out[key] = bool(raw[key])
    if job:
        adhoc = job.get("adhoc_overrides")
        if isinstance(adhoc, dict):
            pp = adhoc.get("postprocess")
            if isinstance(pp, dict):
                for key in EDITORIAL_KEYS:
                    if key in pp:
                        out[key] = bool(pp[key])
    return out


def infer_editorial_from_workflow(workflow: dict[str, Any]) -> dict[str, bool]:
    out = dict(DEFAULT_EDITORIAL)
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        key = EDITORIAL_NODE_TYPES.get(str(node.get("type") or ""))
        if key and int(node.get("mode") or 0) == MODE_ACTIVE:
            out[key] = True
    return out


def _set_node_mode(node: dict[str, Any], enabled: bool) -> bool:
    target = MODE_ACTIVE if enabled else MODE_BYPASS
    current = int(node.get("mode") or 0)
    if current == target:
        return False
    node["mode"] = target
    return True


def apply_shape_editorial_ui(
    workflow: dict[str, Any],
    shape: dict[str, Any],
    job: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    raw = shape_editorial_raw(shape)
    job_pp = None
    if job and isinstance(job.get("adhoc_overrides"), dict):
        job_pp = job["adhoc_overrides"].get("postprocess")
    if not raw and not isinstance(job_pp, dict):
        return {}

    policy = resolve_editorial(shape, job)
    changes: dict[str, Any] = {"profile_id": raw.get("profile_id"), "applied": {}}

    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        key = EDITORIAL_NODE_TYPES.get(str(node.get("type") or ""))
        if key is None:
            continue
        if _set_node_mode(node, policy[key]):
            changes["applied"][f"node:{node.get('id')}:{key}"] = {
                "enabled": policy[key],
                "mode": MODE_ACTIVE if policy[key] else MODE_BYPASS,
            }

    if not changes["applied"]:
        changes.pop("applied", None)
    if changes.get("profile_id") is None:
        changes.pop("profile_id", None)
    return changes


def apply_shape_editorial_api(
    prompt: dict[str, Any],
    shape: dict[str, Any],
    job: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    _ = (prompt, shape, job)
    return {}


# Back-compat aliases while callers migrate off the interim postprocess name.
apply_shape_postprocess_ui = apply_shape_editorial_ui
apply_shape_postprocess_api = apply_shape_editorial_api
infer_postprocess_from_workflow = infer_editorial_from_workflow
resolve_postprocess = resolve_editorial
