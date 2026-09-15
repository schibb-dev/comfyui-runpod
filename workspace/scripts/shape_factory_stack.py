"""Named generation stacks: UNet family+quant, matching TeaCache coeffs, virt VRAM.

A stack is not a workflow and not a family. Apply by node type so the same
``i2v-720p-Q5`` file works on Kneel (node 458) and any other I2V graph.
CLIP filename is patched only when the loader class matches ``clip_loader_type``.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

STACKS_DIR = Path(__file__).resolve().parents[2] / ".data" / "stacks"
STACK_SCHEMA = "comfyui-runpod.stack.v0"

_UNET_TYPES = {
    "UnetLoaderGGUFDisTorchMultiGPU",
    "UnetLoaderGGUF",
    "UNETLoader",
    "UNETLoaderKJ",
}
_TEA_TYPES = {"WanVideoTeaCacheKJ"}
_SIZE_RE = re.compile(r"(720p|480p)", re.I)
_COEFF_SIZE_RE = re.compile(r"^i2v_(720|480)$", re.I)

_UNET_WIDGET = {"unet_name": 0, "virtual_vram_gb": 2}
_TEA_WIDGET = {"coefficients": 4}
_CLIP_WIDGET = {"clip_name": 0}


def default_stacks_dir() -> Path:
    return STACKS_DIR


def stack_id_from_shape(shape: Dict[str, Any], job: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Job override, then shape ``stack:`` (string or ``{id:}``)."""
    if isinstance(job, dict):
        adhoc = job.get("adhoc_overrides")
        if isinstance(adhoc, dict):
            raw = adhoc.get("stack") or adhoc.get("stack_id")
            sid = _as_stack_id(raw)
            if sid:
                return sid
        sid = _as_stack_id(job.get("stack_id") or job.get("stack"))
        if sid:
            return sid
    if not isinstance(shape, dict):
        return None
    return _as_stack_id(shape.get("stack"))


def _as_stack_id(raw: Any) -> Optional[str]:
    if isinstance(raw, dict):
        text = str(raw.get("id") or raw.get("stack_id") or "").strip()
        return text or None
    text = str(raw or "").strip()
    return text or None


def load_stack(
    stack_id: str,
    *,
    stacks_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    sid = str(stack_id or "").strip()
    if not sid:
        raise ValueError("empty stack_id")
    root = Path(stacks_dir) if stacks_dir is not None else default_stacks_dir()
    path = root / f"{sid}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"stack not found: {path}")
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"stack {sid!r} is not a mapping")
    doc = copy.deepcopy(doc)
    doc.setdefault("stack_id", sid)
    if str(doc.get("stack_id") or "").strip() != sid:
        raise ValueError(f"stack file {path.name} stack_id {doc.get('stack_id')!r} != {sid!r}")
    errs = validate_stack(doc)
    if errs:
        raise ValueError(f"invalid stack {sid}: " + "; ".join(errs))
    return doc


