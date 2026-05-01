#!/usr/bin/env python3
"""
WebSocket event tap for ComfyUI.

Primary purpose:
- Record *true execution timings* per prompt_id using ComfyUI's /ws message stream:
  - execution_start  (prompt about to run)
  - executing        (node-by-node; node=None indicates completion)
  - execution_success / execution_error / execution_interrupted

We correlate prompt_id -> (exp_id, run_id, run_dir) by scanning experiment run directories
for submit.json / metrics.json. We then merge timing fields into <run_dir>/metrics.json.

This is intentionally best-effort:
- If the WS stream disconnects, we reconnect with backoff.
- If we can't map a prompt_id to a run, we ignore it (it may not belong to our experiments).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


def _utc_iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_json_dict(path: Path) -> Dict[str, Any]:
    try:
        obj = _read_json(path)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _write_json_atomic(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(raw, encoding="utf-8")
    tmp.replace(path)


class _Lock:
    def __init__(self, lock_path: Path, *, timeout_s: float = 5.0) -> None:
        self.lock_path = lock_path
        self.timeout_s = float(timeout_s)
        self._held = False

    def __enter__(self) -> "_Lock":
        deadline = time.time() + max(0.0, self.timeout_s)
        while True:
            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(fd, f"pid={os.getpid()} ts={time.time()}\n".encode("utf-8", "replace"))
                finally:
                    os.close(fd)
                self._held = True
                return self
            except FileExistsError:
                if time.time() >= deadline:
                    # Best-effort: proceed without a lock if we can't acquire quickly.
                    return self
                time.sleep(0.05 + random.random() * 0.15)

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self._held:
            return
        try:
            self.lock_path.unlink(missing_ok=True)
        except Exception:
            return


def _merge_metrics(metrics_path: Path, patch: Dict[str, Any]) -> Dict[str, Any]:
    lock_path = metrics_path.with_suffix(metrics_path.suffix + ".lock")
    with _Lock(lock_path, timeout_s=5.0):
        base = _read_json_dict(metrics_path) if metrics_path.exists() else {}
        merged = {**base, **patch}
        _write_json_atomic(metrics_path, merged)
        return merged


def _prompt_id_from_submit(submit_path: Path) -> Optional[str]:
    try:
        obj = _read_json(submit_path)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    pid = obj.get("prompt_id")
    return pid.strip() if isinstance(pid, str) and pid.strip() else None


def _looks_like_epoch_seconds(ts: float) -> bool:
    # 2001-09-09 .. 2286-11-20 (fits typical unix epoch float range)
    return 1_000_000_000.0 <= ts <= 10_000_000_000.0


def _min_or_keep(prev: Any, new_ts: float) -> float:
    if isinstance(prev, (int, float)):
        try:
            return float(min(float(prev), float(new_ts)))
        except Exception:
            return float(new_ts)
    return float(new_ts)


def _max_or_keep(prev: Any, new_ts: float) -> float:
    if isinstance(prev, (int, float)):
        try:
            return float(max(float(prev), float(new_ts)))
        except Exception:
            return float(new_ts)
    return float(new_ts)


def apply_ws_event_to_metrics(
    metrics: Dict[str, Any],
    *,
    msg_type: str,
    data: Dict[str, Any],
    recv_ts: float,
) -> Dict[str, Any]:
    """
    Pure-ish update function: returns a patch dict to merge into metrics.json.

    We never delete or regress existing fields; starts prefer earliest seen timestamp,
    ends prefer earliest end (but if already set, we keep it).
    """
    patch: Dict[str, Any] = {"ws_timing_schema": 1}

    prompt_id = data.get("prompt_id")
    if not (isinstance(prompt_id, str) and prompt_id.strip()):
        return {}
    patch["prompt_id"] = prompt_id.strip()

    # execution_start: prompt is about to run
    if msg_type == "execution_start":
        prev = metrics.get("exec_started_ts")
        patch["exec_started_ts"] = _min_or_keep(prev, float(recv_ts))
        patch["exec_started_at"] = _utc_iso(float(patch["exec_started_ts"]))
        patch["exec_started_ts_source"] = "ws.execution_start.recv_ts"
        return patch

    # executing: node-by-node; node=None indicates completion
    if msg_type == "executing":
        node = data.get("node")
        # First active node execution is closest to "actively generating".
        if node is not None:
            prev = metrics.get("active_started_ts")
            patch["active_started_ts"] = _min_or_keep(prev, float(recv_ts))
            patch["active_started_at"] = _utc_iso(float(patch["active_started_ts"]))
            patch["active_started_ts_source"] = "ws.executing.first_node.recv_ts"
        else:
            # completion marker (fallback end reason if we don't get execution_success)
            if not isinstance(metrics.get("exec_ended_ts"), (int, float)):
                patch["exec_ended_ts"] = float(recv_ts)
                patch["exec_ended_at"] = _utc_iso(float(recv_ts))
                patch["exec_ended_ts_source"] = "ws.executing.node_none.recv_ts"
                patch["exec_end_reason"] = metrics.get("exec_end_reason") or "success"
        return patch

    if msg_type == "execution_success":
        # docs mention a timestamp field; use it if it looks like epoch seconds, else use recv_ts
        end_ts = float(recv_ts)
        ts = data.get("timestamp")
        if isinstance(ts, (int, float)) and _looks_like_epoch_seconds(float(ts)):
            end_ts = float(ts)
            src = "ws.execution_success.timestamp"
        else:
            src = "ws.execution_success.recv_ts"
        if not isinstance(metrics.get("exec_ended_ts"), (int, float)):
            patch["exec_ended_ts"] = float(end_ts)
            patch["exec_ended_at"] = _utc_iso(float(end_ts))
            patch["exec_ended_ts_source"] = src
        patch["exec_end_reason"] = "success"
        return patch

    if msg_type in ("execution_error", "execution_interrupted"):
        if not isinstance(metrics.get("exec_ended_ts"), (int, float)):
            patch["exec_ended_ts"] = float(recv_ts)
            patch["exec_ended_at"] = _utc_iso(float(recv_ts))
            patch["exec_ended_ts_source"] = f"ws.{msg_type}.recv_ts"
        patch["exec_end_reason"] = "error" if msg_type == "execution_error" else "interrupted"
        return patch

    return {}


def _derive_durations(merged: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add derived runtime fields when possible. Never overwrites existing derived values.
    """
    out: Dict[str, Any] = {}
    a0 = merged.get("active_started_ts")
    e0 = merged.get("exec_started_ts")
    e1 = merged.get("exec_ended_ts")
    if isinstance(a0, (int, float)) and isinstance(e1, (int, float)):
        if not isinstance(merged.get("active_runtime_sec"), (int, float)):
            out["active_runtime_sec"] = float(max(0.0, float(e1) - float(a0)))
            out["active_runtime_sec_source"] = merged.get("exec_ended_ts_source") or "ws"
    if isinstance(e0, (int, float)) and isinstance(e1, (int, float)):
        if not isinstance(merged.get("wall_runtime_sec"), (int, float)):
            out["wall_runtime_sec"] = float(max(0.0, float(e1) - float(e0)))
            out["wall_runtime_sec_source"] = merged.get("exec_ended_ts_source") or "ws"
    return out


