"""Job-owned Power LoRA stack: template seed vs instance snowflake.

Mirrors owned-params for the primary ``Power Lora Loader (rgthree)`` node.
Entries are on/off + strengths for existing slots — no topology changes.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

POWER_LORA_TYPE = "Power Lora Loader (rgthree)"


class OwnedLorasFrozenError(RuntimeError):
    pass


def _coerce_float(raw: Any) -> Optional[float]:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _coerce_bool(raw: Any) -> Optional[bool]:
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def normalize_lora_entry(raw: Any) -> Optional[Dict[str, Any]]:
    """Return a compact entry, or None if the slot has no LoRA name."""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("lora") or "").strip()
    if not name:
        return None
    on = _coerce_bool(raw.get("on"))
    strength = _coerce_float(raw.get("strength"))
    strength_two = _coerce_float(raw.get("strengthTwo"))
    out: Dict[str, Any] = {"lora": name, "on": True if on is None else bool(on)}
    if strength is not None:
        out["strength"] = strength
    if strength_two is not None:
        out["strengthTwo"] = strength_two
    return out


def normalize_entries(entries: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(entries, list):
        return out
    for raw in entries:
        row = normalize_lora_entry(raw)
        if row:
            out.append(row)
    return out


def entries_content_hash(entries: List[Dict[str, Any]]) -> str:
    payload = []
    for e in normalize_entries(entries):
        payload.append(
            {
                "lora": e.get("lora"),
                "on": bool(e.get("on")),
                "strength": e.get("strength"),
                "strengthTwo": e.get("strengthTwo"),
            }
        )
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def entries_equal(a: Any, b: Any) -> bool:
    return normalize_entries(a) == normalize_entries(b)


def _is_power_lora_type(ntype: str) -> bool:
    t = str(ntype or "").strip().lower()
    return "power lora loader" in t


def find_power_lora_node(workflow: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(workflow, dict):
        return None
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        return None
    best: Optional[Dict[str, Any]] = None
    best_count = -1
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if not _is_power_lora_type(str(node.get("type") or node.get("class_type") or "")):
            continue
        widgets = node.get("widgets_values")
        count = 0
        if isinstance(widgets, list):
            count = sum(1 for w in widgets if isinstance(w, dict) and str(w.get("lora") or "").strip())
        elif isinstance(widgets, dict):
            count = sum(
                1
                for k, v in widgets.items()
                if str(k).lower().startswith("lora_") and isinstance(v, dict) and str(v.get("lora") or "").strip()
            )
        if count > best_count:
            best = node
            best_count = count
    return best


def extract_loras_from_workflow(workflow: Optional[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Optional[int]]:
    """Return (named entries, node_id) from the primary Power Lora Loader."""
    node = find_power_lora_node(workflow)
    if node is None:
        return [], None
    try:
        node_id = int(node.get("id"))
    except (TypeError, ValueError):
        node_id = None
    widgets = node.get("widgets_values")
    entries: List[Dict[str, Any]] = []
    if isinstance(widgets, list):
        for item in widgets:
            row = normalize_lora_entry(item)
            if row:
                entries.append(row)
    elif isinstance(widgets, dict):
        keys = sorted(
            (k for k in widgets.keys() if str(k).lower().startswith("lora_")),
            key=lambda k: int("".join(ch for ch in str(k) if ch.isdigit()) or "0"),
        )
        for k in keys:
            row = normalize_lora_entry(widgets.get(k))
            if row:
                entries.append(row)
    return entries, node_id


def extract_loras_from_api_prompt(prompt: Optional[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Extract Power LoRA slots from a Comfy API prompt map (node_id → {class_type, inputs})."""
    if not isinstance(prompt, dict):
        return [], None
    best_id: Optional[str] = None
    best: List[Dict[str, Any]] = []
    for nid, node in prompt.items():
        if not isinstance(node, dict):
            continue
        ct = str(node.get("class_type") or "")
        if not _is_power_lora_type(ct):
            continue
        ins = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        entries: List[Dict[str, Any]] = []
        keys = sorted(
            (k for k in ins.keys() if str(k).lower().startswith("lora_")),
            key=lambda k: int("".join(ch for ch in str(k) if ch.isdigit()) or "0"),
        )
        for k in keys:
            row = normalize_lora_entry(ins.get(k))
            if row:
                entries.append(row)
        if len(entries) >= len(best):
            best = entries
            best_id = str(nid)
    return best, best_id


