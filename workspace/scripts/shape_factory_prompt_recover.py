#!/usr/bin/env python3
"""Recover prompt_profile JSON from a generated UI workflow when pool files are missing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple


def prompt_binding_from_shape(shape: dict[str, Any]) -> Optional[dict[str, Any]]:
    for req in shape.get("requires") or []:
        if not isinstance(req, dict):
            continue
        binding = req.get("binding") if isinstance(req.get("binding"), dict) else {}
        if str(binding.get("type") or "") == "prompt_bundle":
            return binding
    return None


def find_ui_node(workflow: dict[str, Any], node_id: int) -> Optional[dict[str, Any]]:
    for node in workflow.get("nodes") or []:
        if isinstance(node, dict) and int(node.get("id") or -1) == int(node_id):
            return node
    return None


def _links_by_id(workflow: dict[str, Any]) -> Dict[int, Any]:
    out: Dict[int, Any] = {}
    for link in workflow.get("links") or []:
        if isinstance(link, (list, tuple)) and len(link) >= 5:
            try:
                out[int(link[0])] = link
            except Exception:
                continue
        elif isinstance(link, dict):
            try:
                out[int(link.get("id"))] = link
            except Exception:
                continue
    return out


def _origin_node_id(link: Any) -> Optional[int]:
    if isinstance(link, (list, tuple)) and len(link) >= 2:
        try:
            return int(link[1])
        except Exception:
            return None
    if isinstance(link, dict):
        for key in ("origin_id", "from", "from_node", "src_node_id"):
            if link.get(key) is not None:
                try:
                    return int(link[key])
                except Exception:
                    return None
    return None


def text_input_link_id(node: dict[str, Any]) -> Optional[int]:
    """Return the LiteGraph link id for a node's ``text`` input, if connected."""
    for inp in node.get("inputs") or []:
        if not isinstance(inp, dict):
            continue
        if str(inp.get("name") or "") != "text":
            continue
        if inp.get("link") is None:
            return None
        try:
            return int(inp["link"])
        except Exception:
            return None
    return None


def _widgets_text(node: dict[str, Any], widget_index: int = 0) -> str:
    widgets = node.get("widgets_values")
    if isinstance(widgets, list):
        if not widgets:
            return ""
        idx = min(max(0, widget_index), len(widgets) - 1)
        return str(widgets[idx] or "")
    if isinstance(widgets, dict):
        return str(widgets.get("text") or widgets.get("value") or "")
    return ""


def resolve_node_text(
    workflow: dict[str, Any],
    node_id: int,
    widget_index: int = 0,
    *,
    _seen: Optional[Set[int]] = None,
) -> str:
    """
    Resolve authoritative text for a UI node.

    If the node's ``text`` input is linked, follow the link to the upstream node.
    Never return a linked node's leftover ``widgets_values`` default — that string is
    unused at runtime and must not be treated as prompt data.
    """
    node = find_ui_node(workflow, int(node_id))
    if not node:
        return ""
    seen = _seen if _seen is not None else set()
    nid = int(node_id)
    if nid in seen:
        return ""
    seen.add(nid)

    link_id = text_input_link_id(node)
    if link_id is not None:
        link = _links_by_id(workflow).get(link_id)
        src_id = _origin_node_id(link) if link is not None else None
        if src_id is None:
            # Linked but unresolved: do not fall back to stale widget defaults.
            return ""
        return resolve_node_text(workflow, src_id, widget_index=0, _seen=seen)

    return _widgets_text(node, widget_index)


def resolve_text_owner_node_id(
    workflow: dict[str, Any],
    node_id: int,
    *,
    _seen: Optional[Set[int]] = None,
) -> Optional[int]:
    """
    Return the node id that owns live text for ``node_id``.

    Follows ``text`` links; returns None if the chain is broken or cyclic.
    Linked nodes are never treated as owners of their leftover widget defaults.
    """
    node = find_ui_node(workflow, int(node_id))
    if not node:
        return None
    seen = _seen if _seen is not None else set()
    nid = int(node_id)
    if nid in seen:
        return None
    seen.add(nid)

    link_id = text_input_link_id(node)
    if link_id is None:
        return nid
    link = _links_by_id(workflow).get(link_id)
    src_id = _origin_node_id(link) if link is not None else None
    if src_id is None:
        return None
    return resolve_text_owner_node_id(workflow, src_id, _seen=seen)


def widget_text(workflow: dict[str, Any], node_id: int, widget_index: int = 0) -> str:
    """Resolve prompt text for a node; never use unused linked-widget defaults."""
    return resolve_node_text(workflow, node_id, widget_index=widget_index)


def extract_prompt_texts_from_ui_workflow(
    workflow: dict[str, Any],
    shape: dict[str, Any],
) -> Tuple[str, str]:
    """Return (positive, negative) from shape prompt_bundle node/widget indices."""
    binding = prompt_binding_from_shape(shape)
    if not binding:
        return "", ""
    pos_spec = binding.get("positive") if isinstance(binding.get("positive"), dict) else {}
    neg_spec = binding.get("negative") if isinstance(binding.get("negative"), dict) else {}
    positive = resolve_node_text(
        workflow,
        int(pos_spec.get("node_id") or 0),
        int(pos_spec.get("widget_index") or 0),
    )
    negative = resolve_node_text(
        workflow,
        int(neg_spec.get("node_id") or 0),
        int(neg_spec.get("widget_index") or 0),
    )
    return positive, negative


