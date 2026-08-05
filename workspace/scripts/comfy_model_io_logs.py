#!/usr/bin/env python3
"""
ComfyUI model load/unload timings from the in-memory log ring.

Comfy does not expose load/unload over WS/history. Stdout does:
  ``Requested to load NAME`` → ``loaded completely; …`` (or DisTorch complete)
  ``Got an OOM, unloading all loaded models.`` / ``N models unloaded.``

Used by:
  - ``comfy_queue_ledger`` (continuous follower, durable jsonl)
  - ``shape_factory`` (best-effort attach onto a job's execution window)
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Callable, Optional

_RE_MODEL_REQUESTED = re.compile(r"Requested to load\s+(\S+)")
_RE_MODEL_LOADED = re.compile(
    r"loaded completely;\s*([\d.]+)\s*MB usable,\s*([\d.]+)\s*MB loaded",
    re.IGNORECASE,
)
_RE_MODELS_UNLOADED = re.compile(r"(\d+)\s+models unloaded\.", re.IGNORECASE)
_RE_OOM_UNLOAD = re.compile(r"Got an OOM, unloading all loaded models", re.IGNORECASE)
_RE_DISTORCH_DONE = re.compile(r"DisTorch loading completed", re.IGNORECASE)


def normalize_comfy_timestamp(ts: float) -> float:
    """Comfy WS/history timestamps may be epoch seconds or milliseconds."""
    ts_f = float(ts)
    if ts_f > 1_000_000_000_000:
        return ts_f / 1000.0
    return ts_f


def parse_comfy_log_timestamp(value: Any) -> Optional[float]:
    """Parse Comfy ``/internal/logs/raw`` entry timestamps (ISO-ish, no TZ → local)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return normalize_comfy_timestamp(float(value))
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            dt = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            dt = _dt.datetime.fromisoformat(text)
            if dt.tzinfo is None:
                # Comfy's log ring stamps naive local wall time (not UTC).
                local_tz = _dt.datetime.now().astimezone().tzinfo or _dt.timezone.utc
                dt = dt.replace(tzinfo=local_tz)
        return float(dt.timestamp())
    except Exception:
        return None


def normalize_log_message(msg: str) -> str:
    text = str(msg or "").strip()
    if not text:
        return ""
    # Progress bars often share a line with a trailing log message.
    if "\r" in text:
        text = text.rsplit("\r", 1)[-1].strip()
    return text


def log_entry_key(row: dict[str, Any]) -> str:
    return f"{row.get('t')}\0{row.get('m')}"


def fetch_comfy_log_entries(
    server: str,
    *,
    timeout_s: int = 8,
    http_json: Optional[Callable[..., Any]] = None,
) -> list[dict[str, Any]]:
    """Fetch Comfy's in-memory log ring (``GET /internal/logs/raw``)."""
    if http_json is None:
        from comfyui_submit import _http_json as http_json  # type: ignore
    try:
        obj = http_json("GET", f"{server.rstrip('/')}/internal/logs/raw", timeout_s=timeout_s)
    except Exception:
        return []
    if not isinstance(obj, dict):
        return []
    rows = obj.get("entries") if isinstance(obj.get("entries"), list) else []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        msg = row.get("m")
        if msg is None:
            continue
        out.append({"t": row.get("t"), "m": str(msg)})
    return out