@dataclass(frozen=True)
class RunInfo:
    run_dir: Path
    exp_id: str
    run_id: str


def _scan_prompt_id_map(experiments_root: Path) -> Dict[str, RunInfo]:
    """
    Build a best-effort map: prompt_id -> RunInfo by scanning submit.json files.
    """
    out: Dict[str, RunInfo] = {}
    if not experiments_root.exists():
        return out
    try:
        for exp_dir in experiments_root.iterdir():
            if not exp_dir.is_dir():
                continue
            runs_dir = exp_dir / "runs"
            if not runs_dir.is_dir():
                continue
            exp_id = exp_dir.name
            for run_dir in runs_dir.iterdir():
                if not run_dir.is_dir():
                    continue
                sp = run_dir / "submit.json"
                if not sp.is_file():
                    continue
                pid = _prompt_id_from_submit(sp)
                if not pid:
                    continue
                out[pid] = RunInfo(run_dir=run_dir, exp_id=exp_id, run_id=run_dir.name)
    except Exception:
        return out
    return out


def _ws_url_from_server(server: str, *, client_id: str) -> str:
    s = server.rstrip("/")
    if s.startswith("https://"):
        ws_base = "wss://" + s[len("https://") :]
    elif s.startswith("http://"):
        ws_base = "ws://" + s[len("http://") :]
    elif s.startswith("ws://") or s.startswith("wss://"):
        ws_base = s
    else:
        ws_base = "ws://" + s
    if ws_base.endswith("/ws"):
        return f"{ws_base}?clientId={client_id}"
    return f"{ws_base}/ws?clientId={client_id}"


