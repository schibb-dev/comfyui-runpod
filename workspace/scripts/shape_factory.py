#!/usr/bin/env python3
"""
Shape-aware workflow factory (iteration slice).

Shapes define slot contracts (requires/produces). Pools hold members independent of
provenance. Generate binds picks from pools onto a catalog template and emits
runnable workflow JSON + job metadata.

  python3 shape_factory.py pools list --pools .data/pools/FB9_GEX2/pools.yaml
  python3 shape_factory.py generate --shape .data/shapes/FB9_GEX2.shape.yaml \\
      --pools .data/pools/FB9_GEX2/pools.yaml --pick zip --limit 4 --dev
  python3 shape_factory.py submit --family FB9_GEX2 --limit 2
  python3 shape_factory.py pool sync --pools .data/pools/FB9_GEX2/pools.yaml
  python3 shape_factory.py status --family FB9_GEX2 --wait --deposit
  python3 shape_factory.py timings summary --family FB9_GEX2
  python3 shape_factory.py timings compare --baseline job_a.job.json --candidate job_b.job.json
  python3 shape_factory.py validate --catalog --comfy-check
  python3 shape_factory.py quarantine list --status quarantined
  python3 shape_factory.py quarantine release --workflow path/to/workflow.json --note "reviewed"
"""

from __future__ import annotations

import argparse
import copy
import datetime as _dt
import itertools
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

import yaml

from comfyui_submit import _http_json, convert_ui_workflow_to_prompt, submit_prompt_to_comfyui
from comfy_meta_lib import extract_prompt_workflow_from_png_chunks, read_png_text_chunks
from snowflake_factory import strip_video_previews_and_redirect_outputs
from snowflake_inventory import is_litegraph_workflow, read_json
from output_path_lib import flatten_output_prefix, normalize_prompt_output_prefixes
from workflow_repair import (
    RepairContext,
    RepairFix,
    default_repair_rules,
    load_type_mappings,
    load_prompt_error_rules,
    patchable_missing_types,
    repair_until_stable,
    sanitize_prompt_string_inputs,
)
from shape_factory_ratings import add_ratings_subparser
from shape_factory_heuristics import add_heuristics_subparser
from shape_factory_rating_sampler import add_rating_sampler_subparser
from shape_factory_tags import add_tags_subparser
from shape_factory_source_facets import add_source_facets_subparser
from shape_factory_seed_sources import add_seed_sources_subparser
from shape_factory_backfill import add_backfill_subparser

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}


DEFAULT_DATA_ROOT = Path("/home/yuji/comfyui-runpod-data")
DEFAULT_JOB_DIR = Path("/home/yuji/src/comfyui-runpod/.data/shape_factory/jobs")
DEFAULT_WORKFLOW_DIR = Path(
    "/home/yuji/comfyui-runpod-data/comfyui_user/default/workflows/generated/shape_factory"
)
DEFAULT_CATALOG_DIR = Path(
    "/home/yuji/comfyui-runpod-data/comfyui_user/default/workflows/generated/catalog"
)
DEFAULT_COMFY_SERVER = os.environ.get("COMFYUI_SERVER", "http://127.0.0.1:8188")
DEFAULT_POOLS_ROOT = Path("/home/yuji/src/comfyui-runpod/.data/pools")
DEFAULT_DEV_TUNING = Path("/home/yuji/src/comfyui-runpod/.data/shapes/dev-fast.yaml")
TIMINGS_SCHEMA = "comfyui-runpod.shape-timings.v0"
DEFAULT_TIMINGS_LEDGER = Path("/home/yuji/src/comfyui-runpod/.data/shape_factory/timings.jsonl")
POOL_INDEX_SCHEMA = "comfyui-runpod.pool-index.v0"
QUARANTINE_SCHEMA = "comfyui-runpod.workflow-quarantine.v0"
DEFAULT_QUARANTINE_PATH = Path("/home/yuji/src/comfyui-runpod/.data/shape_factory/quarantine.json")
DEFAULT_NODE_TYPE_MAP = Path(__file__).resolve().parents[2] / "scripts" / "workflow_node_id_map.yaml"
DEFAULT_REPAIR_RULES_PATH = Path(__file__).resolve().parents[2] / "scripts" / "workflow_repair_rules.yaml"


def utc_now() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat(timespec="seconds")


def slug(value: str, limit: int = 100) -> str:
    out = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_")
    return (out or "item")[:limit]


def load_yaml(path: Path) -> dict[str, Any]:
    obj = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise RuntimeError(f"expected mapping in {path}")
    return obj