def slice_new_log_entries(
    entries: list[dict[str, Any]],
    *,
    cursor_key: Optional[str],
    cursor_ts: Optional[float] = None,
    skip_history_on_empty_cursor: bool = True,
) -> tuple[list[dict[str, Any]], Optional[str], Optional[float]]:
    """
    Return entries after ``cursor_key``.

    On first run (no cursor), skip the current ring contents so we only follow
    live traffic going forward (avoids mis-attributing old loads to the current prompt).
    """
    if not entries:
        return [], cursor_key, cursor_ts
    if not cursor_key:
        last = entries[-1]
        return (
            ([] if skip_history_on_empty_cursor else list(entries)),
            log_entry_key(last),
            parse_comfy_log_timestamp(last.get("t")),
        )
    for i, row in enumerate(entries):
        if log_entry_key(row) == cursor_key:
            new_rows = entries[i + 1 :]
            if not new_rows:
                return [], cursor_key, cursor_ts
            last = new_rows[-1]
            return new_rows, log_entry_key(last), parse_comfy_log_timestamp(last.get("t"))
    # Cursor scrolled out of the ring — take rows newer than cursor_ts when known.
    if isinstance(cursor_ts, (int, float)):
        new_rows = []
        for row in entries:
            ts = parse_comfy_log_timestamp(row.get("t"))
            if ts is not None and ts > float(cursor_ts) + 1e-6:
                new_rows.append(row)
        if new_rows:
            last = new_rows[-1]
            return new_rows, log_entry_key(last), parse_comfy_log_timestamp(last.get("t"))
    # Fallback: treat whole buffer as new (better to double-count than miss).
    last = entries[-1]
    return list(entries), log_entry_key(last), parse_comfy_log_timestamp(last.get("t"))


def parse_model_io_from_comfy_logs(
    entries: list[dict[str, Any]],
    *,
    window_start_ts: Optional[float],
    window_end_ts: Optional[float],
    unload_grace_sec: float = 45.0,
) -> dict[str, Any]:
    """
    Batch derive model load/unload timings for a job execution window.

    Loads are attributed when the *request* falls inside the window.
    Unload events may fall slightly after ``window_end``; following reloads are
    not counted as this job's loads — only ``to_next_load_sec`` is recorded.
    """
    if not entries or window_start_ts is None:
        return {}
    start = float(window_start_ts)
    end = float(window_end_ts) if window_end_ts is not None else start + 86400.0
    unload_end = end + max(0.0, float(unload_grace_sec))

    parsed: list[tuple[float, str]] = []
    for row in entries:
        ts = parse_comfy_log_timestamp(row.get("t"))
        if ts is None:
            continue
        msg = normalize_log_message(str(row.get("m") or ""))
        if not msg:
            continue
        parsed.append((ts, msg))
    if not parsed:
        return {}

    loads: list[dict[str, Any]] = []
    unloads: list[dict[str, Any]] = []
    pending: Optional[dict[str, Any]] = None

    def close_pending(finished_ts: float, *, method: str, mb_loaded: Optional[float] = None) -> None:
        nonlocal pending
        if not pending:
            return
        req_ts = float(pending["requested_ts"])
        entry: dict[str, Any] = {
            "name": pending["name"],
            "requested_ts": req_ts,
            "finished_ts": float(finished_ts),
            "sec": round(max(0.0, float(finished_ts) - req_ts), 3),
            "method": method,
        }
        if mb_loaded is not None:
            entry["mb_loaded"] = mb_loaded
        usable = pending.get("mb_usable")
        if isinstance(usable, (int, float)):
            entry["mb_usable"] = float(usable)
        loads.append(entry)
        pending = None

    for ts, msg in parsed:
        m_req = _RE_MODEL_REQUESTED.search(msg)
        m_loaded = _RE_MODEL_LOADED.search(msg)
        m_unloaded = _RE_MODELS_UNLOADED.search(msg)
        oom_unload = bool(_RE_OOM_UNLOAD.search(msg))
        distorch_done = bool(_RE_DISTORCH_DONE.search(msg))

        if m_req:
            name = m_req.group(1).rstrip(".,;")
            if pending is not None and start - 1.0 <= float(pending["requested_ts"]) <= end + 1.0:
                close_pending(ts, method="next_request")
            if start - 1.0 <= ts <= end + 1.0:
                pending = {"name": name, "requested_ts": ts}
            else:
                pending = None
            continue

        if m_loaded and pending is not None:
            try:
                mb_usable = float(m_loaded.group(1))
                mb_loaded_v = float(m_loaded.group(2))
            except (TypeError, ValueError):
                mb_usable = None
                mb_loaded_v = None
            if mb_usable is not None:
                pending["mb_usable"] = mb_usable
            if start - 1.0 <= float(pending["requested_ts"]) <= end + 1.0:
                close_pending(ts, method="full", mb_loaded=mb_loaded_v)
            else:
                pending = None
            continue

        if distorch_done and pending is not None:
            if start - 1.0 <= float(pending["requested_ts"]) <= end + 1.0:
                close_pending(ts, method="distorch")
            else:
                pending = None
            continue

        if oom_unload and start - 1.0 <= ts <= unload_end:
            unloads.append({"kind": "oom_all", "ts": ts})
            continue

        if m_unloaded and start - 1.0 <= ts <= unload_end:
            try:
                count = int(m_unloaded.group(1))
            except (TypeError, ValueError):
                count = None
            ev: dict[str, Any] = {"kind": "count", "ts": ts}
            if count is not None:
                ev["count"] = count
            unloads.append(ev)

    if pending is not None and start - 1.0 <= float(pending["requested_ts"]) <= end + 1.0:
        loads.append(
            {
                "name": pending["name"],
                "requested_ts": float(pending["requested_ts"]),
                "sec": None,
                "method": "incomplete",
            }
        )

    request_times = [ts for ts, msg in parsed if _RE_MODEL_REQUESTED.search(msg)]
    for ev in unloads:
        uts = float(ev["ts"])
        nxt = next((t for t in request_times if t >= uts), None)
        if nxt is not None:
            ev["to_next_load_sec"] = round(max(0.0, float(nxt) - uts), 3)

    if not loads and not unloads:
        return {}

    load_secs = [float(x["sec"]) for x in loads if isinstance(x.get("sec"), (int, float))]
    unload_gaps = [
        float(x["to_next_load_sec"])
        for x in unloads
        if isinstance(x.get("to_next_load_sec"), (int, float))
    ]
    totals: dict[str, Any] = {
        "load_count": len([x for x in loads if x.get("method") != "incomplete"]),
        "unload_event_count": len(unloads),
    }
    if load_secs:
        totals["load_sec"] = round(sum(load_secs), 3)
    if unload_gaps:
        totals["unload_to_reload_sec"] = round(sum(unload_gaps), 3)

    return {
        "source": "comfy.internal.logs",
        "loads": loads,
        "unloads": unloads,
        "totals": totals,
    }