def _legacy_client_id_for_exp(exp_id: str) -> str:
    # Back-compat: earlier versions used per-experiment clientIds.
    return f"comfy_tool_{exp_id}"


def _split_client_ids(values: List[str]) -> List[str]:
    out: List[str] = []
    for v in values:
        for part in str(v).split(","):
            s = part.strip()
            if s:
                out.append(s)
    # stable unique, preserve order
    seen: Set[str] = set()
    uniq: List[str] = []
    for x in out:
        if x in seen:
            continue
        seen.add(x)
        uniq.append(x)
    return uniq


def _http_json(url: str, *, timeout_s: int = 10) -> Any:
    # Keep deps minimal: urllib is in stdlib.
    import urllib.request

    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def _queue_prompt_ids(server: str) -> Set[str]:
    """
    Return prompt_ids currently pending or running (best-effort).
    """
    server = server.rstrip("/")
    try:
        obj = _http_json(f"{server}/queue", timeout_s=10)
    except Exception:
        return set()
    out: Set[str] = set()
    if not isinstance(obj, dict):
        return out
    for key in ("queue_pending", "queue_running"):
        items = obj.get(key)
        if not isinstance(items, list):
            continue
        for it in items:
            if isinstance(it, list) and len(it) >= 2 and isinstance(it[1], str) and it[1].strip():
                out.add(it[1].strip())
    return out


async def _run_ws_tap(
    *,
    server: str,
    experiments_root: Path,
    client_id: str,
    scan_every_s: float,
    debug: bool,
) -> int:
    # Import lazily so unit tests can import this module without requiring aiohttp at import time.
    import aiohttp  # type: ignore

    pid_map: Dict[str, RunInfo] = {}
    last_scan = 0.0

    ws_url = _ws_url_from_server(server, client_id=client_id)
    print(f"[ws_event_tap] ws_url={ws_url}")
    print(f"[ws_event_tap] experiments_root={experiments_root}")

    backoff = 1.0
    while True:
        # periodic rescan
        now = time.time()
        if (now - last_scan) >= max(1.0, scan_every_s):
            pid_map = _scan_prompt_id_map(experiments_root)
            last_scan = now

        try:
            timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=None)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.ws_connect(ws_url, heartbeat=20) as ws:
                    print("[ws_event_tap] connected")
                    backoff = 1.0
                    async for msg in ws:
                        recv_ts = time.time()
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                obj = json.loads(msg.data)
                            except Exception:
                                continue
                            if not isinstance(obj, dict):
                                continue
                            msg_type = obj.get("type")
                            data = obj.get("data")
                            if not isinstance(msg_type, str) or not isinstance(data, dict):
                                continue
                            if debug and msg_type in (
                                "execution_start",
                                "executing",
                                "execution_success",
                                "execution_error",
                                "execution_interrupted",
                            ):
                                pid_dbg = data.get("prompt_id")
                                print(f"[ws_event_tap][debug] {msg_type} prompt_id={pid_dbg}")
                            pid = data.get("prompt_id")
                            if not isinstance(pid, str) or not pid.strip():
                                continue

                            info = pid_map.get(pid.strip())
                            if info is None:
                                # refresh map and try once more (helps catch newly submitted runs quickly)
                                pid_map = _scan_prompt_id_map(experiments_root)
                                last_scan = time.time()
                                info = pid_map.get(pid.strip())
                            if info is None:
                                continue

                            metrics_path = info.run_dir / "metrics.json"
                            base = _read_json_dict(metrics_path) if metrics_path.exists() else {}
                            patch = apply_ws_event_to_metrics(base, msg_type=msg_type, data=data, recv_ts=float(recv_ts))
                            if not patch:
                                continue
                            merged = _merge_metrics(metrics_path, patch)
                            deriv = _derive_durations(merged)
                            if deriv:
                                _merge_metrics(metrics_path, deriv)
                        else:
                            # We intentionally ignore BINARY frames for now (pinned follow-up: previews/latents).
                            continue
        except Exception as e:
            print(f"[ws_event_tap] disconnected: {e}")
            sleep_s = float(min(30.0, backoff)) + random.random() * 0.25
            await _async_sleep(sleep_s)
            backoff = min(30.0, backoff * 1.7)