def deep_merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge_dict(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def resolve_dev_tuning(
    *,
    dev: bool,
    dev_tuning_path: Optional[str],
    dev_frames: Optional[int],
    dev_steps: Optional[int],
    shape: dict[str, Any],
) -> Optional[dict[str, Any]]:
    if os.environ.get("SHAPE_FACTORY_DEV", "").strip().lower() in {"1", "true", "yes"}:
        dev = True

    tuning: Optional[dict[str, Any]] = None
    if dev_tuning_path:
        tuning = load_yaml(Path(dev_tuning_path).expanduser().resolve())
    elif dev:
        if DEFAULT_DEV_TUNING.is_file():
            tuning = load_yaml(DEFAULT_DEV_TUNING)
        else:
            tuning = {"profile_id": "dev-inline", "ui_nodes": {}, "api_nodes": {}}

    shape_tuning = shape.get("dev_tuning")
    if isinstance(shape_tuning, dict):
        tuning = deep_merge_dict(tuning or {"profile_id": "shape"}, shape_tuning)

    if not tuning and not dev_frames and not dev_steps:
        return None

    tuning = tuning or {"profile_id": "dev-inline", "ui_nodes": {}, "api_nodes": {}}

    if dev_frames is not None:
        frames = int(dev_frames)
        ui = tuning.setdefault("ui_nodes", {})
        if isinstance(ui, dict):
            ui[84] = {"type": "mxSlider", "widgets_values": [frames, frames, 0]}
        api = tuning.setdefault("api_nodes", {})
        if isinstance(api, dict):
            api["84"] = {"inputs": {"Xi": frames, "Xf": frames}}
    if dev_steps is not None:
        steps = int(dev_steps)
        ui = tuning.setdefault("ui_nodes", {})
        if isinstance(ui, dict):
            ui[82] = {"type": "mxSlider", "widgets_values": [steps, steps, 0]}
        api = tuning.setdefault("api_nodes", {})
        if isinstance(api, dict):
            api["82"] = {"inputs": {"Xi": steps, "Xf": steps}}

    tuning.setdefault("output_prefix_suffix", "_dev")
    return tuning


def apply_dev_tuning_ui(workflow: dict[str, Any], tuning: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {"ui_nodes": [], "vhs": []}
    ui_nodes = tuning.get("ui_nodes") if isinstance(tuning.get("ui_nodes"), dict) else {}
    for raw_id, spec in ui_nodes.items():
        if not isinstance(spec, dict):
            continue
        node_id = int(raw_id)
        node = find_node(workflow, node_id)
        if node is None:
            changes["ui_nodes"].append({"node_id": node_id, "status": "missing"})
            continue
        if spec.get("widgets_values") is not None:
            node["widgets_values"] = copy.deepcopy(spec["widgets_values"])
            changes["ui_nodes"].append({"node_id": node_id, "widgets_values": node["widgets_values"]})

    vhs_spec = tuning.get("vhs_load_video_path") if isinstance(tuning.get("vhs_load_video_path"), dict) else {}
    frame_cap = vhs_spec.get("frame_load_cap")
    if frame_cap is not None:
        for node in workflow.get("nodes") or []:
            if not isinstance(node, dict) or node.get("type") != "VHS_LoadVideoPath":
                continue
            widgets = node.setdefault("widgets_values", {})
            if not isinstance(widgets, dict):
                continue
            widgets["frame_load_cap"] = int(frame_cap)
            preview = widgets.get("videopreview")
            if isinstance(preview, dict):
                params = preview.get("params")
                if isinstance(params, dict):
                    params["frame_load_cap"] = int(frame_cap)
            changes["vhs"].append({"node_id": node.get("id"), "frame_load_cap": int(frame_cap)})
    return changes


def apply_dev_tuning_api(prompt: dict[str, Any], tuning: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {"api_nodes": [], "vhs": []}
    api_nodes = tuning.get("api_nodes") if isinstance(tuning.get("api_nodes"), dict) else {}
    for node_key, spec in api_nodes.items():
        if not isinstance(spec, dict):
            continue
        node = prompt.get(str(node_key))
        if not isinstance(node, dict):
            changes["api_nodes"].append({"node_id": str(node_key), "status": "missing"})
            continue
        inputs_patch = spec.get("inputs")
        if isinstance(inputs_patch, dict):
            node.setdefault("inputs", {}).update(copy.deepcopy(inputs_patch))
            changes["api_nodes"].append({"node_id": str(node_key), "inputs": inputs_patch})

    vhs_spec = tuning.get("vhs_load_video_path") if isinstance(tuning.get("vhs_load_video_path"), dict) else {}
    frame_cap = vhs_spec.get("frame_load_cap")
    if frame_cap is not None:
        for key, node in prompt.items():
            if not isinstance(node, dict) or node.get("class_type") != "VHS_LoadVideoPath":
                continue
            node.setdefault("inputs", {})["frame_load_cap"] = int(frame_cap)
            changes["vhs"].append({"node_id": str(key), "frame_load_cap": int(frame_cap)})
    return changes


def ensure_timings(job: dict[str, Any]) -> dict[str, Any]:
    timings = job.get("timings")
    if not isinstance(timings, dict):
        timings = {"schema_version": TIMINGS_SCHEMA}
        job["timings"] = timings
    return timings


def timings_sidecar_path(job_path: Path) -> Path:
    return job_path.with_name(job_path.name.replace(".job.json", ".timings.json"))


def deep_merge_timings(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge_timings(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def recompute_timing_totals(job: dict[str, Any]) -> None:
    timings = ensure_timings(job)
    totals = timings.setdefault("totals", {})
    submit_ts = (timings.get("queue") or {}).get("submitted_ts")
    if submit_ts is None:
        submit_ts = (timings.get("submit") or {}).get("finished_ts")
    complete_ts = (timings.get("execution") or {}).get("finished_ts")
    generate_ts = (timings.get("generate") or {}).get("started_ts")
    if isinstance(submit_ts, (int, float)) and isinstance(complete_ts, (int, float)):
        totals["submit_to_complete_sec"] = round(max(0.0, float(complete_ts) - float(submit_ts)), 3)
    if isinstance(generate_ts, (int, float)) and isinstance(complete_ts, (int, float)):
        totals["generate_to_complete_sec"] = round(max(0.0, float(complete_ts) - float(generate_ts)), 3)
    recompute_efficiency_metrics(job)
    timings["updated_at"] = utc_now()


def recompute_efficiency_metrics(job: dict[str, Any]) -> None:
    """Derived metrics for comparing workflow optimization runs (sec/frame, sec/step)."""
    timings = ensure_timings(job)
    workload = timings.get("workload") if isinstance(timings.get("workload"), dict) else {}
    execution = timings.get("execution") if isinstance(timings.get("execution"), dict) else {}
    totals = timings.get("totals") if isinstance(timings.get("totals"), dict) else {}
    efficiency = timings.setdefault("efficiency", {})

    frames = workload.get("frames")
    steps = workload.get("steps")
    exec_sec = execution.get("sec")
    total_sec = totals.get("submit_to_complete_sec")

    for key in (
        "exec_sec_per_frame",
        "exec_sec_per_step",
        "total_sec_per_frame",
        "frames_per_min_exec",
        "frame_steps",
    ):
        efficiency.pop(key, None)

    if isinstance(exec_sec, (int, float)) and isinstance(frames, (int, float)) and float(frames) > 0:
        efficiency["exec_sec_per_frame"] = round(float(exec_sec) / float(frames), 4)
        if exec_sec > 0:
            efficiency["frames_per_min_exec"] = round(float(frames) / (float(exec_sec) / 60.0), 2)
    if isinstance(exec_sec, (int, float)) and isinstance(steps, (int, float)) and float(steps) > 0:
        efficiency["exec_sec_per_step"] = round(float(exec_sec) / float(steps), 4)
    if isinstance(total_sec, (int, float)) and isinstance(frames, (int, float)) and float(frames) > 0:
        efficiency["total_sec_per_frame"] = round(float(total_sec) / float(frames), 4)
    if isinstance(frames, (int, float)) and isinstance(steps, (int, float)):
        efficiency["frame_steps"] = round(float(frames) * float(steps), 1)


def normalize_comfy_timestamp(ts: float) -> float:
    """Comfy WS/history timestamps may be epoch seconds or milliseconds."""
    ts_f = float(ts)
    if ts_f > 1_000_000_000_000:
        return ts_f / 1000.0
    return ts_f


def parse_history_execution_timings(history: dict[str, Any]) -> dict[str, Any]:
    status = history.get("status") if isinstance(history.get("status"), dict) else {}
    exec_start: Optional[float] = None
    exec_end: Optional[float] = None
    for msg in status.get("messages") or []:
        if not isinstance(msg, (list, tuple)) or not msg:
            continue
        kind = str(msg[0])
        payload = msg[1] if len(msg) > 1 and isinstance(msg[1], dict) else {}
        ts = payload.get("timestamp")
        if not isinstance(ts, (int, float)):
            continue
        ts_f = normalize_comfy_timestamp(float(ts))
        if ts_f < 1_000_000_000:
            continue
        if kind == "execution_start" and exec_start is None:
            exec_start = ts_f
        if kind in {"execution_success", "execution_cached"}:
            exec_end = ts_f
    out: dict[str, Any] = {}
    if exec_start is not None:
        out["started_ts"] = exec_start
    if exec_end is not None:
        out["finished_ts"] = exec_end
    if exec_start is not None and exec_end is not None:
        out["sec"] = round(max(0.0, exec_end - exec_start), 3)
        out["source"] = "history.messages"
    return out


def output_completion_ts(outputs: list[Path]) -> Optional[float]:
    latest: Optional[float] = None
    for path in outputs:
        try:
            if path.is_file():
                latest = max(latest or 0.0, float(path.stat().st_mtime))
        except Exception:
            continue
    return latest


def should_append_timings_ledger(job: dict[str, Any]) -> bool:
    timings = job.get("timings") if isinstance(job.get("timings"), dict) else {}
    if timings.get("ledger_written_at"):
        return False
    status = str((job.get("submit") or {}).get("status") or "")
    return status in {"complete", "error"}


def append_timings_ledger(job_path: Path, job: dict[str, Any]) -> None:
    if not should_append_timings_ledger(job):
        return
    timings = ensure_timings(job)
    submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    entry = {
        "recorded_at": utc_now(),
        "job_path": str(job_path),
        "job_key": job.get("job_key"),
        "family_slug": job.get("family_slug"),
        "shape_id": job.get("shape_id"),
        "graph_hash": job.get("graph_hash"),
        "prompt_id": submit.get("prompt_id"),
        "status": submit.get("status"),
        "dev_profile": (job.get("dev_tuning") or {}).get("profile_id")
        if isinstance(job.get("dev_tuning"), dict)
        else None,
        "workload": copy.deepcopy((timings.get("workload") or {})),
        "efficiency": copy.deepcopy((timings.get("efficiency") or {})),
        "timings": copy.deepcopy(timings),
    }
    DEFAULT_TIMINGS_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with DEFAULT_TIMINGS_LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    timings["ledger_written_at"] = utc_now()


def persist_timings(job_path: Path, job: dict[str, Any], *, ledger: bool = False) -> None:
    recompute_timing_totals(job)
    atomic_write_json(timings_sidecar_path(job_path), ensure_timings(job))
    if ledger:
        append_timings_ledger(job_path, job)


def format_timing_hint(job: dict[str, Any]) -> str:
    timings = job.get("timings") if isinstance(job.get("timings"), dict) else {}
    parts: list[str] = []
    execution = timings.get("execution") if isinstance(timings.get("execution"), dict) else {}
    if isinstance(execution.get("sec"), (int, float)):
        parts.append(f"exec={execution['sec']:.0f}s")
    queue = timings.get("queue") if isinstance(timings.get("queue"), dict) else {}
    if isinstance(queue.get("wait_sec"), (int, float)) and queue["wait_sec"] > 0:
        parts.append(f"queue={queue['wait_sec']:.0f}s")
    totals = timings.get("totals") if isinstance(timings.get("totals"), dict) else {}
    if isinstance(totals.get("submit_to_complete_sec"), (int, float)):
        parts.append(f"total={totals['submit_to_complete_sec']:.0f}s")
    efficiency = timings.get("efficiency") if isinstance(timings.get("efficiency"), dict) else {}
    if isinstance(efficiency.get("exec_sec_per_frame"), (int, float)):
        parts.append(f"{efficiency['exec_sec_per_frame']:.2f}s/frame")
    return f" ({', '.join(parts)})" if parts else ""


def update_job_timings_on_status(
    job: dict[str, Any],
    *,
    status: str,
    history: Optional[dict[str, Any]],
    now: float,
    data_root: Path,
) -> None:
    timings = ensure_timings(job)
    submit = job.get("submit")
    if not isinstance(submit, dict):
        submit = {}
        job["submit"] = submit
    queue = timings.setdefault("queue", {})
    execution = timings.setdefault("execution", {})

    if status == "running" and not queue.get("running_first_seen_ts"):
        queue["running_first_seen_ts"] = now
        queue["running_first_seen_at"] = utc_now()
        submitted_ts = queue.get("submitted_ts") or (timings.get("submit") or {}).get("finished_ts")
        if isinstance(submitted_ts, (int, float)):
            queue["wait_sec"] = round(max(0.0, now - float(submitted_ts)), 3)

    if isinstance(history, dict):
        hist_times = parse_history_execution_timings(history)
        if hist_times:
            execution.update({k: v for k, v in hist_times.items() if v is not None})
        node_times = parse_history_node_timings(history)
        if node_times:
            execution["nodes"] = node_times.get("nodes")
            execution["nodes_tracked_sec"] = node_times.get("tracked_sec")
            execution["nodes_source"] = node_times.get("source")
        hist_outputs = extract_history_output_paths(history, data_root)
        if hist_outputs:
            submit["outputs"] = [str(p) for p in hist_outputs]
            submit["output_discovery"] = "comfy_history"

    if status in {"complete", "error"}:
        outputs = discover_job_outputs(job, data_root)
        if outputs and not submit.get("outputs"):
            submit["outputs"] = [str(p) for p in outputs]
            submit["output_discovery"] = submit.get("output_discovery") or "filesystem"
        probes = probe_job_output_media(job, data_root)
        if probes:
            timings.setdefault("outputs", {})["probes"] = probes
            fc = probes[0].get("probe", {}).get("frame_count") if probes else None
            workload = timings.setdefault("workload", {})
            if isinstance(fc, int) and fc > 0:
                workload["output_frame_count"] = fc
                cfg = workload.get("frames")
                if isinstance(cfg, int) and cfg > 0 and fc != cfg:
                    workload["frame_count_delta"] = fc - cfg
        output_ts = output_completion_ts(outputs)
        if output_ts is not None and execution.get("finished_ts") is None:
            execution["finished_ts"] = output_ts
            execution.setdefault("source", "output_mtime")

        if execution.get("finished_ts") is None:
            execution["finished_ts"] = now
            execution.setdefault("source", "status_poll_wall")
        execution.setdefault("finished_at", utc_now())

        if execution.get("started_ts") is None:
            started = queue.get("running_first_seen_ts") or queue.get("submitted_ts")
            if isinstance(started, (int, float)):
                execution["started_ts"] = float(started)

        if execution.get("sec") is None and isinstance(execution.get("started_ts"), (int, float)):
            execution["sec"] = round(
                max(0.0, float(execution["finished_ts"]) - float(execution["started_ts"])),
                3,
            )
            execution.setdefault("source", execution.get("source") or "status_poll_wall")

        if status == "error":
            execution["error"] = True


def collect_timing_rows(args: argparse.Namespace) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for job_path in iter_job_paths(args):
        try:
            job = json.loads(job_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        timings = job.get("timings") if isinstance(job.get("timings"), dict) else {}
        submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
        rows.append(
            {
                "job_path": str(job_path),
                "job_key": job.get("job_key"),
                "family_slug": job.get("family_slug"),
                "shape_id": job.get("shape_id"),
                "graph_hash": job.get("graph_hash"),
                "status": submit.get("status"),
                "prompt_id": submit.get("prompt_id"),
                "dev_profile": (job.get("dev_tuning") or {}).get("profile_id")
                if isinstance(job.get("dev_tuning"), dict)
                else None,
                "timings": timings,
                "job": job,
            }
        )
    return rows


def efficiency_label(row: dict[str, Any]) -> str:
    dev = row.get("dev_profile")
    return str(dev) if dev else "prod"


def summarize_numeric(values: list[float]) -> dict[str, float]:
    values_sorted = sorted(values)
    n = len(values_sorted)
    return {
        "n": float(n),
        "avg": sum(values_sorted) / n,
        "median": values_sorted[n // 2],
        "min": values_sorted[0],
        "max": values_sorted[-1],
    }


def timing_compare_row(row: dict[str, Any]) -> dict[str, Any]:
    timings = row.get("timings") or {}
    workload = timings.get("workload") if isinstance(timings.get("workload"), dict) else {}
    execution = timings.get("execution") if isinstance(timings.get("execution"), dict) else {}
    totals = timings.get("totals") if isinstance(timings.get("totals"), dict) else {}
    efficiency = timings.get("efficiency") if isinstance(timings.get("efficiency"), dict) else {}
    return {
        "job_key": row.get("job_key"),
        "graph_hash": row.get("graph_hash"),
        "dev_profile": efficiency_label(row),
        "frames": workload.get("frames"),
        "steps": workload.get("steps"),
        "exec_sec": execution.get("sec"),
        "total_sec": totals.get("submit_to_complete_sec"),
        "exec_sec_per_frame": efficiency.get("exec_sec_per_frame"),
        "exec_sec_per_step": efficiency.get("exec_sec_per_step"),
    }


def print_timing_compare(baseline: dict[str, Any], candidate: dict[str, Any]) -> None:
    keys = [
        ("frames", "frames", False),
        ("steps", "steps", False),
        ("exec_sec", "exec_sec", False),
        ("total_sec", "total_sec", False),
        ("exec_sec_per_frame", "exec_sec_per_frame", True),
        ("exec_sec_per_step", "exec_sec_per_step", True),
    ]
    print("# Workflow efficiency compare\n")
    print(f"- baseline: `{baseline.get('job_key')}` ({baseline.get('dev_profile')})")
    print(f"- candidate: `{candidate.get('job_key')}` ({candidate.get('dev_profile')})")
    if baseline.get("graph_hash") and baseline.get("graph_hash") != candidate.get("graph_hash"):
        print(
            f"- warning: graph_hash differs "
            f"({str(baseline.get('graph_hash'))[:12]} vs {str(candidate.get('graph_hash'))[:12]})"
        )
    print()
    print("| metric | baseline | candidate | delta |")
    print("|--------|----------|-----------|-------|")
    for label, key, lower_is_better in keys:
        b = baseline.get(key)
        c = candidate.get(key)
        if not isinstance(b, (int, float)) or not isinstance(c, (int, float)):
            continue
        delta = float(c) - float(b)
        if float(b) != 0:
            pct = (delta / float(b)) * 100.0
            pct_str = f"{pct:+.1f}%"
        else:
            pct_str = "n/a"
        good = (delta < 0) if lower_is_better else (delta > 0)
        flag = " ✓" if good else ""
        print(f"| {label} | {b} | {c} | {delta:+.3f} ({pct_str}){flag} |")


def cmd_timings(args: argparse.Namespace) -> int:
    if args.timings_cmd == "compare":
        baseline_path = Path(args.baseline).expanduser().resolve()
        candidate_path = Path(args.candidate).expanduser().resolve()
        baseline_job = json.loads(baseline_path.read_text(encoding="utf-8"))
        candidate_job = json.loads(candidate_path.read_text(encoding="utf-8"))
        baseline_row = {
            "job_key": baseline_job.get("job_key"),
            "graph_hash": baseline_job.get("graph_hash"),
            "dev_profile": (baseline_job.get("dev_tuning") or {}).get("profile_id"),
            "timings": baseline_job.get("timings") or {},
        }
        candidate_row = {
            "job_key": candidate_job.get("job_key"),
            "graph_hash": candidate_job.get("graph_hash"),
            "dev_profile": (candidate_job.get("dev_tuning") or {}).get("profile_id"),
            "timings": candidate_job.get("timings") or {},
        }
        print_timing_compare(timing_compare_row(baseline_row), timing_compare_row(candidate_row))
        return 0

    rows = collect_timing_rows(args)
    if not rows:
        print("error: no jobs with timings found", file=sys.stderr)
        return 1

    if args.timings_cmd == "list":
        print(f"# Shape factory timings ({len(rows)} jobs)\n")
        for row in rows:
            timings = row.get("timings") or {}
            totals = timings.get("totals") if isinstance(timings.get("totals"), dict) else {}
            execution = timings.get("execution") if isinstance(timings.get("execution"), dict) else {}
            submit = timings.get("submit") if isinstance(timings.get("submit"), dict) else {}
            workload = timings.get("workload") if isinstance(timings.get("workload"), dict) else {}
            efficiency = timings.get("efficiency") if isinstance(timings.get("efficiency"), dict) else {}
            print(
                f"{row.get('job_key')}: status={row.get('status')} "
                f"frames={workload.get('frames')} steps={workload.get('steps')} "
                f"exec={execution.get('sec')} submit={submit.get('total_sec')} "
                f"total={totals.get('submit_to_complete_sec')} "
                f"sec/frame={efficiency.get('exec_sec_per_frame')} "
                f"dev={efficiency_label(row)}"
            )
        return 0

    group_by = str(getattr(args, "group_by", "graph_hash") or "graph_hash")
    completed = [
        r
        for r in rows
        if str(r.get("status")) == "complete"
        and isinstance((r.get("timings") or {}).get("execution"), dict)
        and isinstance((r["timings"]["execution"]).get("sec"), (int, float))
    ]
    print(f"# Shape factory timings summary\n")
    print(f"- jobs_scanned: {len(rows)}")
    print(f"- jobs_complete_with_exec: {len(completed)}")
    print(f"- group_by: {group_by}\n")

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in completed:
        if group_by == "family":
            key = str(row.get("family_slug") or "?")
        elif group_by == "shape_id":
            key = str(row.get("shape_id") or "?")
        elif group_by == "dev_profile":
            key = efficiency_label(row)
        else:
            gh = str(row.get("graph_hash") or "?")
            key = f"{gh[:12]}…/{efficiency_label(row)}"
        groups.setdefault(key, []).append(row)

    for key, group_rows in sorted(groups.items()):
        exec_secs = [float(r["timings"]["execution"]["sec"]) for r in group_rows]
        exec_summary = summarize_numeric(exec_secs)
        spf_vals = [
            float(r["timings"]["efficiency"]["exec_sec_per_frame"])
            for r in group_rows
            if isinstance((r.get("timings") or {}).get("efficiency"), dict)
            and isinstance(r["timings"]["efficiency"].get("exec_sec_per_frame"), (int, float))
        ]
        sample = group_rows[0]
        workload = (sample.get("timings") or {}).get("workload") or {}
        line = (
            f"## {key} n={int(exec_summary['n'])} "
            f"exec_avg={exec_summary['avg']:.1f}s median={exec_summary['median']:.1f}s "
            f"max={exec_summary['max']:.1f}s "
            f"frames={workload.get('frames')} steps={workload.get('steps')}"
        )
        if spf_vals:
            spf_summary = summarize_numeric(spf_vals)
            line += f" sec/frame_avg={spf_summary['avg']:.3f}"
        print(line)
    if not groups:
        print("No completed jobs with execution timings yet.")
        print("Tip: use --dev for fast iteration, then compare prod vs optimized with `timings compare`.")
    return 0


def expand_date_tokens(value: str) -> str:
    now = _dt.datetime.now()
    fmt_aliases = {
        "yyyy-MM-dd": "%Y-%m-%d",
        "yyyyMMdd": "%Y%m%d",
        "HHmmss": "%H%M%S",
    }

    def repl(match: re.Match[str]) -> str:
        token = match.group(1)
        fmt = fmt_aliases.get(token, token)
        return now.strftime(fmt)

    return re.sub(r"%date:([^%]+)%", repl, value)


def comfy_workspace_relpath(path: Path, data_root: Path) -> tuple[str, Optional[str]]:
    """Map host bind paths to ComfyUI-facing paths (input/…, output/…)."""
    path = path.expanduser().resolve()
    data_root = data_root.expanduser().resolve()
    input_root = data_root / "input"
    output_root = data_root / "output"
    try:
        if path.is_relative_to(input_root):
            return f"input/{path.relative_to(input_root).as_posix()}", None
        if path.is_relative_to(output_root):
            return f"output/{path.relative_to(output_root).as_posix()}", None
    except AttributeError:
        # Python <3.9 compat (not expected here, but safe)
        try:
            rel_in = path.relative_to(input_root)
            return f"input/{rel_in.as_posix()}", None
        except Exception:
            pass
        try:
            rel_out = path.relative_to(output_root)
            return f"output/{rel_out.as_posix()}", None
        except Exception:
            pass
    return path.name, f"path outside data root {data_root}; using basename only"


def resolve_glob(spec: dict[str, Any]) -> list[Path]:
    pattern = str(spec.get("glob") or "").strip()
    if not pattern:
        return []
    limit = spec.get("limit")
    # stdlib glob so year folders like og/2025-* and nested ** patterns both work.
    import glob as _glob

    expanded = str(Path(pattern).expanduser())
    raw_paths = _glob.glob(expanded, recursive=True)
    paths = sorted({Path(p).resolve() for p in raw_paths if Path(p).is_file()})
    if isinstance(limit, int) and limit > 0:
        paths = paths[:limit]
    return paths


def resolve_dir(spec: dict[str, Any]) -> list[Path]:
    root = Path(str(spec.get("dir") or "")).expanduser()
    if not root.is_dir():
        return []
    exts = {str(e).lower() for e in (spec.get("ext") or [".json"])}
    paths = sorted(
        p.resolve()
        for p in root.iterdir()
        if p.is_file() and p.suffix.lower() in exts
    )
    limit = spec.get("limit")
    if isinstance(limit, int) and limit > 0:
        paths = paths[:limit]
    return paths


def pool_index_path_for_pools(pools_path: Path) -> Path:
    return pools_path.parent / "index.json"


def family_for_pool_id(pool_id: str) -> str:
    if "_X_" in pool_id:
        return pool_id.split("_X_", 1)[0]
    return pool_id


def default_pool_index_path(pool_id: str) -> Path:
    return DEFAULT_POOLS_ROOT / family_for_pool_id(pool_id) / "index.json"


def load_pool_index(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": POOL_INDEX_SCHEMA, "pools": {}}
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise RuntimeError(f"pool index is not an object: {path}")
    obj.setdefault("schema_version", POOL_INDEX_SCHEMA)
    obj.setdefault("pools", {})
    return obj


def pool_index_member_paths(index_doc: dict[str, Any], pool_id: str) -> list[Path]:
    pools = index_doc.get("pools") if isinstance(index_doc.get("pools"), dict) else {}
    pool = pools.get(pool_id) if isinstance(pools, dict) else None
    if not isinstance(pool, dict):
        return []
    out: list[Path] = []
    for member in pool.get("members") or []:
        if not isinstance(member, dict):
            continue
        raw = member.get("path")
        if isinstance(raw, str) and raw.strip():
            out.append(Path(raw).expanduser().resolve())
    return out


def resolve_pool_index(spec: dict[str, Any]) -> list[Path]:
    index_path = Path(str(spec.get("glob") or spec.get("index") or "")).expanduser()
    pool_id = str(spec.get("pool_id") or "").strip()
    if not index_path.is_file():
        return []
    if not pool_id:
        raise RuntimeError(f"pool_index spec missing pool_id: {spec}")
    return pool_index_member_paths(load_pool_index(index_path), pool_id)


def resolve_pool_members(pool_def: dict[str, Any]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for spec in pool_def.get("members") or []:
        if not isinstance(spec, dict):
            continue
        if str(spec.get("kind") or "") == "pool_index":
            batch = resolve_pool_index(spec)
        elif spec.get("glob"):
            batch = resolve_glob(spec)
        elif spec.get("dir"):
            batch = resolve_dir(spec)
        else:
            continue
        for path in batch:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            out.append(path)
    return out


def member_record_for_path(path: Path, *, job_key: Optional[str] = None, source: str = "seed") -> dict[str, Any]:
    rec: dict[str, Any] = {
        "path": str(path.resolve()),
        "kind": "video" if path.suffix.lower() in VIDEO_EXTS else "file",
        "source": source,
        "added_at": utc_now(),
    }
    if job_key:
        rec["job_key"] = job_key
    png = png_path_for_binding(path)
    if png.is_file():
        rec["companion_png"] = str(png.resolve())
    return rec


def upsert_pool_index_members(
    index_doc: dict[str, Any],
    pool_id: str,
    new_members: list[dict[str, Any]],
    *,
    description: Optional[str] = None,
) -> int:
    pools = index_doc.setdefault("pools", {})
    if not isinstance(pools, dict):
        raise RuntimeError("pool index pools field is not an object")
    pool = pools.get(pool_id)
    if not isinstance(pool, dict):
        pool = {"pool_id": pool_id, "members": []}
        pools[pool_id] = pool
    if description and not pool.get("description"):
        pool["description"] = description
    existing_paths = {
        str(m.get("path"))
        for m in (pool.get("members") or [])
        if isinstance(m, dict) and isinstance(m.get("path"), str)
    }
    added = 0
    members = pool.setdefault("members", [])
    if not isinstance(members, list):
        members = []
        pool["members"] = members
    for rec in new_members:
        if not isinstance(rec, dict):
            continue
        p = rec.get("path")
        if not isinstance(p, str) or p in existing_paths:
            continue
        existing_paths.add(p)
        members.append(rec)
        added += 1
    return added


def parse_pool_ref(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("pool:"):
        return text.split(":", 1)[1]
    return text


def find_node(workflow: dict[str, Any], node_id: int) -> Optional[dict[str, Any]]:
    for node in workflow.get("nodes") or []:
        if isinstance(node, dict) and node.get("id") == node_id:
            return node
    return None


def mx_slider_value(node: Optional[dict[str, Any]]) -> Optional[int]:
    if node is None:
        return None
    widgets = node.get("widgets_values")
    if isinstance(widgets, list) and len(widgets) >= 2:
        try:
            return int(widgets[1])
        except Exception:
            pass
    if isinstance(widgets, list) and widgets:
        try:
            return int(widgets[0])
        except Exception:
            pass
    return None


def extract_workload_from_workflow(workflow: dict[str, Any]) -> dict[str, Any]:
    """Capture generation knobs used for normalized efficiency metrics."""
    frames = mx_slider_value(find_node(workflow, 84))
    steps = mx_slider_value(find_node(workflow, 82))
    overlap = mx_slider_value(find_node(workflow, 387))
    wan = find_node(workflow, 133)
    width = height = wan_length = batch_size = None
    if wan is not None:
        widgets = wan.get("widgets_values")
        if isinstance(widgets, list):
            if len(widgets) >= 1:
                width = widgets[0]
            if len(widgets) >= 2:
                height = widgets[1]
            if len(widgets) >= 3:
                wan_length = widgets[2]
            if len(widgets) >= 4:
                batch_size = widgets[3]
    if frames is None and isinstance(wan_length, (int, float)):
        frames = int(wan_length)
    out: dict[str, Any] = {}
    if frames is not None:
        out["frames"] = int(frames)
    if steps is not None:
        out["steps"] = int(steps)
    if overlap is not None:
        out["overlap"] = int(overlap)
    if width is not None:
        out["width"] = int(width)
    if height is not None:
        out["height"] = int(height)
    if batch_size is not None:
        out["batch_size"] = int(batch_size)
    return out


def capture_job_workload(job: dict[str, Any], workflow: dict[str, Any]) -> dict[str, Any]:
    timings = ensure_timings(job)
    workload = extract_workload_from_workflow(workflow)
    workload["captured_at"] = utc_now()
    if job.get("graph_hash"):
        workload["graph_hash"] = job.get("graph_hash")
    if job.get("shape_id"):
        workload["shape_id"] = job.get("shape_id")
    dev = job.get("dev_tuning") if isinstance(job.get("dev_tuning"), dict) else {}
    if dev.get("profile_id"):
        workload["dev_profile"] = dev.get("profile_id")
    timings["workload"] = workload
    return workload


def apply_load_image(workflow: dict[str, Any], node_id: int, asset_path: Path, data_root: Path) -> list[str]:
    warnings: list[str] = []
    node = find_node(workflow, node_id)
    if node is None:
        raise RuntimeError(f"LoadImage node {node_id} not found")
    rel, warn = comfy_workspace_relpath(asset_path, data_root)
    if warn:
        warnings.append(warn)
    widgets = node.get("widgets_values")
    if isinstance(widgets, list):
        if widgets:
            widgets[0] = rel
        else:
            widgets.append(rel)
        if len(widgets) == 1:
            widgets.append("image")
    else:
        node["widgets_values"] = [rel, "image"]
    return warnings


def apply_vhs_load_video_path(
    workflow: dict[str, Any], node_id: int, asset_path: Path, data_root: Path
) -> list[str]:
    warnings: list[str] = []
    node = find_node(workflow, node_id)
    if node is None:
        raise RuntimeError(f"VHS_LoadVideoPath node {node_id} not found")
    rel, warn = comfy_workspace_relpath(asset_path, data_root)
    if warn:
        warnings.append(warn)
    widgets = node.get("widgets_values")
    if not isinstance(widgets, dict):
        widgets = {}
        node["widgets_values"] = widgets
    widgets["video"] = rel
    preview = widgets.get("videopreview")
    if isinstance(preview, dict):
        params = preview.get("params")
        if isinstance(params, dict):
            params["filename"] = rel
    return warnings


def sanitize_linked_text_widget_defaults(workflow: dict[str, Any]) -> int:
    """
    Clear unused text widget defaults when a node's ``text`` input is linked.

    LiteGraph often leaves stale ``widgets_values`` (e.g. an old Idle Animation
    placeholder) after the widget is converted to a linked input. Naive readers
    that copy widgets_values can then treat that dead string as a real prompt.
    """
    cleared = 0
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        text_linked = False
        for inp in node.get("inputs") or []:
            if isinstance(inp, dict) and str(inp.get("name") or "") == "text" and inp.get("link") is not None:
                text_linked = True
                break
        if not text_linked:
            continue
        widgets = node.get("widgets_values")
        if isinstance(widgets, list):
            for i, val in enumerate(widgets):
                if isinstance(val, str) and val.strip():
                    widgets[i] = ""
                    cleared += 1
        elif isinstance(widgets, dict):
            text = widgets.get("text")
            if isinstance(text, str) and text.strip():
                widgets["text"] = ""
                cleared += 1
    return cleared


def _prompt_write_target(
    workflow: dict[str, Any],
    node_id: int,
) -> tuple[Optional[dict[str, Any]], int, int]:
    """
    Resolve the node that actually owns prompt text.

    If ``text`` is linked, follow upstream and write there — never stuff a linked
    node's unused ``widgets_values`` default (those are not live prompt data).
    Returns (node, resolved_node_id, widget_index_for_write).
    """
    from shape_factory_prompt_recover import resolve_text_owner_node_id

    owner_id = resolve_text_owner_node_id(workflow, int(node_id))
    if owner_id is None:
        return None, int(node_id), 0
    node = find_node(workflow, owner_id)
    if node is None:
        return None, owner_id, 0
    return node, owner_id, 0


def apply_prompt_bundle(workflow: dict[str, Any], binding: dict[str, Any], profile_path: Path) -> list[str]:
    warnings: list[str] = []
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if not isinstance(profile, dict):
        raise RuntimeError(f"prompt profile is not a JSON object: {profile_path}")

    for key in ("positive", "negative"):
        spec = binding.get(key)
        if not isinstance(spec, dict):
            continue
        text = profile.get(key)
        if text is None:
            continue
        node_id = int(spec["node_id"])
        widget_index = int(spec.get("widget_index", 0))
        node, resolved_id, _ = _prompt_write_target(workflow, node_id)
        if node is None:
            warnings.append(f"prompt node {node_id} ({key}) not found or text is linked with no source")
            continue
        if resolved_id != node_id:
            # Binding pointed at a linked encode; write live text upstream.
            widget_index = 0
        widgets = node.get("widgets_values")
        if isinstance(widgets, list):
            if len(widgets) <= widget_index:
                widgets.extend([""] * (widget_index + 1 - len(widgets)))
            widgets[widget_index] = str(text)
        else:
            node["widgets_values"] = [str(text)]
    return warnings


def apply_slot_binding(
    workflow: dict[str, Any],
    require: dict[str, Any],
    asset_path: Path,
    data_root: Path,
) -> list[str]:
    binding = require.get("binding") or {}
    btype = str(binding.get("type") or "")
    if btype == "load_image":
        return apply_load_image(workflow, int(binding["node_id"]), asset_path, data_root)
    if btype == "vhs_load_video_path":
        return apply_vhs_load_video_path(workflow, int(binding["node_id"]), asset_path, data_root)
    if btype == "prompt_bundle":
        return apply_prompt_bundle(workflow, binding, asset_path)
    raise RuntimeError(f"unsupported binding type {btype!r} for slot {require.get('slot')!r}")


def requires_by_slot(shape: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for req in shape.get("requires") or []:
        if isinstance(req, dict) and req.get("slot"):
            out[str(req["slot"])] = req
    return out


def pick_combinations(
    pool_paths: dict[str, list[Path]],
    *,
    mode: str,
    limit: Optional[int],
) -> list[dict[str, Path]]:
    slots = sorted(pool_paths.keys())
    if not slots:
        return []
    lists = [pool_paths[s] for s in slots]
    if any(not lst for lst in lists):
        empty = [s for s in slots if not pool_paths[s]]
        raise RuntimeError(f"empty pool(s): {', '.join(empty)}")

    combos: list[dict[str, Path]] = []
    if mode == "product":
        for tup in itertools.product(*lists):
            combos.append(dict(zip(slots, tup)))
            if limit and len(combos) >= limit:
                break
    else:
        n = min(len(lst) for lst in lists)
        if limit:
            n = min(n, limit)
        for i in range(n):
            combos.append({s: pool_paths[s][i] for s in slots})
    return combos


def cmd_pools_list(args: argparse.Namespace) -> int:
    pools_doc = load_yaml(Path(args.pools).expanduser().resolve())
    req = requires_by_slot(load_yaml(Path(pools_doc.get("shape") or args.shape).expanduser().resolve())) if (
        pools_doc.get("shape") or args.shape
    ) else {}

    print(f"# Pools `{args.pools}`\n")
    for name, pool_def in (pools_doc.get("pools") or {}).items():
        if not isinstance(pool_def, dict):
            continue
        members = resolve_pool_members(pool_def)
        slot = pool_def.get("slot", name)
        role = (req.get(str(slot)) or {}).get("role", "?")
        print(f"## {name} (slot={slot}, role={role}) — {len(members)} members")
        if pool_def.get("description"):
            print(f"   {pool_def['description']}")
        for path in members[: args.limit]:
            print(f"   - `{path}`")
        if len(members) > args.limit:
            print(f"   … +{len(members) - args.limit} more")
        print()
    return 0


def apply_binds_override(
    pool_paths: dict[str, list[Path]],
    overrides: dict[str, Any],
    *,
    pick_index: int = 0,
) -> dict[str, list[Path]]:
    """Replace slot picks with explicit pool-index bindings (pipeline step 2)."""
    out = dict(pool_paths)
    for slot, spec in (overrides or {}).items():
        if not isinstance(spec, dict):
            continue
        if str(spec.get("from") or "") != "pool":
            continue
        pool_id = str(spec.get("pool") or "").strip()
        if not pool_id:
            continue
        index_path = Path(str(spec.get("index") or default_pool_index_path(pool_id))).expanduser()
        members = pool_index_member_paths(load_pool_index(index_path), pool_id)
        if not members:
            raise RuntimeError(f"binds_override pool {pool_id!r} has no members in {index_path}")
        pick = str(spec.get("pick") or "one").lower()
        if pick in {"one", "first"}:
            idx = min(pick_index, len(members) - 1)
            out[str(slot)] = [members[idx]]
        elif pick in {"last", "newest"}:
            idx = max(0, len(members) - 1 - pick_index)
            out[str(slot)] = [members[idx]]
        elif pick == "zip":
            out[str(slot)] = members
        else:
            raise RuntimeError(f"unsupported binds_override pick {pick!r} for slot {slot!r}")
    return out


def generate_job_for_picks(
    *,
    picks: dict[str, Path],
    shape: dict[str, Any],
    shape_path: Path,
    pools_path: Path,
    template_path: Path,
    data_root: Path,
    workflow_dir: Path,
    job_dir: Path,
    pick_index: int = 0,
    pick_mode: str = "product",
    job_suffix: str = "",
    output_prefix_root: Optional[str] = None,
    job_key_prefix: Optional[str] = None,
    dev: bool = False,
    dev_tuning_path: Optional[str] = None,
    dev_frames: Optional[int] = None,
    dev_steps: Optional[int] = None,
    dev_tuning_override: Optional[dict[str, Any]] = None,
    adhoc_overrides: Optional[dict[str, Any]] = None,
    recipe_output_path: Optional[str] = None,
    disposition_entry: Optional[str] = None,
    disposition_note: Optional[str] = None,
    parent_output: Optional[str] = None,
    rating_kind: Optional[str] = None,
    construction: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Generate one workflow + job metadata file for explicit slot picks."""
    if not template_path.is_file():
        raise RuntimeError(f"template not found: {template_path}")

    req_by_slot = requires_by_slot(shape)
    missing = [s for s, req in req_by_slot.items() if s not in picks and not req.get("optional")]
    if missing:
        raise RuntimeError(f"missing required slot picks: {missing}")

    workflow_dir.mkdir(parents=True, exist_ok=True)
    job_dir.mkdir(parents=True, exist_ok=True)

    family = str(shape.get("family_slug") or shape_path.stem)
    root_tmpl = str(output_prefix_root or shape.get("output_prefix_root") or "og/%date:yyyy-MM-dd%").strip()
    prefix_root = flatten_output_prefix(expand_date_tokens(root_tmpl))

    gen_t0 = time.time()
    workflow = read_json(template_path)
    if not is_litegraph_workflow(workflow):
        raise RuntimeError(f"not a LiteGraph workflow: {template_path}")
    sanitize_linked_text_widget_defaults(workflow)

    warnings: list[str] = []
    bindings_meta: dict[str, Any] = {}
    for slot, path in sorted(picks.items()):
        req = req_by_slot.get(slot)
        if req is None:
            raise RuntimeError(f"unknown slot pick {slot!r}")
        warnings.extend(apply_slot_binding(workflow, req, path, data_root))
        bindings_meta[slot] = {
            "role": req.get("role"),
            "path": str(path),
            "binding_type": (req.get("binding") or {}).get("type"),
        }

    pick_slug = slug("__".join(f"{s}-{Path(picks[s]).stem}" for s in sorted(picks)), 90)
    # Optional leading stem (e.g. "hourly") so filenames don't all sort under family_slug.
    key_stem = str(job_key_prefix or "").strip() or family
    job_key = slug(f"{key_stem}__{pick_slug}__{pick_index:03d}", 120)

    dev_tuning = dev_tuning_override
    if dev_tuning is None:
        dev_tuning = resolve_dev_tuning(
            dev=dev,
            dev_tuning_path=dev_tuning_path,
            dev_frames=dev_frames,
            dev_steps=dev_steps,
            shape=shape,
        )
    dev_changes: dict[str, Any] = {}
    if dev_tuning:
        dev_changes = apply_dev_tuning_ui(workflow, dev_tuning)
        suffix = str(dev_tuning.get("output_prefix_suffix") or "_dev")
        if suffix and not job_key.endswith(suffix):
            job_key = slug(f"{job_key}{suffix}", 120)

    extra_suffix = str(job_suffix or "").strip()
    if extra_suffix:
        # Preserve the unique suffix (e.g. _ui<timestamp>); slug() truncates the end.
        room = max(32, 120 - len(extra_suffix))
        job_key = slug(job_key, room) + extra_suffix

    output_prefix = f"{prefix_root}/{job_key}"
    changes = strip_video_previews_and_redirect_outputs(workflow, output_prefix)

    workflow_out = workflow_dir / family / f"{job_key}.workflow.json"
    workflow_out.parent.mkdir(parents=True, exist_ok=True)
    workflow_out.write_text(json.dumps(workflow, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")

    job_meta: dict[str, Any] = {
        "schema_version": "comfyui-runpod.shape-job.v0",
        "created_at": utc_now(),
        "shape_path": str(shape_path),
        "pools_path": str(pools_path),
        "shape_id": shape.get("shape_id"),
        "family_slug": family,
        "graph_hash": shape.get("graph_hash"),
        "job_key": job_key,
        "pick_index": pick_index,
        "pick_mode": pick_mode,
        "bindings": bindings_meta,
        "output_prefix": output_prefix,
        "generated_workflow_path": str(workflow_out),
        "template_path": str(template_path),
        "changes": changes,
        "warnings": warnings,
        "deposits": shape.get("deposits") or {},
    }
    if disposition_entry:
        job_meta["disposition_entry"] = str(disposition_entry).strip()
        if disposition_note:
            job_meta["disposition_note"] = str(disposition_note).strip()
    if parent_output:
        job_meta["parent_output"] = str(parent_output).strip()
    if rating_kind:
        job_meta["rating_kind"] = str(rating_kind).strip()
    if isinstance(construction, dict) and construction:
        # Compact selection/debug trail from hourly plan (or other callers).
        job_meta["construction"] = construction
    if recipe_output_path:
        out_raw = str(recipe_output_path).strip()
        if out_raw:
            job_meta["recipe_output_path"] = out_raw
            seed_candidate = Path(out_raw).expanduser()
            if seed_candidate.suffix.lower() != ".png":
                seed_candidate = seed_candidate.with_suffix(".png")
            if seed_candidate.is_file() and _png_has_api_prompt(seed_candidate):
                job_meta["prompt_seed_png"] = str(seed_candidate.resolve())
    if adhoc_overrides:
        job_meta["adhoc_overrides"] = adhoc_overrides
    if dev_tuning:
        job_meta["dev_tuning"] = {
            "profile_id": dev_tuning.get("profile_id"),
            "path": dev_tuning_path or (str(DEFAULT_DEV_TUNING) if dev else None),
            "applied_ui": dev_changes,
            "spec": {
                "ui_nodes": dev_tuning.get("ui_nodes"),
                "api_nodes": dev_tuning.get("api_nodes"),
                "vhs_load_video_path": dev_tuning.get("vhs_load_video_path"),
                "output_prefix_suffix": dev_tuning.get("output_prefix_suffix"),
            },
        }
    job_path = job_dir / family / f"{job_key}.job.json"
    job_path.parent.mkdir(parents=True, exist_ok=True)
    gen_t1 = time.time()
    prev_timings: dict[str, Any] = {}
    if job_path.is_file():
        try:
            prev = json.loads(job_path.read_text(encoding="utf-8"))
            if isinstance(prev.get("submit"), dict) and prev["submit"].get("prompt_id"):
                job_meta["submit"] = prev["submit"]
            if isinstance(prev.get("deposit"), dict):
                job_meta["deposit"] = prev["deposit"]
            if isinstance(prev.get("timings"), dict):
                prev_timings = prev["timings"]
        except Exception:
            pass
    job_meta["timings"] = deep_merge_timings(
        prev_timings if prev_timings else {"schema_version": TIMINGS_SCHEMA},
        {
            "generate": {
                "started_ts": gen_t0,
                "finished_ts": gen_t1,
                "sec": round(gen_t1 - gen_t0, 3),
            }
        },
    )
    capture_job_workload(job_meta, workflow)
    job_path.write_text(json.dumps(job_meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    persist_timings(job_path, job_meta)

    return {
        "job_key": job_key,
        "job_path": job_path,
        "workflow_path": workflow_out,
        "job_meta": job_meta,
        "bindings": bindings_meta,
    }


def cmd_generate(args: argparse.Namespace) -> int:
    shape_path = Path(args.shape).expanduser().resolve()
    pools_path = Path(args.pools).expanduser().resolve()
    shape = load_yaml(shape_path)
    pools_doc = load_yaml(pools_path)
    data_root = Path(args.data_root).expanduser().resolve()
    template_path = Path(str(shape["template"])).expanduser().resolve()
    if not template_path.exists():
        raise RuntimeError(f"template not found: {template_path}")

    quarantine_path = Path(getattr(args, "quarantine_path", DEFAULT_QUARANTINE_PATH)).expanduser().resolve()
    registry, _effective = load_effective_quarantine_registry(
        data_root=data_root,
        quarantine_path=quarantine_path,
    )
    assert_workflows_not_quarantined(
        registry,
        [template_path],
        ignore=bool(getattr(args, "ignore_quarantine", False)),
    )

    req_by_slot = requires_by_slot(shape)
    pool_paths: dict[str, list[Path]] = {}
    for _name, pool_def in (pools_doc.get("pools") or {}).items():
        if not isinstance(pool_def, dict):
            continue
        slot = str(pool_def.get("slot") or "")
        req = req_by_slot.get(slot)
        if req is None:
            print(f"warning: pool slot {slot!r} not in shape requires", file=sys.stderr)
            continue
        members = resolve_pool_members(pool_def)
        if not members:
            if req.get("optional"):
                print(f"warning: optional pool {slot!r} has no members; skipping slot", file=sys.stderr)
                continue
            print(f"warning: pool {slot!r} has no members", file=sys.stderr)
        pool_paths[slot] = members

    binds_override = getattr(args, "binds_override", None)
    pick_index = int(getattr(args, "pick_index", 0) or 0)
    if isinstance(binds_override, dict) and binds_override:
        pool_paths = apply_binds_override(pool_paths, binds_override, pick_index=pick_index)

    missing = [
        s
        for s, req in req_by_slot.items()
        if s not in pool_paths and not req.get("optional")
    ]
    if missing:
        raise RuntimeError(f"shape requires slots without pools: {missing}")

    combo_limit = int(args.limit or 1)
    picks_json = getattr(args, "picks_json", None)
    if picks_json:
        raw = json.loads(Path(picks_json).expanduser().read_text(encoding="utf-8"))
        picks_raw = raw.get("picks") if isinstance(raw.get("picks"), dict) else raw
        if not isinstance(picks_raw, dict) or not picks_raw:
            raise RuntimeError(f"--picks-json must be a slot→path object or {{\"picks\": ...}}: {picks_json}")
        combos = [{str(slot): Path(str(path)).expanduser().resolve() for slot, path in picks_raw.items()}]
        pick_mode = str(raw.get("pick_mode") or "replay")
        recipe_output_path = str(raw.get("output_path") or "").strip() or None
        disposition_entry = str(raw.get("disposition_entry") or "").strip() or None
        disposition_note = str(raw.get("disposition_note") or "").strip() or None
        parent_output = str(raw.get("parent_output") or "").strip() or None
        rating_kind = str(raw.get("rating_kind") or "").strip() or None
        # Predicted hourly seeds always land as derived disposition.
        if rating_kind == "predicted" or str(raw.get("step") or "") == "predicted_derive":
            disposition_entry = disposition_entry or "derived"
            pick_mode = "derive"
        try:
            from shape_factory_work_products import construction_from_plan
        except ImportError:
            construction_from_plan = None  # type: ignore
        construction = construction_from_plan(raw) if construction_from_plan else None
        if isinstance(raw.get("construction"), dict) and raw.get("construction"):
            construction = {**(construction or {}), **raw["construction"]}
    else:
        fetch_limit = combo_limit + pick_index if pick_index else combo_limit
        combos = pick_combinations(pool_paths, mode=args.pick, limit=fetch_limit if args.pick == "zip" else args.limit)
        if pick_index:
            if pick_index >= len(combos):
                raise RuntimeError(f"pick_index={pick_index} out of range (combos={len(combos)})")
            combos = combos[pick_index : pick_index + combo_limit]
        pick_mode = str(args.pick)
        recipe_output_path = None
        disposition_entry = None
        disposition_note = None
        parent_output = None
        rating_kind = None
        construction = None
    if not combos:
        print("generated_jobs=0")
        return 1

    workflow_dir = Path(args.workflow_dir).expanduser().resolve()
    job_dir = Path(args.job_dir).expanduser().resolve()
    job_suffix = str(getattr(args, "job_suffix", "") or "").strip()
    output_prefix_root = str(getattr(args, "output_prefix_root", "") or "").strip() or None
    job_key_prefix = str(getattr(args, "job_key_prefix", "") or "").strip() or None

    generated = 0
    for idx, picks in enumerate(combos):
        result = generate_job_for_picks(
            picks=picks,
            shape=shape,
            shape_path=shape_path,
            pools_path=pools_path,
            template_path=template_path,
            data_root=data_root,
            workflow_dir=workflow_dir,
            job_dir=job_dir,
            pick_index=idx,
            pick_mode=pick_mode,
            job_suffix=job_suffix,
            output_prefix_root=output_prefix_root,
            job_key_prefix=job_key_prefix,
            dev=bool(getattr(args, "dev", False)),
            dev_tuning_path=getattr(args, "dev_tuning", None),
            dev_frames=getattr(args, "dev_frames", None),
            dev_steps=getattr(args, "dev_steps", None),
            recipe_output_path=recipe_output_path,
            disposition_entry=disposition_entry,
            disposition_note=disposition_note,
            parent_output=parent_output,
            rating_kind=rating_kind,
            construction=construction,
        )
        print(f"generated_workflow={result['workflow_path']}")
        print(f"job_metadata={result['job_path']}")
        for slot, meta in result["bindings"].items():
            print(f"  bind {slot} ({meta.get('role')}) <- `{Path(meta['path']).name}`")
        generated += 1

    print(f"generated_jobs={generated}")
    return 0


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def iter_job_paths(args: argparse.Namespace) -> list[Path]:
    paths: list[Path] = []
    if args.job:
        paths.append(Path(args.job).expanduser().resolve())
    if args.jobs_dir:
        root = Path(args.jobs_dir).expanduser().resolve()
        paths.extend(sorted(root.rglob("*.job.json")))
    if args.family:
        root = Path(args.job_dir).expanduser().resolve() / args.family
        paths.extend(sorted(root.glob("*.job.json")))
    # dedupe preserve order
    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if p.is_file():
            out.append(p)
    if args.limit and len(out) > args.limit:
        out = out[: args.limit]
    return out


def job_already_submitted(job: dict[str, Any]) -> bool:
    submit = job.get("submit")
    if not isinstance(submit, dict):
        return False
    pid = submit.get("prompt_id")
    return isinstance(pid, str) and bool(pid.strip())


def job_abandoned(job: dict[str, Any]) -> bool:
    submit = job.get("submit")
    if not isinstance(submit, dict):
        return False
    return str(submit.get("status") or "").strip().lower() == "abandoned"


def submit_max_attempts() -> int:
    """Max failed submit attempts before a job is abandoned (env override)."""
    raw = os.environ.get("SHAPE_FACTORY_SUBMIT_MAX_ATTEMPTS", "3").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


def submit_attempt_count(job: dict[str, Any]) -> int:
    submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    for key in ("attempts", "fail_count"):
        raw = submit.get(key)
        if isinstance(raw, int) and raw >= 0:
            return raw
        if isinstance(raw, str) and raw.strip().isdigit():
            return int(raw.strip())
    # Legacy error rows without a counter already failed at least once.
    if str(submit.get("status") or "").strip().lower() == "error":
        return 1
    return 0


def job_submit_failed(job: dict[str, Any]) -> bool:
    """True when a prior submit attempt failed (no prompt_id) and status is error."""
    submit = job.get("submit")
    if not isinstance(submit, dict):
        return False
    return str(submit.get("status") or "").strip().lower() == "error"


def job_retries_exhausted(job: dict[str, Any]) -> bool:
    """True when failed attempts have reached the retry cap."""
    if job_abandoned(job):
        return True
    if not job_submit_failed(job):
        return False
    return submit_attempt_count(job) >= submit_max_attempts()


def is_permanent_submit_failure(exc: BaseException | str) -> bool:
    """Failures unlikely to self-heal (still counted toward the retry cap)."""
    msg = str(exc).lower()
    needles = (
        "invalid image file",
        "custom_validation_failed",
        "no companion png",
        "cannot build api prompt",
        "workflow missing",
        "shape missing",
        "quarantined template",
        "not a litegraph workflow",
    )
    return any(n in msg for n in needles)


def abandon_submit_failure(
    job: dict[str, Any],
    *,
    error: str,
    server: str = "",
    previous_status: str = "error",
    attempts: Optional[int] = None,
) -> None:
    """Mark a job abandoned so hourly/pending-only will never retry it."""
    prev = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    n = int(attempts if attempts is not None else submit_attempt_count(job) or submit_max_attempts())
    job["submit"] = {
        "status": "abandoned",
        "error": str(error),
        "attempts": n,
        "max_attempts": submit_max_attempts(),
        "abandoned_at": utc_now(),
        "abandoned_from": previous_status,
        "attempted_at": utc_now(),
        "comfy_server": server or str(prev.get("comfy_server") or ""),
    }


def record_submit_failure(
    job: dict[str, Any],
    *,
    error: str,
    server: str = "",
) -> str:
    """
    Record a failed submit attempt.

    Increments ``submit.attempts``. Returns ``\"error\"`` while retries remain,
    otherwise marks abandoned and returns ``\"abandoned\"``.
    """
    prev = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    attempts = submit_attempt_count(job) + 1
    max_attempts = submit_max_attempts()
    if attempts >= max_attempts:
        abandon_submit_failure(
            job,
            error=str(error),
            server=server,
            previous_status=str(prev.get("status") or "error"),
            attempts=attempts,
        )
        return "abandoned"
    job["submit"] = {
        "status": "error",
        "error": str(error),
        "attempts": attempts,
        "max_attempts": max_attempts,
        "attempted_at": utc_now(),
        "comfy_server": server or str(prev.get("comfy_server") or ""),
        "retryable": True,
        "permanent_hint": is_permanent_submit_failure(error),
    }
    return "error"


def job_pending_submit(job: dict[str, Any]) -> bool:
    if job_already_submitted(job) or job_abandoned(job):
        return False
    if job_submit_failed(job):
        return submit_attempt_count(job) < submit_max_attempts()
    return True


def png_path_for_binding(asset_path: Path) -> Path:
    if asset_path.suffix.lower() in VIDEO_EXTS:
        return asset_path.with_suffix(".png")
    return asset_path


def extract_api_prompt_from_png(png_path: Path) -> dict[str, Any]:
    chunks = read_png_text_chunks(png_path)
    prompt, _workflow = extract_prompt_workflow_from_png_chunks(chunks)
    if not isinstance(prompt, dict) or not prompt:
        raise RuntimeError(f"no API prompt in PNG metadata: {png_path}")
    return copy.deepcopy(prompt)


def _png_has_api_prompt(png_path: Path) -> bool:
    try:
        chunks = read_png_text_chunks(png_path)
        prompt, _workflow = extract_prompt_workflow_from_png_chunks(chunks)
        return isinstance(prompt, dict) and bool(prompt)
    except Exception:
        return False


def prompt_seed_path_for_job(job: dict[str, Any], *, data_root: Optional[Path] = None) -> Optional[Path]:
    """Best PNG to seed API prompt when /workflow/convert is unavailable."""
    bindings = job.get("bindings") if isinstance(job.get("bindings"), dict) else {}
    dr = (data_root or DEFAULT_DATA_ROOT).expanduser().resolve()
    candidates: list[Path] = []
    seen: set[str] = set()

    def add(path_like: Optional[str | Path]) -> None:
        if path_like is None:
            return
        raw = str(path_like).strip()
        if not raw:
            return
        try:
            asset = resolve_job_asset_path(raw, data_root=dr)
        except FileNotFoundError:
            asset = Path(raw).expanduser()
        png = png_path_for_binding(asset) if asset.suffix.lower() != ".png" else asset
        key = str(png)
        if key in seen:
            return
        seen.add(key)
        candidates.append(png)

    # Explicit stamp from hourly/OG recipe planning.
    add(job.get("prompt_seed_png"))
    add(job.get("recipe_output_path"))

    for slot in ("source_video", "source_still"):
        meta = bindings.get(slot)
        if isinstance(meta, dict):
            add(meta.get("path"))

    for _slot, meta in bindings.items():
        if isinstance(meta, dict):
            add(meta.get("path"))

    for png in candidates:
        try:
            if png.is_file() and _png_has_api_prompt(png):
                return png.resolve()
        except Exception:
            continue

    # Same-directory fallback: many OG source videos lack a sibling PNG, but a
    # nearby family render PNG still carries a usable API prompt graph.
    for meta in bindings.values():
        if not isinstance(meta, dict):
            continue
        raw = str(meta.get("path") or "").strip()
        if not raw:
            continue
        try:
            asset = resolve_job_asset_path(raw, data_root=dr)
        except FileNotFoundError:
            continue
        parent = asset.parent if asset.suffix.lower() != ".png" else asset.parent
        family = str(job.get("family_slug") or "").strip()
        patterns = []
        if family:
            patterns.append(f"{family}_*.png")
        patterns.append("*.png")
        for pattern in patterns:
            try:
                hits = sorted(parent.glob(pattern))
            except Exception:
                hits = []
            for hit in hits:
                if hit.is_file() and _png_has_api_prompt(hit):
                    return hit.resolve()

    return None


def apply_api_slot_bindings(
    prompt: dict[str, Any],
    shape: dict[str, Any],
    job: dict[str, Any],
    data_root: Path,
) -> list[str]:
    """Patch API prompt dict using shape slot bindings (fallback when /workflow/convert unavailable)."""
    warnings: list[str] = []
    req_by_slot = requires_by_slot(shape)
    bindings = job.get("bindings") if isinstance(job.get("bindings"), dict) else {}

    for slot, meta in bindings.items():
        req = req_by_slot.get(slot)
        if not req:
            continue
        binding = req.get("binding") or {}
        btype = str(binding.get("type") or "")
        raw_path = str(meta.get("path") or "").strip()
        if not raw_path:
            continue
        try:
            asset_path = resolve_job_asset_path(raw_path, data_root=data_root)
        except FileNotFoundError:
            warnings.append(f"missing binding asset for slot {slot!r}: {raw_path}")
            continue

        if btype == "vhs_load_video_path":
            rel, warn = comfy_workspace_relpath(asset_path, data_root)
            if warn:
                warnings.append(warn)
            node_id = binding.get("node_id")
            patched = 0
            for key, node in prompt.items():
                if not isinstance(node, dict) or node.get("class_type") != "VHS_LoadVideoPath":
                    continue
                if node_id is not None and str(key) != str(node_id):
                    continue
                node.setdefault("inputs", {})["video"] = rel
                patched += 1
            if patched == 0:
                warnings.append(f"no VHS_LoadVideoPath node {node_id!r} in API prompt")
        elif btype == "load_image":
            rel, warn = comfy_workspace_relpath(asset_path, data_root)
            if warn:
                warnings.append(warn)
            node_id = binding.get("node_id")
            patched = 0
            for key, node in prompt.items():
                if not isinstance(node, dict) or node.get("class_type") != "LoadImage":
                    continue
                if node_id is not None and str(key) != str(node_id):
                    continue
                node.setdefault("inputs", {})["image"] = rel
                patched += 1
            if patched == 0:
                warnings.append(f"no LoadImage node {node_id!r} in API prompt")
        elif btype == "prompt_bundle":
            profile = json.loads(asset_path.read_text(encoding="utf-8"))
            if not isinstance(profile, dict):
                raise RuntimeError(f"prompt profile is not JSON object: {asset_path}")
            pos = str(profile.get("positive") or "")
            neg = str(profile.get("negative") or "")
            pos_spec = binding.get("positive") if isinstance(binding.get("positive"), dict) else {}
            neg_spec = binding.get("negative") if isinstance(binding.get("negative"), dict) else {}
            pos_id = pos_spec.get("node_id")
            neg_id = neg_spec.get("node_id")
            pos_key = str(pos_spec.get("input") or pos_spec.get("input_key") or "text")
            neg_key = str(neg_spec.get("input") or neg_spec.get("input_key") or "text")

            def _set_text(node_key: Any, text: str, input_key: str) -> bool:
                node = prompt.get(str(node_key))
                if not isinstance(node, dict):
                    return False
                inputs = node.setdefault("inputs", {})
                inputs[input_key] = text
                return True

            if pos_id is not None or neg_id is not None:
                if pos_id is not None and not _set_text(pos_id, pos, pos_key):
                    warnings.append(f"prompt positive node {pos_id!r} not found in API prompt")
                if neg_id is not None and not _set_text(neg_id, neg, neg_key):
                    warnings.append(f"prompt negative node {neg_id!r} not found in API prompt")
            else:
                warnings.append(
                    f"prompt_bundle for slot {slot!r} missing node_id; falling back to class_type paint"
                )
                for node in prompt.values():
                    if not isinstance(node, dict):
                        continue
                    ct = str(node.get("class_type") or "")
                    inputs = node.setdefault("inputs", {})
                    if ct == "CLIPTextEncode" and isinstance(inputs.get("text"), str):
                        inputs["text"] = neg
                    if ct in {"Text Multiline", "PrimitiveStringMultiline"}:
                        inputs["text"] = pos
            if not pos and not neg:
                warnings.append(f"empty prompt profile: {asset_path.name}")

    prefix = str(job.get("output_prefix") or "").rstrip("/")
    prefix = flatten_output_prefix(prefix)
    if prefix:
        for node in prompt.values():
            if not isinstance(node, dict) or node.get("class_type") != "VHS_VideoCombine":
                continue
            inputs = node.setdefault("inputs", {})
            if inputs.get("save_output") is False:
                continue
            if inputs.get("save_metadata") is True:
                inputs["filename_prefix"] = prefix
            else:
                inputs["filename_prefix"] = f"{prefix}_PREVIEW"

    return warnings


def sync_prompt_inputs_from_ui_workflow(workflow: dict[str, Any], prompt: dict[str, Any]) -> list[str]:
    """Reconcile API prompt links from LiteGraph link table (fixes /workflow/convert misroutes)."""
    warnings: list[str] = []
    link_by_id: dict[Any, list[Any]] = {}
    for link in workflow.get("links") or []:
        if isinstance(link, list) and link:
            link_by_id[link[0]] = link
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        nid = str(node.get("id"))
        api_node = prompt.get(nid)
        if not isinstance(api_node, dict):
            continue
        inputs = api_node.setdefault("inputs", {})
        for inp in node.get("inputs") or []:
            if not isinstance(inp, dict):
                continue
            link_id = inp.get("link")
            name = inp.get("name")
            if link_id is None or not name:
                continue
            link = link_by_id.get(link_id)
            if not link or len(link) < 3:
                continue
            desired: list[Any] = [str(link[1]), link[2]]
            if str(link[1]) not in prompt:
                # Bypassed/omitted UI sources: keep convert's wiring (it often already
                # walked the bypass). Broken IMAGE→STRING links are dropped by sanitize.
                continue
            if inputs.get(name) != desired:
                warnings.append(f"relinked {nid}.{name}: {inputs.get(name)!r} -> {desired}")
                inputs[name] = desired
    return warnings


def sanitize_converted_prompt(workflow: dict[str, Any], prompt: dict[str, Any]) -> list[str]:
    """Fix common /workflow/convert issues before Comfy validation."""
    warnings = [f.summary for f in sanitize_prompt_string_inputs(workflow, prompt)]
    warnings.extend(normalize_prompt_output_prefixes(prompt))
    return warnings


def comfy_node_errors(submit_body: dict[str, Any]) -> dict[str, Any]:
    errors = submit_body.get("node_errors")
    return errors if isinstance(errors, dict) and errors else {}


def ffprobe_video_info(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_streams",
                "-count_frames",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if proc.returncode != 0:
            return {"error": (proc.stderr or proc.stdout or "ffprobe failed").strip()[:200]}
        obj = json.loads(proc.stdout or "{}")
        streams = obj.get("streams") if isinstance(obj.get("streams"), list) else []
        video = next((s for s in streams if isinstance(s, dict) and s.get("codec_type") == "video"), None)
        if not isinstance(video, dict):
            return {}
        info: dict[str, Any] = {}
        for key in ("nb_read_frames", "nb_frames", "duration", "avg_frame_rate", "width", "height"):
            if video.get(key) is not None:
                info[key] = video.get(key)
        if "nb_read_frames" in info:
            info["frame_count"] = int(info["nb_read_frames"])
        elif "nb_frames" in info:
            try:
                info["frame_count"] = int(str(info["nb_frames"]))
            except Exception:
                pass
        return info
    except FileNotFoundError:
        return {"error": "ffprobe not installed"}
    except Exception as exc:
        return {"error": str(exc)}


def probe_job_output_media(job: dict[str, Any], data_root: Path) -> list[dict[str, Any]]:
    submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    outputs = submit.get("outputs")
    paths: list[Path] = []
    if isinstance(outputs, list):
        paths = [Path(str(p)).expanduser() for p in outputs if str(p).lower().endswith(".mp4")]
    if not paths:
        paths = discover_job_outputs(job, data_root)
    out: list[dict[str, Any]] = []
    for path in paths:
        rec = {"path": str(path.resolve()), "probe": ffprobe_video_info(path)}
        out.append(rec)
    return out


def parse_history_node_timings(history: dict[str, Any]) -> dict[str, Any]:
    """Best-effort per-node timing from Comfy history status.messages."""
    status = history.get("status") if isinstance(history.get("status"), dict) else {}
    node_last: dict[str, float] = {}
    node_total: dict[str, float] = {}
    prev_ts: Optional[float] = None
    for msg in status.get("messages") or []:
        if not isinstance(msg, (list, tuple)) or not msg:
            continue
        kind = str(msg[0])
        payload = msg[1] if len(msg) > 1 and isinstance(msg[1], dict) else {}
        ts = payload.get("timestamp")
        ts_f = normalize_comfy_timestamp(float(ts)) if isinstance(ts, (int, float)) else None
        if ts_f is not None and ts_f < 1_000_000_000:
            ts_f = None
        if kind == "execution_start" and ts_f is not None:
            prev_ts = ts_f
            continue
        if kind == "executing":
            node = payload.get("node")
            if node is None:
                continue
            node_id = str(node)
            if ts_f is None:
                continue
            if prev_ts is not None:
                node_total[node_id] = node_total.get(node_id, 0.0) + max(0.0, ts_f - prev_ts)
            node_last[node_id] = ts_f
            prev_ts = ts_f
    nodes = {
        node_id: {"sec": round(sec, 3)}
        for node_id, sec in sorted(node_total.items(), key=lambda kv: kv[1], reverse=True)
    }
    if not nodes:
        return {}
    total = round(sum(node_total.values()), 3)
    return {"nodes": nodes, "tracked_sec": total, "source": "history.messages"}


def backfill_timings_from_submit_record(timings: dict[str, Any], submit_record: dict[str, Any]) -> None:
    submit = timings.setdefault("submit", {})
    for src, dst in (
        ("submit_started_ts", "started_ts"),
        ("submit_finished_ts", "finished_ts"),
        ("prompt_prepare_sec", "prompt_prepare_sec"),
        ("submit_http_sec", "submit_http_sec"),
        ("submit_http_sec_total", "total_sec"),
    ):
        if submit_record.get(src) is not None:
            submit[dst] = submit_record[src]
    if submit.get("total_sec") is None and submit.get("started_ts") and submit.get("finished_ts"):
        submit["total_sec"] = round(float(submit["finished_ts"]) - float(submit["started_ts"]), 3)
    if submit_record.get("submitted_at"):
        queue = timings.setdefault("queue", {})
        queue.setdefault("submitted_at", submit_record.get("submitted_at"))
        if submit_record.get("submit_finished_ts") is not None:
            queue.setdefault("submitted_ts", submit_record.get("submit_finished_ts"))


def repair_job_from_sidecars(
    job_path: Path,
    *,
    data_root: Path,
    server: str,
    refresh_prompt: bool = False,
    convert_timeout: int = 180,
) -> dict[str, Any]:
    job = json.loads(job_path.read_text(encoding="utf-8"))
    changes: list[str] = []

    submit_path = job_path.with_name(job_path.stem.replace(".job", "") + ".submit.json")
    if submit_path.is_file():
        submit_record = json.loads(submit_path.read_text(encoding="utf-8"))
        backfill_timings_from_submit_record(ensure_timings(job), submit_record)
        node_errors = comfy_node_errors(submit_record.get("comfy_response") or {})
        submit_block = job.setdefault("submit", {})
        if submit_record.get("prompt_id"):
            submit_block["prompt_id"] = submit_record.get("prompt_id")
        if submit_record.get("prompt_source"):
            submit_block["prompt_source"] = submit_record.get("prompt_source")
        if submit_record.get("submitted_at"):
            submit_block["submitted_at"] = submit_record.get("submitted_at")
        submit_block["submit_path"] = str(submit_path)
        if submit_record.get("prompt_path"):
            submit_block["prompt_path"] = submit_record.get("prompt_path")
        if node_errors:
            submit_block["status"] = "error"
            submit_block["node_errors"] = node_errors
            changes.append("marked error from submit node_errors")
        elif submit_block.get("status") in {None, "pending"}:
            submit_block["status"] = "queued"
            changes.append("restored submit block from sidecar")

    timings_path = timings_sidecar_path(job_path)
    if timings_path.is_file():
        sidecar = json.loads(timings_path.read_text(encoding="utf-8"))
        if isinstance(sidecar, dict):
            job["timings"] = deep_merge_timings(job.get("timings") or {"schema_version": TIMINGS_SCHEMA}, sidecar)

    submit_status = str((job.get("submit") or {}).get("status") or "")
    execution = (job.get("timings") or {}).get("execution") if isinstance(job.get("timings"), dict) else {}
    if submit_status not in {"complete", "error"} and isinstance(execution, dict):
        if execution.get("sec") and execution.get("source") != "history.messages":
            execution.clear()
            changes.append("cleared stale execution timings")

    workflow_path = Path(str(job.get("generated_workflow_path") or "")).expanduser()
    if refresh_prompt and workflow_path.is_file() and is_litegraph_workflow(read_json(workflow_path)):
        shape_path = Path(str(job.get("shape_path") or "")).expanduser()
        shape = load_yaml(shape_path) if shape_path.is_file() else {}
        workflow = read_json(workflow_path)
        if not job.get("timings", {}).get("workload"):
            capture_job_workload(job, workflow)
        prompt_obj, prompt_source, prep_warnings = resolve_prompt_for_job(
            job, shape, workflow, data_root, server, convert_timeout
        )
        prompt_path = job_path.with_name(job_path.stem.replace(".job", "") + ".prompt.json")
        atomic_write_json(prompt_path, prompt_obj)
        job.setdefault("submit", {})["prompt_path"] = str(prompt_path)
        job.setdefault("submit", {})["prompt_source"] = prompt_source
        changes.append(f"refreshed prompt ({prompt_source}, {len(prep_warnings)} relinks)")

    persist_timings(job_path, job)
    atomic_write_json(job_path, job)
    return {"job_key": job.get("job_key"), "changes": changes, "status": (job.get("submit") or {}).get("status")}


def cmd_jobs_repair(args: argparse.Namespace) -> int:
    job_paths = iter_job_paths(args)
    if not job_paths:
        print("error: no job files found", file=sys.stderr)
        return 1
    data_root = Path(args.data_root).expanduser().resolve()
    server = str(args.server).rstrip("/")
    print(f"# Shape factory jobs repair\n")
    for job_path in job_paths:
        result = repair_job_from_sidecars(
            job_path,
            data_root=data_root,
            server=server,
            refresh_prompt=bool(args.refresh_prompts),
            convert_timeout=args.convert_timeout,
        )
        changes = result.get("changes") or []
        hint = "; ".join(changes) if changes else "ok"
        print(f"{result.get('job_key')}: status={result.get('status')} {hint}")
    return 0


_OBJECT_INFO_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_VALIDATE_UI_NODE_TYPES = {
    "PrimitiveNode",
    "Note",
    "MarkdownNote",
    "Reroute",
    "Fast Groups Bypasser (rgthree)",
    "Fast Groups Muter (rgthree)",
    "Fast Groups Bypasser",
    "Fast Groups Muter",
}


def fetch_object_info(server: str, *, timeout_s: int = 120, cache_ttl_s: float = 300.0) -> dict[str, Any]:
    server = server.rstrip("/")
    now = time.time()
    cached = _OBJECT_INFO_CACHE.get(server)
    if cached and (now - cached[0]) < cache_ttl_s:
        return cached[1]
    obj = _http_json("GET", f"{server}/object_info", timeout_s=timeout_s)
    if not isinstance(obj, dict):
        raise RuntimeError("Comfy /object_info returned non-object")
    _OBJECT_INFO_CACHE[server] = (now, obj)
    return obj


def missing_node_types(workflow: dict[str, Any], object_info: dict[str, Any]) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("type") or "")
        if not class_type or class_type in object_info or class_type in seen:
            continue
        seen.add(class_type)
        missing.append(
            {
                "node_id": node.get("id"),
                "class_type": class_type,
                "title": node.get("title"),
            }
        )
    return missing


def backup_workflow_file(workflow_path: Path) -> Path:
    workflow_path = workflow_path.expanduser().resolve()
    stamp = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = workflow_path.with_name(workflow_path.name + f".bak.{stamp}")
    backup.write_bytes(workflow_path.read_bytes())
    return backup


def maybe_auto_patch_workflow(
    workflow_path: Path,
    workflow: dict[str, Any],
    *,
    server: str,
    auto_patch: bool,
    write_patches: bool,
    map_path: Path,
    object_info: Optional[dict[str, Any]] = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], Optional[Path]]:
    if not auto_patch:
        return workflow, [], None
    if object_info is None:
        try:
            object_info = fetch_object_info(server)
        except Exception:
            object_info = None
    ctx = RepairContext(workflow=workflow, object_info=object_info, map_path=map_path)
    loop = repair_until_stable(ctx, rules=default_repair_rules(map_path, None), validate_fn=None, max_rounds=1)
    backup_path: Optional[Path] = None
    if loop.fixes and write_patches:
        backup_path = backup_workflow_file(workflow_path)
        atomic_write_json(workflow_path, ctx.workflow)
    return ctx.workflow, loop.fixes_as_dicts(), backup_path


def validate_workflow_document(
    *,
    workflow_path: Path,
    workflow: dict[str, Any],
    server: str,
    data_root: Path,
    shape: Optional[dict[str, Any]] = None,
    job: Optional[dict[str, Any]] = None,
    convert_timeout: int = 180,
    comfy_check: bool = False,
    compat_patches: Optional[list[dict[str, Any]]] = None,
    patch_backup_path: Optional[str] = None,
    prompt_override: Optional[dict[str, Any]] = None,
    repair_round: Optional[int] = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": "comfyui-runpod.shape-validate.v0",
        "validated_at": utc_now(),
        "workflow_path": str(workflow_path),
        "ok": True,
        "missing_node_types": [],
        "convert_ok": False,
        "convert_error": None,
        "sanitize_warnings": [],
        "relink_warnings": [],
        "prompt_source": None,
        "node_errors": {},
        "comfy_check_prompt_id": None,
        "compat_patches": compat_patches or [],
        "patch_backup_path": patch_backup_path,
        "repair_round": repair_round,
    }
    try:
        object_info = fetch_object_info(server)
        missing = missing_node_types(workflow, object_info)
        required_missing = [m for m in missing if str(m.get("class_type") or "") not in _VALIDATE_UI_NODE_TYPES]
        ui_missing = [m for m in missing if str(m.get("class_type") or "") in _VALIDATE_UI_NODE_TYPES]
        report["missing_node_types"] = missing
        report["missing_required_node_types"] = required_missing
        report["missing_ui_node_types"] = ui_missing
        if required_missing:
            report["ok"] = False
    except Exception as exc:
        report["ok"] = False
        report["object_info_error"] = str(exc)

    try:
        if isinstance(prompt_override, dict):
            prompt_obj = copy.deepcopy(prompt_override)
            report["convert_ok"] = True
            report["prompt_source"] = "repair_prompt_override"
        else:
            prompt_obj = convert_ui_workflow_to_prompt(server, workflow, timeout_s=convert_timeout)
            report["convert_ok"] = True
            report["prompt_source"] = "workflow_convert"
        report["relink_warnings"] = sync_prompt_inputs_from_ui_workflow(workflow, prompt_obj)
        report["sanitize_warnings"] = sanitize_converted_prompt(workflow, prompt_obj)
        if isinstance(job, dict) and shape:
            dev_spec = job.get("dev_tuning", {}).get("spec") if isinstance(job.get("dev_tuning"), dict) else None
            if isinstance(dev_spec, dict):
                apply_dev_tuning_api(prompt_obj, dev_spec)
            warnings = apply_api_slot_bindings(prompt_obj, shape, job, data_root)
            report["binding_warnings"] = warnings
        report["prompt_object"] = prompt_obj
        if comfy_check:
            submit_body = submit_prompt_to_comfyui(
                server,
                prompt_obj,
                workflow_ui=workflow,
                client_id="shape_factory_validate",
                timeout_s=60,
            )
            node_errors = comfy_node_errors(submit_body)
            report["node_errors"] = node_errors
            prompt_id = submit_body.get("prompt_id")
            if isinstance(prompt_id, str) and prompt_id.strip():
                report["comfy_check_prompt_id"] = prompt_id
                try:
                    _http_json("POST", f"{server.rstrip('/')}/queue", {"delete": [prompt_id]}, timeout_s=15)
                    report["comfy_check_dequeued"] = True
                except Exception as exc:
                    report["comfy_check_dequeued"] = False
                    report["comfy_check_dequeue_error"] = str(exc)
                    # Prompt was accepted; dequeue failure is non-fatal for validation.
            if node_errors:
                report["ok"] = False
    except Exception as exc:
        report["convert_ok"] = False
        report["convert_error"] = str(exc)
        report["ok"] = False

    return report


def validate_with_repair_loop(
    *,
    workflow_path: Path,
    workflow: dict[str, Any],
    server: str,
    data_root: Path,
    shape: Optional[dict[str, Any]] = None,
    job: Optional[dict[str, Any]] = None,
    convert_timeout: int = 180,
    comfy_check: bool = False,
    auto_repair: bool = True,
    write_patches: bool = True,
    map_path: Optional[Path] = None,
    repair_rules_path: Optional[Path] = None,
    max_repair_rounds: int = 5,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], Optional[Path]]:
    map_path = (map_path or DEFAULT_NODE_TYPE_MAP).expanduser().resolve()
    repair_rules_path = (repair_rules_path or DEFAULT_REPAIR_RULES_PATH).expanduser().resolve()
    object_info: Optional[dict[str, Any]] = None
    try:
        object_info = fetch_object_info(server)
    except Exception:
        object_info = None

    ctx = RepairContext(
        workflow=copy.deepcopy(workflow),
        object_info=object_info,
        map_path=map_path,
        repair_rules_path=repair_rules_path,
        data_root=data_root,
    )
    backup_path: Optional[Path] = None
    round_counter = {"n": 0}

    def validate_fn(repair_ctx: RepairContext) -> dict[str, Any]:
        round_counter["n"] += 1
        report = validate_workflow_document(
            workflow_path=workflow_path,
            workflow=repair_ctx.workflow,
            server=server,
            data_root=data_root,
            shape=shape,
            job=job,
            convert_timeout=convert_timeout,
            comfy_check=comfy_check,
            prompt_override=repair_ctx.prompt,
            repair_round=round_counter["n"],
        )
        repair_ctx.prompt = report.get("prompt_object") if isinstance(report.get("prompt_object"), dict) else repair_ctx.prompt
        repair_ctx.report = report
        return report

    if auto_repair:
        loop = repair_until_stable(
            ctx,
            rules=default_repair_rules(map_path, repair_rules_path),
            validate_fn=validate_fn,
            max_rounds=max_repair_rounds,
        )
        fixes = loop.fixes_as_dicts()
        report = ctx.report if isinstance(ctx.report, dict) else validate_fn(ctx)
        report["repair_fixes"] = fixes
        report["repair_rounds"] = loop.rounds
        report["repair_stable"] = loop.stable
        report["compat_patches"] = fixes
        if write_patches and any(str(f.get("phase") or "") == "ui_workflow" for f in fixes):
            backup_path = backup_workflow_file(workflow_path)
            atomic_write_json(workflow_path, ctx.workflow)
            report["patch_backup_path"] = str(backup_path)
    else:
        fixes = []
        report = validate_workflow_document(
            workflow_path=workflow_path,
            workflow=workflow,
            server=server,
            data_root=data_root,
            shape=shape,
            job=job,
            convert_timeout=convert_timeout,
            comfy_check=comfy_check,
        )

    report.pop("prompt_object", None)
    return ctx.workflow, report, fixes, backup_path


def iter_catalog_workflow_paths(catalog_dir: Path) -> list[Path]:
    root = catalog_dir.expanduser().resolve()
    if not root.is_dir():
        return []
    paths = sorted(p.resolve() for p in root.glob("*.json") if p.is_file())
    return paths


def validation_failure_reasons(report: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if report.get("object_info_error"):
        reasons.append("object_info_error")
    required = report.get("missing_required_node_types")
    if required is None:
        required = [
            m
            for m in (report.get("missing_node_types") or [])
            if str(m.get("class_type") or "") not in _VALIDATE_UI_NODE_TYPES
        ]
    if required:
        reasons.append("missing_required_nodes")
    if not report.get("convert_ok"):
        reasons.append("convert_failed")
    if report.get("node_errors"):
        reasons.append("node_errors")
    return reasons


def quarantine_category(report: dict[str, Any], reasons: list[str]) -> str:
    if "missing_required_nodes" in reasons:
        return "missing_module"
    node_errors = report.get("node_errors") if isinstance(report.get("node_errors"), dict) else {}
    for err_block in node_errors.values():
        if not isinstance(err_block, dict):
            continue
        for err in err_block.get("errors") or []:
            if not isinstance(err, dict):
                continue
            details = str(err.get("details") or "").lower()
            if "invalid image file" in details or "invalid video file" in details:
                return "missing_asset"
    if "convert_failed" in reasons:
        return "convert_error"
    if "node_errors" in reasons:
        return "prompt_wiring"
    if report.get("ok"):
        return "ok"
    return "unknown"


def quarantine_key(path: Path) -> str:
    return str(path.expanduser().resolve())


def quarantine_path_is_writable(path: Path) -> bool:
    """True when we can atomically replace ``path`` (parent must be writable)."""
    p = path.expanduser()
    parent = p.parent
    probe = parent / f".quarantine_write_probe_{os.getpid()}"
    try:
        if not parent.is_dir():
            return False
        probe.write_text("ok", encoding="utf-8")
        try:
            probe.unlink()
        except OSError:
            pass
        return True
    except OSError:
        try:
            if probe.exists():
                probe.unlink()
        except OSError:
            pass
        return False


def resolve_quarantine_registry_path(
    *,
    data_root: Optional[Path] = None,
    quarantine_path: Optional[Path] = None,
    for_write: bool = False,
) -> Path:
    """
    Choose the quarantine registry file.

    Prefer an explicit path, then ``data_root/shape_factory/quarantine.json``,
    then ``DEFAULT_QUARANTINE_PATH``. When the preferred file sits on a read-only
    mount (common for ``/workspace/.data`` in Docker), fall back to the writable
    jobs mount: ``data_root/shape_factory/jobs/quarantine.json``.
    """
    candidates: list[Path] = []
    if quarantine_path is not None:
        candidates.append(Path(quarantine_path).expanduser())
    if data_root is not None:
        root = Path(data_root).expanduser()
        candidates.append(root / "shape_factory" / "quarantine.json")
        candidates.append(root / "shape_factory" / "jobs" / "quarantine.json")
    candidates.append(DEFAULT_QUARANTINE_PATH)

    # De-dupe while preserving order.
    seen: set[str] = set()
    uniq: list[Path] = []
    for c in candidates:
        key = str(c)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)

    if for_write:
        for c in uniq:
            if quarantine_path_is_writable(c):
                return c.resolve() if c.exists() else c
        # Last resort: jobs overlay under data_root.
        if data_root is not None:
            overlay = Path(data_root).expanduser() / "shape_factory" / "jobs" / "quarantine.json"
            overlay.parent.mkdir(parents=True, exist_ok=True)
            return overlay
        raise RuntimeError("no writable quarantine registry path")

    # Read: prefer an existing writable overlay, else first existing file, else default.
    for c in uniq:
        if c.is_file() and quarantine_path_is_writable(c):
            return c.resolve()
    for c in uniq:
        if c.is_file():
            return c.resolve()
    return uniq[0]


def ensure_writable_quarantine_registry(
    *,
    data_root: Optional[Path] = None,
    quarantine_path: Optional[Path] = None,
) -> tuple[dict[str, Any], Path]:
    """
    Load the registry for mutation, seeding a writable overlay from a read-only
    source when needed.
    """
    read_path = resolve_quarantine_registry_path(
        data_root=data_root, quarantine_path=quarantine_path, for_write=False
    )
    write_path = resolve_quarantine_registry_path(
        data_root=data_root, quarantine_path=quarantine_path, for_write=True
    )
    registry = load_quarantine_registry(read_path)
    if write_path.resolve() != read_path.resolve() and not write_path.is_file():
        # Seed overlay once so release/sync mutations persist.
        save_quarantine_registry(write_path, registry)
    elif write_path.is_file() and write_path.resolve() != read_path.resolve():
        registry = load_quarantine_registry(write_path)
    return registry, write_path


def load_effective_quarantine_registry(
    *,
    data_root: Optional[Path] = None,
    quarantine_path: Optional[Path] = None,
) -> tuple[dict[str, Any], Path]:
    """
    Load the registry used for gates and UI list.

    Prefer an existing jobs-mount overlay (``shape_factory/jobs/quarantine.json``)
    whenever present — that is the writable copy used inside Docker when
    ``.data/shape_factory`` is read-only.
    """
    if data_root is not None:
        overlay = Path(data_root).expanduser() / "shape_factory" / "jobs" / "quarantine.json"
        if overlay.is_file():
            return load_quarantine_registry(overlay), overlay.resolve()
    write_path = resolve_quarantine_registry_path(
        data_root=data_root, quarantine_path=quarantine_path, for_write=True
    )
    if write_path.is_file():
        return load_quarantine_registry(write_path), write_path
    read_path = resolve_quarantine_registry_path(
        data_root=data_root, quarantine_path=quarantine_path, for_write=False
    )
    return load_quarantine_registry(read_path), read_path


def quarantine_workflow_name(path_or_name: Path | str) -> str:
    raw = str(path_or_name or "").strip()
    if not raw:
        return ""
    name = Path(raw).name
    return name if name.lower().endswith(".json") else f"{name}.json"


_STRONG_QUARANTINE_REASONS = frozenset({"missing_required_nodes", "node_errors"})


def quarantine_failure_is_strong(reasons: list[str] | None) -> bool:
    """True when failure should override a sticky human release."""
    return bool(_STRONG_QUARANTINE_REASONS.intersection(str(r) for r in (reasons or [])))


def find_quarantine_entry_key(
    registry: dict[str, Any],
    workflow_ref: Path | str,
) -> Optional[str]:
    """
    Resolve a registry key for a workflow path or basename.

    Prefer exact resolved-path match; fall back to workflow_name / basename so
    host and container path aliases (``/home/yuji/...`` vs ``/workspace/...``)
    hit the same entry.
    """
    entries = registry.get("entries") if isinstance(registry.get("entries"), dict) else {}
    if not entries:
        return None
    raw = str(workflow_ref or "").strip()
    if not raw:
        return None
    as_path = Path(raw).expanduser()
    try:
        exact = quarantine_key(as_path)
    except Exception:
        exact = raw
    if exact in entries and isinstance(entries.get(exact), dict):
        return exact
    if raw in entries and isinstance(entries.get(raw), dict):
        return raw
    want_name = quarantine_workflow_name(raw)
    if not want_name:
        return None
    for key, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        entry_name = str(entry.get("workflow_name") or Path(str(entry.get("workflow_path") or key)).name)
        if quarantine_workflow_name(entry_name) == want_name or quarantine_workflow_name(key) == want_name:
            return str(key)
    return None


def empty_quarantine_registry() -> dict[str, Any]:
    return {"schema_version": QUARANTINE_SCHEMA, "updated_at": utc_now(), "entries": {}}


def load_quarantine_registry(path: Path) -> dict[str, Any]:
    p = path.expanduser().resolve()
    if not p.is_file():
        return empty_quarantine_registry()
    obj = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        return empty_quarantine_registry()
    obj.setdefault("schema_version", QUARANTINE_SCHEMA)
    obj.setdefault("entries", {})
    if not isinstance(obj["entries"], dict):
        obj["entries"] = {}
    return obj


def save_quarantine_registry(path: Path, registry: dict[str, Any]) -> None:
    registry["schema_version"] = QUARANTINE_SCHEMA
    registry["updated_at"] = utc_now()
    atomic_write_json(path.expanduser().resolve(), registry)


def derive_repair_outcome(report: dict[str, Any]) -> str:
    if report.get("ok"):
        return "cleared"
    fixes = report.get("repair_fixes") if isinstance(report.get("repair_fixes"), list) else []
    if fixes or report.get("repair_rounds"):
        return "exhausted"
    return "failed_no_repair"


def quarantine_entry_from_report(
    workflow_path: Path,
    report: dict[str, Any],
    *,
    report_path: Optional[Path] = None,
    comfy_check: bool = False,
) -> dict[str, Any]:
    reasons = validation_failure_reasons(report)
    required_missing = report.get("missing_required_node_types")
    if required_missing is None:
        required_missing = [
            m
            for m in (report.get("missing_node_types") or [])
            if str(m.get("class_type") or "") not in _VALIDATE_UI_NODE_TYPES
        ]
    ok = bool(report.get("ok"))
    repair_fixes = report.get("repair_fixes") if isinstance(report.get("repair_fixes"), list) else []
    return {
        "workflow_path": quarantine_key(workflow_path),
        "workflow_name": workflow_path.name,
        "status": "ok" if ok else "quarantined",
        "category": quarantine_category(report, reasons),
        "reasons": reasons,
        "missing_required_node_types": required_missing,
        "node_errors": report.get("node_errors") or {},
        "convert_ok": bool(report.get("convert_ok")),
        "convert_error": report.get("convert_error"),
        "validated_at": report.get("validated_at") or utc_now(),
        "comfy_check": bool(comfy_check),
        "report_path": str(report_path) if report_path else None,
        "compat_patches": report.get("compat_patches") or repair_fixes,
        "repair_fixes": repair_fixes,
        "repair_rounds": report.get("repair_rounds"),
        "repair_stable": report.get("repair_stable"),
        "repair_outcome": derive_repair_outcome(report),
        "patch_backup_path": report.get("patch_backup_path"),
        "released_at": None,
        "release_note": None,
    }


def apply_report_to_quarantine_registry(
    registry: dict[str, Any],
    workflow_path: Path,
    report: dict[str, Any],
    *,
    report_path: Optional[Path] = None,
    comfy_check: bool = False,
) -> dict[str, Any]:
    """
    Merge a validation report into the quarantine registry.

    Sticky release: if the entry is already ``released`` and the new failure is
    soft (e.g. convert 405 only), keep ``released`` and refresh diagnostics.
    Strong failures (missing required nodes / Comfy node_errors) re-quarantine.
    """
    existing_key = find_quarantine_entry_key(registry, workflow_path)
    key = existing_key or quarantine_key(workflow_path)
    prev = registry.get("entries", {}).get(key) if isinstance(registry.get("entries"), dict) else None
    prev = prev if isinstance(prev, dict) else {}
    entry = quarantine_entry_from_report(
        workflow_path, report, report_path=report_path, comfy_check=comfy_check
    )
    reasons = entry.get("reasons") if isinstance(entry.get("reasons"), list) else []
    if str(prev.get("status") or "") == "released":
        if entry.get("status") == "ok" or not quarantine_failure_is_strong(reasons):
            entry["status"] = "released"
            entry["released_at"] = prev.get("released_at")
            entry["release_note"] = prev.get("release_note")
        else:
            entry["released_at"] = None
            entry["release_note"] = None
    # Prefer the established registry key so aliases don't fork entries.
    if existing_key:
        entry["workflow_path"] = existing_key
        if prev.get("workflow_name"):
            entry["workflow_name"] = prev.get("workflow_name")
    registry.setdefault("entries", {})[key] = entry
    return entry


def is_workflow_blocked(registry: dict[str, Any], workflow_path: Path) -> tuple[bool, Optional[dict[str, Any]]]:
    key = find_quarantine_entry_key(registry, workflow_path)
    if not key:
        return False, None
    entry = registry.get("entries", {}).get(key)
    if not isinstance(entry, dict):
        return False, None
    if entry.get("status") == "quarantined":
        return True, entry
    return False, entry


def release_workflow_in_registry(
    registry: dict[str, Any],
    workflow_path: Path,
    *,
    note: str = "",
) -> dict[str, Any]:
    key = find_quarantine_entry_key(registry, workflow_path) or quarantine_key(workflow_path)
    entry = registry.get("entries", {}).get(key)
    if not isinstance(entry, dict):
        entry = {
            "workflow_path": key,
            "workflow_name": Path(str(workflow_path)).name,
            "category": "manual",
            "reasons": [],
            "missing_required_node_types": [],
            "node_errors": {},
            "convert_ok": None,
            "validated_at": None,
            "comfy_check": None,
            "report_path": None,
        }
    entry["status"] = "released"
    entry["released_at"] = utc_now()
    entry["release_note"] = note.strip() or None
    entry.setdefault("workflow_path", key)
    entry.setdefault("workflow_name", Path(str(workflow_path)).name)
    registry.setdefault("entries", {})[key] = entry
    return entry


def list_quarantine_entries(
    registry: dict[str, Any],
    *,
    status: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Return quarantine entries as dicts, optionally filtered by status (or ``all``)."""
    entries = registry.get("entries") if isinstance(registry.get("entries"), dict) else {}
    rows = [dict(e) for e in entries.values() if isinstance(e, dict)]
    want = str(status or "").strip().lower()
    if want and want != "all":
        rows = [e for e in rows if str(e.get("status") or "").strip().lower() == want]
    rows.sort(key=lambda e: (str(e.get("status") or ""), str(e.get("workflow_name") or "")))
    return rows


def release_quarantine_entry(
    registry: dict[str, Any],
    *,
    workflow_path: Optional[str] = None,
    workflow_name: Optional[str] = None,
    note: str = "",
) -> dict[str, Any]:
    """
    Release by absolute/relative path or workflow basename.

    Raises ``FileNotFoundError`` when no matching registry entry (and no path file) exists.
    """
    ref = str(workflow_path or workflow_name or "").strip()
    if not ref:
        raise ValueError("workflow_path or workflow_name is required")
    key = find_quarantine_entry_key(registry, ref)
    if key is None:
        cand = Path(ref).expanduser()
        if cand.is_file():
            return release_workflow_in_registry(registry, cand, note=note)
        # Basename-only release of a known-missing entry: create released stub.
        if workflow_name and not workflow_path:
            stub = Path(quarantine_workflow_name(workflow_name))
            return release_workflow_in_registry(registry, stub, note=note)
        raise FileNotFoundError(f"no quarantine entry for {ref!r}")
    entry = registry["entries"][key]
    path_hint = Path(str(entry.get("workflow_path") or key))
    return release_workflow_in_registry(registry, path_hint, note=note)


def format_quarantine_block(entry: dict[str, Any]) -> str:
    name = str(entry.get("workflow_name") or Path(str(entry.get("workflow_path") or "")).name or "workflow")
    reasons = ", ".join(entry.get("reasons") or []) or "unknown"
    category = entry.get("category") or "unknown"
    missing = entry.get("missing_required_node_types") or []
    missing_types = sorted({str(m.get("class_type") or "") for m in missing if m.get("class_type")})
    parts = [
        f"workflow={name}",
        f"status={entry.get('status')}",
        f"category={category}",
        f"reasons={reasons}",
    ]
    if missing_types:
        parts.append(f"missing_modules={missing_types}")
    if entry.get("release_note"):
        parts.append(f"release_note={entry['release_note']!r}")
    return "; ".join(parts)


def assert_workflows_not_quarantined(
    registry: dict[str, Any],
    workflow_paths: list[Path],
    *,
    ignore: bool = False,
) -> None:
    if ignore:
        return
    blocked: list[str] = []
    for wf_path in workflow_paths:
        is_blocked, entry = is_workflow_blocked(registry, wf_path)
        if is_blocked and entry:
            blocked.append(f"{wf_path.name}: {format_quarantine_block(entry)}")
    if blocked:
        raise RuntimeError(
            "workflow quarantined — fix validation issues, release after review, or pass --ignore-quarantine:\n"
            + "\n".join(f"  - {line}" for line in blocked)
        )


def apply_validation_reports_to_quarantine(
    registry: dict[str, Any],
    report_dir: Path,
    *,
    comfy_check: Optional[bool] = None,
) -> int:
    updated = 0
    for report_path in sorted(report_dir.glob("*.validate.json")):
        if report_path.name in {"catalog_summary.validate.json", "summary.validate.json"}:
            continue
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(report, dict):
            continue
        wf_raw = report.get("workflow_path")
        if not isinstance(wf_raw, str) or not wf_raw.strip():
            stem = report_path.name.replace(".validate.json", "")
            wf_path = DEFAULT_CATALOG_DIR / f"{stem}.json"
        else:
            wf_path = Path(wf_raw)
        if not wf_path.is_file():
            continue
        cc = comfy_check if comfy_check is not None else bool(report.get("comfy_check"))
        apply_report_to_quarantine_registry(
            registry,
            wf_path,
            report,
            report_path=report_path,
            comfy_check=cc,
        )
        updated += 1
    return updated


def iter_validate_targets(args: argparse.Namespace) -> list[tuple[Path, Optional[dict[str, Any]], Optional[dict[str, Any]]]]:
    targets: list[tuple[Path, Optional[dict[str, Any]], Optional[dict[str, Any]]]] = []
    if getattr(args, "catalog", False):
        catalog_dir = Path(getattr(args, "catalog_dir", DEFAULT_CATALOG_DIR))
        for wf_path in iter_catalog_workflow_paths(catalog_dir):
            targets.append((wf_path, None, None))
    if args.workflow:
        for raw in args.workflow:
            targets.append((Path(raw).expanduser().resolve(), None, None))
    if args.shape:
        shape_path = Path(args.shape).expanduser().resolve()
        shape = load_yaml(shape_path)
        template = Path(str(shape["template"])).expanduser().resolve()
        targets.append((template, shape, None))
    for job_path in iter_job_paths(args):
        job = json.loads(job_path.read_text(encoding="utf-8"))
        wf_path = Path(str(job.get("generated_workflow_path") or "")).expanduser()
        if wf_path.is_file():
            shape = None
            shape_path = Path(str(job.get("shape_path") or "")).expanduser()
            if shape_path.is_file():
                shape = load_yaml(shape_path)
            targets.append((wf_path, shape, job))
    # dedupe by workflow path
    seen: set[str] = set()
    out: list[tuple[Path, Optional[dict[str, Any]], Optional[dict[str, Any]]]] = []
    for item in targets:
        key = str(item[0])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    if args.limit and len(out) > args.limit:
        out = out[: args.limit]
    return out


def cmd_validate(args: argparse.Namespace) -> int:
    targets = iter_validate_targets(args)
    if not targets:
        print("error: no workflows to validate (use --catalog, --workflow, --shape, or --family)", file=sys.stderr)
        return 1
    server = str(args.server).rstrip("/")
    data_root = Path(args.data_root).expanduser().resolve()
    out_dir = Path(args.report_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"# Shape factory validate\n")
    print(f"- targets: {len(targets)}")
    print(f"- comfy_check: {bool(args.comfy_check)}")
    print(f"- auto_patch: {bool(getattr(args, 'auto_patch', True))}")
    print(f"- max_repair_rounds: {int(getattr(args, 'max_repair_rounds', 5))}\n")

    failed = 0
    summary_rows: list[dict[str, Any]] = []
    quarantine_path = Path(getattr(args, "quarantine_path", DEFAULT_QUARANTINE_PATH)).expanduser().resolve()
    update_quarantine = bool(getattr(args, "update_quarantine", True))
    registry = load_quarantine_registry(quarantine_path) if update_quarantine else None
    quarantine_added = 0
    quarantine_cleared = 0
    patches_applied = 0
    map_path = Path(getattr(args, "node_type_map", DEFAULT_NODE_TYPE_MAP)).expanduser().resolve()
    repair_rules_path = Path(getattr(args, "repair_rules", DEFAULT_REPAIR_RULES_PATH)).expanduser().resolve()
    for workflow_path, shape, job in targets:
        workflow = read_json(workflow_path)
        if not is_litegraph_workflow(workflow):
            print(f"## {workflow_path.name}\n   error: not a LiteGraph workflow")
            failed += 1
            continue
        workflow, report, repair_fixes, backup_path = validate_with_repair_loop(
            workflow_path=workflow_path,
            workflow=workflow,
            server=server,
            data_root=data_root,
            shape=shape,
            job=job,
            convert_timeout=args.convert_timeout,
            comfy_check=bool(args.comfy_check),
            auto_repair=bool(getattr(args, "auto_patch", True)),
            write_patches=bool(getattr(args, "write_patches", True)),
            map_path=map_path,
            repair_rules_path=repair_rules_path,
            max_repair_rounds=int(getattr(args, "max_repair_rounds", 5)),
        )
        compat_patches = repair_fixes
        if compat_patches:
            patches_applied += len(compat_patches)
        report_path = out_dir / f"{workflow_path.stem}.validate.json"
        atomic_write_json(report_path, report)
        if registry is not None:
            prev = registry.get("entries", {}).get(quarantine_key(workflow_path), {})
            prev_status = prev.get("status") if isinstance(prev, dict) else None
            entry = apply_report_to_quarantine_registry(
                registry,
                workflow_path,
                report,
                report_path=report_path,
                comfy_check=bool(args.comfy_check),
            )
            if entry.get("status") == "quarantined" and prev_status != "quarantined":
                quarantine_added += 1
            elif entry.get("status") == "ok" and prev_status == "quarantined":
                quarantine_cleared += 1
        reasons = validation_failure_reasons(report)
        required_missing = report.get("missing_required_node_types")
        if required_missing is None:
            required_missing = [
                m
                for m in (report.get("missing_node_types") or [])
                if str(m.get("class_type") or "") not in _VALIDATE_UI_NODE_TYPES
            ]
        summary_rows.append(
            {
                "workflow": workflow_path.name,
                "workflow_path": str(workflow_path),
                "ok": bool(report.get("ok")),
                "reasons": reasons,
                "convert_ok": bool(report.get("convert_ok")),
                "missing_required_node_types": required_missing,
                "missing_ui_node_types": report.get("missing_ui_node_types") or [],
                "node_errors": report.get("node_errors") or {},
                "report_path": str(report_path),
                "repair_outcome": derive_repair_outcome(report),
                "repair_fixes_count": len(report.get("repair_fixes") or []),
            }
        )
        status = "OK" if report.get("ok") else "FAIL"
        print(f"## {workflow_path.name} [{status}]")
        if compat_patches:
            for p in compat_patches:
                print(
                    f"   repair: [{p.get('rule_id')}] {p.get('summary')}"
                    + (f" node={p.get('node_id')}" if p.get("node_id") is not None else "")
                )
            if report.get("repair_rounds"):
                print(f"   repair_rounds={report.get('repair_rounds')} stable={report.get('repair_stable')}")
            if backup_path or report.get("patch_backup_path"):
                print(f"   patch_backup={backup_path or report.get('patch_backup_path')}")
        if required_missing:
            for m in required_missing:
                print(f"   missing node: id={m.get('node_id')} type={m.get('class_type')!r} title={m.get('title')!r}")
        ui_missing = report.get("missing_ui_node_types")
        if ui_missing:
            print(f"   ui-only nodes (ok): {len(ui_missing)}")
        if report.get("convert_error"):
            print(f"   convert_error: {report['convert_error'][:200]}")
        if report.get("sanitize_warnings"):
            for w in report["sanitize_warnings"]:
                print(f"   sanitize: {w}")
        if report.get("node_errors"):
            print(f"   node_errors: {json.dumps(report['node_errors'], ensure_ascii=False)[:300]}")
        if report.get("relink_warnings"):
            print(f"   relinks: {len(report['relink_warnings'])}")
        if reasons:
            print(f"   reasons: {', '.join(reasons)}")
        print(f"   report={report_path}")
        if not report.get("ok"):
            failed += 1
        print()

    summary = {
        "schema_version": "comfyui-runpod.shape-validate-summary.v0",
        "validated_at": utc_now(),
        "comfy_check": bool(args.comfy_check),
        "targets": len(targets),
        "ok": len(targets) - failed,
        "failed": failed,
        "workflows": summary_rows,
    }
    summary_path = out_dir / ("catalog_summary.validate.json" if getattr(args, "catalog", False) else "summary.validate.json")
    atomic_write_json(summary_path, summary)

    if registry is not None:
        save_quarantine_registry(quarantine_path, registry)

    print(f"validate_ok={len(targets) - failed}")
    print(f"validate_failed={failed}")
    print(f"summary={summary_path}")
    if registry is not None:
        q_count = sum(
            1 for e in registry.get("entries", {}).values() if isinstance(e, dict) and e.get("status") == "quarantined"
        )
        print(f"quarantine={quarantine_path} quarantined={q_count} added={quarantine_added} cleared={quarantine_cleared}")
    if patches_applied:
        print(f"compat_patches_applied={patches_applied}")
    return 0 if failed == 0 else 1


def cmd_repair(args: argparse.Namespace) -> int:
    cmd = str(getattr(args, "repair_cmd", "") or "")
    map_path = Path(getattr(args, "node_type_map", DEFAULT_NODE_TYPE_MAP)).expanduser().resolve()
    repair_rules_path = Path(getattr(args, "repair_rules", DEFAULT_REPAIR_RULES_PATH)).expanduser().resolve()
    rules = default_repair_rules(map_path, repair_rules_path)

    if cmd == "rules":
        print("# Shape factory repair rules\n")
        for rule in rules:
            print(f"- {rule.rule_id} phase={rule.phase}")
            if hasattr(rule, "rule_ids"):
                for rid in rule.rule_ids():  # type: ignore[attr-defined]
                    print(f"    yaml: {rid}")
        print(f"\nnode_type_map={map_path}")
        print(f"repair_rules={repair_rules_path}")
        print("Add prompt rules to workflow_repair_rules.yaml; node renames to workflow_node_id_map.yaml")
        print("Asset remap: missing_asset_remap searches data_root input/ and output/ by hash suffix")
        return 0

    if cmd == "run":
        targets: list[Path] = []
        if getattr(args, "catalog", False):
            targets.extend(iter_catalog_workflow_paths(Path(args.catalog_dir)))
        if args.workflow:
            targets.extend(Path(w).expanduser().resolve() for w in args.workflow)
        if args.limit and len(targets) > args.limit:
            targets = targets[: args.limit]
        if not targets:
            print("error: no workflows (use --catalog or --workflow)", file=sys.stderr)
            return 1
        server = str(args.server).rstrip("/")
        data_root = Path(args.data_root).expanduser().resolve()
        out_dir = Path(args.report_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        print("# Shape factory repair run\n")
        failed = 0
        for workflow_path in targets:
            workflow = read_json(workflow_path)
            if not is_litegraph_workflow(workflow):
                print(f"skip {workflow_path.name}: not LiteGraph")
                continue
            _, report, fixes, backup = validate_with_repair_loop(
                workflow_path=workflow_path,
                workflow=workflow,
                server=server,
                data_root=data_root,
                convert_timeout=args.convert_timeout,
                comfy_check=bool(args.comfy_check),
                auto_repair=True,
                write_patches=not bool(getattr(args, "dry_run", False)),
                map_path=map_path,
                repair_rules_path=repair_rules_path,
                max_repair_rounds=int(getattr(args, "max_repair_rounds", 5)),
            )
            status = "OK" if report.get("ok") else "FAIL"
            print(f"## {workflow_path.name} [{status}] fixes={len(fixes)} rounds={report.get('repair_rounds')}")
            for fix in fixes:
                print(f"   [{fix.get('rule_id')}] {fix.get('summary')}")
            report_path = out_dir / f"{workflow_path.stem}.validate.json"
            atomic_write_json(report_path, report)
            if not report.get("ok"):
                failed += 1
        return 0 if failed == 0 else 1

    print(f"error: unknown repair command {cmd!r}", file=sys.stderr)
    return 1


def cmd_quarantine(args: argparse.Namespace) -> int:
    quarantine_path = Path(args.quarantine_path).expanduser().resolve()
    data_root = Path(getattr(args, "data_root", DEFAULT_DATA_ROOT)).expanduser().resolve()
    registry, effective_path = load_effective_quarantine_registry(
        data_root=data_root,
        quarantine_path=quarantine_path,
    )
    cmd = str(getattr(args, "quarantine_cmd", "") or "")

    if cmd == "list":
        status_filter = getattr(args, "status", None)
        rows = list_quarantine_entries(registry, status=status_filter)
        print(f"# Shape factory quarantine\n")
        print(f"- registry: {effective_path}")
        print(f"- entries: {len(rows)}\n")
        for entry in rows:
            print(f"## {entry.get('workflow_name')} [{entry.get('status')}]")
            print(f"   category={entry.get('category')} reasons={','.join(entry.get('reasons') or []) or '-'}")
            outcome = entry.get("repair_outcome")
            if outcome and entry.get("status") == "quarantined":
                fixes = entry.get("repair_fixes") or entry.get("compat_patches") or []
                print(
                    f"   repair_outcome={outcome}"
                    + (f" rounds={entry.get('repair_rounds')}" if entry.get("repair_rounds") is not None else "")
                    + (f" attempted_fixes={len(fixes)}" if fixes else "")
                )
                for fix in fixes[:5]:
                    if isinstance(fix, dict):
                        print(f"     tried: [{fix.get('rule_id')}] {fix.get('summary')}")
                if len(fixes) > 5:
                    print(f"     ... +{len(fixes) - 5} more")
            missing = entry.get("missing_required_node_types") or []
            if missing:
                types = sorted({str(m.get('class_type') or '') for m in missing})
                print(f"   missing_modules={types}")
            if entry.get("release_note"):
                print(f"   release_note={entry.get('release_note')!r}")
            if entry.get("compat_patches"):
                print(f"   compat_patches={len(entry.get('compat_patches') or [])}")
            if entry.get("report_path"):
                print(f"   report={entry.get('report_path')}")
            print(f"   path={entry.get('workflow_path')}")
            print()
        return 0

    if cmd == "show":
        wf_ref = str(args.workflow or "").strip()
        key = find_quarantine_entry_key(registry, wf_ref)
        entry = registry.get("entries", {}).get(key) if key else None
        if not isinstance(entry, dict):
            print(f"no quarantine entry for {wf_ref}", file=sys.stderr)
            return 1
        print(json.dumps(entry, indent=2, ensure_ascii=False))
        return 0

    if cmd == "apply":
        report_dir = Path(args.report_dir).expanduser().resolve()
        if not report_dir.is_dir():
            print(f"error: report dir not found: {report_dir}", file=sys.stderr)
            return 1
        updated = apply_validation_reports_to_quarantine(
            registry,
            report_dir,
            comfy_check=True if args.comfy_check else None,
        )
        save_quarantine_registry(quarantine_path, registry)
        q_count = sum(
            1 for e in registry.get("entries", {}).values() if isinstance(e, dict) and e.get("status") == "quarantined"
        )
        print(f"quarantine_apply updated={updated} quarantined={q_count} registry={quarantine_path}")
        return 0

    if cmd == "release":
        wf_ref = str(args.workflow or "").strip()
        registry, write_path = ensure_writable_quarantine_registry(
            data_root=data_root,
            quarantine_path=quarantine_path,
        )
        try:
            entry = release_quarantine_entry(
                registry,
                workflow_path=wf_ref,
                note=str(args.note or ""),
            )
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        save_quarantine_registry(write_path, registry)
        print(f"released {entry.get('workflow_name')} note={entry.get('release_note')!r} registry={write_path}")
        return 0

    if cmd == "patch":
        server = str(getattr(args, "server", DEFAULT_COMFY_SERVER)).rstrip("/")
        map_path = Path(getattr(args, "node_type_map", DEFAULT_NODE_TYPE_MAP)).expanduser().resolve()
        targets: list[Path] = []
        if getattr(args, "catalog", False):
            targets.extend(iter_catalog_workflow_paths(Path(args.catalog_dir)))
        if args.workflow:
            targets.extend(Path(w).expanduser().resolve() for w in args.workflow)
        if args.limit and len(targets) > args.limit:
            targets = targets[: args.limit]
        if not targets:
            print("error: no workflows to patch (use --catalog or --workflow)", file=sys.stderr)
            return 1
        try:
            object_info = fetch_object_info(server)
        except Exception as exc:
            print(f"warning: could not fetch object_info ({exc}); patching from map only", file=sys.stderr)
            object_info = None
        patched_files = 0
        patched_nodes = 0
        print(f"# Shape factory quarantine patch\n")
        for workflow_path in targets:
            workflow = read_json(workflow_path)
            if not is_litegraph_workflow(workflow):
                print(f"skip {workflow_path.name}: not LiteGraph")
                continue
            patchable = patchable_missing_types(workflow, object_info or {}, map_path=map_path)
            patched, records, backup_path = maybe_auto_patch_workflow(
                workflow_path,
                workflow,
                server=server,
                auto_patch=True,
                write_patches=not bool(getattr(args, "dry_run", False)),
                map_path=map_path,
                object_info=object_info,
            )
            if not records:
                if patchable:
                    print(f"## {workflow_path.name} patchable={patchable} (no write)")
                continue
            patched_files += 1
            patched_nodes += len(records)
            print(f"## {workflow_path.name}")
            for p in records:
                print(f"   {p.get('old_type')!r} -> {p.get('new_type')!r} node={p.get('node_id')}")
            if backup_path:
                print(f"   backup={backup_path}")
        print(f"\npatch_files={patched_files} patch_nodes={patched_nodes}")
        if bool(getattr(args, "revalidate", False)) and patched_files:
            val_args = argparse.Namespace(
                catalog=bool(getattr(args, "catalog", False)),
                catalog_dir=getattr(args, "catalog_dir", str(DEFAULT_CATALOG_DIR)),
                workflow=args.workflow,
                shape=None,
                job=None,
                jobs_dir=None,
                family=None,
                job_dir=DEFAULT_JOB_DIR,
                limit=args.limit,
                server=server,
                data_root=getattr(args, "data_root", str(DEFAULT_DATA_ROOT)),
                convert_timeout=getattr(args, "convert_timeout", 180),
                comfy_check=bool(getattr(args, "comfy_check", False)),
                report_dir=getattr(args, "report_dir", str(DEFAULT_JOB_DIR.parent / "validation")),
                quarantine_path=str(quarantine_path),
                update_quarantine=True,
                auto_patch=False,
                write_patches=False,
                node_type_map=str(map_path),
                ignore_quarantine=False,
            )
            return cmd_validate(val_args)
        return 0

    if cmd == "sync":
        sync_args = argparse.Namespace(
            catalog=True,
            catalog_dir=args.catalog_dir,
            workflow=None,
            shape=None,
            job=None,
            jobs_dir=None,
            family=None,
            job_dir=DEFAULT_JOB_DIR,
            limit=args.limit,
            server=args.server,
            data_root=args.data_root,
            convert_timeout=args.convert_timeout,
            comfy_check=bool(args.comfy_check),
            report_dir=args.report_dir,
            quarantine_path=str(quarantine_path),
            update_quarantine=True,
            ignore_quarantine=False,
            auto_patch=True,
            write_patches=True,
            node_type_map=str(getattr(args, "node_type_map", DEFAULT_NODE_TYPE_MAP)),
            max_repair_rounds=int(getattr(args, "max_repair_rounds", 5)),
        )
        return cmd_validate(sync_args)

    print(f"error: unknown quarantine command {cmd!r}", file=sys.stderr)
    return 1


def _binding_patch_failures(warnings: list[str], shape: dict[str, Any], job: dict[str, Any]) -> list[str]:
    """Return fatal companion-PNG patch failures for required media/prompt slots."""
    fatal: list[str] = []
    bindings = job.get("bindings") if isinstance(job.get("bindings"), dict) else {}
    req_by_slot = requires_by_slot(shape)
    for slot, meta in bindings.items():
        req = req_by_slot.get(slot)
        if not req:
            continue
        btype = str((req.get("binding") or {}).get("type") or "")
        if btype == "vhs_load_video_path":
            node_id = (req.get("binding") or {}).get("node_id")
            needle = f"no VHS_LoadVideoPath node {node_id!r} in API prompt"
            if any(needle in w for w in warnings):
                fatal.append(
                    f"companion_png_missing_video_slot:{slot} (node {node_id!r}); "
                    "install workflow-to-api-converter or fix convert"
                )
        elif btype == "load_image":
            node_id = (req.get("binding") or {}).get("node_id")
            needle = f"no LoadImage node {node_id!r} in API prompt"
            if any(needle in w for w in warnings):
                fatal.append(
                    f"companion_png_missing_image_slot:{slot} (node {node_id!r}); "
                    "install workflow-to-api-converter or fix convert"
                )
        elif btype == "prompt_bundle":
            if any(f"prompt positive node" in w and "not found" in w for w in warnings) and meta:
                # Only fatal when the shape declared explicit node ids we failed to paint.
                pos = ((req.get("binding") or {}).get("positive") or {}).get("node_id")
                if pos is not None and any(f"prompt positive node {pos!r}" in w for w in warnings):
                    fatal.append(f"companion_png_missing_prompt_positive:{slot} (node {pos!r})")
    return fatal


def resolve_prompt_for_job(
    job: dict[str, Any],
    shape: dict[str, Any],
    workflow: dict[str, Any],
    data_root: Path,
    server: str,
    convert_timeout: int,
) -> tuple[dict[str, Any], str, list[str]]:
    warnings: list[str] = []
    dev_spec = job.get("dev_tuning", {}).get("spec") if isinstance(job.get("dev_tuning"), dict) else None
    try:
        prompt_obj = convert_ui_workflow_to_prompt(server, workflow, timeout_s=convert_timeout)
        warnings.extend(sync_prompt_inputs_from_ui_workflow(workflow, prompt_obj))
        warnings.extend(sanitize_converted_prompt(workflow, prompt_obj))
        if isinstance(dev_spec, dict):
            apply_dev_tuning_api(prompt_obj, dev_spec)
        return prompt_obj, "workflow_convert", warnings
    except Exception as exc:
        warnings.append(f"workflow_convert_failed: {exc}")

    seed_png = prompt_seed_path_for_job(job, data_root=data_root)
    if seed_png is None:
        raise RuntimeError("no companion PNG for bindings; cannot build API prompt without /workflow/convert")
    prompt = extract_api_prompt_from_png(seed_png)
    warnings.extend(apply_api_slot_bindings(prompt, shape, job, data_root))
    if isinstance(dev_spec, dict):
        apply_dev_tuning_api(prompt, dev_spec)
    fatal = _binding_patch_failures(warnings, shape, job)
    if fatal:
        raise RuntimeError(
            "companion_png fallback cannot apply required bindings "
            f"(convert unavailable): {'; '.join(fatal)}"
        )
    return prompt, "companion_png", warnings


def default_workspace_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _running_in_docker() -> bool:
    return Path("/.dockerenv").exists()


def shape_factory_repo_root() -> Path:
    """
    Repo (or Docker /workspace) root that owns `.data/`.

    Layouts:
    - Host: ``<repo>/workspace/scripts/shape_factory.py`` → ``<repo>``
    - Docker bind: ``/workspace/ws_scripts/shape_factory.py`` → ``/workspace``
      (``parents[2]`` would be ``/``, which must not be treated as the repo)
    """
    here = Path(__file__).resolve()
    scripts_dir = here.parent
    parent = scripts_dir.parent  # workspace/ or /workspace

    def _has_shapes(root: Path) -> bool:
        return (root / ".data" / "shapes").is_dir()

    # Docker bind of workspace/scripts → /workspace/ws_scripts (data next to it).
    if scripts_dir.name == "ws_scripts":
        if _has_shapes(parent) or (parent / ".data").is_dir():
            return parent
        return parent
    # Host checkout: <repo>/workspace/scripts → <repo> (not workspace/, even if an
    # empty workspace/.data directory exists from other tooling).
    if scripts_dir.name == "scripts" and parent.name == "workspace":
        return parent.parent
    for cand in (Path("/workspace"), here.parents[2] if len(here.parents) > 2 else parent):
        if _has_shapes(cand) or (cand / ".data").is_dir():
            return cand
    return parent.parent if parent.name == "workspace" else parent


def hostify_repo_path(raw: str | Path) -> Path:
    """Map container paths (/workspace/...) to host paths when running outside Docker."""
    text = str(raw or "").strip()
    if not text:
        return Path(text)
    # Inside the container, /workspace/... is already authoritative — rewriting via
    # parents[2] (often "/") corrupts paths to "/.data/..." and breaks submit/replay.
    if _running_in_docker() and text.startswith("/workspace/"):
        return Path(text).expanduser()

    # Prefer explicit repo .data over workspace/.data for factory metadata.
    repo_root = shape_factory_repo_root()
    repo_data = repo_root / ".data"
    workspace = default_workspace_root()
    comfy_user = Path("/home/yuji/comfyui-runpod-data/comfyui_user")
    output_root = Path("/home/yuji/comfyui-runpod-data/output")

    if text.startswith("/workspace/.data/") or text == "/workspace/.data":
        rel = text[len("/workspace/.data") :].lstrip("/")
        return (repo_data / rel).resolve() if rel else repo_data.resolve()
    if text.startswith("/workspace/comfyui_user/") or text == "/workspace/comfyui_user":
        rel = text[len("/workspace/comfyui_user") :].lstrip("/")
        return (comfy_user / rel).resolve() if rel else comfy_user.resolve()
    if text.startswith("/workspace/output/") or text == "/workspace/output":
        rel = text[len("/workspace/output") :].lstrip("/")
        return (output_root / rel).resolve() if rel else output_root.resolve()
    if text.startswith("/workspace/"):
        rel = text[len("/workspace/") :]
        # Prefer data-root comfyui_user when the workspace copy is empty/missing.
        if rel.startswith("comfyui_user/") or rel == "comfyui_user":
            sub = rel[len("comfyui_user") :].lstrip("/")
            cand = (comfy_user / sub).resolve() if sub else comfy_user.resolve()
            if cand.exists() or not (workspace / "comfyui_user").exists():
                return cand
        return (workspace / rel).resolve()
    return Path(text).expanduser()


_JOB_PATH_FIELDS = (
    "pools_path",
    "shape_path",
    "template_path",
    "generated_workflow_path",
    "prompt_seed_png",
    "recipe_output_path",
)


def hostify_job_paths(job: dict[str, Any]) -> bool:
    """Rewrite /workspace job path fields to host paths. Returns True if any field changed."""
    changed = False
    for key in _JOB_PATH_FIELDS:
        raw = job.get(key)
        if not isinstance(raw, str) or "/workspace" not in raw:
            continue
        mapped = str(hostify_repo_path(raw))
        if mapped != raw:
            job[key] = mapped
            changed = True
    dep = job.get("deposit")
    if isinstance(dep, dict):
        for key in ("index_path",):
            raw = dep.get(key)
            if isinstance(raw, str) and "/workspace" in raw:
                mapped = str(hostify_repo_path(raw))
                if mapped != raw:
                    dep[key] = mapped
                    changed = True
        videos = dep.get("videos")
        if isinstance(videos, list):
            new_videos: list[str] = []
            vids_changed = False
            for item in videos:
                if isinstance(item, str) and "/workspace" in item:
                    mapped = str(hostify_repo_path(item))
                    new_videos.append(mapped)
                    if mapped != item:
                        vids_changed = True
                else:
                    new_videos.append(item)
            if vids_changed:
                dep["videos"] = new_videos
                changed = True
    bindings = job.get("bindings")
    if isinstance(bindings, dict):
        for _slot, meta in bindings.items():
            if not isinstance(meta, dict):
                continue
            raw = meta.get("path")
            if isinstance(raw, str) and "/workspace" in raw:
                mapped = str(hostify_repo_path(raw))
                if mapped != raw:
                    meta["path"] = mapped
                    changed = True
    return changed


def resolve_job_asset_path(
    raw: str,
    *,
    data_root: Path,
    workspace_root: Optional[Path] = None,
) -> Path:
    from shape_factory_map import resolve_existing_path  # type: ignore

    ws = workspace_root or default_workspace_root()
    output_root = data_root / "output"
    sf_data = data_root if (data_root / "shapes").is_dir() else (shape_factory_repo_root() / ".data")
    return resolve_existing_path(
        raw,
        output_root=output_root,
        data_root=sf_data,
        workspace_root=ws,
    )


def rebuild_job_workflow(
    job: dict[str, Any],
    *,
    data_root: Path,
    workspace_root: Path,
    workflow_dir: Path,
) -> Path:
    """Regenerate a missing generated workflow JSON from job metadata (bindings, dev tuning, output prefix)."""
    job_key = str(job.get("job_key") or "").strip()
    family = str(job.get("family_slug") or "").strip()
    if not job_key or not family:
        raise RuntimeError("job missing job_key or family_slug — cannot rebuild workflow")

    shape_path = resolve_job_asset_path(
        str(job.get("shape_path") or ""),
        data_root=data_root,
        workspace_root=workspace_root,
    )
    shape = load_yaml(shape_path)
    template_raw = str(job.get("template_path") or shape.get("template") or "")
    template_path = resolve_job_asset_path(template_raw, data_root=data_root, workspace_root=workspace_root)

    workflow = read_json(template_path)
    if not is_litegraph_workflow(workflow):
        raise RuntimeError(f"not a LiteGraph workflow: {template_path}")

    req_by_slot = requires_by_slot(shape)
    bindings = job.get("bindings") if isinstance(job.get("bindings"), dict) else {}
    picks: dict[str, Path] = {}
    for slot, meta in bindings.items():
        if not isinstance(meta, dict):
            continue
        raw_path = str(meta.get("path") or "").strip()
        if not raw_path:
            continue
        picks[str(slot)] = resolve_job_asset_path(raw_path, data_root=data_root, workspace_root=workspace_root)

    warnings: list[str] = []
    for slot, path in sorted(picks.items()):
        req = req_by_slot.get(slot)
        if req is None:
            warnings.append(f"unknown binding slot {slot!r}")
            continue
        warnings.extend(apply_slot_binding(workflow, req, path, data_root))

    dev_block = job.get("dev_tuning") if isinstance(job.get("dev_tuning"), dict) else {}
    dev_spec = dev_block.get("spec") if isinstance(dev_block.get("spec"), dict) else dev_block
    if isinstance(dev_spec, dict) and dev_spec:
        apply_dev_tuning_ui(workflow, dev_spec)

    output_prefix = flatten_output_prefix(str(job.get("output_prefix") or ""))
    if output_prefix:
        strip_video_previews_and_redirect_outputs(workflow, output_prefix)

    workflow_out = workflow_dir / family / f"{job_key}.workflow.json"
    workflow_out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(workflow_out, workflow)

    job["generated_workflow_path"] = str(workflow_out.resolve())
    prev = job.get("warnings")
    if not isinstance(prev, list):
        prev = []
    job["warnings"] = prev + [f"rebuilt workflow at {workflow_out}"] + warnings
    return workflow_out.resolve()


def ensure_job_workflow_path(
    job: dict[str, Any],
    *,
    data_root: Path,
    workspace_root: Optional[Path] = None,
    workflow_dir: Optional[Path] = None,
) -> Path:
    """Resolve generated_workflow_path on disk, rebuilding from job metadata when missing."""
    hostify_job_paths(job)
    ws = (workspace_root or default_workspace_root()).expanduser().resolve()
    wf_dir = (workflow_dir or DEFAULT_WORKFLOW_DIR).expanduser().resolve()
    raw = str(job.get("generated_workflow_path") or "").strip()
    job_key = str(job.get("job_key") or "").strip()
    family = str(job.get("family_slug") or "").strip()

    if raw:
        try:
            resolved = resolve_job_asset_path(raw, data_root=data_root, workspace_root=ws)
            job["generated_workflow_path"] = str(resolved)
            return resolved
        except FileNotFoundError:
            pass

    if job_key and family:
        fallback = wf_dir / family / f"{job_key}.workflow.json"
        if fallback.is_file():
            resolved = fallback.resolve()
            job["generated_workflow_path"] = str(resolved)
            return resolved

    rebuilt = rebuild_job_workflow(job, data_root=data_root, workspace_root=ws, workflow_dir=wf_dir)
    return rebuilt


def submit_job_file(
    job_path: Path,
    *,
    server: str,
    data_root: Path,
    dry_run: bool = False,
    force: bool = False,
    pending_only: bool = False,
    client_id: str = "shape_factory",
    front: bool = False,
    timeout: int = 120,
    convert_timeout: int = 90,
    ignore_quarantine: bool = False,
    quarantine_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Generate prompt + submit one shape-factory job to ComfyUI."""
    job_path = job_path.expanduser().resolve()
    job = json.loads(job_path.read_text(encoding="utf-8"))
    if hostify_job_paths(job):
        atomic_write_json(job_path, job)
    # Cap retries: error jobs that hit max attempts become abandoned.
    submit_block = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    if str(submit_block.get("status") or "") == "error" and job_retries_exhausted(job):
        abandon_submit_failure(
            job,
            error=str(submit_block.get("error") or "submit retries exhausted"),
            server=str(submit_block.get("comfy_server") or server),
            previous_status="error",
            attempts=submit_attempt_count(job),
        )
        atomic_write_json(job_path, job)
    job_key = str(job.get("job_key") or job_path.stem.replace(".job", ""))

    if pending_only and job_already_submitted(job):
        return {"ok": True, "skipped": True, "reason": "already_submitted", "job_key": job_key, "job_path": str(job_path)}

    if job_abandoned(job) and not force:
        return {
            "ok": True,
            "skipped": True,
            "reason": "abandoned",
            "job_key": job_key,
            "job_path": str(job_path),
        }

    # Retry error jobs while attempts remain; skip only when exhausted (unless --force).
    if pending_only and job_submit_failed(job) and job_retries_exhausted(job) and not force:
        return {
            "ok": True,
            "skipped": True,
            "reason": "submit_error",
            "job_key": job_key,
            "job_path": str(job_path),
        }

    if job_already_submitted(job) and not force:
        pid = (job.get("submit") or {}).get("prompt_id")
        return {
            "ok": True,
            "skipped": True,
            "reason": "already_submitted",
            "job_key": job_key,
            "job_path": str(job_path),
            "prompt_id": pid,
        }

    workflow_path = ensure_job_workflow_path(
        job,
        data_root=data_root,
    )
    if str(job.get("generated_workflow_path") or "") != str(workflow_path):
        atomic_write_json(job_path, job)
    if not workflow_path.is_file():
        raise RuntimeError(f"workflow missing: {workflow_path}")

    workflow = read_json(workflow_path)
    if not is_litegraph_workflow(workflow):
        raise RuntimeError("not a LiteGraph workflow")

    shape_path = resolve_job_asset_path(
        str(job.get("shape_path") or ""),
        data_root=data_root,
    )
    if not shape_path.is_file():
        raise RuntimeError(f"shape missing: {shape_path}")
    shape = load_yaml(shape_path)

    template_path = Path(str(shape.get("template") or "")).expanduser().resolve()
    if template_path.is_file():
        qpath = (quarantine_path or DEFAULT_QUARANTINE_PATH).expanduser().resolve()
        registry, _effective = load_effective_quarantine_registry(
            data_root=data_root,
            quarantine_path=qpath,
        )
        is_blocked, entry = is_workflow_blocked(registry, template_path)
        if is_blocked and not ignore_quarantine:
            raise RuntimeError(f"quarantined template: {format_quarantine_block(entry or {})}")

    if dry_run:
        seed = prompt_seed_path_for_job(job, data_root=data_root)
        out: dict[str, Any] = {
            "ok": True,
            "dry_run": True,
            "job_key": job_key,
            "job_path": str(job_path),
            "workflow_path": str(workflow_path),
        }
        if seed:
            out["prompt_seed_png"] = str(seed)
        return out

    t0 = time.time()
    t_prep0 = time.time()
    prompt_obj, prompt_source, prep_warnings = resolve_prompt_for_job(
        job,
        shape,
        workflow,
        data_root,
        server,
        convert_timeout,
    )
    t_prep1 = time.time()

    prompt_path = job_path.with_name(job_path.stem.replace(".job", "") + ".prompt.json")
    atomic_write_json(prompt_path, prompt_obj)

    submit_body = submit_prompt_to_comfyui(
        server,
        prompt_obj,
        workflow_ui=workflow,
        workflow_name=job_key,
        client_id=client_id,
        front=front,
        timeout_s=timeout,
    )
    t1 = time.time()
    prompt_id = str(submit_body.get("prompt_id"))
    node_errors = comfy_node_errors(submit_body)
    if node_errors:
        raise RuntimeError(f"Comfy rejected prompt (node_errors): {json.dumps(node_errors, ensure_ascii=False)[:500]}")

    prompt_prepare_sec = round(t_prep1 - t_prep0, 3)
    submit_http_sec = round(t1 - t_prep1, 3)

    submit_record = {
        "schema_version": "comfyui-runpod.shape-submit.v0",
        "submitted_at": utc_now(),
        "comfy_server": server,
        "prompt_id": prompt_id,
        "prompt_source": prompt_source,
        "client_id": client_id,
        "front": bool(front),
        "submit_started_ts": t0,
        "submit_finished_ts": t1,
        "prompt_prepare_sec": prompt_prepare_sec,
        "submit_http_sec": submit_http_sec,
        "submit_http_sec_total": round(t1 - t0, 3),
        "prompt_path": str(prompt_path),
        "workflow_path": str(workflow_path),
        "prep_warnings": prep_warnings,
        "comfy_response": submit_body,
    }
    submit_path = job_path.with_name(job_path.stem.replace(".job", "") + ".submit.json")
    atomic_write_json(submit_path, submit_record)

    job["submit"] = {
        "status": "queued",
        "prompt_id": prompt_id,
        "prompt_source": prompt_source,
        "submitted_at": submit_record["submitted_at"],
        "comfy_server": server,
        "submit_path": str(submit_path),
        "prompt_path": str(prompt_path),
    }
    timings = ensure_timings(job)
    timings["submit"] = {
        "started_ts": t0,
        "finished_ts": t1,
        "prompt_prepare_sec": prompt_prepare_sec,
        "submit_http_sec": submit_http_sec,
        "total_sec": round(t1 - t0, 3),
    }
    timings["queue"] = {
        "submitted_ts": t1,
        "submitted_at": submit_record["submitted_at"],
    }
    atomic_write_json(job_path, job)
    persist_timings(job_path, job)

    return {
        "ok": True,
        "job_key": job_key,
        "job_path": str(job_path),
        "prompt_id": prompt_id,
        "prompt_source": prompt_source,
        "prompt_path": str(prompt_path),
        "submit_path": str(submit_path),
        "prep_warnings": prep_warnings,
    }


def cmd_submit(args: argparse.Namespace) -> int:
    job_paths = iter_job_paths(args)
    if not job_paths:
        print("error: no job files found (use --job, --jobs-dir, or --family)", file=sys.stderr)
        return 1

    server = str(args.server).rstrip("/")
    submitted = 0
    skipped = 0
    failed = 0
    data_root = Path(args.data_root).expanduser().resolve()
    quarantine_path = Path(getattr(args, "quarantine_path", DEFAULT_QUARANTINE_PATH)).expanduser().resolve()

    print(f"# Shape factory queue submit\n")
    print(f"- Comfy server: `{server}`")
    print(f"- Jobs: {len(job_paths)}")
    print(f"- dry_run: {args.dry_run}\n")

    quiet = bool(getattr(args, "quiet", False))
    max_attempts_override = getattr(args, "max_attempts", None)
    if isinstance(max_attempts_override, int) and max_attempts_override > 0:
        os.environ["SHAPE_FACTORY_SUBMIT_MAX_ATTEMPTS"] = str(max_attempts_override)
    print(f"- max_attempts: {submit_max_attempts()}\n")
    for job_path in job_paths:
        job_key = job_path.stem.replace(".job", "")
        try:
            result = submit_job_file(
                job_path,
                server=server,
                data_root=data_root,
                dry_run=bool(args.dry_run),
                force=bool(args.force),
                pending_only=bool(getattr(args, "pending_only", False)),
                client_id=str(args.client_id),
                front=bool(args.front),
                timeout=int(args.timeout),
                convert_timeout=int(args.convert_timeout),
                ignore_quarantine=bool(getattr(args, "ignore_quarantine", False)),
                quarantine_path=quarantine_path,
            )
            if result.get("skipped"):
                reason = str(result.get("reason") or "skipped")
                if quiet and reason in {"already_submitted", "submit_error", "abandoned"}:
                    skipped += 1
                    continue
                print(f"## {job_key}")
                pid = result.get("prompt_id")
                if pid:
                    print(f"skip (already submitted prompt_id={pid})")
                else:
                    print(f"skip ({reason})")
                skipped += 1
            elif result.get("dry_run"):
                print(f"## {job_key}")
                print(f"dry_run workflow={result.get('workflow_path')}")
                if result.get("prompt_seed_png"):
                    print(f"  prompt_seed_png={result['prompt_seed_png']}")
                submitted += 1
            else:
                print(f"## {job_key}")
                print(f"queued prompt_id={result.get('prompt_id')} source={result.get('prompt_source')}")
                print(f"  prompt={result.get('prompt_path')}")
                print(f"  submit={result.get('submit_path')}")
                submitted += 1
        except Exception as exc:
            print(f"## {job_key}")
            print(f"error: {exc}", file=sys.stderr)
            try:
                job = json.loads(job_path.read_text(encoding="utf-8"))
                outcome = record_submit_failure(job, error=str(exc), server=server)
                atomic_write_json(job_path, job)
                if quiet and outcome == "abandoned":
                    pass
                elif not quiet:
                    attempts = submit_attempt_count(job)
                    print(
                        f"  submit_{outcome} attempts={attempts}/{submit_max_attempts()}",
                        file=sys.stderr,
                    )
            except Exception:
                pass
            failed += 1

        if args.delay and not args.dry_run:
            time.sleep(args.delay)

    print(f"\nsubmit_ok={submitted}")
    print(f"submit_skipped={skipped}")
    print(f"submit_failed={failed}")
    return 0 if failed == 0 else 1


def fetch_comfy_queue(server: str, *, timeout_s: int = 15) -> dict[str, Any]:
    obj = _http_json("GET", f"{server.rstrip('/')}/queue", timeout_s=timeout_s)
    return obj if isinstance(obj, dict) else {}


def fetch_comfy_history(server: str, prompt_id: str, *, timeout_s: int = 30) -> Optional[dict[str, Any]]:
    try:
        obj = _http_json("GET", f"{server.rstrip('/')}/history/{prompt_id}", timeout_s=timeout_s)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    rec = obj.get(prompt_id)
    return rec if isinstance(rec, dict) else None


def queue_prompt_ids(server: str, *, timeout_s: int = 15) -> set[str]:
    q = fetch_comfy_queue(server, timeout_s=timeout_s)
    ids: set[str] = set()
    for key in ("queue_running", "queue_pending"):
        for item in q.get(key) or []:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                pid = item[1]
                if isinstance(pid, str) and pid.strip():
                    ids.add(pid.strip())
    return ids


def history_status_str(history: dict[str, Any]) -> str:
    status = history.get("status") if isinstance(history.get("status"), dict) else {}
    if status.get("completed") is True:
        return "complete"
    status_str = str(status.get("status_str") or "").lower()
    if "error" in status_str or status.get("status_str") == "error":
        return "error"
    return "unknown"


def host_dir_for_output_prefix(output_prefix: str, data_root: Path) -> Path:
    prefix = str(output_prefix or "").strip().strip("/")
    return (data_root / prefix).resolve()


def host_path_candidates_for_comfy_output(
    *,
    data_root: Path,
    subfolder: str,
    filename: str,
    fullpath: Optional[str] = None,
) -> list[Path]:
    candidates: list[Path] = []
    if fullpath:
        fp = str(fullpath).replace("\\", "/")
        if fp.startswith("/ComfyUI/"):
            candidates.append((data_root / fp[len("/ComfyUI/") :]).resolve())
        elif fp.startswith("/"):
            candidates.append(Path(fp).resolve())
    sub = str(subfolder or "").strip().strip("/")
    fname = str(filename or "").strip()
    if fname:
        if sub:
            candidates.append((data_root / sub / fname).resolve())
            candidates.append((data_root / "output" / sub / fname).resolve())
        else:
            candidates.append((data_root / "output" / fname).resolve())
    # dedupe
    seen: set[str] = set()
    out: list[Path] = []
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def extract_history_output_paths(history: dict[str, Any], data_root: Path) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    outputs = history.get("outputs") if isinstance(history.get("outputs"), dict) else {}
    for node_out in outputs.values():
        if not isinstance(node_out, dict):
            continue
        for key in ("gifs", "videos", "images"):
            for item in node_out.get(key) or []:
                if not isinstance(item, dict):
                    continue
                filename = str(item.get("filename") or "")
                if not filename.lower().endswith(".mp4"):
                    continue
                if str(item.get("type") or "") == "temp":
                    continue
                for cand in host_path_candidates_for_comfy_output(
                    data_root=data_root,
                    subfolder=str(item.get("subfolder") or ""),
                    filename=filename,
                    fullpath=str(item.get("fullpath") or "") or None,
                ):
                    if cand.is_file():
                        key = str(cand)
                        if key not in seen:
                            seen.add(key)
                            paths.append(cand)
    return sorted(paths)


def discover_job_outputs(job: dict[str, Any], data_root: Path) -> list[Path]:
    prefix = str(job.get("output_prefix") or "").strip()
    if not prefix:
        return []
    roots = [
        host_dir_for_output_prefix(prefix, data_root),
        (data_root / "output" / prefix).resolve(),
        (data_root / prefix.lstrip("output/")).resolve() if prefix.startswith("output/") else None,
    ]
    videos: list[Path] = []
    seen: set[str] = set()
    for base in roots:
        if base is None:
            continue
        if base.is_dir():
            batch = sorted(p.resolve() for p in base.rglob("*.mp4") if p.is_file())
        else:
            parent = base.parent
            stem = base.name
            batch = sorted(p.resolve() for p in parent.glob(f"{stem}*.mp4") if p.is_file()) if parent.is_dir() else []
        for path in batch:
            key = str(path)
            if key not in seen:
                seen.add(key)
                videos.append(path)
    return videos


def deposit_targets_for_job(job: dict[str, Any]) -> dict[str, str]:
    deposits = job.get("deposits") if isinstance(job.get("deposits"), dict) else {}
    out: dict[str, str] = {}
    for slot, spec in deposits.items():
        if isinstance(spec, dict):
            pool_ref = spec.get("to_pool")
            if isinstance(pool_ref, str) and pool_ref.strip():
                out[str(slot)] = parse_pool_ref(pool_ref)
    return out


def cmd_pool_sync(args: argparse.Namespace) -> int:
    pools_path = Path(args.pools).expanduser().resolve()
    pools_doc = load_yaml(pools_path)
    shape_path = Path(args.shape or pools_doc.get("shape") or "").expanduser()
    shape = load_yaml(shape_path) if str(shape_path) and shape_path.is_file() else {}

    index_path = Path(args.index or pool_index_path_for_pools(pools_path)).expanduser().resolve()
    index_doc = load_pool_index(index_path)
    index_doc["updated_at"] = utc_now()
    index_doc["pools_yaml"] = str(pools_path)
    if shape:
        index_doc["shape_path"] = str(shape_path)

    added_total = 0
    deposit_pools = pools_doc.get("deposit_pools") if isinstance(pools_doc.get("deposit_pools"), dict) else {}
    if not deposit_pools and shape.get("deposits"):
        deposit_pools = {}
        for slot, spec in (shape.get("deposits") or {}).items():
            if not isinstance(spec, dict):
                continue
            pool_id = parse_pool_ref(str(spec.get("to_pool") or ""))
            if pool_id:
                deposit_pools[pool_id] = {"slot": slot, "description": f"Deposit target for shape slot {slot}"}

    for pool_id, pool_spec in deposit_pools.items():
        if not isinstance(pool_spec, dict):
            continue
        new_members: list[dict[str, Any]] = []
        for spec in pool_spec.get("seed_members") or []:
            if not isinstance(spec, dict):
                continue
            for path in resolve_glob(spec) if spec.get("glob") else resolve_dir(spec) if spec.get("dir") else []:
                new_members.append(member_record_for_path(path, source="seed"))
        added = upsert_pool_index_members(
            index_doc,
            str(pool_id),
            new_members,
            description=str(pool_spec.get("description") or ""),
        )
        pool = index_doc["pools"][str(pool_id)]
        if pool_spec.get("slot"):
            pool["slot"] = pool_spec["slot"]
        print(f"pool={pool_id} added={added} total={len(pool.get('members') or [])}")
        added_total += added

    atomic_write_json(index_path, index_doc)
    print(f"pool_index={index_path}")
    print(f"pool_sync_added={added_total}")
    return 0


def update_job_status_from_comfy(
    job: dict[str, Any],
    *,
    server: str,
    data_root: Path,
    queue_ids: Optional[set[str]] = None,
    now: Optional[float] = None,
) -> str:
    submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    prompt_id = str(submit.get("prompt_id") or "").strip()
    if not prompt_id:
        return str(submit.get("status") or "pending")

    now_ts = float(now if now is not None else time.time())
    if queue_ids is None:
        queue_ids = queue_prompt_ids(server)
    if prompt_id in queue_ids:
        submit["status"] = "running"
        update_job_timings_on_status(job, status="running", history=None, now=now_ts, data_root=data_root)
        return "running"

    history = fetch_comfy_history(server, prompt_id)
    if history is None:
        outputs = discover_job_outputs(job, data_root)
        if outputs:
            submit["status"] = "complete"
            submit["outputs"] = [str(p) for p in outputs]
            submit["output_discovery"] = "filesystem"
            update_job_timings_on_status(
                job, status="complete", history=None, now=now_ts, data_root=data_root
            )
            return "complete"
        if submit.get("status") in {"queued", "running"}:
            submit["status"] = "unknown"
        return str(submit.get("status") or "unknown")

    status = history_status_str(history)
    submit["status"] = status
    submit["history_checked_at"] = utc_now()
    hist_outputs = extract_history_output_paths(history, data_root)
    if hist_outputs:
        submit["outputs"] = [str(p) for p in hist_outputs]
        submit["output_discovery"] = "comfy_history"
    else:
        outputs = discover_job_outputs(job, data_root)
        if outputs:
            submit["outputs"] = [str(p) for p in outputs]
            submit["output_discovery"] = "filesystem"
    update_job_timings_on_status(
        job, status=status, history=history, now=now_ts, data_root=data_root
    )
    return status


def cmd_status(args: argparse.Namespace) -> int:
    job_paths = iter_job_paths(args)
    if not job_paths:
        print("error: no job files found", file=sys.stderr)
        return 1

    server = str(args.server).rstrip("/")
    data_root = Path(args.data_root).expanduser().resolve()
    deadline = time.time() + float(args.timeout) if args.wait else None

    print(f"# Shape factory status\n")
    print(f"- Comfy server: `{server}`")
    print(f"- Jobs: {len(job_paths)}")
    print(f"- wait: {bool(args.wait)} deposit: {bool(args.deposit)}\n")

    quiet = bool(getattr(args, "quiet", False))
    while True:
        queue_ids = queue_prompt_ids(server)
        counts = {
            "pending": 0,
            "queued": 0,
            "running": 0,
            "complete": 0,
            "error": 0,
            "abandoned": 0,
            "unknown": 0,
        }
        for job_path in job_paths:
            job = json.loads(job_path.read_text(encoding="utf-8"))
            if hostify_job_paths(job):
                atomic_write_json(job_path, job)
            # Cap retries: promote exhausted error jobs to abandoned.
            submit_block = job.get("submit") if isinstance(job.get("submit"), dict) else {}
            if str(submit_block.get("status") or "") == "error" and job_retries_exhausted(job):
                abandon_submit_failure(
                    job,
                    error=str(submit_block.get("error") or "submit retries exhausted"),
                    server=str(submit_block.get("comfy_server") or server),
                    previous_status="error",
                    attempts=submit_attempt_count(job),
                )
                atomic_write_json(job_path, job)
            job_key = str(job.get("job_key") or job_path.stem)
            status = update_job_status_from_comfy(job, server=server, data_root=data_root, queue_ids=queue_ids)
            if status == "completed":
                status = "complete"
            bucket = status if status in counts else "unknown"
            counts[bucket] = counts.get(bucket, 0) + 1
            atomic_write_json(job_path, job)
            persist_timings(job_path, job, ledger=status in {"complete", "error"})
            if quiet and status in {"complete", "abandoned", "error"}:
                continue
            outputs = (job.get("submit") or {}).get("outputs") if isinstance(job.get("submit"), dict) else None
            out_hint = f" outputs={len(outputs)}" if isinstance(outputs, list) else ""
            timing_hint = format_timing_hint(job)
            print(f"{job_key}: {status}{out_hint}{timing_hint}")

        if args.deposit:
            dep_args = argparse.Namespace(
                family=args.family,
                job=args.job if hasattr(args, "job") else None,
                jobs_dir=args.jobs_dir if hasattr(args, "jobs_dir") else None,
                job_dir=args.job_dir,
                limit=args.limit,
                data_root=args.data_root,
                index=None,
                pools=None,
            )
            cmd_deposit(dep_args)

        pending = counts.get("queued", 0) + counts.get("running", 0) + counts.get("unknown", 0)
        print(f"\nstatus_summary={counts}")
        if not args.wait or pending == 0:
            break
        if deadline is not None and time.time() >= deadline:
            print("status_timeout=1")
            return 2
        time.sleep(args.poll)

    return 0


def cmd_deposit(args: argparse.Namespace) -> int:
    job_paths = iter_job_paths(args)
    if not job_paths:
        print("error: no job files found", file=sys.stderr)
        return 1

    data_root = Path(args.data_root).expanduser().resolve()
    deposited = 0
    skipped = 0

    print(f"# Shape factory deposit\n")
    quiet = bool(getattr(args, "quiet", False))
    for job_path in job_paths:
        job = json.loads(job_path.read_text(encoding="utf-8"))
        if hostify_job_paths(job):
            atomic_write_json(job_path, job)
        job_key = str(job.get("job_key") or job_path.stem)
        submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
        status = str(submit.get("status") or "")
        if status != "complete":
            if not quiet or status not in {"error", "abandoned", "pending", ""}:
                print(f"skip {job_key} (status={status or 'pending'})")
            skipped += 1
            continue

        outputs = submit.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            outputs = [str(p) for p in discover_job_outputs(job, data_root)]
        video_paths = [Path(str(p)).expanduser() for p in outputs if str(p).lower().endswith(".mp4")]
        if not video_paths:
            print(f"skip {job_key} (no mp4 outputs)")
            skipped += 1
            continue

        targets = deposit_targets_for_job(job)
        if not targets:
            print(f"skip {job_key} (no deposit targets in job)")
            skipped += 1
            continue

        pools_path = hostify_repo_path(str(job.get("pools_path") or args.pools or ""))
        if args.index:
            index_path = hostify_repo_path(args.index)
        else:
            index_path = hostify_repo_path(pool_index_path_for_pools(pools_path))
        index_doc = load_pool_index(index_path)
        index_doc["updated_at"] = utc_now()

        for slot, pool_id in targets.items():
            new_members = [
                member_record_for_path(p, job_key=job_key, source="shape_factory")
                for p in video_paths
            ]
            added = upsert_pool_index_members(index_doc, pool_id, new_members)
            print(f"deposit {job_key} slot={slot} pool={pool_id} added={added} index={index_path}")
            deposited += added

        dep = job.setdefault("deposit", {})
        dep["started_ts"] = time.time()
        dep["deposited_at"] = utc_now()
        dep["pools"] = targets
        dep["videos"] = [str(p) for p in video_paths]
        dep["index_path"] = str(index_path)

        # Predicted/hourly jobs can request a disposition stamp on deposit.
        disp_entry = str(job.get("disposition_entry") or "").strip()
        if disp_entry and video_paths:
            try:
                from shape_factory_disposition import stamp_output_disposition

                note = str(job.get("disposition_note") or "").strip() or (
                    f"hourly predicted derive job={job_key}"
                    if str(job.get("rating_kind") or "") == "predicted"
                    else f"shape_factory job={job_key}"
                )
                stamped = []
                for vp in video_paths:
                    try:
                        saved = stamp_output_disposition(
                            media_abs=vp,
                            marker_id=disp_entry,
                            note=note,
                        )
                        stamped.append({"path": str(vp), "markers": saved.get("markers")})
                    except Exception as exc:
                        if not quiet:
                            print(f"  disposition_warn {vp.name}: {exc}", file=sys.stderr)
                if stamped:
                    dep["disposition"] = {"entry": disp_entry, "outputs": stamped}
                    if not quiet:
                        print(f"  disposition={disp_entry} on {len(stamped)} output(s)")
            except Exception as exc:
                if not quiet:
                    print(f"  disposition_skip: {exc}", file=sys.stderr)

        dep_t1 = time.time()
        dep_started = dep.get("started_ts")
        if isinstance(dep_started, (int, float)):
            timings = ensure_timings(job)
            timings["deposit"] = {
                "started_ts": dep_started,
                "finished_ts": dep_t1,
                "sec": round(dep_t1 - float(dep_started), 3),
            }
        atomic_write_json(job_path, job)
        atomic_write_json(index_path, index_doc)
        persist_timings(job_path, job, ledger=should_append_timings_ledger(job))

    print(f"\ndeposit_added={deposited}")
    print(f"deposit_skipped={skipped}")
    return 0


def cmd_pipeline(args: argparse.Namespace) -> int:
    pipeline_path = Path(args.pipeline).expanduser().resolve()
    pipeline = load_yaml(pipeline_path)
    steps = pipeline.get("steps") or []
    if not isinstance(steps, list) or not steps:
        raise RuntimeError(f"pipeline has no steps: {pipeline_path}")

    print(f"# Pipeline `{pipeline.get('pipeline_id') or pipeline_path.stem}`\n")
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id") or "?")
        print(f"## step {step_id}")

        gen_args = argparse.Namespace(
            shape=step["shape"],
            pools=step["pools"],
            pick=str(step.get("pick") or "zip"),
            limit=int(step.get("limit") or args.limit or 1),
            pick_index=int(step.get("pick_index") or 0),
            data_root=args.data_root,
            workflow_dir=args.workflow_dir,
            job_dir=args.job_dir,
            binds_override=step.get("binds_override") if isinstance(step.get("binds_override"), dict) else None,
            dev=bool(getattr(args, "dev", False)),
            dev_tuning=getattr(args, "dev_tuning", None),
            dev_frames=getattr(args, "dev_frames", None),
            dev_steps=getattr(args, "dev_steps", None),
            quarantine_path=getattr(args, "quarantine_path", str(DEFAULT_QUARANTINE_PATH)),
            ignore_quarantine=bool(getattr(args, "ignore_quarantine", False)),
        )
        if cmd_generate(gen_args) != 0:
            return 1

        shape_doc = load_yaml(Path(step["shape"]).expanduser().resolve())
        family = str(shape_doc.get("family_slug") or slug(Path(str(step["shape"])).stem.replace(".shape", ""), 80))

        if not args.generate_only:
            sub_args = argparse.Namespace(
                family=family,
                job=None,
                jobs_dir=None,
                job_dir=args.job_dir,
                limit=gen_args.limit,
                server=args.server,
                client_id=args.client_id,
                front=False,
                force=False,
                dry_run=args.dry_run,
                data_root=args.data_root,
                timeout=args.timeout,
                convert_timeout=args.convert_timeout,
                delay=0.0,
                quarantine_path=getattr(args, "quarantine_path", str(DEFAULT_QUARANTINE_PATH)),
                ignore_quarantine=bool(getattr(args, "ignore_quarantine", False)),
            )
            if cmd_submit(sub_args) != 0 and not args.dry_run:
                return 1

            if args.wait and not args.dry_run:
                st_args = argparse.Namespace(
                    family=family,
                    job=None,
                    jobs_dir=None,
                    job_dir=args.job_dir,
                    limit=gen_args.limit,
                    server=args.server,
                    data_root=args.data_root,
                    wait=True,
                    timeout=args.wait_timeout,
                    poll=args.poll,
                    deposit=False,
                )
                cmd_status(st_args)

            if not args.dry_run:
                pools_path = Path(step["pools"]).expanduser().resolve()
                sync_args = argparse.Namespace(
                    pools=str(pools_path),
                    shape=step.get("shape"),
                    index=None,
                )
                cmd_pool_sync(sync_args)

                dep_args = argparse.Namespace(
                    family=family,
                    job=None,
                    jobs_dir=None,
                    job_dir=args.job_dir,
                    limit=gen_args.limit,
                    data_root=args.data_root,
                    index=None,
                    pools=str(pools_path),
                )
                cmd_deposit(dep_args)

        print()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Shape + pool workflow factory")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pools = sub.add_parser("pools", help="Inspect pool manifests")
    pools_sub = pools.add_subparsers(dest="pools_cmd", required=True)
    pools_list = pools_sub.add_parser("list", help="List pool members")
    pools_list.add_argument("--pools", required=True, help="pools.yaml path")
    pools_list.add_argument("--shape", help="shape.yaml (optional if pools file references it)")
    pools_list.add_argument("--limit", type=int, default=8, help="Max members to print per pool")
    pools_list.set_defaults(func=cmd_pools_list)

    gen = sub.add_parser("generate", help="Generate workflows by binding pool picks to a shape")
    gen.add_argument("--shape", required=True, help="shape.yaml path")
    gen.add_argument("--pools", required=True, help="pools.yaml path")
    gen.add_argument("--pick", choices=["zip", "product", "replay", "derive", "extend", "pool_product"], default="zip", help="Combine pools: zip (default), product, replay/derive/extend/pool_product (with --picks-json)")
    gen.add_argument("--limit", type=int, default=4, help="Max jobs to generate")
    gen.add_argument("--pick-index", type=int, default=0, dest="pick_index", help="Skip first N zip combos (replay chain N)")
    gen.add_argument(
        "--picks-json",
        type=Path,
        default=None,
        help="Explicit slot→path picks JSON (or plan-replay output); uses replay bindings instead of pool product grid",
    )
    gen.add_argument(
        "--job-suffix",
        default=None,
        help="Append to job_key for repeat runs (e.g. hourly tag _h2026070320)",
    )
    gen.add_argument(
        "--output-prefix-root",
        default=None,
        dest="output_prefix_root",
        help="Override shape output_prefix_root (supports %%date:yyyy-MM-dd%% tokens), e.g. og/%%date:yyyy-MM-dd%%/hourly",
    )
    gen.add_argument(
        "--job-key-prefix",
        default=None,
        dest="job_key_prefix",
        help="Replace the family_slug leading stem in job_key/filenames (e.g. hourly → hourly__prompt_profile-…)",
    )
    gen.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT), help="Comfy bind data root on host")
    gen.add_argument("--workflow-dir", default=str(DEFAULT_WORKFLOW_DIR), help="Output workflow JSON directory")
    gen.add_argument("--job-dir", default=str(DEFAULT_JOB_DIR), help="Output job metadata directory")
    gen.add_argument(
        "--binds-override",
        help="YAML/JSON file with slot→pool bindings (pipeline step 2)",
    )
    gen.add_argument(
        "--dev",
        action="store_true",
        help=f"Apply fast dev tuning profile ({DEFAULT_DEV_TUNING.name}: fewer frames/steps)",
    )
    gen.add_argument("--dev-tuning", help="Custom dev tuning YAML (overrides --dev defaults)")
    gen.add_argument("--dev-frames", type=int, help="Override generation frame count (mxSlider node 84)")
    gen.add_argument("--dev-steps", type=int, help="Override sampler steps (mxSlider node 82)")
    gen.add_argument("--quarantine-path", default=str(DEFAULT_QUARANTINE_PATH), help="Workflow quarantine registry JSON")
    gen.add_argument(
        "--ignore-quarantine",
        action="store_true",
        help="Allow generate even when shape template is quarantined",
    )
    gen.set_defaults(
        func=cmd_generate,
        binds_override=None,
        pick_index=0,
        job_suffix=None,
        output_prefix_root=None,
        job_key_prefix=None,
        dev=False,
        dev_tuning=None,
        dev_frames=None,
        dev_steps=None,
        ignore_quarantine=False,
    )

    sub_p = sub.add_parser("submit", help="Convert shape jobs to API prompts and POST to Comfy /prompt")
    sub_p.add_argument("--job", help="Single .job.json path")
    sub_p.add_argument("--jobs-dir", help="Directory tree to scan for *.job.json")
    sub_p.add_argument("--family", help="Family subfolder under --job-dir (e.g. FB9_GEX2)")
    sub_p.add_argument("--job-dir", default=str(DEFAULT_JOB_DIR), help="Base job directory (with --family)")
    sub_p.add_argument("--limit", type=int, help="Max jobs to submit")
    sub_p.add_argument("--server", default=DEFAULT_COMFY_SERVER, help="ComfyUI base URL")
    sub_p.add_argument("--client-id", default="shape_factory", help="Comfy client_id")
    sub_p.add_argument("--front", action="store_true", help="Queue to front of Comfy queue")
    sub_p.add_argument("--force", action="store_true", help="Re-submit even if job already has prompt_id")
    sub_p.add_argument(
        "--pending-only",
        action="store_true",
        help="Prefer never-queued jobs; also retry failed jobs until SHAPE_FACTORY_SUBMIT_MAX_ATTEMPTS",
    )
    sub_p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-job skip noise (already_submitted / abandoned / submit_error)",
    )
    sub_p.add_argument(
        "--max-attempts",
        type=int,
        default=None,
        help="Override SHAPE_FACTORY_SUBMIT_MAX_ATTEMPTS for this run (failed submits before abandon)",
    )
    sub_p.add_argument("--dry-run", action="store_true", help="Validate jobs only; do not call Comfy")
    sub_p.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT), help="Host Comfy bind data root")
    sub_p.add_argument("--timeout", type=int, default=60, help="HTTP timeout for /prompt")
    sub_p.add_argument("--convert-timeout", type=int, default=180, help="HTTP timeout for /workflow/convert")
    sub_p.add_argument("--delay", type=float, default=0.0, help="Seconds between submits")
    sub_p.add_argument("--quarantine-path", default=str(DEFAULT_QUARANTINE_PATH), help="Workflow quarantine registry JSON")
    sub_p.add_argument(
        "--ignore-quarantine",
        action="store_true",
        help="Submit jobs even when shape template is quarantined",
    )
    sub_p.set_defaults(func=cmd_submit, ignore_quarantine=False, pending_only=False)

    pool = sub.add_parser("pool", help="Pool index maintenance")
    pool_sub = pool.add_subparsers(dest="pool_cmd", required=True)
    pool_sync = pool_sub.add_parser("sync", help="Build/update JSON pool index from seed globs")
    pool_sync.add_argument("--pools", required=True, help="pools.yaml path")
    pool_sync.add_argument("--shape", help="shape.yaml (optional)")
    pool_sync.add_argument("--index", help="Override index.json output path")
    pool_sync.set_defaults(func=cmd_pool_sync)

    st = sub.add_parser("status", help="Poll Comfy queue/history for submitted shape jobs")
    st.add_argument("--job", help="Single .job.json path")
    st.add_argument("--jobs-dir", help="Directory tree to scan for *.job.json")
    st.add_argument("--family", help="Family subfolder under --job-dir")
    st.add_argument("--job-dir", default=str(DEFAULT_JOB_DIR), help="Base job directory")
    st.add_argument("--limit", type=int, help="Max jobs to check")
    st.add_argument("--server", default=DEFAULT_COMFY_SERVER, help="ComfyUI base URL")
    st.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT), help="Host Comfy bind data root")
    st.add_argument("--wait", action="store_true", help="Block until no queued/running jobs")
    st.add_argument("--poll", type=float, default=5.0, help="Poll interval when --wait")
    st.add_argument("--timeout", type=int, default=7200, help="Max seconds to wait")
    st.add_argument("--deposit", action="store_true", help="Run deposit after status update")
    st.add_argument("--quiet", action="store_true", help="Hide complete/abandoned/error per-job lines")
    st.set_defaults(func=cmd_status)

    dep = sub.add_parser("deposit", help="Register completed job outputs into pool index")
    dep.add_argument("--job", help="Single .job.json path")
    dep.add_argument("--jobs-dir", help="Directory tree to scan for *.job.json")
    dep.add_argument("--family", help="Family subfolder under --job-dir")
    dep.add_argument("--job-dir", default=str(DEFAULT_JOB_DIR), help="Base job directory")
    dep.add_argument("--limit", type=int, help="Max jobs to deposit")
    dep.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT), help="Host Comfy bind data root")
    dep.add_argument("--pools", help="pools.yaml (default from job metadata)")
    dep.add_argument("--index", help="Override index.json path")
    dep.add_argument("--quiet", action="store_true", help="Hide routine skip lines for incomplete jobs")
    dep.set_defaults(func=cmd_deposit)

    pipe = sub.add_parser("pipeline", help="Run multi-step shape pipelines")
    pipe_sub = pipe.add_subparsers(dest="pipeline_cmd", required=True)
    pipe_run = pipe_sub.add_parser("run", help="Generate/submit/wait/deposit each pipeline step")
    pipe_run.add_argument("--pipeline", required=True, help="pipeline.yaml path")
    pipe_run.add_argument("--limit", type=int, default=1, help="Jobs per step")
    pipe_run.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    pipe_run.add_argument("--workflow-dir", default=str(DEFAULT_WORKFLOW_DIR))
    pipe_run.add_argument("--job-dir", default=str(DEFAULT_JOB_DIR))
    pipe_run.add_argument("--server", default=DEFAULT_COMFY_SERVER)
    pipe_run.add_argument("--client-id", default="shape_factory")
    pipe_run.add_argument("--dry-run", action="store_true")
    pipe_run.add_argument("--generate-only", action="store_true")
    pipe_run.add_argument("--wait", action="store_true", help="Wait for each step to complete")
    pipe_run.add_argument("--wait-timeout", type=int, default=7200)
    pipe_run.add_argument("--poll", type=float, default=10.0)
    pipe_run.add_argument("--timeout", type=int, default=60, help="HTTP timeout for /prompt")
    pipe_run.add_argument("--convert-timeout", type=int, default=180)
    pipe_run.add_argument("--dev", action="store_true", help="Use dev-fast tuning for every pipeline step")
    pipe_run.add_argument("--dev-tuning", help="Custom dev tuning YAML for pipeline steps")
    pipe_run.add_argument("--dev-frames", type=int, help="Override frame count for pipeline steps")
    pipe_run.add_argument("--dev-steps", type=int, help="Override steps for pipeline steps")
    pipe_run.add_argument("--quarantine-path", default=str(DEFAULT_QUARANTINE_PATH))
    pipe_run.add_argument("--ignore-quarantine", action="store_true")
    pipe_run.set_defaults(func=cmd_pipeline, dev=False, dev_tuning=None, dev_frames=None, dev_steps=None, ignore_quarantine=False)

    timings = sub.add_parser("timings", help="List or summarize generation timings")
    timings_sub = timings.add_subparsers(dest="timings_cmd", required=True)
    timings_list = timings_sub.add_parser("list", help="Per-job timing rows")
    timings_list.add_argument("--job", help="Single .job.json path")
    timings_list.add_argument("--jobs-dir", help="Directory tree to scan for *.job.json")
    timings_list.add_argument("--family", help="Family subfolder under --job-dir")
    timings_list.add_argument("--job-dir", default=str(DEFAULT_JOB_DIR), help="Base job directory")
    timings_list.add_argument("--limit", type=int, help="Max jobs")
    timings_list.set_defaults(func=cmd_timings, timings_cmd="list")

    timings_summary = timings_sub.add_parser("summary", help="Aggregate execution times by family")
    timings_summary.add_argument("--job", help="Single .job.json path")
    timings_summary.add_argument("--jobs-dir", help="Directory tree to scan for *.job.json")
    timings_summary.add_argument("--family", help="Family subfolder under --job-dir")
    timings_summary.add_argument("--job-dir", default=str(DEFAULT_JOB_DIR), help="Base job directory")
    timings_summary.add_argument("--limit", type=int, help="Max jobs")
    timings_summary.add_argument(
        "--group-by",
        choices=["graph_hash", "family", "shape_id", "dev_profile"],
        default="graph_hash",
        help="Efficiency summary grouping (default: graph_hash + dev/prod)",
    )
    timings_summary.set_defaults(func=cmd_timings, timings_cmd="summary")

    timings_compare = timings_sub.add_parser("compare", help="Compare baseline vs candidate workflow efficiency")
    timings_compare.add_argument("--baseline", required=True, help="Baseline .job.json (e.g. prod run)")
    timings_compare.add_argument("--candidate", required=True, help="Candidate .job.json (e.g. optimized run)")
    timings_compare.set_defaults(func=cmd_timings, timings_cmd="compare")

    jobs = sub.add_parser("jobs", help="Job metadata maintenance")
    jobs_sub = jobs.add_subparsers(dest="jobs_cmd", required=True)
    jobs_repair = jobs_sub.add_parser("repair", help="Sync job.json from submit/timings sidecars; optional prompt refresh")
    jobs_repair.add_argument("--job", help="Single .job.json path")
    jobs_repair.add_argument("--jobs-dir", help="Directory tree to scan for *.job.json")
    jobs_repair.add_argument("--family", help="Family subfolder under --job-dir")
    jobs_repair.add_argument("--job-dir", default=str(DEFAULT_JOB_DIR), help="Base job directory")
    jobs_repair.add_argument("--limit", type=int, help="Max jobs")
    jobs_repair.add_argument("--server", default=DEFAULT_COMFY_SERVER)
    jobs_repair.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    jobs_repair.add_argument("--convert-timeout", type=int, default=180)
    jobs_repair.add_argument(
        "--refresh-prompts",
        action="store_true",
        help="Regenerate .prompt.json from workflow (applies UI link relink fix)",
    )
    jobs_repair.set_defaults(func=cmd_jobs_repair)

    val = sub.add_parser("validate", help="Validate workflows against Comfy node registry and convert/prompt checks")
    val.add_argument(
        "--catalog",
        action="store_true",
        help="Validate all *.json workflows under --catalog-dir",
    )
    val.add_argument(
        "--catalog-dir",
        default=str(DEFAULT_CATALOG_DIR),
        help="Catalog workflow directory (used with --catalog)",
    )
    val.add_argument("--workflow", action="append", help="LiteGraph workflow JSON (repeatable)")
    val.add_argument("--shape", help="Validate shape template workflow")
    val.add_argument("--job", help="Single .job.json (uses generated workflow)")
    val.add_argument("--jobs-dir", help="Directory tree to scan for *.job.json")
    val.add_argument("--family", help="Family subfolder under --job-dir")
    val.add_argument("--job-dir", default=str(DEFAULT_JOB_DIR))
    val.add_argument("--limit", type=int)
    val.add_argument("--server", default=DEFAULT_COMFY_SERVER)
    val.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    val.add_argument("--convert-timeout", type=int, default=180)
    val.add_argument(
        "--comfy-check",
        action="store_true",
        help="POST /prompt to Comfy for validation (dequeues immediately if accepted)",
    )
    val.add_argument(
        "--report-dir",
        default=str(DEFAULT_JOB_DIR.parent / "validation"),
        help="Write per-workflow *.validate.json reports",
    )
    val.add_argument("--quarantine-path", default=str(DEFAULT_QUARANTINE_PATH), help="Workflow quarantine registry JSON")
    val.add_argument(
        "--update-quarantine",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Update quarantine registry from validation results (default: on)",
    )
    val.add_argument(
        "--auto-patch",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Apply workflow_node_id_map compat patches before validation (default: on)",
    )
    val.add_argument(
        "--write-patches",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write patched workflows back to disk with .bak backup (default: on)",
    )
    val.add_argument(
        "--node-type-map",
        default=str(DEFAULT_NODE_TYPE_MAP),
        help="YAML map of deprecated node type renames (workflow_node_id_map.yaml)",
    )
    val.add_argument(
        "--repair-rules",
        default=str(DEFAULT_REPAIR_RULES_PATH),
        help="YAML prompt error repair rules",
    )
    val.add_argument(
        "--max-repair-rounds",
        type=int,
        default=5,
        help="Max pattern→fix→retry rounds during validate (default: 5)",
    )
    val.set_defaults(func=cmd_validate, ignore_quarantine=False)

    repair = sub.add_parser("repair", help="Workflow repair rules (pattern → fix → retry)")
    repair_sub = repair.add_subparsers(dest="repair_cmd", required=True)
    repair_list = repair_sub.add_parser("rules", help="List registered repair rules")
    repair_list.add_argument("--repair-rules", default=str(DEFAULT_REPAIR_RULES_PATH))
    repair_list.set_defaults(func=cmd_repair, repair_cmd="rules")

    repair_run = repair_sub.add_parser("run", help="Run repair loop on workflow(s) without full quarantine update")
    repair_run.add_argument("--catalog", action="store_true")
    repair_run.add_argument("--catalog-dir", default=str(DEFAULT_CATALOG_DIR))
    repair_run.add_argument("--workflow", action="append")
    repair_run.add_argument("--limit", type=int)
    repair_run.add_argument("--server", default=DEFAULT_COMFY_SERVER)
    repair_run.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    repair_run.add_argument("--convert-timeout", type=int, default=180)
    repair_run.add_argument("--comfy-check", action="store_true")
    repair_run.add_argument("--dry-run", action="store_true")
    repair_run.add_argument("--max-repair-rounds", type=int, default=5)
    repair_run.add_argument("--node-type-map", default=str(DEFAULT_NODE_TYPE_MAP))
    repair_run.add_argument("--repair-rules", default=str(DEFAULT_REPAIR_RULES_PATH))
    repair_run.add_argument(
        "--report-dir",
        default=str(DEFAULT_JOB_DIR.parent / "validation"),
    )
    repair_run.set_defaults(func=cmd_repair, repair_cmd="run")

    quarantine = sub.add_parser("quarantine", help="Managed quarantine state for catalog/shape workflows")
    quarantine.add_argument("--quarantine-path", default=str(DEFAULT_QUARANTINE_PATH), help="Registry JSON path")
    quarantine_sub = quarantine.add_subparsers(dest="quarantine_cmd", required=True)

    q_list = quarantine_sub.add_parser("list", help="List quarantine registry entries")
    q_list.add_argument(
        "--status",
        choices=["quarantined", "ok", "released"],
        help="Filter by status",
    )
    q_list.set_defaults(func=cmd_quarantine, quarantine_cmd="list")

    q_show = quarantine_sub.add_parser("show", help="Show one registry entry as JSON")
    q_show.add_argument("--workflow", required=True, help="Catalog workflow JSON path")
    q_show.set_defaults(func=cmd_quarantine, quarantine_cmd="show")

    q_apply = quarantine_sub.add_parser("apply", help="Update registry from existing *.validate.json reports")
    q_apply.add_argument(
        "--report-dir",
        default=str(DEFAULT_JOB_DIR.parent / "validation"),
        help="Directory with per-workflow validation reports",
    )
    q_apply.add_argument(
        "--comfy-check",
        action="store_true",
        help="Mark entries as comfy-check validated when applying reports",
    )
    q_apply.set_defaults(func=cmd_quarantine, quarantine_cmd="apply")

    q_release = quarantine_sub.add_parser("release", help="Manually release a workflow after human review")
    q_release.add_argument("--workflow", required=True, help="Catalog workflow JSON path")
    q_release.add_argument("--note", default="", help="Review note (why released)")
    q_release.set_defaults(func=cmd_quarantine, quarantine_cmd="release")

    q_patch = quarantine_sub.add_parser("patch", help="Auto-patch deprecated node types (e.g. LoadImageWithFilename)")
    q_patch.add_argument("--catalog", action="store_true", help="Patch all workflows under --catalog-dir")
    q_patch.add_argument("--catalog-dir", default=str(DEFAULT_CATALOG_DIR))
    q_patch.add_argument("--workflow", action="append", help="Workflow JSON path (repeatable)")
    q_patch.add_argument("--limit", type=int)
    q_patch.add_argument("--server", default=DEFAULT_COMFY_SERVER)
    q_patch.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    q_patch.add_argument("--convert-timeout", type=int, default=180)
    q_patch.add_argument("--dry-run", action="store_true", help="Show patches without writing workflow files")
    q_patch.add_argument("--revalidate", action="store_true", help="Run validate after patching")
    q_patch.add_argument("--comfy-check", action="store_true", help="Use --comfy-check when --revalidate")
    q_patch.add_argument(
        "--report-dir",
        default=str(DEFAULT_JOB_DIR.parent / "validation"),
    )
    q_patch.add_argument("--node-type-map", default=str(DEFAULT_NODE_TYPE_MAP))
    q_patch.set_defaults(func=cmd_quarantine, quarantine_cmd="patch")

    q_sync = quarantine_sub.add_parser("sync", help="Validate catalog and refresh quarantine registry")
    q_sync.add_argument("--catalog-dir", default=str(DEFAULT_CATALOG_DIR))
    q_sync.add_argument("--limit", type=int)
    q_sync.add_argument("--server", default=DEFAULT_COMFY_SERVER)
    q_sync.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    q_sync.add_argument("--convert-timeout", type=int, default=180)
    q_sync.add_argument("--comfy-check", action="store_true", help="Include Comfy /prompt accept check")
    q_sync.add_argument(
        "--report-dir",
        default=str(DEFAULT_JOB_DIR.parent / "validation"),
    )
    q_sync.add_argument("--node-type-map", default=str(DEFAULT_NODE_TYPE_MAP))
    q_sync.add_argument("--max-repair-rounds", type=int, default=5)
    q_sync.set_defaults(func=cmd_quarantine, quarantine_cmd="sync")

    add_ratings_subparser(sub)
    add_heuristics_subparser(sub)
    add_rating_sampler_subparser(sub)
    add_tags_subparser(sub)
    add_source_facets_subparser(sub)
    add_seed_sources_subparser(sub)
    add_backfill_subparser(sub)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "binds_override", None) and isinstance(args.binds_override, str):
        override_path = Path(args.binds_override).expanduser()
        text = override_path.read_text(encoding="utf-8")
        args.binds_override = yaml.safe_load(text) if override_path.suffix in {".yaml", ".yml"} else json.loads(text)
        if not isinstance(args.binds_override, dict):
            raise RuntimeError(f"binds_override must be a mapping: {override_path}")
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