def get_owned_loras(job: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    raw = job.get("loras")
    if not isinstance(raw, dict):
        return None
    return raw


def attach_content_hash(owned: Dict[str, Any]) -> Dict[str, Any]:
    entries = normalize_entries(owned.get("entries"))
    owned["entries"] = entries
    owned["content_hash"] = entries_content_hash(entries)
    return owned


def freeze_owned_loras(job: Dict[str, Any], *, at: Optional[str] = None) -> bool:
    owned = get_owned_loras(job)
    if owned is None:
        return False
    owned["frozen"] = True
    if at:
        owned["frozen_at"] = at
    attach_content_hash(owned)
    job["loras"] = owned
    return True


def ensure_owned_loras_from_workflow(
    job: Dict[str, Any],
    *,
    data_root: Path,
    workflow: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Seed ``job["loras"]`` from generated/template workflow when missing."""
    existing = get_owned_loras(job)
    if existing is not None:
        if not existing.get("content_hash"):
            attach_content_hash(existing)
        job["loras"] = existing
        return existing

    wf = workflow
    if wf is None:
        try:
            from shape_factory import ensure_job_workflow_path, read_json  # type: ignore

            wp = ensure_job_workflow_path(job, data_root=Path(data_root))
            if wp.is_file():
                loaded = read_json(wp)
                if isinstance(loaded, dict):
                    wf = loaded
        except Exception:
            wf = None
    if wf is None:
        seed, template_path, node_id = load_template_lora_seed(job, data_root=Path(data_root))
        if not seed:
            return None
        owned = {
            "node_id": node_id,
            "source_template": template_path,
            "frozen": False,
            "entries": seed,
        }
        attach_content_hash(owned)
        job["loras"] = owned
        return owned

    entries, node_id = extract_loras_from_workflow(wf)
    if not entries:
        return None
    owned = {
        "node_id": node_id,
        "frozen": False,
        "entries": entries,
    }
    attach_content_hash(owned)
    job["loras"] = owned
    return owned


def merge_owned_loras(job: Dict[str, Any], entries: List[Dict[str, Any]], *, node_id: Any = None) -> Dict[str, Any]:
    owned = get_owned_loras(job)
    if owned is None:
        owned = {"frozen": False, "entries": []}
    if owned.get("frozen"):
        raise OwnedLorasFrozenError("owned loras are frozen (job already on Comfy)")
    cleaned = normalize_entries(entries)
    owned["entries"] = cleaned
    if node_id is not None:
        try:
            owned["node_id"] = int(node_id)
        except (TypeError, ValueError):
            pass
    attach_content_hash(owned)
    job["loras"] = owned
    return owned


def load_template_lora_seed(
    job: Dict[str, Any],
    *,
    data_root: Path,
) -> Tuple[List[Dict[str, Any]], Optional[str], Optional[int]]:
    """Return (seed entries, template_path, node_id)."""
    from shape_factory import load_yaml, read_json, resolve_job_asset_path  # type: ignore

    data_root = Path(data_root).expanduser().resolve()
    workspace_root = data_root.parent if data_root.name == ".data" else data_root
    shape_path_raw = str(job.get("shape_path") or "").strip()
    shape: Dict[str, Any] = {}
    if shape_path_raw:
        sp = resolve_job_asset_path(shape_path_raw, data_root=data_root, workspace_root=workspace_root)
        if sp is not None and Path(sp).is_file():
            try:
                loaded = load_yaml(Path(sp)) if str(sp).endswith((".yaml", ".yml")) else read_json(Path(sp))
                if isinstance(loaded, dict):
                    shape = loaded
            except Exception:
                shape = {}

    template_raw = str(job.get("template_path") or shape.get("template") or "").strip()
    if not template_raw:
        return [], None, None
    template_path = resolve_job_asset_path(template_raw, data_root=data_root, workspace_root=workspace_root)
    if template_path is None or not Path(template_path).is_file():
        return [], template_raw, None
    try:
        workflow = read_json(Path(template_path))
    except Exception:
        return [], str(template_path), None
    entries, node_id = extract_loras_from_workflow(workflow if isinstance(workflow, dict) else None)
    return entries, str(template_path), node_id


def extract_job_current_loras(
    job: Dict[str, Any],
    job_path: Optional[Path] = None,
    *,
    data_root: Optional[Path] = None,
) -> Tuple[List[Dict[str, Any]], Optional[int]]:
    owned = get_owned_loras(job)
    if owned is not None:
        entries = normalize_entries(owned.get("entries"))
        node_id = owned.get("node_id")
        try:
            nid = int(node_id) if node_id is not None else None
        except (TypeError, ValueError):
            nid = None
        if entries:
            return entries, nid

    root = Path(data_root).expanduser().resolve() if data_root else None
    if root is None and job_path is not None:
        cand = Path(job_path).resolve()
        for parent in cand.parents:
            if parent.name == ".data":
                root = parent
                break
    if root is not None:
        try:
            from shape_factory import ensure_job_workflow_path, read_json  # type: ignore

            wp = ensure_job_workflow_path(job, data_root=root)
            if wp.is_file():
                wf = read_json(wp)
                return extract_loras_from_workflow(wf if isinstance(wf, dict) else None)
        except Exception:
            pass
    return [], None


def owned_loras_to_profile(
    job: Dict[str, Any],
    *,
    data_root: Path,
    job_path: Optional[Path] = None,
) -> Dict[str, Any]:
    current, cur_nid = extract_job_current_loras(job, job_path, data_root=data_root)
    seed, template_path, seed_nid = load_template_lora_seed(job, data_root=data_root)
    snowflake = bool(seed) and bool(current) and not entries_equal(current, seed)
    # Also snowflake when job has entries and template has none (or vice versa with named jobs).
    if bool(current) != bool(seed) and (current or seed):
        # Only when we have a template path to compare against.
        if template_path:
            snowflake = True
    return {
        "current": current,
        "seed": seed,
        "snowflake": snowflake,
        "node_id": cur_nid if cur_nid is not None else seed_nid,
        "template_path": template_path,
        "content_hash": entries_content_hash(current) if current else None,
        "seed_hash": entries_content_hash(seed) if seed else None,
        "frozen": bool((get_owned_loras(job) or {}).get("frozen")),
    }


def patch_power_lora_widgets(
    workflow: Dict[str, Any],
    entries: List[Dict[str, Any]],
    *,
    node_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Apply named LoRA entries onto the Power Lora Loader widgets (by slot order).

    Existing slot count is preserved: we update matching indices that already hold
    dict slots (or the first N dict slots). Extra named entries beyond slot count
    are ignored (no topology growth).
    """
    node = None
    if node_id is not None:
        for n in workflow.get("nodes") or []:
            if not isinstance(n, dict):
                continue
            try:
                if int(n.get("id")) == int(node_id) and _is_power_lora_type(str(n.get("type") or "")):
                    node = n
                    break
            except (TypeError, ValueError):
                continue
    if node is None:
        node = find_power_lora_node(workflow)
    if node is None:
        return {"ok": False, "error": "power_lora_node_missing", "changed": 0}

    cleaned = normalize_entries(entries)
    widgets = node.get("widgets_values")
    changed = 0
    if isinstance(widgets, list):
        slot_idxs = [i for i, w in enumerate(widgets) if isinstance(w, dict)]
        for i, entry in enumerate(cleaned):
            if i >= len(slot_idxs):
                break
            idx = slot_idxs[i]
            prev = widgets[idx] if isinstance(widgets[idx], dict) else {}
            nxt = dict(prev)
            nxt["lora"] = entry["lora"]
            nxt["on"] = bool(entry.get("on", True))
            if "strength" in entry:
                nxt["strength"] = entry["strength"]
            if "strengthTwo" in entry:
                nxt["strengthTwo"] = entry["strengthTwo"]
            if nxt != prev:
                widgets[idx] = nxt
                changed += 1
        # Turn off remaining named slots beyond provided entries (keep name).
        for j in range(len(cleaned), len(slot_idxs)):
            idx = slot_idxs[j]
            prev = widgets[idx] if isinstance(widgets[idx], dict) else {}
            if not str(prev.get("lora") or "").strip():
                continue
            if prev.get("on") is False:
                continue
            nxt = dict(prev)
            nxt["on"] = False
            widgets[idx] = nxt
            changed += 1
        node["widgets_values"] = widgets
    elif isinstance(widgets, dict):
        keys = sorted(
            (k for k in widgets.keys() if str(k).lower().startswith("lora_")),
            key=lambda k: int("".join(ch for ch in str(k) if ch.isdigit()) or "0"),
        )
        for i, entry in enumerate(cleaned):
            if i >= len(keys):
                break
            k = keys[i]
            prev = widgets[k] if isinstance(widgets[k], dict) else {}
            nxt = dict(prev)
            nxt["lora"] = entry["lora"]
            nxt["on"] = bool(entry.get("on", True))
            if "strength" in entry:
                nxt["strength"] = entry["strength"]
            if "strengthTwo" in entry:
                nxt["strengthTwo"] = entry["strengthTwo"]
            if nxt != prev:
                widgets[k] = nxt
                changed += 1
        for j in range(len(cleaned), len(keys)):
            k = keys[j]
            prev = widgets[k] if isinstance(widgets[k], dict) else {}
            if not str(prev.get("lora") or "").strip():
                continue
            if prev.get("on") is False:
                continue
            nxt = dict(prev)
            nxt["on"] = False
            widgets[k] = nxt
            changed += 1
        node["widgets_values"] = widgets
    else:
        return {"ok": False, "error": "unsupported_widgets", "changed": 0}

    try:
        nid = int(node.get("id"))
    except (TypeError, ValueError):
        nid = node_id
    return {"ok": True, "changed": changed, "node_id": nid, "entries": cleaned}


def apply_owned_loras_to_workflow(job: Dict[str, Any], workflow: Dict[str, Any]) -> Dict[str, Any]:
    owned = get_owned_loras(job)
    if owned is None:
        return {"ok": True, "skipped": True, "changed": 0}
    entries = normalize_entries(owned.get("entries"))
    if not entries:
        return {"ok": True, "skipped": True, "changed": 0}
    node_id = owned.get("node_id")
    try:
        nid = int(node_id) if node_id is not None else None
    except (TypeError, ValueError):
        nid = None
    return patch_power_lora_widgets(workflow, entries, node_id=nid)


def write_json_with_bak(path: Path, doc: Dict[str, Any]) -> Optional[Path]:
    path = Path(path)
    bak: Optional[Path] = None
    if path.is_file():
        bak = path.with_suffix(path.suffix + ".bak")
        bak.write_bytes(path.read_bytes())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return bak


def promote_loras_to_catalog(
    *,
    data_root: Path,
    job: Dict[str, Any],
    mode: str = "overwrite",
    entries: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Write job (or provided) LoRA stack into the catalog readable."""
    from shape_factory import is_litegraph_workflow, read_json  # type: ignore

    profile = owned_loras_to_profile(job, data_root=data_root)
    template_path_s = str(profile.get("template_path") or job.get("template_path") or "").strip()
    if not template_path_s:
        return {"ok": False, "error": "missing_template"}
    template_path = Path(template_path_s).expanduser()
    if not template_path.is_file():
        return {"ok": False, "error": "template_missing", "path": str(template_path)}

    current = normalize_entries(entries if entries is not None else profile.get("current"))
    if not current:
        return {"ok": False, "error": "no_loras"}

    try:
        workflow = read_json(template_path)
    except Exception as exc:
        return {"ok": False, "error": "template_read_failed", "detail": str(exc)}
    if not is_litegraph_workflow(workflow):
        return {"ok": False, "error": "not_litegraph"}

    node_id = profile.get("node_id")
    try:
        nid = int(node_id) if node_id is not None else None
    except (TypeError, ValueError):
        nid = None
    patch = patch_power_lora_widgets(workflow, current, node_id=nid)
    if not patch.get("ok"):
        return {"ok": False, "error": patch.get("error") or "patch_failed", "detail": patch}

    mode_s = str(mode or "overwrite").strip().lower() or "overwrite"
    if mode_s == "fork":
        from datetime import datetime, timezone

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = template_path.with_name(f"{template_path.stem}-loras-{stamp}{template_path.suffix}")
        dest.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {
            "ok": True,
            "mode": "fork",
            "path": str(dest),
            "bak_path": None,
            "changed": patch.get("changed"),
            "detail": "Forked catalog readable; shape.template not retargeted.",
        }

    bak = write_json_with_bak(template_path, workflow)
    return {
        "ok": True,
        "mode": "overwrite",
        "path": str(template_path),
        "bak_path": str(bak) if bak else None,
        "changed": patch.get("changed"),
    }