def write_replay_prompt_profile(
    *,
    family: str,
    data_root: Path,
    label: str,
    positive: str,
    negative: str,
) -> Path:
    replay_dir = data_root / "pools" / family / "prompts" / "_replay"
    replay_dir.mkdir(parents=True, exist_ok=True)
    slug = hashlib.sha256(f"{label}\n{positive}\n{negative}".encode("utf-8")).hexdigest()[:12]
    path = replay_dir / f"{slug}.json"
    if not path.is_file():
        doc = {"label": label, "positive": positive, "negative": negative}
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_workflow_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except Exception:
        return None


def _resolve_existing_asset(path_raw: str, *, data_root: Path) -> Optional[Path]:
    """Resolve host/container path aliases to an on-disk file, if present."""
    raw = str(path_raw or "").strip()
    if not raw:
        return None
    direct = Path(raw).expanduser()
    if direct.is_file():
        return direct.resolve()
    alt = data_root / raw.lstrip("/")
    if alt.is_file():
        return alt.resolve()
    try:
        from shape_factory import default_workspace_root, resolve_job_asset_path  # type: ignore

        return resolve_job_asset_path(
            raw,
            data_root=data_root,
            workspace_root=default_workspace_root(),
        )
    except Exception:
        pass
    # Common host→container remap when shape_factory helpers are unavailable.
    text = raw.replace("\\", "/")
    for host_prefix, cont_prefix in (
        ("/home/yuji/comfyui-runpod-data/comfyui_user/", "/workspace/comfyui_user/"),
        ("/home/yuji/src/comfyui-runpod/.data/", "/workspace/.data/"),
        ("/home/yuji/comfyui-runpod-data/output/", "/workspace/output/"),
    ):
        if text.startswith(host_prefix):
            cand = Path(cont_prefix + text[len(host_prefix) :])
            if cand.is_file():
                return cand.resolve()
    return None


def recover_prompt_profile_path(
    *,
    family: str,
    data_root: Path,
    shape: dict[str, Any],
    workflow: dict[str, Any],
    label: str,
) -> Path:
    """Extract prompt text from a UI workflow and write a durable `_replay` profile."""
    positive, negative = extract_prompt_texts_from_ui_workflow(workflow, shape)
    if not positive.strip():
        raise ValueError("cannot recover prompt_profile: empty positive text in generated workflow")
    return write_replay_prompt_profile(
        family=family,
        data_root=data_root,
        label=label,
        positive=positive,
        negative=negative,
    )


def recover_prompt_profile_for_job(
    job: dict[str, Any],
    *,
    shape: dict[str, Any],
    data_root: Path,
    family: Optional[str] = None,
) -> Path:
    """Recover prompt_profile from job.generated_workflow_path (hard error if impossible)."""
    fam = str(family or job.get("family_slug") or shape.get("family_slug") or "").strip()
    if not fam:
        raise ValueError("cannot recover prompt_profile: missing family_slug")
    wf_path = str(job.get("generated_workflow_path") or "").strip()
    if not wf_path:
        raise ValueError("cannot recover prompt_profile: job has no generated_workflow_path")
    resolved = _resolve_existing_asset(wf_path, data_root=data_root)
    if resolved is None:
        raise ValueError(f"cannot recover prompt_profile: workflow missing or invalid: {wf_path}")
    workflow = load_workflow_json(resolved)
    if workflow is None:
        raise ValueError(f"cannot recover prompt_profile: workflow missing or invalid: {resolved}")
    label = str(job.get("job_key") or resolved.stem or "job")
    return recover_prompt_profile_path(
        family=fam,
        data_root=data_root,
        shape=shape,
        workflow=workflow,
        label=label,
    )


def resolve_or_recover_prompt_profile_binding(
    bindings: Dict[str, str],
    *,
    job: Optional[dict[str, Any]],
    shape: dict[str, Any],
    data_root: Path,
    family: str,
) -> Tuple[Dict[str, str], Optional[str]]:
    """
    Ensure ``prompt_profile`` in bindings points at an existing file.

    Returns (updated_bindings, recovered_path_or_None).
    Raises ValueError when the profile is missing and cannot be recovered.
    """
    out = dict(bindings)
    raw = str(out.get("prompt_profile") or "").strip()
    if raw:
        found = _resolve_existing_asset(raw, data_root=data_root)
        if found is not None:
            out["prompt_profile"] = str(found)
            return out, None

    if job is None:
        raise ValueError(
            f"prompt_profile missing or not found ({raw or 'unset'}) and no parent job to recover from"
        )
    recovered = recover_prompt_profile_for_job(job, shape=shape, data_root=data_root, family=family)
    out["prompt_profile"] = str(recovered.resolve())
    return out, str(recovered.resolve())