def _prompt_rollup(bucket: dict[str, Any]) -> dict[str, Any]:
    loads = bucket.get("loads") if isinstance(bucket.get("loads"), list) else []
    names: list[str] = []
    load_sec = 0.0
    for row in loads:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
        if isinstance(row.get("sec"), (int, float)):
            load_sec += float(row["sec"])
    unloads = bucket.get("unloads") if isinstance(bucket.get("unloads"), list) else []
    return {
        "prompt_id": bucket.get("prompt_id"),
        "load_count": len([x for x in loads if isinstance(x, dict) and x.get("method") != "incomplete"]),
        "load_sec": round(load_sec, 3),
        "models": names,
        "unload_event_count": len(unloads),
        "started_ts": bucket.get("started_ts"),
        "finished_ts": bucket.get("finished_ts"),
    }


class ModelIoFollower:
    """
    Incremental follower for Comfy log rings.

    Correlates load/unload lines with the currently running ``prompt_id`` and
    emits durable event dicts suitable for JSONL.
    """

    def __init__(self, state: Optional[dict[str, Any]] = None) -> None:
        self.state: dict[str, Any] = dict(state or {})
        self.state.setdefault("cursor_key", None)
        self.state.setdefault("cursor_ts", None)
        self.state.setdefault("pending", None)
        self.state.setdefault("running_prompt_id", None)
        self.state.setdefault("current", None)
        self.state.setdefault("previous", None)
        self.state.setdefault("stats", {"loads": 0, "unloads": 0, "switches": 0})

    def dump_state(self) -> dict[str, Any]:
        return dict(self.state)

    def _ensure_current(self, prompt_id: Optional[str], ts: float) -> dict[str, Any]:
        cur = self.state.get("current")
        if not isinstance(cur, dict) or cur.get("prompt_id") != prompt_id:
            cur = {
                "prompt_id": prompt_id,
                "loads": [],
                "unloads": [],
                "started_ts": ts,
                "finished_ts": ts,
            }
            self.state["current"] = cur
        else:
            cur["finished_ts"] = ts
            if cur.get("started_ts") is None:
                cur["started_ts"] = ts
        return cur

    def note_running_prompt(self, prompt_id: Optional[str], *, now_ts: float) -> list[dict[str, Any]]:
        """Call each poll with the live running prompt (or None when idle)."""
        events: list[dict[str, Any]] = []
        prev_id = self.state.get("running_prompt_id")
        new_id = str(prompt_id).strip() if prompt_id else None
        if prev_id == new_id:
            return events
        # Close out previous prompt bucket.
        cur = self.state.get("current")
        if isinstance(cur, dict) and cur.get("prompt_id") == prev_id:
            cur["finished_ts"] = float(now_ts)
            self.state["previous"] = {
                "rollup": _prompt_rollup(cur),
                "closed_ts": float(now_ts),
            }
            events.append(
                {
                    "type": "model_prompt_closed",
                    "prompt_id": prev_id,
                    "rollup": _prompt_rollup(cur),
                    "ts_epoch": float(now_ts),
                }
            )
        # Open switch event when we move from one prompt to another (or idle→busy).
        prev_rollup = None
        if isinstance(self.state.get("previous"), dict):
            prev_rollup = self.state["previous"].get("rollup")
        events.append(
            {
                "type": "model_switch",
                "from_prompt_id": prev_id,
                "to_prompt_id": new_id,
                "from_rollup": prev_rollup,
                "ts_epoch": float(now_ts),
            }
        )
        stats = self.state.setdefault("stats", {})
        stats["switches"] = int(stats.get("switches") or 0) + 1
        self.state["running_prompt_id"] = new_id
        self.state["current"] = {
            "prompt_id": new_id,
            "loads": [],
            "unloads": [],
            "started_ts": float(now_ts),
            "finished_ts": float(now_ts),
        }
        # Pending load that straddles a prompt boundary stays attributed to whoever
        # was running when Requested fired (stored on pending).
        return events

    def feed_entries(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        new_rows, new_key, new_ts = slice_new_log_entries(
            entries,
            cursor_key=self.state.get("cursor_key"),
            cursor_ts=self.state.get("cursor_ts") if isinstance(self.state.get("cursor_ts"), (int, float)) else None,
            skip_history_on_empty_cursor=True,
        )
        self.state["cursor_key"] = new_key
        if isinstance(new_ts, (int, float)):
            self.state["cursor_ts"] = float(new_ts)
        if not new_rows:
            return []

        events: list[dict[str, Any]] = []
        pending = self.state.get("pending") if isinstance(self.state.get("pending"), dict) else None
        running_id = self.state.get("running_prompt_id")

        for row in new_rows:
            ts = parse_comfy_log_timestamp(row.get("t"))
            if ts is None:
                continue
            msg = normalize_log_message(str(row.get("m") or ""))
            if not msg:
                continue

            m_req = _RE_MODEL_REQUESTED.search(msg)
            m_loaded = _RE_MODEL_LOADED.search(msg)
            m_unloaded = _RE_MODELS_UNLOADED.search(msg)
            oom_unload = bool(_RE_OOM_UNLOAD.search(msg))
            distorch_done = bool(_RE_DISTORCH_DONE.search(msg))

            if m_req:
                name = m_req.group(1).rstrip(".,;")
                if pending is not None:
                    # Close prior incomplete load at next request boundary.
                    ev = self._finish_pending(pending, finished_ts=ts, method="next_request")
                    if ev:
                        events.append(ev)
                pending = {
                    "name": name,
                    "requested_ts": float(ts),
                    "prompt_id": running_id,
                }
                self.state["pending"] = pending
                continue

            if m_loaded and pending is not None:
                try:
                    mb_usable = float(m_loaded.group(1))
                    mb_loaded_v = float(m_loaded.group(2))
                except (TypeError, ValueError):
                    mb_usable = None
                    mb_loaded_v = None
                if mb_usable is not None:
                    pending["mb_usable"] = mb_usable
                ev = self._finish_pending(
                    pending, finished_ts=ts, method="full", mb_loaded=mb_loaded_v
                )
                if ev:
                    events.append(ev)
                pending = None
                self.state["pending"] = None
                continue

            if distorch_done and pending is not None:
                ev = self._finish_pending(pending, finished_ts=ts, method="distorch")
                if ev:
                    events.append(ev)
                pending = None
                self.state["pending"] = None
                continue

            if oom_unload:
                events.append(self._record_unload({"kind": "oom_all", "ts": float(ts)}, running_id))
                continue

            if m_unloaded:
                try:
                    count = int(m_unloaded.group(1))
                except (TypeError, ValueError):
                    count = None
                payload: dict[str, Any] = {"kind": "count", "ts": float(ts)}
                if count is not None:
                    payload["count"] = count
                events.append(self._record_unload(payload, running_id))

        self.state["pending"] = pending
        return events

    def _finish_pending(
        self,
        pending: dict[str, Any],
        *,
        finished_ts: float,
        method: str,
        mb_loaded: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        req_ts = float(pending["requested_ts"])
        prompt_id = pending.get("prompt_id")
        load: dict[str, Any] = {
            "name": pending.get("name"),
            "requested_ts": req_ts,
            "finished_ts": float(finished_ts),
            "sec": round(max(0.0, float(finished_ts) - req_ts), 3),
            "method": method,
            "prompt_id": prompt_id,
        }
        if mb_loaded is not None:
            load["mb_loaded"] = mb_loaded
        usable = pending.get("mb_usable")
        if isinstance(usable, (int, float)):
            load["mb_usable"] = float(usable)
        # Prefer the live current bucket when it matches the pending attribution.
        bucket = self.state.get("current")
        if isinstance(bucket, dict) and bucket.get("prompt_id") == prompt_id:
            loads = bucket.setdefault("loads", [])
            if isinstance(loads, list):
                loads.append(load)
            bucket["finished_ts"] = float(finished_ts)
        elif isinstance(bucket, dict) and prompt_id is None and bucket.get("prompt_id") is None:
            loads = bucket.setdefault("loads", [])
            if isinstance(loads, list):
                loads.append(load)
            bucket["finished_ts"] = float(finished_ts)
        else:
            # Load finished under a prior prompt attribution — still count on current
            # only when the running prompt matches; otherwise keep event-only.
            if isinstance(bucket, dict) and bucket.get("prompt_id") == self.state.get("running_prompt_id"):
                # Mid-switch: attribute completed load to whatever prompt_id was stamped
                # on pending (already on the event). Do not pollute the new prompt rollup.
                pass
        stats = self.state.setdefault("stats", {})
        stats["loads"] = int(stats.get("loads") or 0) + 1
        return {
            "type": "model_load",
            "prompt_id": prompt_id,
            "name": load.get("name"),
            "sec": load.get("sec"),
            "method": method,
            "mb_loaded": load.get("mb_loaded"),
            "mb_usable": load.get("mb_usable"),
            "requested_ts": req_ts,
            "finished_ts": float(finished_ts),
            "ts_epoch": float(finished_ts),
        }

    def _record_unload(self, payload: dict[str, Any], running_id: Optional[str]) -> dict[str, Any]:
        ts = float(payload["ts"])
        bucket = self._ensure_current(running_id, ts)
        unloads = bucket.setdefault("unloads", [])
        if isinstance(unloads, list):
            unloads.append(dict(payload))
        stats = self.state.setdefault("stats", {})
        stats["unloads"] = int(stats.get("unloads") or 0) + 1
        return {
            "type": "model_unload",
            "prompt_id": running_id,
            "kind": payload.get("kind"),
            "count": payload.get("count"),
            "ts_epoch": ts,
        }

    def current_rollup(self) -> Optional[dict[str, Any]]:
        cur = self.state.get("current")
        if not isinstance(cur, dict):
            return None
        return _prompt_rollup(cur)