def validate_stack(doc: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if not str(doc.get("stack_id") or "").strip():
        errors.append("missing stack_id")
    unet = str(doc.get("unet_name") or "").strip()
    if not unet:
        errors.append("missing unet_name")
    coeffs = str(doc.get("teacache_coefficients") or "").strip()
    if not coeffs:
        errors.append("missing teacache_coefficients")
    declared = _size_token(doc.get("training_size"))
    from_unet = _size_token(unet)
    from_coeff = _coeff_size(coeffs)
    if from_unet is None:
        errors.append("unet_name must include 720p or 480p")
    if from_coeff is None:
        errors.append("teacache_coefficients must be i2v_720 or i2v_480")
    if declared and from_unet and declared != from_unet:
        errors.append(f"training_size {declared} disagrees with unet_name {unet}")
    if from_unet and from_coeff and from_unet != from_coeff:
        errors.append(
            f"teacache_coefficients {coeffs} does not match UNet family {from_unet} "
            "(720p↔i2v_720, 480p↔i2v_480)"
        )
    virt = doc.get("virtual_vram_gb")
    if virt is not None:
        try:
            float(virt)
        except (TypeError, ValueError):
            errors.append(f"virtual_vram_gb is not a number: {virt!r}")
    return errors


def _size_token(raw: Any) -> Optional[str]:
    m = _SIZE_RE.search(str(raw or ""))
    return m.group(1).lower() if m else None


def _coeff_size(raw: Any) -> Optional[str]:
    m = _COEFF_SIZE_RE.match(str(raw or "").strip())
    return f"{m.group(1)}p".lower() if m else None


def resolve_stack(
    shape: Dict[str, Any],
    job: Optional[Dict[str, Any]] = None,
    *,
    stacks_dir: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    sid = stack_id_from_shape(shape, job)
    if not sid:
        return None
    return load_stack(sid, stacks_dir=stacks_dir)


def stack_job_fields(stack: Dict[str, Any]) -> Dict[str, Any]:
    """Compact stamp for job.json."""
    virt = stack.get("virtual_vram_gb")
    try:
        virt_n = float(virt) if virt is not None else None
    except (TypeError, ValueError):
        virt_n = None
    out: Dict[str, Any] = {
        "stack_id": str(stack.get("stack_id") or "").strip(),
        "unet_name": str(stack.get("unet_name") or "").strip(),
        "teacache_coefficients": str(stack.get("teacache_coefficients") or "").strip(),
    }
    if str(stack.get("quant") or "").strip():
        out["quant"] = str(stack.get("quant")).strip()
    size = _size_token(stack.get("training_size")) or _size_token(stack.get("unet_name"))
    if size:
        out["training_size"] = size
    if virt_n is not None:
        out["virtual_vram_gb"] = virt_n
    clip = str(stack.get("clip_name") or "").strip()
    if clip:
        out["clip_name"] = clip
        loader = str(stack.get("clip_loader_type") or "").strip()
        if loader:
            out["clip_loader_type"] = loader
    return out


def stamp_job_stack(job_meta: Dict[str, Any], stack: Optional[Dict[str, Any]]) -> None:
    if not isinstance(job_meta, dict) or not isinstance(stack, dict):
        return
    fields = stack_job_fields(stack)
    sid = fields.get("stack_id")
    if not sid:
        return
    job_meta["stack_id"] = sid
    job_meta["stack"] = fields


def apply_shape_stack_ui(
    workflow: dict[str, Any],
    shape: dict[str, Any],
    job: Optional[dict[str, Any]] = None,
    *,
    stacks_dir: Optional[Path] = None,
) -> dict[str, Any]:
    stack = resolve_stack(shape, job, stacks_dir=stacks_dir)
    if not stack:
        return {}
    changes = apply_stack_ui(workflow, stack)
    changes["stack_id"] = stack["stack_id"]
    changes["stack"] = stack_job_fields(stack)
    return changes


def apply_shape_stack_api(
    prompt: dict[str, Any],
    shape: dict[str, Any],
    job: Optional[dict[str, Any]] = None,
    *,
    stacks_dir: Optional[Path] = None,
) -> dict[str, Any]:
    stack = resolve_stack(shape, job, stacks_dir=stacks_dir)
    if not stack:
        return {}
    changes = apply_stack_api(prompt, stack)
    changes["stack_id"] = stack["stack_id"]
    changes["stack"] = stack_job_fields(stack)
    return changes


def apply_stack_ui(workflow: dict[str, Any], stack: dict[str, Any]) -> dict[str, Any]:
    patched: list[dict[str, Any]] = []
    unet = str(stack.get("unet_name") or "").strip()
    virt = stack.get("virtual_vram_gb")
    coeffs = str(stack.get("teacache_coefficients") or "").strip()
    clip_name = str(stack.get("clip_name") or "").strip()
    clip_type = str(stack.get("clip_loader_type") or "").strip()
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        ntype = str(node.get("type") or "")
        nid = node.get("id")
        if ntype in _UNET_TYPES:
            entry: dict[str, Any] = {"node_id": nid, "type": ntype}
            if unet and _set_widget(node, "unet_name", unet, fallback_index=_UNET_WIDGET["unet_name"]):
                entry["unet_name"] = unet
            if virt is not None and _set_widget(
                node, "virtual_vram_gb", _as_number(virt), fallback_index=_UNET_WIDGET["virtual_vram_gb"]
            ):
                entry["virtual_vram_gb"] = _as_number(virt)
            if len(entry) > 2:
                patched.append(entry)
        elif ntype in _TEA_TYPES and coeffs:
            if _set_widget(node, "coefficients", coeffs, fallback_index=_TEA_WIDGET["coefficients"]):
                patched.append({"node_id": nid, "type": ntype, "coefficients": coeffs})
        elif clip_name and clip_type and ntype == clip_type:
            if _set_widget(node, "clip_name", clip_name, fallback_index=_CLIP_WIDGET["clip_name"]):
                patched.append({"node_id": nid, "type": ntype, "clip_name": clip_name})
    return {"ui_nodes": patched}


def apply_stack_api(prompt: dict[str, Any], stack: dict[str, Any]) -> dict[str, Any]:
    patched: list[dict[str, Any]] = []
    unet = str(stack.get("unet_name") or "").strip()
    virt = stack.get("virtual_vram_gb")
    coeffs = str(stack.get("teacache_coefficients") or "").strip()
    clip_name = str(stack.get("clip_name") or "").strip()
    clip_type = str(stack.get("clip_loader_type") or "").strip()
    for key, node in (prompt or {}).items():
        if not isinstance(node, dict):
            continue
        ntype = str(node.get("class_type") or "")
        inputs = node.setdefault("inputs", {})
        if not isinstance(inputs, dict):
            continue
        entry: dict[str, Any] = {"node_id": str(key), "type": ntype}
        if ntype in _UNET_TYPES:
            if unet:
                inputs["unet_name"] = unet
                entry["unet_name"] = unet
            if virt is not None:
                inputs["virtual_vram_gb"] = _as_number(virt)
                entry["virtual_vram_gb"] = _as_number(virt)
        elif ntype in _TEA_TYPES and coeffs:
            inputs["coefficients"] = coeffs
            entry["coefficients"] = coeffs
        elif clip_name and clip_type and ntype == clip_type:
            inputs["clip_name"] = clip_name
            entry["clip_name"] = clip_name
        if len(entry) > 2:
            patched.append(entry)
    return {"api_nodes": patched}


def _as_number(raw: Any) -> float | int:
    n = float(raw)
    if n.is_integer():
        return int(n)
    return n


def _widget_names(node: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for inp in node.get("inputs") or []:
        if not isinstance(inp, dict):
            continue
        widget = inp.get("widget")
        if not widget:
            continue
        names.append(str(inp.get("name") or (widget.get("name") if isinstance(widget, dict) else "") or ""))
    return names


def _set_widget(node: dict[str, Any], name: str, value: Any, *, fallback_index: int) -> bool:
    wv = node.get("widgets_values")
    if isinstance(wv, dict):
        wv[name] = value
        return True
    if not isinstance(wv, list):
        node["widgets_values"] = [value]
        return True
    names = _widget_names(node)
    if name in names:
        idx = names.index(name)
        while len(wv) <= idx:
            wv.append(None)
        wv[idx] = value
        return True
    if fallback_index >= 0:
        while len(wv) <= fallback_index:
            wv.append(None)
        wv[fallback_index] = value
        return True
    return False
