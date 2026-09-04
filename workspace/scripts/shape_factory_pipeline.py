#!/usr/bin/env python3
"""Multi-step pipeline runner (CLI + Experiments UI API)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from shape_factory import (
    DEFAULT_COMFY_SERVER,
    DEFAULT_DATA_ROOT,
    DEFAULT_JOB_DIR,
    DEFAULT_QUARANTINE_PATH,
    DEFAULT_WORKFLOW_DIR,
    cmd_deposit,
    cmd_generate,
    cmd_pool_sync,
    cmd_status,
    cmd_submit,
    load_yaml,
    utc_now,
)
from shape_factory_map import resolve_shape_factory_data_root


PIPELINE_RUNS_DIRNAME = "pipeline_runs"


def default_pipeline_runs_dir(data_root: Optional[Path] = None) -> Path:
    dr = (data_root or Path(__file__).resolve().parents[2] / ".data").expanduser().resolve()
    return dr / "shape_factory" / PIPELINE_RUNS_DIRNAME


def resolve_pipeline_path(
    *,
    data_root: Path,
    pipeline_id: str = "",
    pipeline_path: str = "",
) -> Path:
    raw = str(pipeline_path or "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (data_root.parent / raw).resolve() if raw.startswith(".data/") else path.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"pipeline not found: {path}")
        return path

    pid = str(pipeline_id or "").strip()
    if not pid:
        raise ValueError("pipeline_id or pipeline path is required")

    pipelines_root = data_root / "pipelines"
    direct = pipelines_root / f"{pid}.pipeline.yaml"
    if direct.is_file():
        return direct
    if not pid.endswith(".yaml"):
        alt = pipelines_root / f"{pid}.yaml"
        if alt.is_file():
            return alt

    for path in sorted(pipelines_root.glob("*.yaml")):
        try:
            doc = load_yaml(path)
        except Exception:
            continue
        if str(doc.get("pipeline_id") or "").strip() == pid:
            return path

    raise FileNotFoundError(f"pipeline not found for id {pid!r} under {pipelines_root}")


def _job_paths_for_family(job_dir: Path, family: str, *, limit: int) -> List[Path]:
    root = job_dir / family
    if not root.is_dir():
        return []
    paths = [p for p in root.glob("*.job.json") if p.is_file()]
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return paths[: max(1, int(limit or 1))]


def _summarize_jobs(job_paths: List[Path]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for path in job_paths:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(doc, dict):
            continue
        submit = doc.get("submit") if isinstance(doc.get("submit"), dict) else {}
        out.append(
            {
                "job_key": doc.get("job_key") or path.stem.replace(".job", ""),
                "family_slug": doc.get("family_slug"),
                "status": doc.get("status"),
                "prompt_id": submit.get("prompt_id"),
                "job_path": str(path),
            }
        )
    return out


def _write_run_state(path: Path, doc: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_pipeline_run(run_path: Path) -> Optional[Dict[str, Any]]:
    if not run_path.is_file():
        return None
    try:
        doc = json.loads(run_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def tail_pipeline_log(log_path: Path, *, max_lines: int = 80) -> str:
    if not log_path.is_file():
        return ""
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-max(1, int(max_lines)) :])


def refresh_pipeline_run_process_state(doc: Dict[str, Any]) -> Dict[str, Any]:
    """If run doc has pid and status=running, update from process exit."""
    out = dict(doc)
    terminal = {"complete", "failed", "cancelled"}
    if str(out.get("status") or "") in terminal:
        return out
    pid = out.get("pid")
    try:
        pid_i = int(pid)
    except (TypeError, ValueError):
        return out
    try:
        os.kill(pid_i, 0)
        return out
    except OSError:
        pass
    rc = out.get("returncode")
    if rc is None:
        try:
            _pid, status = os.waitpid(pid_i, os.WNOHANG)
            if status:
                rc = os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else status >> 8
        except ChildProcessError:
            rc = out.get("returncode")
    out["finished_at"] = utc_now()
    out["status"] = "complete" if rc == 0 else "failed"
    if rc is not None:
        out["returncode"] = int(rc)
    return out


def run_pipeline(
    *,
    pipeline_path: Path,
    limit: int = 1,
    data_root: Optional[Path] = None,
    workflow_dir: Optional[Path] = None,
    job_dir: Optional[Path] = None,
    server: str = DEFAULT_COMFY_SERVER,
    client_id: str = "shape_factory",
    dry_run: bool = False,
    generate_only: bool = False,
    wait: bool = False,
    wait_timeout: int = 7200,
    poll: float = 10.0,
    timeout: int = 60,
    convert_timeout: int = 180,
    dev: bool = False,
    dev_tuning: Optional[str] = None,
    dev_frames: Optional[int] = None,
    dev_steps: Optional[int] = None,
    ignore_quarantine: bool = False,
    run_state_path: Optional[Path] = None,
    run_log_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Execute pipeline steps sequentially; return structured summary."""
    pipeline_path = pipeline_path.expanduser().resolve()
    pipeline = load_yaml(pipeline_path)
    steps_in = pipeline.get("steps") or []
    if not isinstance(steps_in, list) or not steps_in:
        raise RuntimeError(f"pipeline has no steps: {pipeline_path}")

    data_root = (data_root or DEFAULT_DATA_ROOT).expanduser().resolve()
    workflow_dir = (workflow_dir or DEFAULT_WORKFLOW_DIR).expanduser().resolve()
    job_dir = (job_dir or DEFAULT_JOB_DIR).expanduser().resolve()

    result: Dict[str, Any] = {
        "ok": True,
        "pipeline_id": pipeline.get("pipeline_id") or pipeline_path.stem,
        "pipeline_path": str(pipeline_path),
        "steps": [],
        "started_at": utc_now(),
    }

    def _persist_step_state(step_result: Dict[str, Any], *, status: str = "running") -> None:
        if run_state_path is None:
            return
        doc = load_pipeline_run(run_state_path) or {}
        doc.update(
            {
                "status": status,
                "pipeline_id": result["pipeline_id"],
                "pipeline_path": result["pipeline_path"],
                "updated_at": utc_now(),
                "steps": list(result["steps"]),
                "current_step": step_result.get("step_id"),
            }
        )
        _write_run_state(run_state_path, doc)

    for step in steps_in:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id") or "?")
        step_result: Dict[str, Any] = {
            "step_id": step_id,
            "ok": False,
            "family_slug": None,
            "jobs": [],
            "error": None,
        }
        result["steps"].append(step_result)
        _persist_step_state(step_result)

        try:
            gen_args = argparse.Namespace(
                shape=step["shape"],
                pools=step["pools"],
                pick=str(step.get("pick") or "zip"),
                limit=int(step.get("limit") or limit or 1),
                pick_index=int(step.get("pick_index") or 0),
                data_root=str(data_root),
                workflow_dir=str(workflow_dir),
                job_dir=str(job_dir),
                binds_override=step.get("binds_override") if isinstance(step.get("binds_override"), dict) else None,
                dev=bool(dev),
                dev_tuning=dev_tuning,
                dev_frames=dev_frames,
                dev_steps=dev_steps,
                quarantine_path=str(DEFAULT_QUARANTINE_PATH),
                ignore_quarantine=bool(ignore_quarantine),
                picks_json=None,
                job_suffix=None,
                output_prefix_root=None,
                job_key_prefix=None,
            )
            if cmd_generate(gen_args) != 0:
                step_result["error"] = "generate_failed"
                result["ok"] = False
                break

            shape_doc = load_yaml(Path(step["shape"]).expanduser().resolve())
            family = str(shape_doc.get("family_slug") or Path(str(step["shape"])).stem.replace(".shape", ""))
            step_result["family_slug"] = family

            if not generate_only:
                sub_args = argparse.Namespace(
                    family=family,
                    job=None,
                    jobs_dir=None,
                    job_dir=str(job_dir),
                    limit=gen_args.limit,
                    server=server,
                    client_id=client_id,
                    front=False,
                    force=False,
                    dry_run=dry_run,
                    data_root=str(data_root),
                    timeout=timeout,
                    convert_timeout=convert_timeout,
                    delay=0.0,
                    quarantine_path=str(DEFAULT_QUARANTINE_PATH),
                    ignore_quarantine=bool(ignore_quarantine),
                )
                if cmd_submit(sub_args) != 0 and not dry_run:
                    step_result["error"] = "submit_failed"
                    result["ok"] = False
                    break

                if wait and not dry_run:
                    st_args = argparse.Namespace(
                        family=family,
                        job=None,
                        jobs_dir=None,
                        job_dir=str(job_dir),
                        limit=gen_args.limit,
                        server=server,
                        data_root=str(data_root),
                        wait=True,
                        timeout=wait_timeout,
                        poll=poll,
                        deposit=False,
                    )
                    cmd_status(st_args)

                if not dry_run:
                    pools_path = Path(step["pools"]).expanduser().resolve()
                    sync_args = argparse.Namespace(pools=str(pools_path), shape=step.get("shape"), index=None)
                    cmd_pool_sync(sync_args)

                    dep_args = argparse.Namespace(
                        family=family,
                        job=None,
                        jobs_dir=None,
                        job_dir=str(job_dir),
                        limit=gen_args.limit,
                        data_root=str(data_root),
                        index=None,
                        pools=str(pools_path),
                        quiet=True,
                    )
                    cmd_deposit(dep_args)

            step_result["jobs"] = _summarize_jobs(
                _job_paths_for_family(job_dir, family, limit=gen_args.limit)
            )
            step_result["ok"] = True
            _persist_step_state(step_result)
        except Exception as exc:
            step_result["error"] = str(exc)
            result["ok"] = False
            _persist_step_state(step_result, status="failed")
            break

    result["finished_at"] = utc_now()
    if run_state_path is not None:
        doc = load_pipeline_run(run_state_path) or {}
        doc.update(result)
        doc["status"] = "complete" if result.get("ok") else "failed"
        doc["updated_at"] = utc_now()
        if run_log_path is not None:
            doc["log_path"] = str(run_log_path)
        _write_run_state(run_state_path, doc)
    return result


