#!/usr/bin/env python3
"""
Best-effort ComfyUI queue ledger + startup restorer.

Design goals:
- Passive: read /queue and keep a shadow ledger on disk.
- Non-ACID: best effort over exact correctness.
- Safe-ish: avoid loops with cooldown/attempt caps.
- Gentle: do not force queue shape every tick.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


def _utc_iso(ts: Optional[float] = None) -> str:
    t = float(time.time() if ts is None else ts)
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _is_probably_repo_root(p: Path) -> bool:
    return (p / "workspace").is_dir() and (p / "workspace" / "scripts").is_dir()


def _resolve_repo_root() -> Path:
    here = Path(__file__).resolve()
    repo = here.parents[2]
    if _is_probably_repo_root(repo):
        return repo
    for parent in here.parents:
        if _is_probably_repo_root(parent):
            return parent
    raise RuntimeError(f"Could not locate repo root from {here}")


def _http_json(method: str, url: str, body: Optional[Dict[str, Any]] = None, timeout_s: int = 10) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper().strip())
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def _now_ts() -> float:
    return float(time.time())


@dataclass
class QueueItem:
    prompt_id: str
    prompt: Optional[Dict[str, Any]]
    extra_data: Optional[Dict[str, Any]]
    outputs_to_execute: Optional[List[Any]]


def _parse_queue_items(raw_items: Any) -> List[QueueItem]:
    out: List[QueueItem] = []
    if not isinstance(raw_items, list):
        return out
    for it in raw_items:
        if not isinstance(it, list) or len(it) < 2:
            continue
        pid = it[1]
        if not isinstance(pid, str) or not pid.strip():
            continue
        prompt_obj = it[2] if len(it) >= 3 and isinstance(it[2], dict) else None
        extra_data = it[3] if len(it) >= 4 and isinstance(it[3], dict) else None
        outputs = it[4] if len(it) >= 5 and isinstance(it[4], list) else None
        out.append(
            QueueItem(
                prompt_id=pid.strip(),
                prompt=prompt_obj,
                extra_data=extra_data,
                outputs_to_execute=outputs,
            )
        )
    return out


def _fetch_queue(server: str, timeout_s: int = 10) -> Optional[Tuple[List[QueueItem], List[QueueItem], Dict[str, Any]]]:
    try:
        obj = _http_json("GET", f"{server.rstrip('/')}/queue", timeout_s=timeout_s)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    running = _parse_queue_items(obj.get("queue_running"))
    pending = _parse_queue_items(obj.get("queue_pending"))
    return running, pending, obj


def _default_state() -> Dict[str, Any]:
    return {
        "version": 1,
        "updated_at": _utc_iso(),
        "mode": "normal",
        "mode_since_ts": _now_ts(),
        "last_snapshot": {"running": [], "pending": []},
        "known": {},
        "backlog": [],
        "restore_attempts": {},
        "restore_last_ts": {},
        "expected_add_until_ts": {},
        "recent_unexpected_ts": [],
        "restore_failures_ts": [],
        "breaker": {"open": False, "opened_ts": 0.0, "reason": "", "open_until_ts": 0.0},
        "paused": False,
        "drain_once_requested_at": 0.0,
        "stats": {
            "restored_startup": 0,
            "restored_refill": 0,
            "spillover_removed": 0,
            "suppressed_breaker": 0,
            "suppressed_cap": 0,
            "suppressed_cooldown": 0,
        },
    }


def _read_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return _default_state()
    try:
        obj = _read_json(path)
    except Exception:
        return _default_state()
    if not isinstance(obj, dict):
        return _default_state()
    base = _default_state()
    for k in base.keys():
        if k in obj:
            base[k] = obj[k]
    if not isinstance(base.get("known"), dict):
        base["known"] = {}
    if not isinstance(base.get("backlog"), list):
        base["backlog"] = []
    return base


def _submit_prompt(
    server: str,
    *,
    prompt: Dict[str, Any],
    client_id: str,
    extra_data: Optional[Dict[str, Any]] = None,
    outputs_to_execute: Optional[List[Any]] = None,
) -> Tuple[bool, Dict[str, Any]]:
    payload: Dict[str, Any] = {"prompt": prompt, "client_id": client_id}
    if isinstance(extra_data, dict):
        payload["extra_data"] = extra_data
    if isinstance(outputs_to_execute, list):
        payload["outputs_to_execute"] = outputs_to_execute
    try:
        res = _http_json("POST", f"{server.rstrip('/')}/prompt", payload, timeout_s=30)
    except Exception as e:
        return False, {"error": "submit_failed", "detail": str(e)}
    if not isinstance(res, dict):
        return False, {"error": "bad_submit_response", "response_type": str(type(res))}
    return True, res


def _delete_pending_prompt(server: str, prompt_id: str, timeout_s: int = 10) -> Tuple[bool, Dict[str, Any]]:
    try:
        res = _http_json("POST", f"{server.rstrip('/')}/queue", {"delete": [prompt_id]}, timeout_s=timeout_s)
    except Exception as e:
        return False, {"error": "delete_failed", "detail": str(e)}
    if not isinstance(res, dict):
        return False, {"error": "bad_delete_response", "response_type": str(type(res))}
    return True, res


def _push_backlog_item(state: Dict[str, Any], prompt_id: str) -> bool:
    known = state.get("known", {})
    if not isinstance(known, dict):
        return False
    rec = known.get(prompt_id)
    if not isinstance(rec, dict) or not isinstance(rec.get("prompt"), dict):
        return False
    backlog = state.setdefault("backlog", [])
    if not isinstance(backlog, list):
        backlog = []
        state["backlog"] = backlog
    for x in backlog:
        if isinstance(x, dict) and x.get("prompt_id") == prompt_id:
            return False
    backlog.append(
        {
            "prompt_id": prompt_id,
            "prompt": rec.get("prompt"),
            "extra_data": rec.get("extra_data") if isinstance(rec.get("extra_data"), dict) else None,
            "outputs_to_execute": rec.get("outputs_to_execute") if isinstance(rec.get("outputs_to_execute"), list) else None,
            "enqueued_backlog_ts": _now_ts(),
            "source": "spillover",
        }
    )
    return True


def _breaker_open(state: Dict[str, Any], *, reason: str, open_for_s: float) -> None:
    now = _now_ts()
    br = state.setdefault("breaker", {})
    if not isinstance(br, dict):
        br = {}
        state["breaker"] = br
    br["open"] = True
    br["opened_ts"] = now
    br["open_until_ts"] = now + max(1.0, float(open_for_s))
    br["reason"] = reason


def _breaker_maybe_close(state: Dict[str, Any]) -> bool:
    br = state.get("breaker")
    if not isinstance(br, dict):
        return False
    if not bool(br.get("open")):
        return False
    now = _now_ts()
    until = br.get("open_until_ts")
    if isinstance(until, (int, float)) and now >= float(until):
        br["open"] = False
        br["reason"] = ""
        return True
    return False


def _prune_state(state: Dict[str, Any], *, keep_known: int = 2000, keep_events_window_s: int = 300) -> None:
    now = _now_ts()
    ex = state.get("expected_add_until_ts")
    if isinstance(ex, dict):
        state["expected_add_until_ts"] = {
            str(k): float(v) for k, v in ex.items() if isinstance(v, (int, float)) and float(v) > now
        }
    ru = state.get("recent_unexpected_ts")
    if isinstance(ru, list):
        state["recent_unexpected_ts"] = [float(x) for x in ru if isinstance(x, (int, float)) and float(x) >= now - keep_events_window_s]
    rf = state.get("restore_failures_ts")
    if isinstance(rf, list):
        state["restore_failures_ts"] = [float(x) for x in rf if isinstance(x, (int, float)) and float(x) >= now - 3600]
    known = state.get("known")
    if isinstance(known, dict) and len(known) > keep_known:
        scored: List[Tuple[float, str]] = []
        for pid, item in known.items():
            ts = 0.0
            if isinstance(item, dict):
                v = item.get("last_seen_ts")
                if isinstance(v, (int, float)):
                    ts = float(v)
            scored.append((ts, str(pid)))
        scored.sort(reverse=True)
        keep = {pid for _ts, pid in scored[:keep_known]}
        state["known"] = {pid: known[pid] for pid in keep if pid in known}


def main() -> int:
    ap = argparse.ArgumentParser(description="Best-effort Comfy queue ledger + startup restore")
    ap.add_argument("--server", default="http://127.0.0.1:8188", help="ComfyUI server URL")
    ap.add_argument("--pending-target", type=int, default=2, help="Desired pending depth (minimum floor is 2)")
    ap.add_argument("--spillover-enabled", action="store_true", help="Enable gentle tail spillover to backlog")
    ap.add_argument("--poll-interval-normal", type=float, default=0.5, help="Sleep seconds in normal mode")
    ap.add_argument("--poll-interval-churn", type=float, default=3.0, help="Sleep seconds in churn mode")
    ap.add_argument("--churn-window-s", type=float, default=30.0, help="Window for unexpected queue deltas")
    ap.add_argument("--quiet-window-s", type=float, default=45.0, help="Quiet period before returning to normal mode")
    ap.add_argument("--churn-threshold", type=int, default=2, help="Unexpected events in churn window to enter churn mode")
    ap.add_argument("--expected-add-ttl-s", type=float, default=20.0, help="Expected add grace window after restore")
    ap.add_argument("--max-restore-attempts", type=int, default=2, help="Per prompt restore attempt cap")
    ap.add_argument("--restore-cooldown-s", type=float, default=120.0, help="Min seconds between restore attempts per prompt")
    ap.add_argument("--breaker-failure-threshold", type=int, default=3, help="Restore failures in window to open breaker")
    ap.add_argument("--breaker-window-s", type=float, default=120.0, help="Window for breaker failure threshold")
    ap.add_argument("--breaker-open-s", type=float, default=180.0, help="Breaker open duration")
    ap.set_defaults(protect_on_deck=True, protect_in_hole=True)
    ap.add_argument("--protect-on-deck", dest="protect_on_deck", action="store_true", help="Never spill pending[0]")
    ap.add_argument("--no-protect-on-deck", dest="protect_on_deck", action="store_false", help="Allow spilling pending[0]")
    ap.add_argument("--protect-in-hole", dest="protect_in_hole", action="store_true", help="Never spill pending[1]")
    ap.add_argument("--no-protect-in-hole", dest="protect_in_hole", action="store_false", help="Allow spilling pending[1]")
    ap.add_argument("--max-actions-normal", type=int, default=2, help="Max queue actions per cycle in normal mode")
    ap.add_argument("--max-actions-churn", type=int, default=1, help="Max queue actions per cycle in churn mode")
    ap.add_argument("--client-id", default="comfy-queue-ledger", help="client_id for restored prompt submissions")
    ap.add_argument("--state-path", default="", help="Path to ledger state JSON")
    ap.add_argument("--events-path", default="", help="Path to ledger events JSONL")
    ap.add_argument("--once", action="store_true", help="Run one cycle and exit")
    ap.add_argument("--no-startup-restore", action="store_true", help="Disable startup restore")
    args = ap.parse_args()

    pending_target = max(2, int(args.pending_target))
    repo = _resolve_repo_root()
    default_state = repo / "workspace" / "output" / "output" / "experiments" / "_status" / "comfy_queue_ledger_state.json"
    default_events = repo / "workspace" / "output" / "output" / "experiments" / "_status" / "comfy_queue_ledger.jsonl"
    state_path = Path(args.state_path) if args.state_path else default_state
    events_path = Path(args.events_path) if args.events_path else default_events

    state = _read_state(state_path)
    now = _now_ts()
    state["updated_at"] = _utc_iso(now)
    state["pending_target"] = pending_target
    _write_json(state_path, state)

    def log_event(kind: str, **data: Any) -> None:
        _append_jsonl(events_path, {"ts": _utc_iso(), "type": kind, **data})

    q = _fetch_queue(args.server)
    if q is None:
        log_event("queue_fetch_failed", server=args.server)
        print("ERROR: failed to fetch queue from ComfyUI", file=sys.stderr)
        return 2

    running, pending, _raw = q
    current_ids: Set[str] = {x.prompt_id for x in running + pending}

    # Startup restore: best effort from previous snapshot (or backlog).
    if not args.no_startup_restore:
        prev = state.get("last_snapshot") if isinstance(state.get("last_snapshot"), dict) else {}
        prev_pending = prev.get("pending") if isinstance(prev, dict) else []
        prev_running = prev.get("running") if isinstance(prev, dict) else []
        candidates: List[str] = []
        for pid in list(prev_pending) + list(prev_running):
            if isinstance(pid, str) and pid.strip() and pid not in candidates:
                candidates.append(pid.strip())
        slots = max(0, pending_target - len(pending))
        restored = 0
        for pid in candidates:
            if restored >= slots:
                break
            if pid in current_ids:
                continue
            known = state.get("known", {}).get(pid) if isinstance(state.get("known"), dict) else None
            if not isinstance(known, dict) or not isinstance(known.get("prompt"), dict):
                log_event("startup_restore_skipped_no_payload", prompt_id=pid)
                continue
            attempts = int(state.get("restore_attempts", {}).get(pid, 0))
            last_ts = float(state.get("restore_last_ts", {}).get(pid, 0.0))
            if attempts >= int(args.max_restore_attempts):
                log_event("startup_restore_suppressed_attempt_cap", prompt_id=pid, attempts=attempts)
                continue
            if now - last_ts < float(args.restore_cooldown_s):
                log_event("startup_restore_suppressed_cooldown", prompt_id=pid, since_s=(now - last_ts))
                continue
            ok, res = _submit_prompt(
                args.server,
                prompt=known["prompt"],
                client_id=args.client_id,
                extra_data=known.get("extra_data") if isinstance(known.get("extra_data"), dict) else None,
                outputs_to_execute=known.get("outputs_to_execute") if isinstance(known.get("outputs_to_execute"), list) else None,
            )
            state.setdefault("restore_attempts", {})[pid] = attempts + 1
            state.setdefault("restore_last_ts", {})[pid] = now
            if ok:
                restored += 1
                state.setdefault("expected_add_until_ts", {})[pid] = now + float(args.expected_add_ttl_s)
                log_event("startup_restored", prompt_id=pid, response=res)
                state.setdefault("stats", {})["restored_startup"] = int(state.setdefault("stats", {}).get("restored_startup", 0)) + 1
            else:
                log_event("startup_restore_failed", prompt_id=pid, error=res)
                state.setdefault("restore_failures_ts", []).append(now)
        # Backlog restore if slots remain.
        backlog = state.get("backlog", [])
        if isinstance(backlog, list) and restored < slots:
            i = 0
            while i < len(backlog) and restored < slots:
                item = backlog[i]
                if not isinstance(item, dict):
                    i += 1
                    continue
                pid = item.get("prompt_id")
                if not isinstance(pid, str) or not pid.strip():
                    backlog.pop(i)
                    continue
                if pid in current_ids:
                    backlog.pop(i)
                    continue
                attempts = int(state.get("restore_attempts", {}).get(pid, 0))
                last_ts = float(state.get("restore_last_ts", {}).get(pid, 0.0))
                if attempts >= int(args.max_restore_attempts) or now - last_ts < float(args.restore_cooldown_s):
                    i += 1
                    continue
                prompt_obj = item.get("prompt")
                if not isinstance(prompt_obj, dict):
                    backlog.pop(i)
                    continue
                ok, res = _submit_prompt(
                    args.server,
                    prompt=prompt_obj,
                    client_id=args.client_id,
                    extra_data=item.get("extra_data") if isinstance(item.get("extra_data"), dict) else None,
                    outputs_to_execute=item.get("outputs_to_execute") if isinstance(item.get("outputs_to_execute"), list) else None,
                )
                state.setdefault("restore_attempts", {})[pid] = attempts + 1
                state.setdefault("restore_last_ts", {})[pid] = now
                if ok:
                    restored += 1
                    state.setdefault("expected_add_until_ts", {})[pid] = now + float(args.expected_add_ttl_s)
                    log_event("startup_restored_backlog", prompt_id=pid, response=res)
                    state.setdefault("stats", {})["restored_startup"] = int(state.setdefault("stats", {}).get("restored_startup", 0)) + 1
                    backlog.pop(i)
                else:
                    state.setdefault("restore_failures_ts", []).append(now)
                    log_event("startup_restore_failed_backlog", prompt_id=pid, error=res)
                    i += 1
        if restored:
            print(f"startup_restored={restored}")

    last_quiet_ts = _now_ts()
    while True:
        now = _now_ts()
        # Merge operator control keys from disk.
        disk_state = _read_state(state_path)
        for k in ("paused", "drain_once_requested_at", "breaker", "restore_failures_ts"):
            if k in disk_state:
                state[k] = disk_state[k]
        if _breaker_maybe_close(state):
            log_event("breaker_closed_auto")
        q2 = _fetch_queue(args.server)
        if q2 is None:
            log_event("queue_fetch_failed", server=args.server)
            sleep_s = float(args.poll_interval_churn if state.get("mode") == "churn" else args.poll_interval_normal)
            time.sleep(max(0.1, sleep_s))
            if args.once:
                break
            continue
        running, pending, _raw = q2
        running_ids = [x.prompt_id for x in running]
        pending_ids = [x.prompt_id for x in pending]
        observed_ids = set(running_ids + pending_ids)

        # Update known prompt payloads whenever available.
        known = state.setdefault("known", {})
        for item in running + pending:
            rec = known.get(item.prompt_id, {}) if isinstance(known.get(item.prompt_id), dict) else {}
            rec["last_seen_ts"] = now
            rec["last_seen_at"] = _utc_iso(now)
            rec["last_phase"] = "running" if item.prompt_id in running_ids else "pending"
            if isinstance(item.prompt, dict):
                rec["prompt"] = item.prompt
            if isinstance(item.extra_data, dict):
                rec["extra_data"] = item.extra_data
            if isinstance(item.outputs_to_execute, list):
                rec["outputs_to_execute"] = item.outputs_to_execute
            known[item.prompt_id] = rec

        prev_snapshot = state.get("last_snapshot") if isinstance(state.get("last_snapshot"), dict) else {}
        prev_running = prev_snapshot.get("running") if isinstance(prev_snapshot.get("running"), list) else []
        prev_pending = prev_snapshot.get("pending") if isinstance(prev_snapshot.get("pending"), list) else []
        prev_ids = {str(x) for x in (prev_running + prev_pending) if isinstance(x, str)}
        added = observed_ids - prev_ids
        removed = prev_ids - observed_ids

        expected = state.get("expected_add_until_ts") if isinstance(state.get("expected_add_until_ts"), dict) else {}
        unexpected = 0
        for pid in added:
            ttl = expected.get(pid)
            if not isinstance(ttl, (int, float)) or float(ttl) < now:
                unexpected += 1
        if removed:
            unexpected += len(removed)
        if prev_pending and pending_ids and set(prev_pending) == set(pending_ids) and prev_pending != pending_ids:
            unexpected += 1
        if unexpected > 0:
            state.setdefault("recent_unexpected_ts", []).extend([now] * unexpected)
            log_event("unexpected_queue_delta", added=sorted(list(added)), removed=sorted(list(removed)), unexpected=unexpected)
        else:
            last_quiet_ts = now

        # Mode switch with hysteresis.
        ru = [float(x) for x in state.get("recent_unexpected_ts", []) if isinstance(x, (int, float))]
        ru = [x for x in ru if x >= now - float(args.churn_window_s)]
        state["recent_unexpected_ts"] = ru
        mode = str(state.get("mode") or "normal")
        if mode != "churn" and len(ru) >= int(args.churn_threshold):
            mode = "churn"
            state["mode"] = mode
            state["mode_since_ts"] = now
            log_event("mode_switched", mode="churn", reason="unexpected_delta_threshold")
        elif mode == "churn" and now - last_quiet_ts >= float(args.quiet_window_s):
            mode = "normal"
            state["mode"] = mode
            state["mode_since_ts"] = now
            log_event("mode_switched", mode="normal", reason="quiet_window")

        # Open breaker if too many recent restore failures.
        rf = [float(x) for x in state.get("restore_failures_ts", []) if isinstance(x, (int, float))]
        rf = [x for x in rf if x >= now - float(args.breaker_window_s)]
        state["restore_failures_ts"] = rf
        br = state.get("breaker", {})
        if (
            isinstance(br, dict)
            and not bool(br.get("open"))
            and len(rf) >= int(args.breaker_failure_threshold)
        ):
            _breaker_open(state, reason="restore_failures_threshold", open_for_s=float(args.breaker_open_s))
            log_event("breaker_opened", reason="restore_failures_threshold", failures=len(rf))

        max_actions = int(args.max_actions_churn if mode == "churn" else args.max_actions_normal)
        max_actions = max(0, max_actions)
        actions = 0
        breaker_open = bool(state.get("breaker", {}).get("open")) if isinstance(state.get("breaker"), dict) else False
        paused = bool(state.get("paused"))

        if paused:
            log_event("actions_paused")
        elif breaker_open:
            state.setdefault("stats", {})["suppressed_breaker"] = int(state.setdefault("stats", {}).get("suppressed_breaker", 0)) + 1
            log_event("actions_suppressed_breaker", breaker=state.get("breaker"))
        else:
            # Spillover: trim only tail, never touch on-deck/in-hole by default.
            if bool(args.spillover_enabled) and actions < max_actions:
                protected = 0
                if bool(args.protect_on_deck):
                    protected = max(protected, 1)
                if bool(args.protect_in_hole):
                    protected = max(protected, 2)
                spill_start = max(pending_target, protected)
                overflow = max(0, len(pending_ids) - spill_start)
                if overflow > 0:
                    tail = pending_ids[spill_start:]
                    for pid in reversed(tail):
                        if actions >= max_actions:
                            break
                        ok_del, del_res = _delete_pending_prompt(args.server, pid)
                        if ok_del:
                            pushed = _push_backlog_item(state, pid)
                            actions += 1
                            if pushed:
                                state.setdefault("stats", {})["spillover_removed"] = int(state.setdefault("stats", {}).get("spillover_removed", 0)) + 1
                            log_event("spillover_removed", prompt_id=pid, backlog_added=bool(pushed), result=del_res)
                        else:
                            log_event("spillover_remove_failed", prompt_id=pid, error=del_res)

            # Refill: fill naturally when slots open.
            if actions < max_actions:
                slots = max(0, pending_target - len(pending_ids))
                backlog = state.get("backlog", [])
                if slots > 0 and isinstance(backlog, list) and backlog:
                    i = 0
                    while i < len(backlog) and slots > 0 and actions < max_actions:
                        item = backlog[i]
                        if not isinstance(item, dict):
                            i += 1
                            continue
                        pid = item.get("prompt_id")
                        prompt_obj = item.get("prompt")
                        if not isinstance(pid, str) or not pid.strip() or not isinstance(prompt_obj, dict):
                            backlog.pop(i)
                            continue
                        if pid in observed_ids:
                            backlog.pop(i)
                            continue
                        attempts = int(state.get("restore_attempts", {}).get(pid, 0))
                        last_ts = float(state.get("restore_last_ts", {}).get(pid, 0.0))
                        if attempts >= int(args.max_restore_attempts):
                            state.setdefault("stats", {})["suppressed_cap"] = int(state.setdefault("stats", {}).get("suppressed_cap", 0)) + 1
                            log_event("refill_suppressed_attempt_cap", prompt_id=pid, attempts=attempts)
                            i += 1
                            continue
                        if now - last_ts < float(args.restore_cooldown_s):
                            state.setdefault("stats", {})["suppressed_cooldown"] = int(state.setdefault("stats", {}).get("suppressed_cooldown", 0)) + 1
                            log_event("refill_suppressed_cooldown", prompt_id=pid, since_s=(now - last_ts))
                            i += 1
                            continue
                        ok, res = _submit_prompt(
                            args.server,
                            prompt=prompt_obj,
                            client_id=args.client_id,
                            extra_data=item.get("extra_data") if isinstance(item.get("extra_data"), dict) else None,
                            outputs_to_execute=item.get("outputs_to_execute") if isinstance(item.get("outputs_to_execute"), list) else None,
                        )
                        state.setdefault("restore_attempts", {})[pid] = attempts + 1
                        state.setdefault("restore_last_ts", {})[pid] = now
                        if ok:
                            state.setdefault("expected_add_until_ts", {})[pid] = now + float(args.expected_add_ttl_s)
                            state.setdefault("stats", {})["restored_refill"] = int(state.setdefault("stats", {}).get("restored_refill", 0)) + 1
                            log_event("refill_restored", prompt_id=pid, response=res)
                            backlog.pop(i)
                            slots -= 1
                            actions += 1
                        else:
                            state.setdefault("restore_failures_ts", []).append(now)
                            log_event("refill_restore_failed", prompt_id=pid, error=res)
                            i += 1

        # One-shot drain trigger from API.
        drain_req = float(state.get("drain_once_requested_at") or 0.0)
        if drain_req > 0 and actions < max_actions:
            state["drain_once_requested_at"] = 0.0
            log_event("drain_once_ack")

        state["last_snapshot"] = {"running": running_ids, "pending": pending_ids}
        state["updated_at"] = _utc_iso(now)
        _prune_state(state)
        _write_json(state_path, state)

        if args.once:
            break
        sleep_s = float(args.poll_interval_churn if mode == "churn" else args.poll_interval_normal)
        time.sleep(max(0.1, sleep_s))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

