"""Apply shape-level delivery postprocess policy (ColorMatch, upscale, RIFE).

Used only on denouement delivery shapes — not on generation workflows.
"""

from __future__ import annotations

from typing import Any, Optional

DELIVERY_KEYS = ("color_match", "upscale", "interpolate")

DELIVERY_NODE_TYPES: dict[str, str] = {
    "ColorMatch": "color_match",
    "ImageUpscaleWithModel": "upscale",
    "UpscaleModelLoader": "upscale",
    "RIFE VFI": "interpolate",
}

DEFAULT_DELIVERY: dict[str, bool] = {
    "color_match": False,
    "upscale": False,
    "interpolate": False,
}

MODE_ACTIVE = 0
MODE_BYPASS = 2


def shape_delivery_raw(shape: dict[str, Any]) -> dict[str, Any]:
    raw = shape.get("delivery")
    return raw if isinstance(raw, dict) else {}


def resolve_delivery(
    shape: dict[str, Any],
    job: Optional[dict[str, Any]] = None,
) -> dict[str, bool]:
    out = dict(DEFAULT_DELIVERY)
    raw = shape_delivery_raw(shape)
    for key in DELIVERY_KEYS:
        if key in raw:
            out[key] = bool(raw[key])
    if job:
        adhoc = job.get("adhoc_overrides")
        if isinstance(adhoc, dict):
            delivery = adhoc.get("delivery")
            if isinstance(delivery, dict):
                for key in DELIVERY_KEYS:
                    if key in delivery:
                        out[key] = bool(delivery[key])
    return out


def infer_delivery_from_workflow(workflow: dict[str, Any]) -> dict[str, bool]:
    out = dict(DEFAULT_DELIVERY)
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        key = DELIVERY_NODE_TYPES.get(str(node.get("type") or ""))
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


def apply_shape_delivery_ui(
    workflow: dict[str, Any],
    shape: dict[str, Any],
    job: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    raw = shape_delivery_raw(shape)
    job_delivery = None
    if job and isinstance(job.get("adhoc_overrides"), dict):
        job_delivery = job["adhoc_overrides"].get("delivery")
    if not raw and not isinstance(job_delivery, dict):
        return {}

    policy = resolve_delivery(shape, job)
    changes: dict[str, Any] = {"profile_id": raw.get("profile_id"), "applied": {}}

    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        key = DELIVERY_NODE_TYPES.get(str(node.get("type") or ""))
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


def apply_shape_delivery_api(
    prompt: dict[str, Any],
    shape: dict[str, Any],
    job: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    _ = (prompt, shape, job)
    return {}