def start_background_pipeline_run(
    *,
    pipeline_path: Path,
    options: Dict[str, Any],
    data_root: Optional[Path] = None,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Spawn ``shape_factory.py pipeline run`` in the background; return run_id."""
    data_root = (data_root or resolve_shape_factory_data_root(repo_root=repo_root or Path(__file__).resolve().parents[2])).resolve()
    runs_dir = default_pipeline_runs_dir(data_root)
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:12]
    run_state = runs_dir / f"{run_id}.json"
    run_log = runs_dir / f"{run_id}.log"

    scripts_dir = Path(__file__).resolve().parent
    shape_factory_py = scripts_dir / "shape_factory.py"
    cmd = [
        sys.executable,
        str(shape_factory_py),
        "pipeline",
        "run",
        "--pipeline",
        str(pipeline_path),
        "--run-state",
        str(run_state),
        "--run-log",
        str(run_log),
        "--limit",
        str(int(options.get("limit") or 1)),
        "--data-root",
        str(data_root),
    ]
    if options.get("wait"):
        cmd.append("--wait")
        cmd.extend(["--wait-timeout", str(int(options.get("wait_timeout") or 7200))])
    if options.get("dev"):
        cmd.append("--dev")
    if options.get("dry_run"):
        cmd.append("--dry-run")
    if options.get("generate_only"):
        cmd.append("--generate-only")
    if options.get("ignore_quarantine"):
        cmd.append("--ignore-quarantine")

    log_f = open(run_log, "a", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=str(scripts_dir),
        stdout=log_f,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log_f.close()

    doc = {
        "ok": True,
        "run_id": run_id,
        "status": "running",
        "pipeline_id": options.get("pipeline_id"),
        "pipeline_path": str(pipeline_path),
        "pid": proc.pid,
        "started_at": utc_now(),
        "options": options,
        "state_path": str(run_state),
        "log_path": str(run_log),
    }
    _write_run_state(run_state, doc)
    return doc


def get_pipeline_run_payload(
    run_id: str,
    *,
    data_root: Optional[Path] = None,
    log_lines: int = 80,
) -> Dict[str, Any]:
    runs_dir = default_pipeline_runs_dir(data_root)
    run_state = runs_dir / f"{run_id}.json"
    doc = load_pipeline_run(run_state)
    if not doc:
        return {"ok": False, "error": "run_not_found", "run_id": run_id}
    doc = refresh_pipeline_run_process_state(doc)
    log_path = Path(str(doc.get("log_path") or runs_dir / f"{run_id}.log"))
    payload = {
        "ok": True,
        "run": doc,
        "log_tail": tail_pipeline_log(log_path, max_lines=log_lines),
    }
    if doc.get("status") != "running":
        _write_run_state(run_state, doc)
    return payload
