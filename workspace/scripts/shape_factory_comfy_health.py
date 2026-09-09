#!/usr/bin/env python3
"""Global Comfy submit health circuit.

Owned by pending-drain / ``submit --pending-only``, not the UI. A failed
``/queue`` probe or a transient ``/prompt`` drop opens backoff; later drain
ticks skip Comfy until ``next_probe_at``. A successful drain ``/queue`` closes
the circuit. Experiments UI only snapshots this file for display.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

SCHEMA_VERSION = "comfyui-runpod.comfy-health.v0"
HEALTH_FILENAME = "comfy_health.json"


def default_comfy_health_path(data_root: Path | str) -> Path:
    return Path(data_root).expanduser().resolve() / "shape_factory" / HEALTH_FILENAME


def _iso(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).isoformat(timespec="seconds")


def _parse_ts(raw: Any) -> Optional[float]:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return _dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "ok": True,
        "error": "",
        "consecutive_failures": 0,
        "first_failed_at": None,
        "last_failed_at": None,
        "last_ok_at": None,
        "last_probe_at": None,
        "next_probe_at": None,
        "backoff_sec": 0,
        "queue_running": None,
        "queue_pending": None,
    }


def load_comfy_health(path: Path | str | None = None, *, data_root: Path | str | None = None) -> dict[str, Any]:
    loc = Path(path) if path is not None else default_comfy_health_path(data_root or ".")
    state = _empty_state()
    if not loc.is_file():
        return state
    try:
        raw = json.loads(loc.read_text(encoding="utf-8"))
    except Exception:
        return state
    if not isinstance(raw, dict):
        return state
    state.update(raw)
    state["schema_version"] = SCHEMA_VERSION
    status = str(state.get("status") or "").strip().lower()
    if status not in {"ok", "backoff"}:
        status = "ok" if bool(state.get("ok", True)) else "backoff"
    state["status"] = status
    state["ok"] = status == "ok"
    return state


def save_comfy_health(state: dict[str, Any], path: Path | str) -> dict[str, Any]:
    loc = Path(path)
    loc.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp = loc.with_name(f"{loc.name}.tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(loc)
    return state


def _backoff_seconds(failures: int) -> int:
    try:
        from shape_factory import submit_backoff_seconds

        return int(submit_backoff_seconds(max(1, int(failures or 1))))
    except Exception:
        steps = (60, 180, 480)
        n = max(1, int(failures or 1))
        return int(steps[min(len(steps) - 1, n - 1)])


def comfy_health_retry_remaining(state: dict[str, Any], *, now: Optional[float] = None) -> float:
    if str(state.get("status") or "") != "backoff":
        return 0.0
    nxt = _parse_ts(state.get("next_probe_at"))
    if nxt is None:
        return 0.0
    clock = time.time() if now is None else float(now)
    return max(0.0, nxt - clock)


def comfy_health_in_backoff(state: dict[str, Any], *, now: Optional[float] = None) -> bool:
    return comfy_health_retry_remaining(state, now=now) > 0.0


def comfy_health_public(state: dict[str, Any], *, now: Optional[float] = None) -> dict[str, Any]:
    remaining = comfy_health_retry_remaining(state, now=now)
    status = str(state.get("status") or "ok")
    err = str(state.get("error") or "").strip()
    return {
        "ok": status == "ok",
        "status": status,
        "error": err or None,
        "consecutive_failures": int(state.get("consecutive_failures") or 0),
        "retry_in_sec": int(remaining + 0.5) if remaining else 0,
        "backoff_sec": int(state.get("backoff_sec") or 0),
        "next_probe_at": state.get("next_probe_at") or None,
        "last_ok_at": state.get("last_ok_at") or None,
        "last_failed_at": state.get("last_failed_at") or None,
        "last_probe_at": state.get("last_probe_at") or None,
        "queue_running": state.get("queue_running"),
        "queue_pending": state.get("queue_pending"),
    }


def observe_comfy_queue_result(
    data_root: Path | str,
    *,
    ok: bool,
    error: str = "",
    running: Optional[int] = None,
    pending: Optional[int] = None,
    now: Optional[float] = None,
    path: Optional[Path] = None,
) -> dict[str, Any]:
    """Record a ``/queue`` (or equivalent) outcome. Climbs backoff only when a probe is due."""
    clock = time.time() if now is None else float(now)
    health_path = Path(path) if path is not None else default_comfy_health_path(data_root)
    state = load_comfy_health(health_path)
    iso = _iso(clock)
    state["last_probe_at"] = iso
    if running is not None:
        state["queue_running"] = int(running)
    if pending is not None:
        state["queue_pending"] = int(pending)

    if ok:
        state["status"] = "ok"
        state["ok"] = True
        state["error"] = ""
        state["consecutive_failures"] = 0
        state["last_ok_at"] = iso
        state["next_probe_at"] = None
        state["backoff_sec"] = 0
        save_comfy_health(state, health_path)
        return state

    if comfy_health_in_backoff(state, now=clock):
        if error:
            state["error"] = str(error)
        save_comfy_health(state, health_path)
        return state

    n = int(state.get("consecutive_failures") or 0) + 1
    wait = _backoff_seconds(n)
    if not state.get("first_failed_at"):
        state["first_failed_at"] = iso
    state["status"] = "backoff"
    state["ok"] = False
    state["error"] = str(error or "comfy queue probe failed")
    state["consecutive_failures"] = n
    state["last_failed_at"] = iso
    state["backoff_sec"] = wait
    state["next_probe_at"] = _iso(clock + wait)
    save_comfy_health(state, health_path)
    return state


def snapshot_comfy_health(data_root: Path | str, *, now: Optional[float] = None) -> dict[str, Any]:
    return comfy_health_public(load_comfy_health(data_root=data_root), now=now)


def _cli(argv: list[str] | None = None) -> int:
    """Drain-timer helper: gate / record / show. Do not call this from the UI."""
    ap = argparse.ArgumentParser(description="Comfy submit health circuit (drain-owned)")
    ap.add_argument("action", choices=("gate", "ok", "fail", "show"))
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--running", type=int, default=None)
    ap.add_argument("--pending", type=int, default=None)
    ap.add_argument("--error", default="comfy unreachable")
    args = ap.parse_args(argv)
    root = Path(args.data_root)
    if args.action == "show":
        print(json.dumps(snapshot_comfy_health(root), ensure_ascii=False))
        return 0
    if args.action == "gate":
        pub = snapshot_comfy_health(root)
        print(json.dumps(pub, ensure_ascii=False))
        return 3 if comfy_health_in_backoff(load_comfy_health(data_root=root)) else 0
    if args.action == "ok":
        observe_comfy_queue_result(root, ok=True, running=args.running, pending=args.pending)
        return 0
    observe_comfy_queue_result(root, ok=False, error=str(args.error or "comfy unreachable"))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