async def _run_ws_tap_multi(
    *,
    server: str,
    experiments_root: Path,
    client_ids: List[str],
    auto_legacy_from_queue: bool,
    max_client_ids: int,
    scan_every_s: float,
    debug: bool,
) -> int:
    import asyncio

    explicit = _split_client_ids(client_ids)
    if not explicit:
        explicit = ["comfy_tool"]

    # Start with explicit clientIds.
    active: List[str] = list(explicit)

    # Optionally add legacy per-exp clientIds for prompt_ids currently in /queue.
    if auto_legacy_from_queue:
        qids = _queue_prompt_ids(server)
        if qids:
            pid_map = _scan_prompt_id_map(experiments_root)
            legacy: List[str] = []
            for pid in sorted(qids):
                info = pid_map.get(pid)
                if not info:
                    continue
                legacy.append(_legacy_client_id_for_exp(info.exp_id))
            for cid in _split_client_ids(legacy):
                if cid not in active:
                    active.append(cid)

    # Cap to avoid opening dozens of sockets if many experiments are in flight.
    if max_client_ids < 1:
        max_client_ids = 1
    if len(active) > max_client_ids:
        active = active[: int(max_client_ids)]

    print(f"[ws_event_tap] client_ids={active} (explicit={explicit}, auto_legacy_from_queue={auto_legacy_from_queue})")

    tasks = [
        asyncio.create_task(
            _run_ws_tap(
                server=server,
                experiments_root=experiments_root,
                client_id=cid,
                scan_every_s=scan_every_s,
                debug=debug,
            )
        )
        for cid in active
    ]
    # Run forever: first task exception should bubble so container restarts.
    await asyncio.gather(*tasks)
    return 0


async def _async_sleep(sec: float) -> None:
    import asyncio

    await asyncio.sleep(float(max(0.0, sec)))


def main() -> int:
    ap = argparse.ArgumentParser(description="Tap ComfyUI /ws events and write per-run timing into metrics.json.")
    ap.add_argument("--server", default="http://127.0.0.1:8188", help="ComfyUI server base URL (http://...:8188)")
    ap.add_argument(
        "--experiments-root",
        default="",
        help="Experiments root folder (default: /workspace/output/output/experiments relative to repo/workspace layout)",
    )
    ap.add_argument(
        "--client-id",
        action="append",
        default=["comfy_tool"],
        help="clientId query parameter for /ws (repeatable or comma-separated). Default: comfy_tool",
    )
    ap.add_argument(
        "--auto-legacy-from-queue",
        action="store_true",
        help="Also listen on legacy per-experiment clientIds (comfy_tool_<exp_id>) for prompt_ids currently in /queue.",
    )
    ap.add_argument(
        "--max-client-ids",
        type=int,
        default=8,
        help="Safety cap on number of concurrent /ws connections (default: 8).",
    )
    ap.add_argument("--scan-every", type=float, default=10.0, help="Rescan submit.json mapping interval seconds")
    ap.add_argument("--debug", action="store_true", help="Print execution events (for diagnosing /ws delivery)")
    args = ap.parse_args()

    # Default experiments root: inside the container we mount ./workspace at /workspace
    exp_root = Path(args.experiments_root) if args.experiments_root else Path("/workspace/output/output/experiments")

    import asyncio

    asyncio.run(
        _run_ws_tap_multi(
            server=str(args.server),
            experiments_root=exp_root,
            client_ids=list(args.client_id) if isinstance(args.client_id, list) else [str(args.client_id)],
            auto_legacy_from_queue=bool(args.auto_legacy_from_queue),
            max_client_ids=int(args.max_client_ids),
            scan_every_s=float(args.scan_every),
            debug=bool(args.debug),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

