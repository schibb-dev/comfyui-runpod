#!/usr/bin/env python3
"""
Best-effort ComfyUI queue ledger + startup restorer.

Design goals:
- Passive: read /queue and keep a shadow ledger on disk.
- Non-ACID: best effort over exact correctness.
- Never silently drop a mirrored job on reboot: re-submit or park in backlog
  (only unrecoverable if the prompt payload was never mirrored) — unless Comfy
  ``/history`` already shows the prompt finished (success/error/interrupted),
  in which case restore is skipped to avoid duplicate identical runs.
- Safe-ish: avoid loops with attempt caps / breaker / history skip.
- Gentle: spillover mode keeps live pending near target; otherwise full restore.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from comfy_model_io_logs import ModelIoFollower, fetch_comfy_log_entries
from http_retry import http_json_with_retry
from output_path_lib import apply_queue_date_to_prompt


def _utc_iso(ts: Optional[float] = None) -> str:
    t = float(time.time() if ts is None else ts)
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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


def _http_json(
    method: str,
    url: str,
    body: Optional[Dict[str, Any]] = None,
    timeout_s: int = 10,
    *,
    retry_attempts: Optional[int] = None,
    retry_backoff_s: float = 0.25,
) -> Any:
    return http_json_with_retry(
        method=method,
        url=url,
        payload=body,
        timeout_s=timeout_s,
        retry_attempts=retry_attempts,
        retry_backoff_s=retry_backoff_s,
    )


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


def _candidate_ids_from_snapshot(snapshot: Any) -> List[str]:
    if not isinstance(snapshot, dict):
        return []
    out: List[str] = []
    for key in ("pending", "running"):
        for pid in snapshot.get(key) or []:
            if isinstance(pid, str) and pid.strip() and pid.strip() not in out:
                out.append(pid.strip())
    return out


def _known_client_id(rec: Any) -> Optional[str]:
    if not isinstance(rec, dict):
        return None
    extra = rec.get("extra_data")
    if isinstance(extra, dict):
        cid = extra.get("client_id")
        if isinstance(cid, str) and cid.strip():
            return cid.strip()
    return None


def _fetch_prompt_history(server: str, prompt_id: str, timeout_s: int = 10) -> Optional[Dict[str, Any]]:
    """
    Return Comfy `/history/{prompt_id}` JSON, or None if the request fails.

    An empty dict means the prompt is not in history (lost / never finished).
    """
    try:
        obj = _http_json(
            "GET",
            f"{server.rstrip('/')}/history/{urllib.parse.quote(prompt_id, safe='')}",
            timeout_s=timeout_s,
        )
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _history_terminal_reason(history_obj: Optional[Dict[str, Any]], prompt_id: str) -> Optional[str]:
    """
    If ``prompt_id`` already finished in Comfy history, return a short reason
    (``success`` / ``error`` / ``interrupted`` / ``completed``). Otherwise None.

    Used to avoid re-queueing jobs that left ``/queue`` because they finished
    during a brief outage (the main source of identical ledger restores).
    """
    if not isinstance(history_obj, dict):
        return None
    entry = history_obj.get(prompt_id)
    if not isinstance(entry, dict) or not entry:
        return None
    status = entry.get("status") if isinstance(entry.get("status"), dict) else {}
    status_str = str(status.get("status_str") or "").strip().lower()
    if status_str in {"success", "error", "interrupted"}:
        return status_str
    if status.get("completed") is True:
        return "completed"
    messages = status.get("messages") if isinstance(status.get("messages"), list) else []
    for msg in messages:
        if not isinstance(msg, (list, tuple)) or not msg:
            continue
        kind = str(msg[0] or "").strip().lower()
        if kind in {"execution_success", "execution_error", "execution_interrupted"}:
            return kind.replace("execution_", "")
    # History payload present with outputs is treated as done even if status is sparse.
    outputs = entry.get("outputs")
    if isinstance(outputs, dict) and outputs:
        return "has_outputs"
    return None


def _forget_mirrored_prompt(state: Dict[str, Any], prompt_id: str) -> None:
    """Drop a finished prompt from restore mirrors so it cannot be re-queued."""
    known = state.get("known")
    if isinstance(known, dict):
        known.pop(prompt_id, None)
    for key in ("restore_attempts", "restore_last_ts", "expected_add_until_ts"):
        bucket = state.get(key)
        if isinstance(bucket, dict):
            bucket.pop(prompt_id, None)
    backlog = state.get("backlog")
    if isinstance(backlog, list):
        # Mutate in place so callers holding a reference stay consistent.
        backlog[:] = [
            x
            for x in backlog
            if not (isinstance(x, dict) and x.get("prompt_id") == prompt_id)
        ]
    snap = state.get("last_snapshot")
    if isinstance(snap, dict):
        for key in ("pending", "running"):
            ids = snap.get(key)
            if isinstance(ids, list):
                ids[:] = [x for x in ids if x != prompt_id]


def _prompt_already_finished(server: str, prompt_id: str) -> Tuple[Optional[str], str]:
    """
    Check Comfy history for a finished prompt.

    Returns ``(reason, check)`` where ``check`` is ``ok`` / ``empty`` / ``error``.
    ``reason`` is set only when the prompt is terminal in history.
    """
    hist = _fetch_prompt_history(server, prompt_id)
    if hist is None:
        return None, "error"
    reason = _history_terminal_reason(hist, prompt_id)
    if reason:
        return reason, "ok"
    return None, "empty"


def _restore_missing_prompts(
    state: Dict[str, Any],
    *,
    server: str,
    client_id: str,
    candidates: List[str],
    current_ids: Set[str],
    spillover: bool,
    pending_target: int,
    live_pending: int,
    max_restore_attempts: int,
    expected_add_ttl_s: float,
    source: str,
    log_event,
    now: Optional[float] = None,
) -> Tuple[int, int, int, Set[str]]:
    """
    Re-submit mirrored prompts missing from the live Comfy queue.

    Skips prompts that already finished in Comfy ``/history`` (success/error/etc.)
    so brief outages do not replay completed work under a new prompt_id.

    Returns (restored, parked, unrecoverable, updated_current_ids).
    ``source`` is used in event names: ``startup`` or ``outage``.
    """
    ts = float(now if now is not None else _now_ts())
    live_slots: Optional[int] = max(0, int(pending_target) - int(live_pending)) if spillover else None
    restored = 0
    parked = 0
    unrecoverable = 0
    skipped_done = 0
    live_ids = set(current_ids)

    def _live_full() -> bool:
        return live_slots is not None and restored >= int(live_slots)

    def _skip_if_finished(pid: str, *, via: str) -> bool:
        nonlocal skipped_done
        reason, check = _prompt_already_finished(server, pid)
        if not reason:
            return False
        skipped_done += 1
        _forget_mirrored_prompt(state, pid)
        log_event(
            f"{source}_restore_skipped_already_done",
            prompt_id=pid,
            reason=reason,
            history_check=check,
            source=via,
        )
        stats = state.setdefault("stats", {})
        stats["skipped_already_done"] = int(stats.get("skipped_already_done", 0)) + 1
        return True

    def _try_submit(pid: str, prompt_obj: Dict[str, Any], *, extra_data: Any, outputs: Any, via: str) -> bool:
        nonlocal restored
        if _skip_if_finished(pid, via=via):
            return True  # accounted for; caller should not park
        attempts = int(state.get("restore_attempts", {}).get(pid, 0))
        if attempts >= int(max_restore_attempts):
            log_event(
                f"{source}_restore_suppressed_attempt_cap",
                prompt_id=pid,
                attempts=attempts,
                source=via,
            )
            return False
        ok, res = _submit_prompt(
            server,
            prompt=prompt_obj,
            client_id=client_id,
            extra_data=extra_data if isinstance(extra_data, dict) else None,
            outputs_to_execute=outputs if isinstance(outputs, list) else None,
        )
        state.setdefault("restore_attempts", {})[pid] = attempts + 1
        state.setdefault("restore_last_ts", {})[pid] = ts
        if ok:
            restored += 1
            np = res.get("prompt_id") if isinstance(res, dict) else None
            if isinstance(np, str) and np.strip():
                live_ids.add(np.strip())
            state.setdefault("expected_add_until_ts", {})[pid] = ts + float(expected_add_ttl_s)
            log_event(
                f"{source}_restored" if via == "snapshot" else f"{source}_restored_backlog",
                prompt_id=pid,
                response=res,
                source=via,
            )
            stats_key = "restored_startup" if source == "startup" else "restored_outage"
            state.setdefault("stats", {})[stats_key] = int(state.setdefault("stats", {}).get(stats_key, 0)) + 1
            return True
        log_event(
            f"{source}_restore_failed" if via == "snapshot" else f"{source}_restore_failed_backlog",
            prompt_id=pid,
            error=res,
            source=via,
        )
        state.setdefault("restore_failures_ts", []).append(ts)
        return False

    log_event(
        f"{source}_restore_begin",
        spillover=spillover,
        live_slots=live_slots,
        pending_target=pending_target,
        candidates=len(candidates),
        live_pending=live_pending,
        live_ids=len(live_ids),
        backlog=len(state.get("backlog") or []) if isinstance(state.get("backlog"), list) else 0,
    )

    for pid in candidates:
        if pid in live_ids:
            continue
        known = state.get("known", {}).get(pid) if isinstance(state.get("known"), dict) else None
        if not isinstance(known, dict) or not isinstance(known.get("prompt"), dict):
            unrecoverable += 1
            log_event(f"{source}_restore_unrecoverable_no_payload", prompt_id=pid)
            continue
        if not _live_full():
            if _try_submit(
                pid,
                known["prompt"],
                extra_data=known.get("extra_data"),
                outputs=known.get("outputs_to_execute"),
                via="snapshot",
            ):
                continue
        if _skip_if_finished(pid, via="snapshot"):
            continue
        if _push_backlog_item(state, pid):
            parked += 1
            log_event(f"{source}_parked_backlog", prompt_id=pid, reason="spillover_or_submit_failed")
        else:
            log_event(f"{source}_already_accounted", prompt_id=pid)

    backlog = state.get("backlog", [])
    if isinstance(backlog, list) and backlog:
        i = 0
        while i < len(backlog):
            if spillover and _live_full():
                break
            item = backlog[i]
            if not isinstance(item, dict):
                i += 1
                continue
            pid = item.get("prompt_id")
            if not isinstance(pid, str) or not pid.strip():
                backlog.pop(i)
                continue
            if pid in live_ids:
                backlog.pop(i)
                continue
            prompt_obj = item.get("prompt")
            if not isinstance(prompt_obj, dict):
                unrecoverable += 1
                log_event(f"{source}_restore_unrecoverable_backlog_no_payload", prompt_id=pid)
                backlog.pop(i)
                continue
            if _try_submit(
                pid,
                prompt_obj,
                extra_data=item.get("extra_data"),
                outputs=item.get("outputs_to_execute"),
                via="backlog",
            ):
                # Successful restore or already-done skip: drop this backlog row if still present.
                if i < len(backlog) and isinstance(backlog[i], dict) and backlog[i].get("prompt_id") == pid:
                    backlog.pop(i)
                continue
            i += 1

    log_event(
        f"{source}_restore_done",
        restored=restored,
        parked_backlog=parked,
        unrecoverable=unrecoverable,
        skipped_already_done=skipped_done,
        spillover=spillover,
        live_slots=live_slots,
    )
    return restored, parked, unrecoverable, live_ids


def _apply_ledger_clear(state: Dict[str, Any]) -> Dict[str, int]:
    """
    Drop mirrored restore state so a later Comfy restart will not re-queue old jobs.

    Does not touch Comfy's live queue. Keeps operator flags (paused/breaker) and stats.
    """
    known = state.get("known") if isinstance(state.get("known"), dict) else {}
    backlog = state.get("backlog") if isinstance(state.get("backlog"), list) else []
    snap = state.get("last_snapshot") if isinstance(state.get("last_snapshot"), dict) else {}
    prev_running = snap.get("running") if isinstance(snap.get("running"), list) else []
    prev_pending = snap.get("pending") if isinstance(snap.get("pending"), list) else []
    counts = {
        "known": len(known),
        "backlog": len(backlog),
        "snapshot": len(prev_running) + len(prev_pending),
    }
    state["last_snapshot"] = {"running": [], "pending": []}
    state["known"] = {}
    state["backlog"] = []
    state["restore_attempts"] = {}
    state["restore_last_ts"] = {}
    state["expected_add_until_ts"] = {}
    state["recent_unexpected_ts"] = []
    state["clear_requested_at"] = 0.0
    state["updated_at"] = _utc_iso()
    return counts


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
        "clear_requested_at": 0.0,
        "park_requested_at": 0.0,
        "stats": {
            "restored_startup": 0,
            "restored_outage": 0,
            "restored_refill": 0,
            "spillover_removed": 0,
            "suppressed_breaker": 0,
            "suppressed_cap": 0,
            "suppressed_cooldown": 0,
            "skipped_already_done": 0,
            "cleared": 0,
        },
    }


def _read_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return _default_state()
    try:
        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            # Concurrent non-atomic writers used to leave an empty file; refuse to
            # clobber a good in-memory/default merge from that transient state.
            raise ValueError("empty ledger state file")
        obj = json.loads(raw)
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
    stamped = json.loads(json.dumps(prompt))
    apply_queue_date_to_prompt(stamped)
    payload: Dict[str, Any] = {"prompt": stamped, "client_id": client_id}
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


def _append_backlog_item(
    state: Dict[str, Any],
    *,
    prompt_id: str,
    prompt: Optional[Dict[str, Any]],
    extra_data: Optional[Dict[str, Any]] = None,
    outputs_to_execute: Optional[List[Any]] = None,
    source: str = "spillover",
) -> bool:
    pid = str(prompt_id or "").strip()
    if not pid or not isinstance(prompt, dict):
        return False
    backlog = state.setdefault("backlog", [])
    if not isinstance(backlog, list):
        backlog = []
        state["backlog"] = backlog
    for x in backlog:
        if isinstance(x, dict) and x.get("prompt_id") == pid:
            return False
    backlog.append(
        {
            "prompt_id": pid,
            "prompt": prompt,
            "extra_data": extra_data if isinstance(extra_data, dict) else None,
            "outputs_to_execute": outputs_to_execute if isinstance(outputs_to_execute, list) else None,
            "enqueued_backlog_ts": _now_ts(),
            "source": str(source or "spillover"),
        }
    )
    return True


def _push_backlog_item(state: Dict[str, Any], prompt_id: str, *, source: str = "spillover") -> bool:
    known = state.get("known", {})
    if not isinstance(known, dict):
        return False
    rec = known.get(prompt_id)
    if not isinstance(rec, dict) or not isinstance(rec.get("prompt"), dict):
        return False
    return _append_backlog_item(
        state,
        prompt_id=prompt_id,
        prompt=rec.get("prompt") if isinstance(rec.get("prompt"), dict) else None,
        extra_data=rec.get("extra_data") if isinstance(rec.get("extra_data"), dict) else None,
        outputs_to_execute=rec.get("outputs_to_execute") if isinstance(rec.get("outputs_to_execute"), list) else None,
        source=source,
    )


def park_items_to_backlog(
    state: Dict[str, Any],
    items: List[QueueItem],
    *,
    source: str = "park",
) -> Dict[str, int]:
    """Copy live queue items into backlog so Comfy can be emptied and restored later."""
    added = 0
    skipped = 0
    no_prompt = 0
    known = state.setdefault("known", {})
    if not isinstance(known, dict):
        known = {}
        state["known"] = known
    now = _now_ts()
    for item in items:
        pid = str(item.prompt_id or "").strip()
        if not pid:
            continue
        rec = known.get(pid) if isinstance(known.get(pid), dict) else {}
        prompt = item.prompt if isinstance(item.prompt, dict) else rec.get("prompt")
        extra = item.extra_data if isinstance(item.extra_data, dict) else rec.get("extra_data")
        outputs = item.outputs_to_execute if isinstance(item.outputs_to_execute, list) else rec.get("outputs_to_execute")
        if not isinstance(rec, dict):
            rec = {}
        if not isinstance(rec.get("first_seen_ts"), (int, float)):
            rec["first_seen_ts"] = now
            rec["first_seen_at"] = _utc_iso(now)
        rec["last_seen_ts"] = now
        rec["last_seen_at"] = _utc_iso(now)
        rec["last_phase"] = rec.get("last_phase") or "pending"
        if isinstance(prompt, dict):
            rec["prompt"] = prompt
        if isinstance(extra, dict):
            rec["extra_data"] = extra
        if isinstance(outputs, list):
            rec["outputs_to_execute"] = outputs
        known[pid] = rec
        if not isinstance(prompt, dict):
            no_prompt += 1
            continue
        if _append_backlog_item(
            state,
            prompt_id=pid,
            prompt=prompt,
            extra_data=extra if isinstance(extra, dict) else None,
            outputs_to_execute=outputs if isinstance(outputs, list) else None,
            source=source,
        ):
            added += 1
        else:
            skipped += 1
    return {"added": added, "skipped": skipped, "no_prompt": no_prompt}


def backlog_item_should_skip_finished(item: Dict[str, Any], done_reason: Optional[str]) -> bool:
    """
    History skip: completed work should not be re-queued.

    Operator-parked jobs are meant to run later even if we interrupted them
    (or they errored) to empty Comfy. Only a successful finish stays skipped.
    """
    if not done_reason:
        return False
    if str(item.get("source") or "") == "park" and str(done_reason) != "success":
        return False
    return True


def default_ledger_control_path(state_path: Path) -> Path:
    return Path(state_path).with_name("comfy_queue_ledger_control.json")


def _read_control(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


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
    ap.add_argument(
        "--control-path",
        default="",
        help="Tiny operator control JSON (pause/park) next to state by default",
    )
    ap.add_argument("--events-path", default="", help="Path to ledger events JSONL")
    ap.add_argument(
        "--model-io-path",
        default="",
        help="Path to model load/unload JSONL (default: sibling of --events-path)",
    )
    ap.add_argument(
        "--model-io-summary-path",
        default="",
        help="Path to rolling model-io summary JSON (default: sibling of --model-io-path)",
    )
    ap.add_argument(
        "--no-model-io",
        action="store_true",
        help="Disable Comfy log follower for model load/unload timings",
    )
    ap.add_argument("--once", action="store_true", help="Run one cycle and exit")
    ap.add_argument("--no-startup-restore", action="store_true", help="Disable startup restore")
    ap.add_argument(
        "--outage-restore-min-s",
        type=float,
        default=5.0,
        help="After Comfy is unreachable for this long, restore last_snapshot on recovery",
    )
    ap.add_argument(
        "--no-outage-restore",
        action="store_true",
        help="Disable restore when Comfy returns after an outage (ledger process stays up)",
    )
    args = ap.parse_args()

    pending_target = max(2, int(args.pending_target))
    repo = _resolve_repo_root()
    default_state = repo / "workspace" / "output" / "output" / "experiments" / "_status" / "comfy_queue_ledger_state.json"
    default_events = repo / "workspace" / "output" / "output" / "experiments" / "_status" / "comfy_queue_ledger.jsonl"
    state_path = Path(args.state_path) if args.state_path else default_state
    control_path = Path(args.control_path) if args.control_path else default_ledger_control_path(state_path)
    events_path = Path(args.events_path) if args.events_path else default_events
    if args.model_io_path:
        model_io_path = Path(args.model_io_path)
    else:
        model_io_path = events_path.with_name("comfy_model_io.jsonl")
    if args.model_io_summary_path:
        model_io_summary_path = Path(args.model_io_summary_path)
    else:
        model_io_summary_path = model_io_path.with_name("comfy_model_io_summary.json")

    state = _read_state(state_path)
    now = _now_ts()
    state["updated_at"] = _utc_iso(now)
    state["pending_target"] = pending_target
    if float(state.get("clear_requested_at") or 0.0) > 0:
        cleared = _apply_ledger_clear(state)
        state.setdefault("stats", {})["cleared"] = int(state.setdefault("stats", {}).get("cleared", 0)) + 1
        _append_jsonl(events_path, {"ts": _utc_iso(now), "type": "ledger_cleared", "source": "startup", **cleared})
    _write_json(state_path, state)

    def log_event(kind: str, **data: Any) -> None:
        _append_jsonl(events_path, {"ts": _utc_iso(), "type": kind, **data})

    def log_model_io(ev: Dict[str, Any]) -> None:
        payload = {"ts": _utc_iso(), **ev}
        _append_jsonl(model_io_path, payload)

    model_io_enabled = not bool(args.no_model_io)
    model_follower = ModelIoFollower(state.get("model_io") if isinstance(state.get("model_io"), dict) else {})

    def poll_model_io(running_ids: List[str]) -> None:
        if not model_io_enabled:
            return
        running_pid = running_ids[0] if running_ids else None
        try:
            for ev in model_follower.note_running_prompt(running_pid, now_ts=_now_ts()):
                log_model_io(ev)
            entries = fetch_comfy_log_entries(args.server, http_json=_http_json)
            for ev in model_follower.feed_entries(entries):
                log_model_io(ev)
            # Enrich latest open switch with cold-start cost from current rollup.
            dump = model_follower.dump_state()
            state["model_io"] = dump
            rollup = model_follower.current_rollup()
            summary = {
                "updated_at": _utc_iso(),
                "running_prompt_id": dump.get("running_prompt_id"),
                "current": rollup,
                "previous": (dump.get("previous") or {}).get("rollup")
                if isinstance(dump.get("previous"), dict)
                else None,
                "stats": dump.get("stats") or {},
                "events_path": str(model_io_path),
            }
            # Switch cost proxy: load_sec accumulated on the new prompt so far.
            if isinstance(rollup, dict) and rollup.get("prompt_id"):
                summary["switch_load_sec"] = rollup.get("load_sec")
                summary["switch_models"] = rollup.get("models")
            _write_json(model_io_summary_path, summary)
        except Exception as exc:
            log_event("model_io_poll_failed", error=str(exc))

    q = _fetch_queue(args.server)
    if q is None:
        log_event("queue_fetch_failed", server=args.server)
        print("ERROR: failed to fetch queue from ComfyUI", file=sys.stderr)
        return 2

    running, pending, _raw = q
    current_ids: Set[str] = {x.prompt_id for x in running + pending}

    # Startup restore: never drop a mirrored job.
    # - spillover off: re-submit the full last snapshot (+ drain backlog) into Comfy.
    # - spillover on: fill pending_target live; park the rest in backlog for refill.
    # Jobs without a stored prompt payload cannot be recovered (logged as unrecoverable).
    # Paused: skip restore (operator holds the ledger; clear/forget stays in effect).
    if not args.no_startup_restore and not bool(state.get("paused")):
        spillover = bool(args.spillover_enabled)
        candidates = _candidate_ids_from_snapshot(state.get("last_snapshot"))
        restored, parked, unrecoverable, current_ids = _restore_missing_prompts(
            state,
            server=args.server,
            client_id=args.client_id,
            candidates=candidates,
            current_ids=current_ids,
            spillover=spillover,
            pending_target=pending_target,
            live_pending=len(pending),
            max_restore_attempts=int(args.max_restore_attempts),
            expected_add_ttl_s=float(args.expected_add_ttl_s),
            source="startup",
            log_event=log_event,
            now=now,
        )
        print(
            f"startup_restored={restored} parked_backlog={parked} "
            f"unrecoverable={unrecoverable} spillover={spillover} "
            f"live_slots={(max(0, pending_target - len(pending)) if spillover else None)}"
        )
        state["updated_at"] = _utc_iso(now)
        _write_json(state_path, state)
    elif bool(state.get("paused")):
        log_event("startup_restore_skipped_paused")
        print("startup_restored=skipped paused=True")

    # Seed model-io follower against whatever is currently running.
    poll_model_io([x.prompt_id for x in running])
    state["model_io"] = model_follower.dump_state()
    _write_json(state_path, state)

    last_quiet_ts = _now_ts()
    outage_since_ts: Optional[float] = None
    last_logged_paused: Optional[bool] = None
    last_logged_breaker: Optional[bool] = None
    while True:
        now = _now_ts()
        # Merge operator control keys from disk.
        disk_state = _read_state(state_path)
        for k in (
            "paused",
            "drain_once_requested_at",
            "clear_requested_at",
            "park_requested_at",
            "breaker",
            "restore_failures_ts",
        ):
            if k in disk_state:
                state[k] = disk_state[k]
        control = _read_control(control_path)
        if control:
            for k in ("paused", "drain_once_requested_at", "clear_requested_at", "park_requested_at", "breaker"):
                if k in control:
                    state[k] = control[k]
        if _breaker_maybe_close(state):
            log_event("breaker_closed_auto")

        # Explicit clear: drop mirrored restore state (does not clear Comfy's live queue).
        if float(state.get("clear_requested_at") or 0.0) > 0:
            cleared = _apply_ledger_clear(state)
            state.setdefault("stats", {})["cleared"] = int(state.setdefault("stats", {}).get("cleared", 0)) + 1
            log_event("ledger_cleared", source="control", **cleared)
            # Forget any in-flight outage so we don't restore a pre-clear snapshot.
            outage_since_ts = None
            _write_json(state_path, state)

        q2 = _fetch_queue(args.server)
        if q2 is None:
            if outage_since_ts is None:
                outage_since_ts = now
                log_event("comfy_outage_begin", server=args.server)
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

        # Comfy came back after an outage: restore mirrored jobs before accepting empty snapshot.
        # Respect pause so "pause → restart Comfy" does not silently re-queue.
        if outage_since_ts is not None:
            outage_s = max(0.0, now - float(outage_since_ts))
            log_event("comfy_outage_end", outage_s=outage_s, live_pending=len(pending_ids), live_running=len(running_ids))
            paused_now = bool(state.get("paused"))
            if paused_now:
                log_event("outage_restore_skipped_paused", outage_s=outage_s)
                print(f"outage_restored=skipped paused=True outage_s={outage_s:.1f}")
            elif (not args.no_outage_restore) and outage_s >= float(args.outage_restore_min_s):
                spillover = bool(args.spillover_enabled)
                candidates = _candidate_ids_from_snapshot(state.get("last_snapshot"))
                restored, parked, unrecoverable, observed_ids = _restore_missing_prompts(
                    state,
                    server=args.server,
                    client_id=args.client_id,
                    candidates=candidates,
                    current_ids=observed_ids,
                    spillover=spillover,
                    pending_target=pending_target,
                    live_pending=len(pending_ids),
                    max_restore_attempts=int(args.max_restore_attempts),
                    expected_add_ttl_s=float(args.expected_add_ttl_s),
                    source="outage",
                    log_event=log_event,
                    now=now,
                )
                print(
                    f"outage_restored={restored} parked_backlog={parked} "
                    f"unrecoverable={unrecoverable} outage_s={outage_s:.1f}"
                )
                # Refresh live view after restores so snapshot/churn math matches reality.
                q3 = _fetch_queue(args.server)
                if q3 is not None:
                    running, pending, _raw = q3
                    running_ids = [x.prompt_id for x in running]
                    pending_ids = [x.prompt_id for x in pending]
                    observed_ids = set(running_ids + pending_ids)
            outage_since_ts = None

        # Update known prompt payloads whenever available.
        known = state.setdefault("known", {})
        for item in running + pending:
            rec = known.get(item.prompt_id, {}) if isinstance(known.get(item.prompt_id), dict) else {}
            if not isinstance(rec.get("first_seen_ts"), (int, float)):
                rec["first_seen_ts"] = now
                rec["first_seen_at"] = _utc_iso(now)
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

        if float(state.get("park_requested_at") or 0.0) > 0:
            counts = park_items_to_backlog(state, running + pending, source="park")
            state["paused"] = True
            state["park_requested_at"] = 0.0
            log_event(
                "queue_parked",
                **counts,
                live_running=len(running_ids),
                live_pending=len(pending_ids),
            )
            ack = dict(control) if isinstance(control, dict) else {}
            ack["park_requested_at"] = 0.0
            ack["paused"] = True
            ack["last_park_at"] = _utc_iso(now)
            ack["last_park"] = counts
            _write_json(control_path, ack)
            _write_json(state_path, state)

        prev_snapshot = state.get("last_snapshot") if isinstance(state.get("last_snapshot"), dict) else {}
        prev_running = prev_snapshot.get("running") if isinstance(prev_snapshot.get("running"), list) else []
        prev_pending = prev_snapshot.get("pending") if isinstance(prev_snapshot.get("pending"), list) else []
        prev_running_set = {str(x) for x in prev_running if isinstance(x, str)}
        prev_pending_set = {str(x) for x in prev_pending if isinstance(x, str)}
        prev_ids = prev_running_set | prev_pending_set
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
            # Reset the quiet clock when the queue membership/order churns.
            last_quiet_ts = now
        # Operator-facing activity: normal enqueue / leave (not the churn counter name).
        running_id_set = set(running_ids)
        for pid in sorted(added):
            rec = known.get(pid) if isinstance(known.get(pid), dict) else {}
            log_event(
                "queue_enqueued",
                prompt_id=pid,
                phase="running" if pid in running_id_set else "pending",
                client_id=_known_client_id(rec),
            )
        for pid in sorted(removed):
            rec = known.get(pid) if isinstance(known.get(pid), dict) else {}
            if pid in prev_running_set:
                was_phase = "running"
            elif pid in prev_pending_set:
                was_phase = "pending"
            else:
                was_phase = "unknown"
            log_event(
                "queue_left",
                prompt_id=pid,
                was_phase=was_phase,
                client_id=_known_client_id(rec),
            )
        # When unexpected==0, leave last_quiet_ts alone so quiet time can accumulate.

        # Mode switch with hysteresis.
        ru = [float(x) for x in state.get("recent_unexpected_ts", []) if isinstance(x, (int, float))]
        ru = [x for x in ru if x >= now - float(args.churn_window_s)]
        state["recent_unexpected_ts"] = ru
        mode = str(state.get("mode") or "normal")
        if mode != "churn" and len(ru) >= int(args.churn_threshold):
            mode = "churn"
            state["mode"] = mode
            state["mode_since_ts"] = now
            last_quiet_ts = now
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
            if last_logged_paused is not True:
                log_event("actions_paused")
                last_logged_paused = True
            last_logged_breaker = False
        elif breaker_open:
            state.setdefault("stats", {})["suppressed_breaker"] = int(state.setdefault("stats", {}).get("suppressed_breaker", 0)) + 1
            if last_logged_breaker is not True:
                log_event("actions_suppressed_breaker", breaker=state.get("breaker"))
                last_logged_breaker = True
            last_logged_paused = False
        else:
            last_logged_paused = False
            last_logged_breaker = False
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
                        done_reason, _done_check = _prompt_already_finished(args.server, pid)
                        if done_reason and backlog_item_should_skip_finished(item, done_reason):
                            _forget_mirrored_prompt(state, pid)
                            state.setdefault("stats", {})["skipped_already_done"] = int(
                                state.setdefault("stats", {}).get("skipped_already_done", 0)
                            ) + 1
                            log_event(
                                "refill_skipped_already_done",
                                prompt_id=pid,
                                reason=done_reason,
                            )
                            # _forget_mirrored_prompt already removed this backlog item.
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
        poll_model_io(running_ids)
        state["model_io"] = model_follower.dump_state()
        _prune_state(state)
        _write_json(state_path, state)

        if args.once:
            break
        sleep_s = float(args.poll_interval_churn if mode == "churn" else args.poll_interval_normal)
        time.sleep(max(0.1, sleep_s))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

