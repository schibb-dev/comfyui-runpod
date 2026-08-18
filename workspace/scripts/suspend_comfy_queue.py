#!/usr/bin/env python3
"""
Park Comfy's live queue and stop feeders so the GPU stays idle.

suspend:
  - stop pending-drain timer and watch_queue
  - unqueue factory waiting jobs back to factory pending
  - copy remaining live prompts into the queue-ledger backlog
  - pause the ledger (no restore/refill)
  - interrupt the running job and clear Comfy waiting

resume:
  - unpause the ledger (refills backlog toward pending_target)
  - restart drain + watch_queue (unless --no-feeders)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from comfy_queue_ledger import (  # noqa: E402
    _fetch_queue,
    _http_json,
    _utc_iso,
    _write_json,
    default_ledger_control_path,
)
from shape_factory import DEFAULT_DATA_ROOT, find_job_by_prompt_id, unqueue_to_pending  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DEFAULT_SERVER = os.environ.get("COMFYUI_SERVER", "http://127.0.0.1:8188").rstrip("/")
DEFAULT_OUTPUT = Path(os.environ.get("COMFYUI_BIND_OUTPUT_DIR", "/home/yuji/comfyui-runpod-data/output"))
LEDGER_CONTAINER = "comfyui0-queue-ledger"
WATCH_CONTAINER = "comfyui0-watch-queue"
DRAIN_TIMER = "shape-factory-pending-drain.timer"


def _status_dir(output_root: Path) -> Path:
    return Path(output_root).expanduser().resolve() / "experiments" / "_status"


def _control_path(output_root: Path) -> Path:
    return default_ledger_control_path(_status_dir(output_root) / "comfy_queue_ledger_state.json")


def _run(cmd: List[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def _unit_is_active(unit: str) -> Optional[bool]:
    proc = _run(["systemctl", "--user", "is-active", unit], check=False)
    text = (proc.stdout or "").strip()
    if text == "active":
        return True
    if text in {"inactive", "failed", "dead"}:
        return False
    return None


def set_drain_timer(*, active: bool) -> Dict[str, str]:
    cmd = ["systemctl", "--user", "enable" if active else "disable", "--now", DRAIN_TIMER]
    proc = _run(cmd, check=False)
    label = "started" if active else "stopped"
    if proc.returncode == 0:
        return {"drain_timer": label}
    return {"drain_timer": (proc.stderr or proc.stdout or "failed").strip() or "failed"}


def set_watch_queue(*, active: bool) -> Dict[str, str]:
    cmd = ["docker", "start" if active else "stop", WATCH_CONTAINER]
    proc = _run(cmd, check=False)
    label = "started" if active else "stopped"
    if proc.returncode == 0:
        return {"watch_queue": label}
    return {"watch_queue": (proc.stderr or proc.stdout or "failed").strip() or "failed"}


def set_hourlies_enabled(*, enabled: bool, data_root: Optional[Path] = None) -> Dict[str, Any]:
    from shape_factory_hourly import (  # type: ignore
        hourly_schedule_status,
        load_hourly_schedule,
        save_hourly_schedule,
    )

    sch = load_hourly_schedule(data_root=data_root)
    sch["enabled"] = bool(enabled)
    save_hourly_schedule(sch, data_root=data_root)
    status = hourly_schedule_status(data_root=data_root)
    return {"ok": True, "hourly": {"enabled": bool(status.get("schedule", {}).get("enabled"))}}


def _stop_feeders() -> Dict[str, str]:
    out: Dict[str, str] = {}
    out.update(set_drain_timer(active=False))
    out.update(set_watch_queue(active=False))
    return out


def _start_feeders() -> Dict[str, str]:
    out: Dict[str, str] = {}
    out.update(set_drain_timer(active=True))
    out.update(set_watch_queue(active=True))
    return out


def _http_ok(method: str, url: str, body: Optional[Dict[str, Any]] = None, timeout_s: int = 10) -> Any:
    """POST helpers that treat an empty Comfy body as success."""
    try:
        return _http_json(method, url, body, timeout_s=timeout_s)
    except json.JSONDecodeError:
        return {"ok": True, "empty_body": True}


def _queue_counts(server: str) -> Tuple[int, int]:
    q = _fetch_queue(server)
    if q is None:
        raise RuntimeError(f"Comfy unreachable at {server}/queue")
    running, pending, _raw = q
    return len(running), len(pending)


def _snapshot_live(server: str) -> Dict[str, Any]:
    q = _fetch_queue(server)
    if q is None:
        raise RuntimeError(f"Comfy unreachable at {server}/queue")
    running, pending, _raw = q
    def _row(item: Any, phase: str) -> Dict[str, Any]:
        extra = item.extra_data if isinstance(item.extra_data, dict) else {}
        return {
            "phase": phase,
            "prompt_id": item.prompt_id,
            "client_id": extra.get("client_id"),
            "has_prompt": isinstance(item.prompt, dict),
        }
    return {
        "ok": True,
        "at": _utc_iso(),
        "running": [_row(x, "running") for x in running],
        "pending": [_row(x, "pending") for x in pending],
        "running_count": len(running),
        "pending_count": len(pending),
    }


def _write_control(path: Path, patch: Dict[str, Any]) -> Dict[str, Any]:
    cur: Dict[str, Any] = {}
    if path.is_file():
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                cur = obj
        except Exception:
            cur = {}
    cur.update(patch)
    _write_json(path, cur)
    return cur


def collect_ops_status(
    *,
    server: str,
    output_root: Path,
    data_root: Optional[Path] = None,
) -> Dict[str, Any]:
    server = str(server).rstrip("/")
    try:
        run_n, pend_n = _queue_counts(server)
        comfy: Dict[str, Any] = {"ok": True, "running": run_n, "pending": pend_n}
    except Exception as exc:
        comfy = {"ok": False, "error": str(exc), "running": None, "pending": None}

    control_path = _control_path(output_root)
    control: Dict[str, Any] = {}
    if control_path.is_file():
        try:
            obj = json.loads(control_path.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                control = obj
        except Exception:
            control = {}

    drain_active = _unit_is_active(DRAIN_TIMER)
    drain_en = _run(["systemctl", "--user", "is-enabled", DRAIN_TIMER], check=False)
    drain_enabled = (drain_en.stdout or "").strip() == "enabled"
    watch = _run(["docker", "inspect", "-f", "{{.State.Status}}", WATCH_CONTAINER], check=False)
    watch_status = (watch.stdout or "").strip() or "unknown"
    docker_ok = watch.returncode == 0
    systemd_ok = drain_active is not None

    hourly_enabled: Optional[bool] = None
    try:
        from shape_factory_hourly import load_hourly_schedule  # type: ignore

        sch = load_hourly_schedule(data_root=data_root)
        hourly_enabled = bool(sch.get("enabled"))
    except Exception:
        hourly_enabled = None

    last_park = control.get("last_park") if isinstance(control.get("last_park"), dict) else None
    return {
        "ok": True,
        "comfy": comfy,
        "hourly": {"enabled": hourly_enabled},
        "drain": {
            "active": drain_active,
            "enabled": drain_enabled,
            "label": "active" if drain_active else ("inactive" if drain_active is False else "unknown"),
        },
        "watch_queue": {
            "running": watch_status == "running",
            "status": watch_status,
        },
        "ledger": {
            "paused": bool(control.get("paused")) if "paused" in control else None,
            "last_park_at": control.get("last_park_at"),
            "last_park": last_park,
        },
        "docker_ok": docker_ok,
        "systemd_ok": systemd_ok,
        "control_path": str(control_path),
    }


def do_suspend(
    *,
    server: str,
    data_root: Path,
    output_root: Path,
    park_timeout: float = 180.0,
) -> Dict[str, Any]:
    server = str(server).rstrip("/")
    data_root = Path(data_root).expanduser().resolve()
    output_root = Path(output_root).expanduser().resolve()
    control_path = _control_path(output_root)
    snap_path = _status_dir(output_root) / "comfy_queue_park.json"

    feeders = _stop_feeders()
    snapshot = _snapshot_live(server)
    _write_json(snap_path, snapshot)

    _restart_ledger()
    _wait_ledger_healthy()
    factory = _unqueue_factory_waiting(server, data_root)
    _write_control(control_path, {"park_requested_at": time.time(), "paused": False})
    parked = _wait_parked(control_path, timeout_s=float(park_timeout))
    emptied = _empty_comfy(server)

    return {
        "ok": emptied.get("running") == 0 and emptied.get("pending") == 0,
        "action": "suspend",
        "paused": True,
        "feeders": feeders,
        "snapshot_path": str(snap_path),
        "before": {"running": snapshot["running_count"], "pending": snapshot["pending_count"]},
        "factory_unqueued": factory,
        "ledger_park": parked.get("last_park") or parked,
        "comfy": emptied,
        "note": "Parked live jobs into the ledger backlog and emptied Comfy. Resume from the Ledger tab when you want them back.",
    }


def do_resume(*, output_root: Path, feeders: bool = True) -> Dict[str, Any]:
    output_root = Path(output_root).expanduser().resolve()
    control_path = _control_path(output_root)
    control = _write_control(control_path, {"paused": False, "park_requested_at": 0.0})
    feeder_out = _start_feeders() if feeders else {"drain_timer": "left", "watch_queue": "left"}
    return {
        "ok": True,
        "action": "resume-ops",
        "paused": False,
        "ledger_control": control,
        "feeders": feeder_out,
        "note": "Ledger will refill parked backlog toward pending_target (default 2).",
    }


def _restart_ledger() -> None:
    proc = _run(["docker", "restart", LEDGER_CONTAINER], check=False)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "docker restart failed").strip())


def _wait_ledger_healthy(*, timeout_s: float = 90.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        proc = _run(
            ["docker", "inspect", "-f", "{{.State.Health.Status}}", LEDGER_CONTAINER],
            check=False,
        )
        status = (proc.stdout or "").strip()
        if status == "healthy":
            return
        if status in {"", "unhealthy"} and proc.returncode != 0:
            running = _run(["docker", "inspect", "-f", "{{.State.Running}}", LEDGER_CONTAINER], check=False)
            if (running.stdout or "").strip() == "true":
                time.sleep(1.0)
                continue
        time.sleep(1.0)
    raise RuntimeError(f"{LEDGER_CONTAINER} did not become healthy")


def _unqueue_factory_waiting(server: str, data_root: Path) -> List[Dict[str, Any]]:
    q = _fetch_queue(server)
    if q is None:
        raise RuntimeError(f"Comfy unreachable at {server}/queue")
    _running, pending, _raw = q
    jobs_root = data_root / "shape_factory" / "jobs"
    results: List[Dict[str, Any]] = []
    for item in pending:
        path, job = find_job_by_prompt_id(jobs_root, item.prompt_id)
        if job is None or path is None:
            continue
        results.append(
            unqueue_to_pending(
                prompt_id=item.prompt_id,
                server=server,
                data_root=data_root,
                job_path=path,
            )
        )
    return results


def _wait_parked(control_path: Path, *, timeout_s: float = 180.0) -> Dict[str, Any]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if control_path.is_file():
            try:
                obj = json.loads(control_path.read_text(encoding="utf-8"))
            except Exception:
                obj = None
            if isinstance(obj, dict) and obj.get("last_park_at") and obj.get("paused") is True:
                if float(obj.get("park_requested_at") or 0.0) <= 0:
                    return obj
        time.sleep(0.5)
    raise RuntimeError(f"ledger did not park within {timeout_s:.0f}s (control={control_path})")


def _empty_comfy(server: str) -> Dict[str, Any]:
    interrupt: Any = None
    clear: Any = None
    try:
        interrupt = _http_ok("POST", f"{server}/interrupt", None, timeout_s=10)
    except Exception as exc:
        interrupt = {"error": str(exc)}
    try:
        clear = _http_ok("POST", f"{server}/queue", {"clear": True}, timeout_s=15)
    except Exception as exc:
        clear = {"error": str(exc)}
    running, pending, _raw = _fetch_queue(server) or ([], [], {})
    leftover = [x.prompt_id for x in pending]
    deleted: List[str] = []
    for pid in leftover:
        try:
            _http_json("POST", f"{server}/queue", {"delete": [pid]}, timeout_s=10)
            deleted.append(pid)
        except Exception:
            pass
    time.sleep(0.8)
    run_n, pend_n = _queue_counts(server)
    return {
        "interrupt": interrupt,
        "clear": clear,
        "deleted_leftover": deleted,
        "running": run_n,
        "pending": pend_n,
    }


def cmd_status(args: argparse.Namespace) -> int:
    payload = collect_ops_status(
        server=str(args.server),
        output_root=Path(args.output_root),
        data_root=None,
    )
    print(json.dumps(payload, indent=2, default=str))
    return 0 if payload.get("ok") else 1


def cmd_suspend(args: argparse.Namespace) -> int:
    payload = do_suspend(
        server=str(args.server),
        data_root=Path(args.data_root),
        output_root=Path(args.output_root),
        park_timeout=float(args.park_timeout),
    )
    print(json.dumps(payload, indent=2, default=str))
    return 0 if payload.get("ok") else 1


def cmd_resume(args: argparse.Namespace) -> int:
    payload = do_resume(output_root=Path(args.output_root), feeders=not args.no_feeders)
    print(json.dumps(payload, indent=2, default=str))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Park or restore the live Comfy queue")
    ap.add_argument("--server", default=DEFAULT_SERVER)
    ap.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    ap.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    ap.add_argument("--park-timeout", type=float, default=180.0)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="Show Comfy / feeder / ledger-pause status")
    sub.add_parser("suspend", help="Park live jobs, pause ledger, empty Comfy")
    res = sub.add_parser("resume", help="Unpause ledger and restart feeders")
    res.add_argument("--no-feeders", action="store_true", help="Unpause ledger only (leave drain/watch stopped)")
    args = ap.parse_args()
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "suspend":
        return cmd_suspend(args)
    if args.cmd == "resume":
        return cmd_resume(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
