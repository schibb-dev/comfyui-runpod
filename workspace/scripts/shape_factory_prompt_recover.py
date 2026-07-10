#!/usr/bin/env python3
"""Recover prompt_profile JSON from a generated UI workflow when pool files are missing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


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


def widget_text(workflow: dict[str, Any], node_id: int, widget_index: int = 0) -> str:
    node = find_ui_node(workflow, node_id)
    if not node:
        return ""
    widgets = node.get("widgets_values")
    if isinstance(widgets, list):
        if not widgets:
            return ""
        idx = min(max(0, widget_index), len(widgets) - 1)
        return str(widgets[idx] or "")
    if isinstance(widgets, dict):
        return str(widgets.get("text") or widgets.get("value") or "")
    return ""


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
    positive = widget_text(
        workflow,
        int(pos_spec.get("node_id") or 0),
        int(pos_spec.get("widget_index") or 0),
    )
    negative = widget_text(
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
    workflow = load_workflow_json(Path(wf_path))
    if workflow is None:
        raise ValueError(f"cannot recover prompt_profile: workflow missing or invalid: {wf_path}")
    label = str(job.get("job_key") or Path(wf_path).stem or "job")
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
        p = Path(raw).expanduser()
        if p.is_file():
            out["prompt_profile"] = str(p.resolve())
            return out, None
        # Also try under data_root if relative-ish
        alt = data_root / raw.lstrip("/")
        if alt.is_file():
            out["prompt_profile"] = str(alt.resolve())
            return out, None

    if job is None:
        raise ValueError(
            f"prompt_profile missing or not found ({raw or 'unset'}) and no parent job to recover from"
        )
    recovered = recover_prompt_profile_for_job(job, shape=shape, data_root=data_root, family=family)
    out["prompt_profile"] = str(recovered.resolve())
    return out, str(recovered.resolve())
