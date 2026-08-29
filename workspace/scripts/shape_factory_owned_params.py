"""Job-owned simple params (Phase B): template seed vs instance snowflake.

Mirrors owned-prompt snowflakes for frames / steps / overlap / seed.
VHS skip/cap stays on the existing trim path.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

PARAM_KEYS = ("frames", "steps", "overlap", "seed")


def _coerce_int(raw: Any) -> Optional[int]:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return int(raw)
    if isinstance(raw, float) and float(raw).is_integer():
        return int(raw)
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _compact(values: Dict[str, Any]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for key in PARAM_KEYS:
        n = _coerce_int(values.get(key))
        if n is not None:
            out[key] = n
    return out


def params_equal(a: Optional[Dict[str, Any]], b: Optional[Dict[str, Any]]) -> bool:
    return _compact(a or {}) == _compact(b or {})


def extract_params_from_workflow(workflow: Optional[Dict[str, Any]]) -> Dict[str, int]:
    if not isinstance(workflow, dict):
        return {}
    from shape_factory import extract_workload_from_workflow  # type: ignore

    wl = extract_workload_from_workflow(workflow)
    out = _compact(wl)
    # Seed from first RandomNoise / KSampler widget when present.
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        ntype = str(node.get("type") or "")
        if ntype not in {"RandomNoise", "KSampler", "KSamplerAdvanced"}:
            continue
        widgets = node.get("widgets_values")
        seed_val: Optional[int] = None
        if isinstance(widgets, list) and widgets:
            seed_val = _coerce_int(widgets[0])
        elif isinstance(widgets, dict):
            seed_val = _coerce_int(widgets.get("noise_seed") if "noise_seed" in widgets else widgets.get("seed"))
        if seed_val is not None:
            out["seed"] = seed_val
            break
    return out


def load_template_param_seed(
    job: Dict[str, Any],
    *,
    data_root: Path,
) -> Tuple[Dict[str, int], Optional[str]]:
    """Return (seed params, template_path) after shape ui_defaults, without job adhoc."""
    from shape_factory import (  # type: ignore
        apply_shape_ui_defaults_ui,
        load_yaml,
        read_json,
        resolve_job_asset_path,
    )

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
        return {}, None
    template_path = resolve_job_asset_path(template_raw, data_root=data_root, workspace_root=workspace_root)
    if template_path is None or not Path(template_path).is_file():
        return {}, template_raw
    try:
        workflow = read_json(Path(template_path))
    except Exception:
        return {}, str(template_path)
    if not isinstance(workflow, dict):
        return {}, str(template_path)
    wf = copy.deepcopy(workflow)
    if shape:
        try:
            apply_shape_ui_defaults_ui(wf, shape)
        except Exception:
            pass
    return extract_params_from_workflow(wf), str(template_path)


def extract_job_current_params(
    job: Dict[str, Any],
    job_path: Optional[Path] = None,
    *,
    data_root: Optional[Path] = None,
) -> Dict[str, int]:
    """Best-effort current frames/steps/overlap/seed on the job instance."""
    out: Dict[str, int] = {}

    timings = job.get("timings") if isinstance(job.get("timings"), dict) else {}
    workload = timings.get("workload") if isinstance(timings.get("workload"), dict) else {}
    out.update(_compact(workload))

    adhoc = job.get("adhoc_overrides") if isinstance(job.get("adhoc_overrides"), dict) else {}
    params = adhoc.get("parameters") if isinstance(adhoc.get("parameters"), dict) else {}
    for key in ("frames", "steps", "overlap"):
        n = _coerce_int(params.get(key))
        if n is not None:
            out[key] = n
    seed_adhoc = _coerce_int(params.get("seed") if params.get("seed") is not None else params.get("noise_seed"))
    if seed_adhoc is not None:
        out["seed"] = seed_adhoc

    dev = job.get("dev_tuning") if isinstance(job.get("dev_tuning"), dict) else {}
    spec = dev.get("spec") if isinstance(dev.get("spec"), dict) else {}
    ui_nodes = spec.get("ui_nodes") if isinstance(spec.get("ui_nodes"), dict) else {}
    node_to_key = {84: "frames", 82: "steps", 387: "overlap", "84": "frames", "82": "steps", "387": "overlap"}
    for nid, key in node_to_key.items():
        node_spec = ui_nodes.get(nid) if nid in ui_nodes else ui_nodes.get(str(nid))
        if not isinstance(node_spec, dict):
            continue
        widgets = node_spec.get("widgets_values")
        if isinstance(widgets, list) and widgets:
            n = _coerce_int(widgets[1] if len(widgets) > 1 else widgets[0])
            if n is not None:
                out[key] = n
    seed_dev = _coerce_int(spec.get("noise_seed") if spec.get("noise_seed") is not None else dev.get("noise_seed"))
    if seed_dev is not None:
        out["seed"] = seed_dev

    try:
        from shape_factory_queue import extract_job_noise_seed  # type: ignore

        seed_job = extract_job_noise_seed(job, job_path)
        if seed_job is not None and "seed" not in out:
            out["seed"] = int(seed_job)
    except Exception:
        pass

    root = Path(data_root).expanduser().resolve() if data_root else None
    if root is not None or job_path is not None:
        try:
            from shape_factory import ensure_job_workflow_path, read_json  # type: ignore

            if root is None and job_path is not None:
                # Best-effort: job files live under data_root/jobs/...
                cand = Path(job_path).resolve()
                for parent in cand.parents:
                    if parent.name == ".data":
                        root = parent
                        break
            if root is not None:
                wp = ensure_job_workflow_path(job, data_root=root)
                if wp.is_file():
                    wf = read_json(wp)
                    live = extract_params_from_workflow(wf if isinstance(wf, dict) else None)
                    for key in ("frames", "steps", "overlap"):
                        if key in live:
                            out[key] = live[key]
                    if "seed" in live and (seed_adhoc is not None or seed_dev is not None or "seed" in out):
                        out["seed"] = live["seed"]
                    elif "seed" in live and "seed" not in out:
                        out["seed"] = live["seed"]
        except Exception:
            pass

    return out


def owned_params_to_profile(
    job: Dict[str, Any],
    *,
    data_root: Path,
    job_path: Optional[Path] = None,
) -> Dict[str, Any]:
    current = extract_job_current_params(job, job_path, data_root=data_root)
    seed, template_path = load_template_param_seed(job, data_root=data_root)
    snowflake = False
    diffs: Dict[str, Dict[str, Optional[int]]] = {}
    for key in PARAM_KEYS:
        cur = current.get(key)
        base = seed.get(key)
        if cur is None or base is None:
            continue
        if cur != base:
            snowflake = True
            diffs[key] = {"job": cur, "seed": base}
    return {
        "current": current,
        "seed": seed,
        "snowflake": snowflake,
        "diffs": diffs,
        "template_path": template_path,
        "keys": list(PARAM_KEYS),
    }


def patch_readable_mx_sliders(
    workflow: Dict[str, Any],
    parameters: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply frames/steps/overlap (+ optional seed) onto a catalog LiteGraph readable."""
    from shape_factory import apply_dev_tuning_ui  # type: ignore
    from shape_factory_queue import build_adhoc_dev_tuning  # type: ignore

    tuning = build_adhoc_dev_tuning(parameters, data_root=Path("."))
    if not tuning:
        return {"ui_nodes": [], "seed": []}
    return apply_dev_tuning_ui(workflow, tuning)


def write_json_with_bak(path: Path, doc: Dict[str, Any]) -> Optional[Path]:
    path = Path(path)
    bak: Optional[Path] = None
    if path.is_file():
        bak = path.with_suffix(path.suffix + ".bak")
        bak.write_bytes(path.read_bytes())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return bak
