#!/usr/bin/env python3
"""
Still auto-tagger store + batch runner (PromptGen-large via Comfy).

SQLite at <data_root>/shape_factory/still_tags.sqlite — not a monolith JSON blob.
Gallery enqueues runs into a backlog; an index-hour drainer submits Comfy Florence
jobs (prefer front-of-queue) and writes events. See docs/STILL_TAG_INDEX_HOUR_PLAN.md.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from vision_tag_judgment_tags import parse_danbooru_tags

SCHEMA_VERSION = 2
DB_BASENAME = "still_tags.sqlite"
DEFAULT_MODEL_PIN = "MiaoshouAI/Florence-2-large-PromptGen-v2.0"
# Same weights/task as V3a day-one; x2 cohort had similar F1 and stronger important-tag recall.
DEFAULT_PIN_POLICY = "cohort_x2_pg_large_tags"
DEFAULT_TASK = "prompt_gen_tags"
DEFAULT_LIMIT = 12
DEFAULT_COMFY_SERVER = "http://127.0.0.1:8188"
# Florence2Run loops generate() per IMAGE batch row; graph batch still saves
# submit/poll/LoadImage round-trips. Override with STILL_TAG_BATCH (1–32).
DEFAULT_STILL_TAG_BATCH = 8
SCHEDULE_BASENAME = "still_tag_schedule.json"
DEFAULT_SCHEDULE: Dict[str, Any] = {
    "schema_version": 1,
    "enabled": False,
    "timezone": "America/New_York",
    # sla: attempt queued stills within max_wait_hours; clock: fixed local window.
    "mode": "sla",
    "max_wait_hours": 3,
    "manual_max_wait_hours": 1,
    "scan_interval_min": 15,
    "evaluate_interval_min": 15,
    "auto_enqueue_untagged": True,
    "auto_enqueue_limit": 96,
    "session_minutes": 15,
    "kill_after_min": 60,
    "resume_gap_min": 20,
    "sec_per_still": 12,
    "occupy_gpu": True,
    "window_start": "03:00",
    "window_duration_min": 15,
    "front": True,
    "max_inflight": 1,
    "max_items_per_tick": 96,
    "comfy_server": None,
    "auto_drain_on_enqueue": False,
}
SESSION_BASENAME = "still_tag_session.json"
TICK_BASENAME = "still_tag_tick.json"
DEFAULT_SEC_PER_STILL = 12.0
MAX_COMFY_BATCH = 32
_SHA256_RE = re.compile(r"([0-9a-f]{64})", re.IGNORECASE)

_worker_lock = threading.Lock()
_worker_thread: Optional[threading.Thread] = None
_drain_thread: Optional[threading.Thread] = None


def _utc_now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def still_tag_batch_size(override: Optional[int] = None) -> int:
    if override is not None:
        try:
            return max(1, min(int(override), MAX_COMFY_BATCH))
        except (TypeError, ValueError):
            pass
    raw = str(os.environ.get("STILL_TAG_BATCH") or DEFAULT_STILL_TAG_BATCH).strip()
    try:
        n = int(raw)
    except ValueError:
        n = DEFAULT_STILL_TAG_BATCH
    return max(1, min(n, MAX_COMFY_BATCH))


def scale_batch_for_session(
    *,
    session_minutes: float = 15,
    sec_per_still: float = DEFAULT_SEC_PER_STILL,
    pending_count: Optional[int] = None,
) -> int:
    """Size one Comfy Florence batch to roughly fill the target window."""
    sec = max(2.0, float(sec_per_still or DEFAULT_SEC_PER_STILL))
    minutes = max(1.0, float(session_minutes or 15))
    fit = max(1, int((minutes * 60.0) / sec))
    batch = min(MAX_COMFY_BATCH, fit)
    if pending_count is not None:
        batch = min(batch, max(1, int(pending_count)))
    return max(1, batch)


def default_session_path(*, data_root: Optional[Path] = None) -> Path:
    root = Path(data_root) if data_root is not None else Path(".")
    return root / "shape_factory" / SESSION_BASENAME


def load_tag_session(*, data_root: Optional[Path] = None, path: Optional[Path] = None) -> Dict[str, Any]:
    p = Path(path) if path is not None else default_session_path(data_root=data_root)
    if not p.is_file():
        return {"status": "idle"}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"status": "idle"}
    return raw if isinstance(raw, dict) else {"status": "idle"}


def save_tag_session(
    session: Dict[str, Any],
    *,
    data_root: Optional[Path] = None,
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    p = Path(path) if path is not None else default_session_path(data_root=data_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(session)
    payload["updated_at"] = _utc_now_iso()
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(p)
    return payload


def default_tick_path(*, data_root: Optional[Path] = None) -> Path:
    root = Path(data_root) if data_root is not None else Path(".")
    return root / "shape_factory" / TICK_BASENAME


def load_tag_tick(*, data_root: Optional[Path] = None, path: Optional[Path] = None) -> Dict[str, Any]:
    p = Path(path) if path is not None else default_tick_path(data_root=data_root)
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def save_tag_tick(
    tick: Dict[str, Any],
    *,
    data_root: Optional[Path] = None,
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    p = Path(path) if path is not None else default_tick_path(data_root=data_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(tick)
    payload["updated_at"] = _utc_now_iso()
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(p)
    return payload


def interval_elapsed(
    last_iso: Any,
    minutes: float,
    *,
    now: Optional[_dt.datetime] = None,
) -> bool:
    """True when *last_iso* is missing or at least *minutes* old."""
    minutes = max(0.0, float(minutes or 0))
    if minutes <= 0:
        return True
    last = _parse_iso_ts(last_iso)
    if last is None:
        return True
    clock = now or _dt.datetime.now(tz=_dt.timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=_dt.timezone.utc)
    return (clock.astimezone(_dt.timezone.utc) - last).total_seconds() >= minutes * 60.0


def estimate_sec_per_still(
    *,
    data_root: Optional[Path] = None,
    fallback: float = DEFAULT_SEC_PER_STILL,
) -> float:
    """Median seconds/still from recent successful runs; ignore timeout-scale outliers."""
    if data_root is None:
        return max(2.0, float(fallback))
    db_path = default_db_path(data_root=data_root)
    if not db_path.is_file():
        return max(2.0, float(fallback))
    con = connect(db_path)
    try:
        rows = con.execute(
            """
            SELECT started_at, finished_at, done_count
            FROM still_tag_runs
            WHERE status='done' AND done_count > 0 AND started_at IS NOT NULL AND finished_at IS NOT NULL
            ORDER BY finished_at DESC
            LIMIT 8
            """
        ).fetchall()
    finally:
        con.close()
    samples: List[float] = []
    for row in rows:
        start = _parse_iso_ts(row["started_at"])
        end = _parse_iso_ts(row["finished_at"])
        done = int(row["done_count"] or 0)
        if start is None or end is None or done < 1:
            continue
        sec = (end - start).total_seconds() / float(done)
        if 2.0 <= sec <= 90.0:
            samples.append(sec)
    if not samples:
        return max(2.0, float(fallback))
    samples.sort()
    return samples[len(samples) // 2]


def _parse_iso_ts(raw: Any) -> Optional[_dt.datetime]:
    if raw in (None, ""):
        return None
    try:
        s = str(raw).strip().replace("Z", "+00:00")
        out = _dt.datetime.fromisoformat(s)
    except Exception:
        return None
    if out.tzinfo is None:
        out = out.replace(tzinfo=_dt.timezone.utc)
    return out.astimezone(_dt.timezone.utc)


def _schedule_mode(sch: Dict[str, Any]) -> str:
    mode = str(sch.get("mode") or "sla").strip().lower()
    return mode if mode in ("sla", "clock") else "sla"


def _schedule_float(sch: Dict[str, Any], key: str, default: float) -> float:
    try:
        return float(sch.get(key) if sch.get(key) is not None else default)
    except (TypeError, ValueError):
        return float(default)


def session_item_budget(
    *,
    session_minutes: float = 15,
    sec_per_still: float = DEFAULT_SEC_PER_STILL,
    cap: Optional[int] = None,
) -> int:
    """How many stills fit in the target window (uncapped except optional *cap*)."""
    sec = max(2.0, float(sec_per_still or DEFAULT_SEC_PER_STILL))
    minutes = max(1.0, float(session_minutes or 15))
    fit = max(1, int((minutes * 60.0) / sec))
    if cap is not None:
        try:
            fit = min(fit, max(1, int(cap)))
        except (TypeError, ValueError):
            pass
    return fit


def _is_dry_provider(provider: Optional[str]) -> bool:
    return str(provider or "").strip().lower() in {"dry-run", "dry_run"}


def _still_unreadable_reason(path: Path) -> Optional[str]:
    """Reject files that will make Comfy LoadImage fail the whole Florence batch."""
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            head = f.read(8)
    except OSError as e:
        return str(e)
    if size < 32:
        return f"truncated still ({size} bytes)"
    if head == b"\x89PNG\r\n\x1a\n":
        try:
            with path.open("rb") as f:
                f.seek(max(0, size - 32))
                tail = f.read()
            if b"IEND" not in tail:
                return "truncated PNG (missing IEND)"
        except OSError as e:
            return str(e)
    return None


def _is_comfy_ref_error(err: BaseException) -> bool:
    msg = str(err or "").lower()
    return (
        "400" in msg
        or "bad request" in msg
        or "loadimage" in msg
        or "invalid image file" in msg
    )


def _default_output_root() -> Path:
    return Path(os.environ.get("COMFYUI_BIND_OUTPUT_DIR") or "/home/yuji/comfyui-runpod-data/output")


def session_is_stale(
    session: Dict[str, Any],
    *,
    now: Optional[_dt.datetime] = None,
    stale_after_min: float = 90,
) -> bool:
    status = str(session.get("status") or "idle").strip().lower()
    if status not in {"occupying", "running", "releasing"}:
        return False
    stamp = _parse_iso_ts(session.get("updated_at") or session.get("started_at"))
    if stamp is None:
        return True
    clock = now or _dt.datetime.now(tz=_dt.timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=_dt.timezone.utc)
    return (clock - stamp).total_seconds() >= float(stale_after_min) * 60.0


def _scope_from_raw(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        obj = json.loads(str(raw))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _scope_is_manual(scope: Optional[Dict[str, Any]]) -> bool:
    sch = scope or {}
    if sch.get("manual") is True:
        return True
    if sch.get("manual") is False:
        return False
    return str(sch.get("request") or "").strip().lower() == "manual"


def run_sla_hours(scope: Optional[Dict[str, Any]], schedule: Optional[Dict[str, Any]] = None) -> float:
    sch = dict(schedule or DEFAULT_SCHEDULE)
    if _scope_is_manual(scope):
        return max(0.25, _schedule_float(sch, "manual_max_wait_hours", 1))
    return max(0.25, _schedule_float(sch, "max_wait_hours", 3))


def infer_manual_request(
    *,
    content_ids: Optional[Sequence[str]] = None,
    collection_id: Optional[str] = None,
    manual: Optional[bool] = None,
) -> bool:
    if manual is not None:
        return bool(manual)
    if collection_id and str(collection_id).strip():
        return True
    return bool(content_ids)


def sla_due_status(
    *,
    schedule: Dict[str, Any],
    backlog: Optional[Dict[str, Any]] = None,
    session: Optional[Dict[str, Any]] = None,
    now: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    """Whether an SLA session should start (or continue) for queued stills."""
    sch = dict(schedule or {})
    enabled = bool(sch.get("enabled"))
    clock = now or _dt.datetime.now(tz=_dt.timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=_dt.timezone.utc)
    clock = clock.astimezone(_dt.timezone.utc)
    sess = dict(session or {"status": "idle"})
    stats = dict(backlog or {})
    queued_runs = int(stats.get("queued_runs") or 0)
    queued_targets = int(stats.get("queued_targets") or 0)
    running_runs = int(stats.get("running_runs") or 0)
    oldest = _parse_iso_ts(stats.get("oldest_queued_at"))
    oldest_manual = _parse_iso_ts(stats.get("oldest_manual_queued_at"))
    oldest_backlog = _parse_iso_ts(stats.get("oldest_backlog_queued_at"))
    wait_hours = None
    if oldest is not None:
        wait_hours = max(0.0, (clock - oldest).total_seconds() / 3600.0)
    bulk_max = max(0.25, _schedule_float(sch, "max_wait_hours", 3))
    manual_max = max(0.25, _schedule_float(sch, "manual_max_wait_hours", 1))
    candidates: List[Tuple[str, float, float]] = []
    if oldest_manual is not None:
        candidates.append(
            ("manual", max(0.0, (clock - oldest_manual).total_seconds() / 3600.0), manual_max)
        )
    if oldest_backlog is not None:
        candidates.append(
            ("backlog", max(0.0, (clock - oldest_backlog).total_seconds() / 3600.0), bulk_max)
        )
    elif oldest is not None and oldest_manual is None:
        candidates.append(("backlog", wait_hours or 0.0, bulk_max))
    sla_class = "backlog"
    max_wait = bulk_max
    if candidates:
        sla_class, wait_hours, max_wait = max(candidates, key=lambda c: c[1] / c[2] if c[2] else 0.0)
    gap_min = max(0.0, _schedule_float(sch, "resume_gap_min", 20))
    sess_status = str(sess.get("status") or "idle").strip().lower()
    stale = session_is_stale(sess, now=clock)

    base = {
        "wait_hours": wait_hours,
        "max_wait_hours": max_wait,
        "manual_max_wait_hours": manual_max,
        "sla_class": sla_class,
        "queued_runs": queued_runs,
        "queued_targets": queued_targets,
        "session_status": sess_status,
        "stale_session": stale,
    }
    if not enabled:
        return {"due": False, "reason": "disabled", **base}
    if sess_status in {"occupying", "running"} and not stale:
        return {"due": True, "reason": "session_active", **base, "stale_session": False}
    if queued_targets < 1 and running_runs < 1:
        return {"due": False, "reason": "no_backlog", **base}

    last_end = _parse_iso_ts(sess.get("ended_at"))
    gap_ok = True
    gap_remaining_min = 0.0
    if last_end is not None and gap_min > 0:
        elapsed = (clock - last_end).total_seconds()
        gap_ok = elapsed >= gap_min * 60.0
        if not gap_ok:
            gap_remaining_min = max(0.0, (gap_min * 60.0 - elapsed) / 60.0)

    sla_hit = wait_hours is not None and wait_hours >= max_wait
    overdue = wait_hours is not None and wait_hours >= max_wait * 1.1
    if sla_hit and (gap_ok or overdue):
        return {
            "due": True,
            "reason": "sla_due",
            **base,
            "gap_remaining_min": 0.0 if gap_ok or overdue else gap_remaining_min,
        }
    if not gap_ok:
        return {
            "due": False,
            "reason": "resume_gap",
            **base,
            "gap_remaining_min": gap_remaining_min,
        }
    return {"due": False, "reason": "waiting_sla", **base}


class _TagJobKilled(Exception):
    """Raised when a Florence job is interrupted after kill_after_min."""


def interrupt_comfy_tag_job(server: str, prompt_id: Optional[str] = None) -> Dict[str, Any]:
    """Stop an in-flight Comfy prompt so a stuck Florence batch cannot run past the hard cap."""
    from vision_slice_runner import _http_json  # type: ignore

    server = str(server or DEFAULT_COMFY_SERVER).rstrip("/")
    out: Dict[str, Any] = {"server": server}
    try:
        _http_json("POST", f"{server}/interrupt", timeout_s=10)
        out["interrupt"] = True
    except Exception as e:
        out["interrupt"] = False
        out["interrupt_error"] = str(e)
    if prompt_id:
        try:
            _http_json("POST", f"{server}/queue", {"delete": [str(prompt_id)]}, timeout_s=10)
            out["deleted"] = str(prompt_id)
        except Exception as e:
            out["delete_error"] = str(e)
    return out


def occupy_gpu_for_tagging(
    *,
    data_root: Path,
    comfy_server: str,
    output_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Pause hourlies and park Comfy/ledger so Florence can own the GPU."""
    from suspend_comfy_queue import do_suspend, set_hourlies_enabled  # type: ignore

    hourly_was_enabled = False
    try:
        from shape_factory_hourly import load_hourly_schedule  # type: ignore

        hourly_was_enabled = bool(load_hourly_schedule(data_root=data_root).get("enabled"))
    except Exception:
        hourly_was_enabled = False
    hourly_out: Dict[str, Any] = {}
    if hourly_was_enabled:
        hourly_out = set_hourlies_enabled(enabled=False, data_root=data_root)
    out_root = Path(output_root) if output_root is not None else _default_output_root()
    suspend = do_suspend(
        server=str(comfy_server).rstrip("/"),
        data_root=Path(data_root),
        output_root=out_root,
    )
    return {
        "ok": bool(suspend.get("ok")),
        "hourly_was_enabled": hourly_was_enabled,
        "hourly": hourly_out,
        "suspend": suspend,
    }


def release_gpu_after_tagging(
    *,
    data_root: Path,
    hourly_was_enabled: bool = True,
    output_root: Optional[Path] = None,
) -> Dict[str, Any]:
    from suspend_comfy_queue import do_resume, set_hourlies_enabled  # type: ignore

    out_root = Path(output_root) if output_root is not None else _default_output_root()
    resume = do_resume(output_root=out_root, feeders=True)
    hourly_out: Dict[str, Any] = {}
    if hourly_was_enabled:
        hourly_out = set_hourlies_enabled(enabled=True, data_root=data_root)
    return {"ok": bool(resume.get("ok")), "resume": resume, "hourly": hourly_out}


def still_tag_image_mode() -> str:
    mode = str(os.environ.get("STILL_TAG_IMAGE_MODE") or "input_ref").strip().lower()
    if mode in ("upload", "input_copy", "input_ref"):
        return mode
    return "input_ref"


def still_tag_poll_interval_s() -> float:
    raw = str(os.environ.get("STILL_TAG_POLL_S") or "0.15").strip()
    try:
        v = float(raw)
    except ValueError:
        v = 0.15
    return max(0.05, min(v, 5.0))


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _json_loads_list(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if not raw:
        return []
    try:
        val = json.loads(str(raw))
    except Exception:
        return []
    if not isinstance(val, list):
        return []
    return [str(x).strip() for x in val if str(x).strip()]


def _dedupe(tags: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for t in tags:
        n = str(t or "").strip().lower()
        if not n or n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def extract_content_id(path: str) -> Optional[str]:
    m = _SHA256_RE.search(Path(str(path or "")).name)
    return m.group(1).lower() if m else None


def default_db_path(*, data_root: Optional[Path] = None) -> Path:
    env = os.environ.get("STILL_TAGS_DB_PATH", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if data_root is None:
        repo = Path(__file__).resolve().parents[2]
        data_root = Path(os.environ.get("SHAPE_FACTORY_DATA_ROOT") or (repo / ".data"))
    return Path(data_root).expanduser().resolve() / "shape_factory" / DB_BASENAME


def default_pin_path(*, status_dir: Optional[Path] = None) -> Path:
    env = os.environ.get("VISION_V3A_TAG_PIN_PATH", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if status_dir is not None:
        return Path(status_dir).expanduser().resolve() / "vision_v3a_tag_pin.json"
    for cand in (
        Path("/home/yuji/comfyui-runpod-data/output/_status/vision_v3a_tag_pin.json"),
        Path("/workspace/output/_status/vision_v3a_tag_pin.json"),
    ):
        if cand.is_file():
            return cand
    return Path("/home/yuji/comfyui-runpod-data/output/_status/vision_v3a_tag_pin.json")


def load_pin(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or default_pin_path()
    out: Dict[str, Any] = {
        "model_pin": DEFAULT_MODEL_PIN,
        "pin_policy": DEFAULT_PIN_POLICY,
        "fp_blocklist": [],
        "path": str(p),
    }
    env_policy = os.environ.get("STILL_TAG_PIN_POLICY", "").strip()
    if env_policy:
        out["pin_policy"] = env_policy
    if not p.is_file():
        return out
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return out
    # Still tagger defaults to x2 PromptGen-large; pin file still supplies FP blocklist.
    # Override with STILL_TAG_PIN_POLICY if set; otherwise keep DEFAULT_PIN_POLICY.
    if not env_policy:
        out["pin_policy"] = DEFAULT_PIN_POLICY
    out["model_pin"] = DEFAULT_MODEL_PIN
    out["fp_blocklist"] = _dedupe([str(x) for x in (doc.get("fp_blocklist") or [])])
    out["path"] = str(p)
    return out


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path), timeout=60.0, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS still_tag_items (
          content_id TEXT PRIMARY KEY,
          editorial_tags TEXT NOT NULL DEFAULT '[]',
          note TEXT,
          provisional_tags TEXT NOT NULL DEFAULT '[]',
          provisional_model_pin TEXT,
          provisional_pin_policy TEXT,
          provisional_run_id TEXT,
          provisional_tagged_at TEXT,
          provisional_raw_caption TEXT,
          suppressed_tags TEXT NOT NULL DEFAULT '[]',
          queue_run_id TEXT,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS still_tag_runs (
          run_id TEXT PRIMARY KEY,
          status TEXT NOT NULL,
          scope_json TEXT NOT NULL,
          enqueued_at TEXT NOT NULL,
          started_at TEXT,
          finished_at TEXT,
          total INT NOT NULL DEFAULT 0,
          done_count INT NOT NULL DEFAULT 0,
          error_count INT NOT NULL DEFAULT 0,
          skipped_count INT NOT NULL DEFAULT 0,
          pin_policy TEXT,
          model_pin TEXT,
          provider TEXT,
          comfy_server TEXT,
          detail TEXT
        );
        CREATE TABLE IF NOT EXISTS still_tag_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id TEXT NOT NULL,
          ts TEXT NOT NULL,
          kind TEXT NOT NULL,
          content_id TEXT,
          message TEXT,
          payload_json TEXT
        );
        CREATE INDEX IF NOT EXISTS still_tag_events_run ON still_tag_events(run_id, id);
        """
    )
    _apply_still_tag_migrations(con)
    con.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    con.commit()


def _still_tag_item_columns(con: sqlite3.Connection) -> set[str]:
    return {str(row[1]) for row in con.execute("PRAGMA table_info(still_tag_items)")}


def _row_has_provisional(row: sqlite3.Row) -> bool:
    return bool(_json_loads_list(row["provisional_tags"]))


def tag_status_from_row(row: sqlite3.Row) -> str:
    """Per-still tag lifecycle: untagged → queued (reserved) → done."""
    if _row_has_provisional(row):
        return "done"
    if "queue_run_id" in row.keys() and str(row["queue_run_id"] or "").strip():
        return "queued"
    return "untagged"


def active_tag_run_ids(con: sqlite3.Connection) -> set[str]:
    rows = con.execute(
        "SELECT run_id FROM still_tag_runs WHERE status IN ('queued', 'running')"
    ).fetchall()
    return {str(r["run_id"]) for r in rows if r and r["run_id"]}


def _apply_still_tag_migrations(con: sqlite3.Connection) -> None:
    cols = _still_tag_item_columns(con)
    if "queue_run_id" not in cols:
        con.execute("ALTER TABLE still_tag_items ADD COLUMN queue_run_id TEXT")
    con.execute(
        "CREATE INDEX IF NOT EXISTS still_tag_items_queue_run ON still_tag_items(queue_run_id)"
    )
    reconcile_still_tag_reservations(con)


def reserve_still_tag_items(
    con: sqlite3.Connection,
    run_id: str,
    content_ids: Sequence[str],
) -> int:
    """Mark stills as reserved for a queued/running batch."""
    want = str(run_id or "").strip()
    if not want:
        return 0
    now = _utc_now_iso()
    n = 0
    for raw in content_ids:
        cid = str(raw or "").strip().lower()
        if not cid:
            continue
        row = con.execute(
            "SELECT content_id, provisional_tags FROM still_tag_items WHERE content_id=?",
            (cid,),
        ).fetchone()
        if row and _row_has_provisional(row):
            continue
        if row:
            con.execute(
                "UPDATE still_tag_items SET queue_run_id=?, updated_at=? WHERE content_id=?",
                (want, now, cid),
            )
        else:
            con.execute(
                """
                INSERT INTO still_tag_items(
                  content_id, editorial_tags, note, provisional_tags, suppressed_tags,
                  queue_run_id, updated_at
                ) VALUES (?, '[]', NULL, '[]', '[]', ?, ?)
                """,
                (cid, want, now),
            )
        n += 1
    return n


def cancel_empty_queued_runs(*, data_root: Path) -> int:
    """Cancel queued runs with no stills so they cannot occupy the GPU."""
    db_path = default_db_path(data_root=data_root)
    if not db_path.is_file():
        return 0
    con = connect(db_path)
    try:
        rows = con.execute(
            """
            SELECT run_id FROM still_tag_runs
            WHERE status='queued' AND (total IS NULL OR total<=0)
            """
        ).fetchall()
        n = 0
        now = _utc_now_iso()
        for row in rows:
            rid = str(row["run_id"])
            con.execute(
                """
                UPDATE still_tag_runs
                SET status=?, finished_at=?, detail=?
                WHERE run_id=? AND status='queued'
                """,
                ("cancelled", now, "empty enqueue (nothing left to reserve)", rid),
            )
            append_event(
                con,
                run_id=rid,
                kind="cancelled",
                message="empty enqueue (nothing left to reserve)",
            )
            n += 1
        if n:
            con.commit()
        return n
    finally:
        con.close()


def release_still_tag_reservations(con: sqlite3.Connection, run_id: str) -> int:
    """Drop queue reservation for stills that never received provisional tags."""
    want = str(run_id or "").strip()
    if not want:
        return 0
    cur = con.execute(
        """
        UPDATE still_tag_items
        SET queue_run_id=NULL, updated_at=?
        WHERE queue_run_id=?
          AND (provisional_tags IS NULL OR provisional_tags='[]')
        """,
        (_utc_now_iso(), want),
    )
    return int(cur.rowcount or 0)


def reconcile_still_tag_reservations(con: sqlite3.Connection) -> Dict[str, int]:
    """Align queue_run_id with active runs; clear stale reservations."""
    now = _utc_now_iso()
    cleared = con.execute(
        """
        UPDATE still_tag_items
        SET queue_run_id=NULL, updated_at=?
        WHERE queue_run_id IS NOT NULL
          AND queue_run_id NOT IN (
            SELECT run_id FROM still_tag_runs WHERE status IN ('queued', 'running')
          )
          AND (provisional_tags IS NULL OR provisional_tags='[]')
        """,
        (now,),
    ).rowcount
    backfilled = 0
    for row in con.execute(
        "SELECT run_id, scope_json FROM still_tag_runs WHERE status IN ('queued', 'running')"
    ):
        run_id = str(row["run_id"] or "").strip()
        if not run_id:
            continue
        try:
            scope = json.loads(str(row["scope_json"] or "{}"))
        except Exception:
            scope = {}
        cids = scope.get("content_ids") if isinstance(scope.get("content_ids"), list) else []
        backfilled += reserve_still_tag_items(con, run_id, cids)
    con.commit()
    return {"cleared_stale": int(cleared or 0), "backfilled": int(backfilled or 0)}


def ensure_db(db_path: Path) -> Path:
    con = connect(db_path)
    try:
        init_db(con)
    finally:
        con.close()
    return db_path


def migrate_editorial_from_json(db_path: Path, json_path: Path) -> int:
    """One-shot import of G1 input_still_tags.json editorial tags."""
    if not json_path.is_file():
        return 0
    try:
        doc = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    items = doc.get("items") if isinstance(doc.get("items"), dict) else {}
    if not items:
        return 0
    con = connect(db_path)
    try:
        init_db(con)
        n = 0
        now = _utc_now_iso()
        for cid, meta in items.items():
            key = str(cid or "").strip().lower()
            if not key:
                continue
            tags: List[str] = []
            note = None
            if isinstance(meta, dict):
                tags = _dedupe([str(t) for t in (meta.get("tags") or [])])
                note = str(meta.get("note") or "").strip() or None
            elif isinstance(meta, list):
                tags = _dedupe([str(t) for t in meta])
            row = con.execute("SELECT content_id FROM still_tag_items WHERE content_id=?", (key,)).fetchone()
            if row:
                continue
            con.execute(
                """
                INSERT INTO still_tag_items(
                  content_id, editorial_tags, note, provisional_tags, suppressed_tags, updated_at
                ) VALUES (?, ?, ?, '[]', '[]', ?)
                """,
                (key, _json_dumps(tags), note, now),
            )
            n += 1
        con.commit()
        return n
    finally:
        con.close()


def rekey_items_to_byte_hash(*, data_root: Path) -> Dict[str, int]:
    """Move tag rows whose key is a filename hex onto sha256(bytes)."""
    from still_identity import load_identity  # type: ignore

    ident = load_identity(data_root)
    if ident is None:
        return {"moved": 0, "merged": 0, "already_hashed": 0, "unmapped": 0}
    db_path = default_db_path(data_root=data_root)
    ensure_db(db_path)
    moved = merged = already = unmapped = 0
    con = connect(db_path)
    try:
        now = _utc_now_iso()
        rows = list(con.execute("SELECT * FROM still_tag_items"))
        for row in rows:
            old = str(row["content_id"] or "").strip().lower()
            if not old:
                continue
            if old in ident.canonical:
                already += 1
                continue
            real = ident.by_name_hex.get(old)
            if not real or real == old:
                unmapped += 1
                continue
            dest = con.execute("SELECT * FROM still_tag_items WHERE content_id=?", (real,)).fetchone()
            if dest is None:
                con.execute(
                    "UPDATE still_tag_items SET content_id=?, updated_at=? WHERE content_id=?",
                    (real, now, old),
                )
                moved += 1
                continue
            editorial = _dedupe(
                _json_loads_list(dest["editorial_tags"]) + _json_loads_list(row["editorial_tags"])
            )
            dest_prov = _json_loads_list(dest["provisional_tags"])
            src_prov = _json_loads_list(row["provisional_tags"])
            use_src_prov = not dest_prov and bool(src_prov)
            note = str(dest["note"] or "").strip() or str(row["note"] or "").strip() or None
            con.execute(
                """
                UPDATE still_tag_items SET
                  editorial_tags=?,
                  note=?,
                  provisional_tags=?,
                  provisional_model_pin=?,
                  provisional_pin_policy=?,
                  provisional_run_id=?,
                  provisional_tagged_at=?,
                  provisional_raw_caption=?,
                  suppressed_tags=?,
                  updated_at=?
                WHERE content_id=?
                """,
                (
                    _json_dumps(editorial),
                    note,
                    dest["provisional_tags"] if not use_src_prov else row["provisional_tags"],
                    dest["provisional_model_pin"] if not use_src_prov else row["provisional_model_pin"],
                    dest["provisional_pin_policy"] if not use_src_prov else row["provisional_pin_policy"],
                    dest["provisional_run_id"] if not use_src_prov else row["provisional_run_id"],
                    dest["provisional_tagged_at"] if not use_src_prov else row["provisional_tagged_at"],
                    dest["provisional_raw_caption"] if not use_src_prov else row["provisional_raw_caption"],
                    _json_dumps(
                        _dedupe(
                            _json_loads_list(dest["suppressed_tags"])
                            + _json_loads_list(row["suppressed_tags"])
                        )
                    ),
                    now,
                    real,
                ),
            )
            con.execute("DELETE FROM still_tag_items WHERE content_id=?", (old,))
            merged += 1
        con.commit()
    finally:
        con.close()
    return {"moved": moved, "merged": merged, "already_hashed": already, "unmapped": unmapped}


def effective_tags_for_row(row: sqlite3.Row, *, fp_blocklist: Optional[Sequence[str]] = None) -> List[str]:
    editorial = _json_loads_list(row["editorial_tags"])
    provisional = _json_loads_list(row["provisional_tags"])
    suppressed = set(_json_loads_list(row["suppressed_tags"]))
    fp = set(_dedupe(list(fp_blocklist or [])))
    out: List[str] = []
    seen: set[str] = set()
    for t in editorial:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    for t in provisional:
        if t in seen or t in suppressed or t in fp:
            continue
        seen.add(t)
        out.append(t)
    return out


def get_item(con: sqlite3.Connection, content_id: str) -> Optional[Dict[str, Any]]:
    cid = str(content_id or "").strip().lower()
    if not cid:
        return None
    row = con.execute("SELECT * FROM still_tag_items WHERE content_id=?", (cid,)).fetchone()
    if not row:
        return None
    return _item_dict(row)


def _item_dict(row: sqlite3.Row, *, fp_blocklist: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    editorial = _json_loads_list(row["editorial_tags"])
    provisional = _json_loads_list(row["provisional_tags"])
    suppressed = _json_loads_list(row["suppressed_tags"])
    queue_run_id = str(row["queue_run_id"] or "").strip() or None if "queue_run_id" in row.keys() else None
    return {
        "content_id": row["content_id"],
        "editorial_tags": editorial,
        "tags": editorial,  # alias for G1 UI
        "note": row["note"],
        "provisional_tags": provisional,
        "provisional_model_pin": row["provisional_model_pin"],
        "provisional_pin_policy": row["provisional_pin_policy"],
        "provisional_run_id": row["provisional_run_id"],
        "provisional_tagged_at": row["provisional_tagged_at"],
        "suppressed_tags": suppressed,
        "queue_run_id": queue_run_id,
        "tag_status": tag_status_from_row(row),
        "effective_tags": effective_tags_for_row(row, fp_blocklist=fp_blocklist),
        "updated_at": row["updated_at"],
    }


def upsert_editorial(
    con: sqlite3.Connection,
    *,
    content_id: str,
    tags: Optional[Sequence[str]] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    cid = str(content_id or "").strip().lower()
    if not cid:
        raise ValueError("missing_content_id")
    now = _utc_now_iso()
    cur = con.execute("SELECT * FROM still_tag_items WHERE content_id=?", (cid,)).fetchone()
    if cur:
        next_tags = _dedupe(tags) if tags is not None else _json_loads_list(cur["editorial_tags"])
        next_note = str(note).strip() if note is not None else cur["note"]
        con.execute(
            """
            UPDATE still_tag_items
            SET editorial_tags=?, note=?, updated_at=?
            WHERE content_id=?
            """,
            (_json_dumps(next_tags), (str(next_note).strip() or None) if next_note else None, now, cid),
        )
    else:
        next_tags = _dedupe(tags or [])
        next_note = str(note).strip() if note is not None else None
        con.execute(
            """
            INSERT INTO still_tag_items(
              content_id, editorial_tags, note, provisional_tags, suppressed_tags, updated_at
            ) VALUES (?, ?, ?, '[]', '[]', ?)
            """,
            (cid, _json_dumps(next_tags), next_note or None, now),
        )
    con.commit()
    return get_item(con, cid) or {}


def upsert_provisional(
    con: sqlite3.Connection,
    *,
    content_id: str,
    tags: Sequence[str],
    model_pin: str,
    pin_policy: str,
    run_id: str,
    raw_caption: Optional[str] = None,
) -> None:
    cid = str(content_id or "").strip().lower()
    if not cid:
        raise ValueError("missing_content_id")
    now = _utc_now_iso()
    tags_j = _json_dumps(_dedupe(tags))
    cur = con.execute("SELECT content_id FROM still_tag_items WHERE content_id=?", (cid,)).fetchone()
    if cur:
        con.execute(
            """
            UPDATE still_tag_items SET
              provisional_tags=?,
              provisional_model_pin=?,
              provisional_pin_policy=?,
              provisional_run_id=?,
              provisional_tagged_at=?,
              provisional_raw_caption=?,
              queue_run_id=NULL,
              updated_at=?
            WHERE content_id=?
            """,
            (tags_j, model_pin, pin_policy, run_id, now, raw_caption, now, cid),
        )
    else:
        con.execute(
            """
            INSERT INTO still_tag_items(
              content_id, editorial_tags, note, provisional_tags,
              provisional_model_pin, provisional_pin_policy, provisional_run_id,
              provisional_tagged_at, provisional_raw_caption, suppressed_tags, updated_at
            ) VALUES (?, '[]', NULL, ?, ?, ?, ?, ?, ?, '[]', ?)
            """,
            (cid, tags_j, model_pin, pin_policy, run_id, now, raw_caption, now),
        )
    con.commit()


def append_event(
    con: sqlite3.Connection,
    *,
    run_id: str,
    kind: str,
    content_id: Optional[str] = None,
    message: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> int:
    cur = con.execute(
        """
        INSERT INTO still_tag_events(run_id, ts, kind, content_id, message, payload_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            _utc_now_iso(),
            kind,
            content_id,
            message,
            _json_dumps(payload) if payload is not None else None,
        ),
    )
    con.commit()
    return int(cur.lastrowid)


def get_run(con: sqlite3.Connection, run_id: str) -> Optional[Dict[str, Any]]:
    row = con.execute("SELECT * FROM still_tag_runs WHERE run_id=?", (run_id,)).fetchone()
    if not row:
        return None
    return {
        "run_id": row["run_id"],
        "status": row["status"],
        "scope": json.loads(row["scope_json"] or "{}"),
        "enqueued_at": row["enqueued_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "total": int(row["total"] or 0),
        "done_count": int(row["done_count"] or 0),
        "error_count": int(row["error_count"] or 0),
        "skipped_count": int(row["skipped_count"] or 0),
        "pin_policy": row["pin_policy"],
        "model_pin": row["model_pin"],
        "provider": row["provider"],
        "comfy_server": row["comfy_server"],
        "detail": row["detail"],
    }


_STILL_TAG_STUB_COLS = (
    "run_id, status, enqueued_at, started_at, finished_at, total, done_count, "
    "error_count, skipped_count, pin_policy, model_pin, provider, detail"
)


def list_recent_still_tag_runs(
    *,
    data_root: Path,
    limit: int = 30,
    statuses: Optional[Sequence[str]] = None,
    include_scope: bool = True,
) -> List[Dict[str, Any]]:
    """Recent still-tag runs for Workbench (newest enqueue first).

    ``include_scope=False`` skips parsing ``scope_json`` (often thousands of
    targets). Workbench list stubs only need counts + status.
    """
    db_path = default_db_path(data_root=data_root)
    if not db_path.is_file():
        return []
    allowed = tuple(statuses or ("queued", "running", "done", "error"))
    placeholders = ",".join("?" for _ in allowed)
    lim = max(1, min(200, int(limit)))
    select = "SELECT *" if include_scope else f"SELECT {_STILL_TAG_STUB_COLS}"
    con = connect(db_path)
    try:
        rows = con.execute(
            f"""
            {select} FROM still_tag_runs
            WHERE status IN ({placeholders})
            ORDER BY enqueued_at DESC
            LIMIT ?
            """,
            (*allowed, lim),
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            if include_scope:
                run = get_run(con, str(r["run_id"]))
                if run:
                    out.append(run)
                continue
            out.append(
                {
                    "run_id": r["run_id"],
                    "status": r["status"],
                    "enqueued_at": r["enqueued_at"],
                    "started_at": r["started_at"],
                    "finished_at": r["finished_at"],
                    "total": int(r["total"] or 0),
                    "done_count": int(r["done_count"] or 0),
                    "error_count": int(r["error_count"] or 0),
                    "skipped_count": int(r["skipped_count"] or 0),
                    "pin_policy": r["pin_policy"],
                    "model_pin": r["model_pin"],
                    "provider": r["provider"],
                    "detail": r["detail"],
                }
            )
        return out
    finally:
        con.close()


def still_tag_run_workbench_status(run_status: str) -> str:
    """Map still_tag_runs.status → Workbench work-product status."""
    s = str(run_status or "").strip().lower()
    if s == "queued":
        return "pending"
    if s == "running":
        return "running"
    if s == "done":
        return "complete"
    if s == "error":
        return "error"
    return "pending"


def list_events(
    con: sqlite3.Connection, *, run_id: str, after_id: int = 0, limit: int = 200
) -> List[Dict[str, Any]]:
    rows = con.execute(
        """
        SELECT id, run_id, ts, kind, content_id, message, payload_json
        FROM still_tag_events
        WHERE run_id=? AND id>?
        ORDER BY id ASC
        LIMIT ?
        """,
        (run_id, int(after_id or 0), max(1, min(2000, int(limit or 200)))),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        payload = None
        if r["payload_json"]:
            try:
                payload = json.loads(r["payload_json"])
            except Exception:
                payload = None
        out.append(
            {
                "id": int(r["id"]),
                "run_id": r["run_id"],
                "ts": r["ts"],
                "kind": r["kind"],
                "content_id": r["content_id"],
                "message": r["message"],
                "payload": payload,
            }
        )
    return out


def _still_tag_file_url(relpath: Optional[str]) -> Optional[str]:
    import urllib.parse

    rel = str(relpath or "").strip().replace("\\", "/").lstrip("/")
    if not rel:
        return None
    if not rel.lower().startswith("input/"):
        rel = "input/" + rel
    return "/files/" + urllib.parse.quote(rel)


def list_still_tag_run_results(
    con: sqlite3.Connection,
    *,
    run_id: str,
) -> List[Dict[str, Any]]:
    """Per-still rows for a batch: image path, tags, and errors from events + SQLite."""
    run = get_run(con, run_id)
    if not run:
        return []
    scope = run.get("scope") if isinstance(run.get("scope"), dict) else {}
    targets = [t for t in (scope.get("targets") or []) if isinstance(t, dict)]
    events = list_events(con, run_id=run_id, after_id=0, limit=2000)
    by_cid: Dict[str, List[Dict[str, Any]]] = {}
    for ev in events:
        cid = str(ev.get("content_id") or "").strip().lower()
        if not cid:
            continue
        by_cid.setdefault(cid, []).append(ev)

    out: List[Dict[str, Any]] = []
    for raw in targets:
        cid = str(raw.get("content_id") or "").strip().lower()
        if not cid:
            continue
        missing = bool(raw.get("missing"))
        relpath = str(raw.get("relpath") or "").strip().replace("\\", "/")
        if relpath and not relpath.lower().startswith("input/"):
            relpath = f"input/{relpath.lstrip('/')}"

        item_meta = get_item(con, cid)
        prov_tags = list(item_meta.get("provisional_tags") or []) if item_meta else []
        editorial_tags = list(item_meta.get("editorial_tags") or []) if item_meta else []
        effective_tags = list(item_meta.get("effective_tags") or []) if item_meta else []
        tagged_at = (
            str(item_meta.get("provisional_tagged_at") or "").strip() or None if item_meta else None
        )

        evs = by_cid.get(cid) or []
        last_done: Optional[Dict[str, Any]] = None
        last_err: Optional[Dict[str, Any]] = None
        for ev in evs:
            if ev.get("kind") == "item_done":
                last_done = ev
            elif ev.get("kind") == "item_error":
                last_err = ev

        tags: List[str] = []
        if last_done and isinstance(last_done.get("payload"), dict):
            raw_tags = last_done["payload"].get("tags")
            if isinstance(raw_tags, list):
                tags = [str(t) for t in raw_tags if str(t).strip()]
        if not tags and prov_tags:
            tags = prov_tags

        error_message: Optional[str] = None
        warning: Optional[str] = None
        if missing:
            status = "missing"
            error_message = "Still file missing on disk"
        elif tags:
            status = "done"
            if last_err:
                warning = str(last_err.get("message") or "").strip() or None
        elif last_err:
            status = "error"
            error_message = str(last_err.get("message") or "").strip() or "tag failed"
        else:
            run_status = str(run.get("status") or "").strip().lower()
            status = "pending" if run_status in {"queued", "running"} else "pending"

        display_tags = tags or prov_tags
        if not effective_tags and display_tags:
            effective_tags = display_tags
        out.append(
            {
                "content_id": cid,
                "relpath": relpath or None,
                "url": _still_tag_file_url(relpath),
                "missing": missing,
                "status": status,
                "tags": display_tags,
                "provisional_tags": prov_tags,
                "editorial_tags": editorial_tags,
                "effective_tags": effective_tags,
                "tag_count": len(effective_tags or display_tags),
                "error_message": error_message,
                "warning": warning,
                "tagged_at": (last_done.get("ts") if last_done else None) or tagged_at,
            }
        )
    return out


def _parse_created_at_ts(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return _dt.datetime.fromisoformat(text).timestamp()
    except Exception:
        return None


def _target_recency_ts(data_root: Path, content_id: str) -> float:
    """Newest-first ordering for tag drain (catalog first_seen, then mtime)."""
    from input_still_catalog import default_catalog_path  # type: ignore

    cid = str(content_id or "").strip().lower()
    if not cid:
        return 0.0
    cat = default_catalog_path(data_root=data_root)
    if not cat.is_file():
        return 0.0
    con_cat = sqlite3.connect(str(cat), timeout=30.0)
    try:
        row = con_cat.execute(
            "SELECT first_seen, mtime FROM stills WHERE lower(path) LIKE ? LIMIT 1",
            (f"%{cid}%",),
        ).fetchone()
    finally:
        con_cat.close()
    if not row:
        return 0.0
    ts = _parse_created_at_ts(row[0])
    if ts is not None:
        return ts
    try:
        return float(row[1] or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _sort_targets_newest_first(
    targets: Sequence[Dict[str, Any]],
    *,
    data_root: Path,
) -> List[Dict[str, Any]]:
    rows = [t for t in targets if isinstance(t, dict)]
    return sorted(
        rows,
        key=lambda t: _target_recency_ts(data_root, str(t.get("content_id") or "")),
        reverse=True,
    )


def current_still_tag_target(
    scope: Dict[str, Any],
    *,
    data_root: Path,
    done_count: int = 0,
    content_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Target still for Workbench live preview.

    When ``content_id`` is set (from a live Florence LoadImage), use that still.
    Otherwise use the next target after ``done_count`` in newest-first drain order.
    """
    targets = [
        t
        for t in (scope.get("targets") or [])
        if isinstance(t, dict) and not t.get("missing")
    ]
    if not targets:
        return None
    ordered = _sort_targets_newest_first(targets, data_root=data_root)
    cid = str(content_id or "").strip().lower()
    if cid:
        for t in ordered:
            if str(t.get("content_id") or "").strip().lower() == cid:
                return t
    idx = min(max(0, int(done_count or 0)), len(ordered) - 1)
    return ordered[idx]


def content_ids_available_for_tagging(
    con: sqlite3.Connection,
    candidates: Sequence[str],
    *,
    only_missing: bool = True,
    force: bool = False,
) -> List[str]:
    """Filter catalog candidates: skip tagged stills and those reserved in active batches."""
    active = active_tag_run_ids(con)
    out: List[str] = []
    for cid in candidates:
        key = str(cid or "").strip().lower()
        if not key:
            continue
        if force:
            out.append(key)
            continue
        row = con.execute(
            "SELECT provisional_tags, queue_run_id FROM still_tag_items WHERE content_id=?",
            (key,),
        ).fetchone()
        if only_missing and row and _row_has_provisional(row):
            continue
        if only_missing and row:
            qrun = str(row["queue_run_id"] or "").strip()
            if qrun and qrun in active:
                continue
        out.append(key)
    return out


def content_ids_missing_provisional(con: sqlite3.Connection, candidates: Sequence[str]) -> List[str]:
    return content_ids_available_for_tagging(con, candidates, only_missing=True, force=False)


def _catalog_newest_content_ids(data_root: Path, *, limit: int = 400) -> List[str]:
    from input_still_catalog import default_catalog_path  # type: ignore

    cat = default_catalog_path(data_root=data_root)
    if not cat.is_file():
        return []
    lim = max(1, int(limit))
    con_cat = sqlite3.connect(str(cat), timeout=30.0)
    try:
        rows = con_cat.execute(
            "SELECT path FROM stills ORDER BY first_seen DESC, mtime DESC LIMIT ?",
            (lim,),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con_cat.close()
    out: List[str] = []
    seen: set[str] = set()
    for (path,) in rows:
        cid = extract_content_id(str(path))
        if cid and cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


def _ensure_still_under_comfy_input(
    path: Path,
    *,
    data_root: Path,
    input_root: Optional[Path] = None,
) -> Path:
    """Hardlink/copy *path* into Comfy's input bind when it lives under output/.

    LoadImage only sees the input root. Factory jobs already stage through
    ``input/_factory/<content_id><ext>``; tagging must do the same.
    """
    from input_still_catalog import default_input_root  # type: ignore

    src = Path(path).expanduser()
    try:
        src = src.resolve()
    except OSError:
        pass
    root = Path(input_root or default_input_root()).expanduser()
    try:
        root = root.resolve()
        if src.is_relative_to(root):
            return src
    except (ValueError, OSError):
        pass
    from shape_factory import (  # type: ignore
        _resolve_load_image_stage_root,
        stage_load_image_for_comfy,
    )

    stage_root = root if root.is_dir() else _resolve_load_image_stage_root(Path(data_root))
    widget, _warn = stage_load_image_for_comfy(src, stage_root)
    dest = stage_root / widget
    try:
        if dest.is_file():
            return dest.resolve()
    except OSError:
        pass
    return src


def resolve_targets(
    *,
    data_root: Path,
    content_ids: Optional[Sequence[str]] = None,
    collection_id: Optional[str] = None,
    only_missing: bool = True,
    limit: int = DEFAULT_LIMIT,
    force: bool = False,
) -> List[Dict[str, Any]]:
    """
    Return list of {content_id, path, relpath} to tag.
    """
    from input_still_catalog import (  # type: ignore
        default_catalog_path,
        default_input_root,
        resolve_catalog_still_path,
        still_relpath_for_comfy,
    )
    from shape_factory_input_curation import load_collections  # type: ignore

    lim = max(1, int(limit or DEFAULT_LIMIT))
    input_root = default_input_root()
    wanted: List[str] = []

    if content_ids:
        wanted = [str(c).strip().lower() for c in content_ids if str(c).strip()]
    elif collection_id:
        cols = load_collections(data_root)
        coll = None
        for c in cols.get("collections") or []:
            if isinstance(c, dict) and str(c.get("id") or "") == str(collection_id):
                coll = c
                break
        if not coll:
            return []
        from still_identity import load_identity  # type: ignore

        ident = load_identity(data_root)
        for it in coll.get("items") or []:
            path = ""
            if isinstance(it, dict):
                path = str(it.get("path") or "")
                cid = str(it.get("content_id") or "").strip().lower()
                if not cid and ident is not None:
                    cid = ident.content_id_for_path(path) or ""
                if not cid:
                    cid = extract_content_id(path) or ""
            else:
                path = str(it)
                cid = ""
                if ident is not None:
                    cid = ident.content_id_for_path(path) or ""
                if not cid:
                    cid = extract_content_id(path) or ""
            if cid:
                wanted.append(cid)
    else:
        from still_identity import iter_unique_stills  # type: ignore

        unique = iter_unique_stills(data_root, min_size=1)
        wanted = [cid for cid, _path in unique]
        # Fresh catalog drops (filename hash) even if content-accounting is stale.
        wanted = _catalog_newest_content_ids(data_root, limit=max(lim * 20, 400)) + wanted

    # Dedupe preserve order
    seen: set[str] = set()
    ordered: List[str] = []
    for cid in wanted:
        if cid in seen:
            continue
        seen.add(cid)
        ordered.append(cid)

    db_path = default_db_path(data_root=data_root)
    ensure_db(db_path)
    con = connect(db_path)
    try:
        if only_missing and not force:
            ordered = content_ids_available_for_tagging(con, ordered, only_missing=True, force=False)
        ordered = ordered[:lim]
    finally:
        con.close()

    # Resolve paths via byte-hash accounting, then catalog filename match.
    from still_identity import load_identity  # type: ignore

    ident = load_identity(data_root)
    path_by_cid: Dict[str, str] = {}
    if ident is not None:
        for cid in ordered:
            canon = ident.canonical_path(cid)
            if canon is not None:
                path_by_cid[cid] = str(canon)
    cat = default_catalog_path(data_root=data_root)
    if cat.is_file():
        con_cat = sqlite3.connect(str(cat), timeout=30.0)
        try:
            for cid in ordered:
                if cid in path_by_cid:
                    continue
                row = con_cat.execute(
                    "SELECT path FROM stills WHERE lower(path) LIKE ? LIMIT 1",
                    (f"%{cid}%",),
                ).fetchone()
                if row:
                    path_by_cid[cid] = str(row[0])
        finally:
            con_cat.close()

    out: List[Dict[str, Any]] = []
    for cid in ordered:
        stored = path_by_cid.get(cid)
        resolved = resolve_catalog_still_path(stored, input_root=input_root) if stored else None
        if resolved is None:
            # Fallback: search input_root by hash substring
            hits = list(input_root.rglob(f"*{cid}*")) if input_root.is_dir() else []
            hits = [h for h in hits if h.is_file()]
            resolved = hits[0] if hits else None
        if resolved is None:
            out.append({"content_id": cid, "path": None, "relpath": None, "missing": True})
            continue
        try:
            resolved = _ensure_still_under_comfy_input(
                resolved, data_root=data_root, input_root=input_root
            )
        except Exception:
            pass
        rel = still_relpath_for_comfy(resolved, input_root=input_root)
        out.append(
            {
                "content_id": cid,
                "path": str(resolved),
                "relpath": rel,
                "missing": False,
            }
        )
    return out


def enqueue_run(
    *,
    data_root: Path,
    content_ids: Optional[Sequence[str]] = None,
    collection_id: Optional[str] = None,
    only_missing: bool = True,
    limit: int = DEFAULT_LIMIT,
    force: bool = False,
    provider: str = "comfy",
    comfy_server: Optional[str] = None,
    dry_run: bool = False,
    pin_path: Optional[Path] = None,
    status_dir: Optional[Path] = None,
    manual: Optional[bool] = None,
) -> Dict[str, Any]:
    db_path = default_db_path(data_root=data_root)
    ensure_db(db_path)
    # Best-effort migrate G1 JSON once
    migrate_editorial_from_json(db_path, data_root / "shape_factory" / "input_still_tags.json")
    rekey_items_to_byte_hash(data_root=data_root)

    pin = load_pin(pin_path or default_pin_path(status_dir=status_dir))
    server = (comfy_server or os.environ.get("VISION_COMFY_SERVER") or DEFAULT_COMFY_SERVER).rstrip("/")
    if dry_run:
        provider = "dry-run"

    targets = resolve_targets(
        data_root=data_root,
        content_ids=content_ids,
        collection_id=collection_id,
        only_missing=only_missing,
        limit=limit,
        force=force,
    )
    runnable = [t for t in targets if not t.get("missing")]
    skipped = [t for t in targets if t.get("missing")]
    is_manual = infer_manual_request(
        content_ids=content_ids,
        collection_id=collection_id,
        manual=manual,
    )
    sla_hours = run_sla_hours({"manual": is_manual, "request": "manual" if is_manual else "backlog"})
    if not runnable:
        return {
            "ok": True,
            "run_id": None,
            "enqueued": 0,
            "skipped": len(skipped),
            "reserved": 0,
            "db_path": str(db_path),
            "manual": is_manual,
            "sla_hours": sla_hours,
        }

    run_id = f"still_tag_{_utc_now_iso().replace(':', '').replace('-', '')}_{uuid.uuid4().hex[:8]}"
    scope = {
        "content_ids": [t["content_id"] for t in targets],
        "collection_id": collection_id,
        "only_missing": only_missing,
        "limit": limit,
        "force": force,
        "manual": is_manual,
        "request": "manual" if is_manual else "backlog",
        "sla_hours": sla_hours,
        "targets": targets,
    }
    reserved = 0
    con = connect(db_path)
    try:
        con.execute(
            """
            INSERT INTO still_tag_runs(
              run_id, status, scope_json, enqueued_at, total, done_count, error_count, skipped_count,
              pin_policy, model_pin, provider, comfy_server, detail
            ) VALUES (?, 'queued', ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                _json_dumps(scope),
                _utc_now_iso(),
                len(runnable),
                len(skipped),
                pin["pin_policy"],
                pin["model_pin"],
                provider,
                server,
                None,
            ),
        )
        reserved = reserve_still_tag_items(
            con,
            run_id,
            [str(t.get("content_id") or "") for t in runnable],
        )
        append_event(
            con,
            run_id=run_id,
            kind="enqueued",
            message=f"enqueued {len(runnable)} (skipped_missing={len(skipped)}, reserved={reserved})",
            payload={
                "total": len(runnable),
                "skipped": len(skipped),
                "reserved": reserved,
                "manual": is_manual,
                "sla_hours": sla_hours,
            },
        )
        con.commit()
    finally:
        con.close()

    return {
        "ok": True,
        "run_id": run_id,
        "enqueued": len(runnable),
        "skipped": len(skipped),
        "reserved": reserved,
        "db_path": str(db_path),
        "model_pin": pin["model_pin"],
        "pin_policy": pin["pin_policy"],
        "provider": provider,
        "comfy_server": server,
        "manual": is_manual,
        "sla_hours": sla_hours,
    }


def process_run(
    *,
    data_root: Path,
    run_id: str,
    status_dir: Optional[Path] = None,
    front: bool = False,
    batch_n: Optional[int] = None,
    hard_deadline: Optional[float] = None,
) -> Dict[str, Any]:
    db_path = default_db_path(data_root=data_root)
    con = connect(db_path)
    try:
        run = get_run(con, run_id)
        if not run:
            return {"ok": False, "error": "run_not_found", "run_id": run_id}
        if run["status"] in ("done", "error", "cancelled"):
            return {"ok": True, "run": run, "already_finished": True}

        pin = load_pin(default_pin_path(status_dir=status_dir))
        fp = set(pin.get("fp_blocklist") or [])
        scope = run.get("scope") or {}
        targets = [t for t in (scope.get("targets") or []) if isinstance(t, dict) and not t.get("missing")]
        targets = _sort_targets_newest_first(targets, data_root=data_root)
        provider = str(run.get("provider") or "comfy")
        server = str(run.get("comfy_server") or DEFAULT_COMFY_SERVER)
        model_pin = str(run.get("model_pin") or pin["model_pin"])
        pin_policy = str(run.get("pin_policy") or pin["pin_policy"])
        image_mode = still_tag_image_mode()
        batch_n = still_tag_batch_size(batch_n)
        poll_s = still_tag_poll_interval_s()

        con.execute(
            "UPDATE still_tag_runs SET status=?, started_at=? WHERE run_id=?",
            ("running", _utc_now_iso(), run_id),
        )
        append_event(
            con,
            run_id=run_id,
            kind="started",
            message=(
                f"provider={provider} server={server} front={bool(front)} "
                f"batch={batch_n} image_mode={image_mode} poll_s={poll_s}"
            ),
        )
        con.commit()

        from vision_slice_runner import CaptionRequest, make_runner  # type: ignore

        runner = make_runner(
            provider=provider,
            runner_label=provider,
            comfy_server=server,
            model_pin=model_pin,
            dry_run=provider in ("dry-run", "dry_run"),
            task=DEFAULT_TASK,
            max_new_tokens=256,
            image_mode=image_mode,
            poll_interval_s=poll_s,
            front=bool(front),
        )

        ident = None
        try:
            from still_identity import load_identity  # type: ignore

            ident = load_identity(data_root)
        except Exception:
            ident = None

        ndjson_path: Optional[Path] = None
        if status_dir is not None:
            status_dir = Path(status_dir)
            status_dir.mkdir(parents=True, exist_ok=True)
            ndjson_path = status_dir / "vision_still_tags.ndjson"

        done = int(run["done_count"] or 0)
        errors = int(run["error_count"] or 0)

        def _record_ok(t: Dict[str, Any], cid: str, caption: str, raw: Dict[str, Any], model_used: str) -> None:
            nonlocal done
            tags = [x for x in parse_danbooru_tags(caption, max_tags=64) if x not in fp]
            if not tags and provider in ("dry-run", "dry_run"):
                tags = _dedupe(caption.split(","))
            upsert_provisional(
                con,
                content_id=cid,
                tags=tags,
                model_pin=model_used,
                pin_policy=pin_policy,
                run_id=run_id,
                raw_caption=caption,
            )
            done += 1
            con.execute(
                "UPDATE still_tag_runs SET done_count=? WHERE run_id=?",
                (done, run_id),
            )
            append_event(
                con,
                run_id=run_id,
                kind="item_done",
                content_id=cid,
                message=f"{len(tags)} tags",
                payload={"tags": tags[:24], "tag_count": len(tags)},
            )
            con.commit()
            if ndjson_path is not None:
                row = {
                    "schema": 1,
                    "content_id": cid,
                    "relpath": t.get("relpath"),
                    "tags": tags,
                    "caption": caption,
                    "model_pin": model_used,
                    "pin_policy": pin_policy,
                    "run_id": run_id,
                    "provider": provider,
                    "raw": raw,
                    "ts": _utc_now_iso(),
                }
                with ndjson_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")

        def _record_err(cid: str, err: BaseException) -> None:
            nonlocal errors
            errors += 1
            con.execute(
                "UPDATE still_tag_runs SET error_count=? WHERE run_id=?",
                (errors, run_id),
            )
            append_event(
                con,
                run_id=run_id,
                kind="item_error",
                content_id=cid,
                message=str(err)[:500],
            )
            con.commit()

        def _caption_one(req: Any, t: Dict[str, Any], cid: str) -> None:
            try:
                result = runner.caption(req)
                _record_ok(t, cid, result.caption, result.raw, result.model_pin)
                return
            except _TagJobKilled:
                raise
            except TimeoutError as e:
                pid = getattr(runner, "last_prompt_id", None)
                interrupt_comfy_tag_job(server, str(pid) if pid else None)
                if hard_deadline is not None and time.time() >= hard_deadline:
                    raise _TagJobKilled(str(e)) from e
                _record_err(cid, e)
                return
            except Exception as e:
                cfg = getattr(runner, "cfg", None)
                mode = str(getattr(cfg, "image_mode", "") or "")
                if cfg is not None and mode != "upload" and _is_comfy_ref_error(e):
                    prev = cfg.image_mode
                    try:
                        cfg.image_mode = "upload"
                        result = runner.caption(req)
                        _record_ok(t, cid, result.caption, result.raw, result.model_pin)
                        return
                    except _TagJobKilled:
                        raise
                    except TimeoutError as e2:
                        pid = getattr(runner, "last_prompt_id", None)
                        interrupt_comfy_tag_job(server, str(pid) if pid else None)
                        if hard_deadline is not None and time.time() >= hard_deadline:
                            raise _TagJobKilled(str(e2)) from e2
                        _record_err(cid, e2)
                        return
                    except Exception as e2:
                        _record_err(cid, e2)
                        return
                    finally:
                        cfg.image_mode = prev
                _record_err(cid, e)

        def _caption_chunk(chunk: List[Tuple[Dict[str, Any], str, Path]]) -> None:
            reqs = [
                CaptionRequest(
                    image_path=path,
                    asset_relpath=str(t.get("relpath") or path.name),
                    meta={"content_id": cid},
                )
                for t, cid, path in chunk
            ]
            caption_many = getattr(runner, "caption_many", None)
            try:
                if len(chunk) > 1 and callable(caption_many):
                    results = caption_many(reqs)
                else:
                    results = [runner.caption(r) for r in reqs]
                if len(results) != len(chunk):
                    raise RuntimeError(
                        f"caption batch size mismatch want={len(chunk)} got={len(results)}"
                    )
                for (t, cid, _path), result in zip(chunk, results):
                    _record_ok(t, cid, result.caption, result.raw, result.model_pin)
                return
            except _TagJobKilled:
                raise
            except TimeoutError as e:
                pid = getattr(runner, "last_prompt_id", None)
                interrupt_comfy_tag_job(server, str(pid) if pid else None)
                if hard_deadline is not None and time.time() >= hard_deadline:
                    raise _TagJobKilled(str(e)) from e
            except Exception:
                pass
            for item, req in zip(chunk, reqs):
                t, cid, _path = item
                _caption_one(req, t, cid)

        try:
            pending: List[Tuple[Dict[str, Any], str, Path]] = []
            for t in targets:
                cid = str(t.get("content_id") or "")
                stored = Path(str(t.get("path") or ""))
                path = stored
                try:
                    live = ident.canonical_path(cid) if ident is not None else None
                    if live is not None:
                        path = live
                except Exception:
                    path = stored
                existing = con.execute(
                    "SELECT provisional_tags FROM still_tag_items WHERE content_id=?",
                    (cid,),
                ).fetchone()
                if existing and _row_has_provisional(existing) and not bool(scope.get("force")):
                    continue
                if not _is_dry_provider(provider) and not path.is_file():
                    _record_err(cid, FileNotFoundError(f"missing still: {path}"))
                    continue
                if not _is_dry_provider(provider) and path.is_file():
                    try:
                        path = _ensure_still_under_comfy_input(path, data_root=data_root)
                    except Exception:
                        pass
                if not _is_dry_provider(provider):
                    bad = _still_unreadable_reason(path)
                    if bad:
                        _record_err(cid, OSError(f"{bad}: {path}"))
                        continue
                pending.append((t, cid, path))

            # Soft session_minutes is a start/budget target only. Once a run is
            # underway, finish remaining stills unless kill_after_min is hit.
            killed = False
            kill_reason: Optional[str] = None
            remaining_after = 0
            for i in range(0, len(pending), batch_n):
                if hard_deadline is not None and time.time() >= hard_deadline:
                    killed = True
                    kill_reason = "kill_after_min"
                    remaining_after = len(pending) - i
                    break
                if hard_deadline is not None:
                    cfg = getattr(runner, "cfg", None)
                    if cfg is not None:
                        remain = max(5.0, hard_deadline - time.time())
                        try:
                            current = float(getattr(cfg, "timeout_s", 900) or 900)
                        except (TypeError, ValueError):
                            current = 900.0
                        cfg.timeout_s = min(current, remain)
                chunk = pending[i : i + batch_n]
                try:
                    if _is_dry_provider(provider):
                        for t, cid, _path in chunk:
                            caption = (
                                "1girl, long hair, looking at viewer, solo, simple background, "
                                f"tag_smoke_{cid[:8]}"
                            )
                            _record_ok(t, cid, caption, {"dry_run": True}, "dry-run")
                    else:
                        _caption_chunk(chunk)
                except _TagJobKilled:
                    killed = True
                    kill_reason = "kill_after_min"
                    remaining_after = len(pending) - i
                    break

            if killed:
                detail = f"killed:{kill_reason} remaining={remaining_after}"
                con.execute(
                    """
                    UPDATE still_tag_runs
                    SET status=?, finished_at=NULL, done_count=?, error_count=?, detail=?
                    WHERE run_id=?
                    """,
                    ("queued", done, errors, detail, run_id),
                )
                append_event(
                    con,
                    run_id=run_id,
                    kind="killed",
                    message=detail,
                    payload={
                        "reason": kill_reason,
                        "remaining": remaining_after,
                        "done": done,
                        "errors": errors,
                    },
                )
                con.commit()
                return {
                    "ok": True,
                    "killed": True,
                    "paused": True,
                    "reason": kill_reason,
                    "remaining": remaining_after,
                    "run": get_run(con, run_id),
                }

            status = "done" if errors == 0 or done > 0 else "error"
            detail = None if errors == 0 else f"{errors} item error(s)"
            con.execute(
                """
                UPDATE still_tag_runs
                SET status=?, finished_at=?, done_count=?, error_count=?, detail=?
                WHERE run_id=?
                """,
                (status, _utc_now_iso(), done, errors, detail, run_id),
            )
            released = release_still_tag_reservations(con, run_id)
            append_event(
                con,
                run_id=run_id,
                kind="finished",
                message=f"status={status} done={done} errors={errors} released={released}",
                payload={"done": done, "errors": errors, "status": status, "released": released},
            )
            con.commit()
            return {"ok": True, "paused": False, "run": get_run(con, run_id)}
        finally:
            try:
                runner.close()
            except Exception:
                pass
    finally:
        con.close()


def kick_worker(
    *,
    data_root: Path,
    status_dir: Optional[Path] = None,
    front: bool = False,
) -> None:
    """Ensure a background thread is draining queued runs (immediate / smoke path)."""
    global _worker_thread

    def _loop() -> None:
        db_path = default_db_path(data_root=data_root)
        ensure_db(db_path)
        while True:
            con = connect(db_path)
            try:
                row = con.execute(
                    """
                    SELECT run_id FROM still_tag_runs
                    WHERE status='queued'
                    ORDER BY enqueued_at ASC
                    LIMIT 1
                    """
                ).fetchone()
            finally:
                con.close()
            if not row:
                break
            try:
                process_run(
                    data_root=data_root,
                    run_id=str(row["run_id"]),
                    status_dir=status_dir,
                    front=bool(front),
                )
            except Exception:
                # process_run should record errors; keep draining
                time.sleep(0.2)

    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return
        t = threading.Thread(target=_loop, name="still-tag-worker", daemon=True)
        _worker_thread = t
        t.start()


def default_schedule_path(*, data_root: Optional[Path] = None) -> Path:
    return default_db_path(data_root=data_root).parent / SCHEDULE_BASENAME


def load_schedule(*, data_root: Optional[Path] = None, path: Optional[Path] = None) -> Dict[str, Any]:
    p = Path(path) if path is not None else default_schedule_path(data_root=data_root)
    out = dict(DEFAULT_SCHEDULE)
    if not p.is_file():
        return out
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return out
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        if k in DEFAULT_SCHEDULE or k == "schema_version":
            out[k] = v
    return out


def save_schedule(
    schedule: Dict[str, Any],
    *,
    data_root: Optional[Path] = None,
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    p = Path(path) if path is not None else default_schedule_path(data_root=data_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    merged = dict(DEFAULT_SCHEDULE)
    if isinstance(schedule, dict):
        for k, v in schedule.items():
            if k in DEFAULT_SCHEDULE or k == "schema_version":
                merged[k] = v
    merged["schema_version"] = 1
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(p)
    return merged


def _parse_hhmm(value: Any) -> Optional[Tuple[int, int]]:
    s = str(value or "").strip()
    if not s or ":" not in s:
        return None
    try:
        hh_s, mm_s = s.split(":", 1)
        hh, mm = int(hh_s), int(mm_s)
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return hh, mm
    except Exception:
        return None
    return None


def _local_window_bounds(
    local: _dt.datetime,
    *,
    start_hhmm: Tuple[int, int],
    duration_min: int,
) -> Tuple[bool, _dt.datetime, _dt.datetime]:
    """True when *local* is inside [start, start+duration), with midnight wrap."""
    duration = max(1, int(duration_min))
    start_dt = local.replace(hour=start_hhmm[0], minute=start_hhmm[1], second=0, microsecond=0)
    end_dt = start_dt + _dt.timedelta(minutes=duration)
    if end_dt <= start_dt:
        end_dt = start_dt + _dt.timedelta(minutes=duration)
    if local < start_dt:
        prev_start = start_dt - _dt.timedelta(days=1)
        prev_end = prev_start + _dt.timedelta(minutes=duration)
        if prev_start <= local < prev_end:
            return True, prev_start, prev_end
        return False, start_dt, end_dt
    if start_dt <= local < end_dt:
        return True, start_dt, end_dt
    return False, start_dt, end_dt


def index_window_status(
    schedule: Optional[Dict[str, Any]] = None,
    *,
    now: Optional[_dt.datetime] = None,
    data_root: Optional[Path] = None,
    backlog: Optional[Dict[str, Any]] = None,
    session: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return whether a drain tick should start (SLA due or clock window)."""
    sch = dict(DEFAULT_SCHEDULE)
    if isinstance(schedule, dict):
        sch.update(schedule)
    enabled = bool(sch.get("enabled"))
    start = _parse_hhmm(sch.get("window_start")) or (2, 0)
    session_minutes = max(1.0, _schedule_float(sch, "session_minutes", 15))
    duration = max(1, int(sch.get("window_duration_min") or session_minutes or 15))
    tz_name = str(sch.get("timezone") or "").strip() or None
    mode = _schedule_mode(sch)

    if now is None:
        now = _dt.datetime.now(tz=_dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)

    local = now
    tz_ok = False
    if tz_name:
        try:
            from zoneinfo import ZoneInfo

            local = now.astimezone(ZoneInfo(tz_name))
            tz_ok = True
        except Exception:
            local = now.astimezone()
    else:
        local = now.astimezone()

    in_window, start_dt, end_dt = _local_window_bounds(local, start_hhmm=start, duration_min=duration)
    window_kind = "primary"
    catch_up_enabled = bool(sch.get("catch_up_enabled"))
    catch_start = _parse_hhmm(sch.get("catch_up_window_start"))
    catch_duration = max(1, int(sch.get("catch_up_window_duration_min") or 120))
    catch_start_dt: Optional[_dt.datetime] = None
    catch_end_dt: Optional[_dt.datetime] = None
    if mode == "clock" and catch_up_enabled and catch_start is not None:
        catch_in, catch_start_dt, catch_end_dt = _local_window_bounds(
            local, start_hhmm=catch_start, duration_min=catch_duration
        )
        if catch_in:
            in_window = True
            start_dt, end_dt = catch_start_dt, catch_end_dt
            window_kind = "catch_up"

    reason = "ok" if (enabled and in_window) else (
        "disabled" if not enabled else "outside_window"
    )
    stats = backlog
    if stats is None and data_root is not None:
        try:
            stats = backlog_stats(data_root=Path(data_root))
        except Exception:
            stats = None
    sess = session
    if sess is None and data_root is not None:
        sess = load_tag_session(data_root=Path(data_root))
    sla: Optional[Dict[str, Any]] = None
    if mode == "sla":
        sla = sla_due_status(schedule=sch, backlog=stats, session=sess, now=now)
        in_window = bool(enabled and sla.get("due"))
        reason = str(sla.get("reason") or reason)
        window_kind = "sla" if in_window else None
        end_dt = local + _dt.timedelta(minutes=session_minutes)
        start_dt = local

    sec_per_still = max(2.0, _schedule_float(sch, "sec_per_still", DEFAULT_SEC_PER_STILL))
    batch_n = scale_batch_for_session(
        session_minutes=session_minutes,
        sec_per_still=sec_per_still,
        pending_count=int((stats or {}).get("queued_targets") or 0) or None,
    )
    out: Dict[str, Any] = {
        "enabled": enabled,
        "mode": mode,
        "in_window": bool(in_window),
        "reason": reason,
        "window_kind": window_kind if in_window else None,
        "timezone": tz_name,
        "timezone_resolved": tz_ok,
        "local_now": local.replace(microsecond=0).isoformat(),
        "window_start_local": start_dt.replace(microsecond=0).isoformat(),
        "window_end_local": end_dt.replace(microsecond=0).isoformat(),
        "window_duration_min": duration,
        "session_minutes": session_minutes,
        "kill_after_min": max(1.0, _schedule_float(sch, "kill_after_min", 60)),
        "max_wait_hours": max(0.25, _schedule_float(sch, "max_wait_hours", 3)),
        "manual_max_wait_hours": max(0.25, _schedule_float(sch, "manual_max_wait_hours", 1)),
        "scan_interval_min": max(0.0, _schedule_float(sch, "scan_interval_min", 15)),
        "evaluate_interval_min": max(0.0, _schedule_float(sch, "evaluate_interval_min", 15)),
        "auto_enqueue_untagged": bool(sch.get("auto_enqueue_untagged", True)),
        "auto_enqueue_limit": max(1, int(sch.get("auto_enqueue_limit") or 96)),
        "resume_gap_min": max(0.0, _schedule_float(sch, "resume_gap_min", 20)),
        "sec_per_still": sec_per_still,
        "occupy_gpu": bool(sch.get("occupy_gpu", True)),
        "scaled_batch": batch_n,
        "front": bool(sch.get("front", True)),
        "max_inflight": max(1, int(sch.get("max_inflight") or 1)),
        "max_items_per_tick": max(1, int(sch.get("max_items_per_tick") or 48)),
        "auto_drain_on_enqueue": bool(sch.get("auto_drain_on_enqueue")),
        "comfy_server": sch.get("comfy_server"),
    }
    if sla is not None:
        out["wait_hours"] = sla.get("wait_hours")
        out["max_wait_hours"] = sla.get("max_wait_hours", out["max_wait_hours"])
        out["sla_class"] = sla.get("sla_class")
        out["session_status"] = sla.get("session_status")
        out["stale_session"] = sla.get("stale_session")
        if sla.get("gap_remaining_min") is not None:
            out["gap_remaining_min"] = sla.get("gap_remaining_min")
    if sess:
        out["session"] = {
            "status": sess.get("status"),
            "started_at": sess.get("started_at"),
            "ended_at": sess.get("ended_at"),
            "batch_n": sess.get("batch_n"),
        }
    if mode == "clock" and catch_up_enabled and catch_start is not None and catch_start_dt is not None and catch_end_dt is not None:
        out["catch_up_enabled"] = True
        out["catch_up_window_start_local"] = catch_start_dt.replace(microsecond=0).isoformat()
        out["catch_up_window_end_local"] = catch_end_dt.replace(microsecond=0).isoformat()
    return out


def should_auto_drain_on_enqueue(
    *,
    data_root: Optional[Path] = None,
    drain_now: bool = False,
    schedule: Optional[Dict[str, Any]] = None,
) -> bool:
    if drain_now:
        return True
    env = str(os.environ.get("STILL_TAG_AUTO_DRAIN") or "").strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    sch = schedule if isinstance(schedule, dict) else load_schedule(data_root=data_root)
    return bool(sch.get("auto_drain_on_enqueue"))


def backlog_stats(*, data_root: Path) -> Dict[str, Any]:
    db_path = default_db_path(data_root=data_root)
    ensure_db(db_path)
    con = connect(db_path)
    try:
        queued = con.execute(
            "SELECT run_id, total, enqueued_at, provider, scope_json FROM still_tag_runs WHERE status='queued' ORDER BY enqueued_at ASC"
        ).fetchall()
        running = con.execute(
            "SELECT COUNT(*) AS c FROM still_tag_runs WHERE status='running'"
        ).fetchone()
        items_total = con.execute("SELECT COUNT(*) AS c FROM still_tag_items").fetchone()
        items_prov = con.execute(
            """
            SELECT COUNT(*) AS c FROM still_tag_items
            WHERE provisional_tags IS NOT NULL AND provisional_tags != '[]'
            """
        ).fetchone()
        items_reserved = con.execute(
            """
            SELECT COUNT(*) AS c FROM still_tag_items
            WHERE queue_run_id IS NOT NULL
              AND (provisional_tags IS NULL OR provisional_tags='[]')
            """
        ).fetchone()
        queued_targets = sum(int(r["total"] or 0) for r in queued)
        tagged = int(items_prov["c"] if items_prov else 0)
        reserved = int(items_reserved["c"] if items_reserved else 0)
        manual_ids = {
            str(r["run_id"]) for r in queued if _scope_is_manual(_scope_from_raw(r["scope_json"]))
        }
        manual_rows = [r for r in queued if str(r["run_id"]) in manual_ids]
        backlog_rows = [r for r in queued if str(r["run_id"]) not in manual_ids]
        return {
            "ok": True,
            "db_path": str(db_path),
            "queued_runs": len(queued),
            "queued_targets": queued_targets,
            "queued_manual_runs": len(manual_rows),
            "queued_manual_targets": sum(int(r["total"] or 0) for r in manual_rows),
            "running_runs": int(running["c"] if running else 0),
            "items_total": int(items_total["c"] if items_total else 0),
            "items_with_provisional": tagged,
            "items_tagged": tagged,
            "items_reserved": reserved,
            "items_queued": reserved,
            "oldest_queued_at": queued[0]["enqueued_at"] if queued else None,
            "oldest_manual_queued_at": manual_rows[0]["enqueued_at"] if manual_rows else None,
            "oldest_backlog_queued_at": backlog_rows[0]["enqueued_at"] if backlog_rows else None,
            "queued_run_ids": [str(r["run_id"]) for r in queued[:20]],
        }
    finally:
        con.close()


def scan_new_stills(*, data_root: Path) -> Dict[str, Any]:
    """Incremental input catalog scan so freshly dropped stills are visible."""
    from input_still_catalog import default_catalog_path, default_input_root, scan_input_stills  # type: ignore

    return scan_input_stills(
        input_root=default_input_root(),
        catalog_path=default_catalog_path(data_root=data_root),
    )


def run_scheduled_tick(
    *,
    data_root: Path,
    status_dir: Optional[Path] = None,
    front: Optional[bool] = None,
    max_items: Optional[int] = None,
    until_minutes: Optional[float] = None,
    provider_override: Optional[str] = None,
    comfy_server_override: Optional[str] = None,
    now: Optional[_dt.datetime] = None,
    force_scan: bool = False,
    force_evaluate: bool = False,
) -> Dict[str, Any]:
    """
    Periodic tick: scan for new stills, enqueue untagged backlog, then evaluate SLAs.

    Timer may wake often; *scan_interval_min* / *evaluate_interval_min* gate the work.
    """
    data_root = Path(data_root)
    sch = load_schedule(data_root=data_root)
    cancel_empty_queued_runs(data_root=data_root)
    if not bool(sch.get("enabled")):
        win = index_window_status(sch, data_root=data_root, now=now)
        return {"ok": True, "skipped": True, "reason": "schedule_disabled", "window": win}

    clock = now or _dt.datetime.now(tz=_dt.timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=_dt.timezone.utc)
    tick = load_tag_tick(data_root=data_root)
    scan_min = max(0.0, _schedule_float(sch, "scan_interval_min", 15))
    eval_min = max(0.0, _schedule_float(sch, "evaluate_interval_min", 15))
    do_scan = bool(force_scan) or interval_elapsed(tick.get("last_scan_at"), scan_min, now=clock)
    do_eval = bool(force_evaluate) or interval_elapsed(tick.get("last_evaluate_at"), eval_min, now=clock)
    if not do_scan and not do_eval:
        win = index_window_status(sch, data_root=data_root, now=now)
        return {
            "ok": True,
            "skipped": True,
            "reason": "tick_wait",
            "window": win,
            "tick": tick,
            "scan_interval_min": scan_min,
            "evaluate_interval_min": eval_min,
        }

    scan_out: Optional[Dict[str, Any]] = None
    enq_out: Optional[Dict[str, Any]] = None
    if do_scan:
        try:
            scan_out = scan_new_stills(data_root=data_root)
        except Exception as e:
            scan_out = {"ok": False, "error": str(e)}
        if bool(sch.get("auto_enqueue_untagged", True)):
            limit = max(1, int(sch.get("auto_enqueue_limit") or sch.get("max_items_per_tick") or 96))
            try:
                enq_out = enqueue_run(
                    data_root=data_root,
                    only_missing=True,
                    force=False,
                    limit=limit,
                    manual=False,
                    status_dir=status_dir,
                )
            except Exception as e:
                enq_out = {"ok": False, "error": str(e), "enqueued": 0}
        tick["last_scan_at"] = _utc_now_iso()
        tick["last_scan"] = {
            "inserted": (scan_out or {}).get("inserted"),
            "updated": (scan_out or {}).get("updated"),
            "enqueued": (enq_out or {}).get("enqueued") or 0,
        }

    drain_out: Optional[Dict[str, Any]] = None
    if do_eval:
        tick["last_evaluate_at"] = _utc_now_iso()
        save_tag_tick(tick, data_root=data_root)
        drain_out = drain_backlog(
            data_root=data_root,
            status_dir=status_dir,
            force=False,
            respect_schedule=True,
            front=front,
            max_items=max_items,
            until_minutes=until_minutes,
            provider_override=provider_override,
            comfy_server_override=comfy_server_override,
        )
    else:
        save_tag_tick(tick, data_root=data_root)

    win = (drain_out or {}).get("window") if isinstance(drain_out, dict) else None
    if not isinstance(win, dict):
        win = index_window_status(sch, data_root=data_root, now=now)
    return {
        "ok": True,
        "skipped": False,
        "reason": "tick",
        "scanned": do_scan,
        "evaluated": do_eval,
        "scan": scan_out,
        "enqueue": enq_out,
        "drain": drain_out,
        "window": win,
        "tick": load_tag_tick(data_root=data_root),
        "scan_interval_min": scan_min,
        "evaluate_interval_min": eval_min,
    }


def drain_backlog(
    *,
    data_root: Path,
    status_dir: Optional[Path] = None,
    force: bool = False,
    respect_schedule: bool = True,
    front: Optional[bool] = None,
    max_items: Optional[int] = None,
    until_minutes: Optional[float] = None,
    provider_override: Optional[str] = None,
    comfy_server_override: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Process queued still-tag runs until empty, item budget, or session target.

    The 15-minute session is a *start* target: do not begin another run after it,
    but always let an in-flight Florence run finish. ``max_inflight`` is recorded
    for ops; processing stays sequential.
    """
    data_root = Path(data_root)
    sch = load_schedule(data_root=data_root)
    cancel_empty_queued_runs(data_root=data_root)
    stats = backlog_stats(data_root=data_root)
    sess = load_tag_session(data_root=data_root)
    kill_after_min = max(1.0, _schedule_float(sch, "kill_after_min", 60))
    stale_after_min = max(90.0, kill_after_min + 30.0)
    if session_is_stale(sess, stale_after_min=stale_after_min):
        try:
            if sess.get("occupied"):
                release_gpu_after_tagging(
                    data_root=data_root,
                    hourly_was_enabled=bool(sess.get("hourly_was_enabled")),
                )
        except Exception:
            pass
        sess = save_tag_session(
            {"status": "idle", "note": "stale_recovered", "ended_at": _utc_now_iso()},
            data_root=data_root,
        )
    win = index_window_status(sch, data_root=data_root, backlog=stats, session=sess)
    if respect_schedule and not force:
        if not win["enabled"]:
            return {"ok": True, "skipped": True, "reason": "schedule_disabled", "window": win}
        if not win["in_window"]:
            skip_reason = (
                "outside_window" if win.get("mode") == "clock" else str(win.get("reason") or "outside_window")
            )
            return {"ok": True, "skipped": True, "reason": skip_reason, "window": win}
    if int(stats.get("queued_targets") or 0) < 1 and int(stats.get("running_runs") or 0) < 1:
        return {
            "ok": True,
            "skipped": True,
            "reason": "no_backlog",
            "window": win,
            "session": sess,
        }

    dry = _is_dry_provider(provider_override)
    sess_status = str((sess or {}).get("status") or "idle").strip().lower()
    if sess_status in {"occupying", "running"} and not force:
        return {
            "ok": True,
            "skipped": True,
            "reason": "session_active",
            "window": win,
            "session": sess,
        }

    use_front = bool(win["front"] if front is None else front)
    session_minutes = float(until_minutes if until_minutes is not None else win.get("session_minutes") or 15)
    sec_est = estimate_sec_per_still(
        data_root=data_root,
        fallback=float(win.get("sec_per_still") or DEFAULT_SEC_PER_STILL),
    )
    pending_n = int(stats.get("queued_targets") or 0) or None
    batch_n = scale_batch_for_session(
        session_minutes=session_minutes,
        sec_per_still=sec_est,
        pending_count=pending_n,
    )
    tick_cap = max(1, int(win.get("max_items_per_tick") or 48))
    if max_items is not None:
        budget = max(1, int(max_items))
    else:
        budget = session_item_budget(
            session_minutes=session_minutes,
            sec_per_still=sec_est,
            cap=tick_cap,
        )
    deadline = time.time() + float(session_minutes) * 60.0
    hard_deadline = time.time() + float(kill_after_min) * 60.0
    if win.get("mode") == "clock" and respect_schedule and not force and win.get("window_end_local"):
        try:
            end_local = _dt.datetime.fromisoformat(str(win["window_end_local"]))
            if end_local.tzinfo is None:
                end_local = end_local.replace(tzinfo=_dt.timezone.utc)
            deadline = min(deadline, end_local.timestamp())
        except Exception:
            pass

    occupy = bool(win.get("occupy_gpu", True)) and not dry
    occupy_out: Optional[Dict[str, Any]] = None
    hourly_was = False
    session_started = _utc_now_iso()
    save_tag_session(
        {
            "status": "occupying" if occupy else "running",
            "started_at": session_started,
            "batch_n": batch_n,
            "sec_per_still": sec_est,
            "session_minutes": session_minutes,
            "kill_after_min": kill_after_min,
            "occupied": occupy,
            "force": bool(force),
        },
        data_root=data_root,
    )
    if occupy:
        server = str(
            comfy_server_override
            or win.get("comfy_server")
            or os.environ.get("VISION_COMFY_SERVER")
            or DEFAULT_COMFY_SERVER
        ).rstrip("/")
        try:
            occupy_out = occupy_gpu_for_tagging(data_root=data_root, comfy_server=server)
            hourly_was = bool(occupy_out.get("hourly_was_enabled"))
            if not occupy_out.get("ok"):
                save_tag_session(
                    {
                        "status": "idle",
                        "ended_at": _utc_now_iso(),
                        "note": "occupy_failed",
                        "occupy": occupy_out,
                    },
                    data_root=data_root,
                )
                return {
                    "ok": False,
                    "skipped": True,
                    "reason": "occupy_failed",
                    "window": win,
                    "occupy": occupy_out,
                }
        except Exception as e:
            save_tag_session(
                {
                    "status": "idle",
                    "ended_at": _utc_now_iso(),
                    "note": f"occupy_error:{e}",
                },
                data_root=data_root,
            )
            return {
                "ok": False,
                "skipped": True,
                "reason": "occupy_failed",
                "window": win,
                "error": str(e),
            }
        save_tag_session(
            {
                "status": "running",
                "started_at": session_started,
                "batch_n": batch_n,
                "sec_per_still": sec_est,
                "session_minutes": session_minutes,
                "kill_after_min": kill_after_min,
                "occupied": True,
                "hourly_was_enabled": hourly_was,
                "occupy": {"ok": True},
            },
            data_root=data_root,
        )
    else:
        save_tag_session(
            {
                "status": "running",
                "started_at": session_started,
                "batch_n": batch_n,
                "sec_per_still": sec_est,
                "session_minutes": session_minutes,
                "kill_after_min": kill_after_min,
                "occupied": False,
            },
            data_root=data_root,
        )

    ensure_db(default_db_path(data_root=data_root))
    done_items = 0
    runs_processed = 0
    errors = 0
    run_results: List[Dict[str, Any]] = []
    order_sql = "ASC" if win.get("mode") == "sla" else "DESC"

    try:
        while done_items < budget:
            if time.time() >= hard_deadline:
                break
            if deadline is not None and time.time() >= deadline and runs_processed > 0:
                break

            con = connect(default_db_path(data_root=data_root))
            try:
                rows = con.execute(
                    f"""
                    SELECT run_id, total, enqueued_at, scope_json FROM still_tag_runs
                    WHERE status='queued'
                    ORDER BY enqueued_at {order_sql}
                    """
                ).fetchall()
            finally:
                con.close()
            if not rows:
                break
            if win.get("mode") == "sla":
                manuals = [r for r in rows if _scope_is_manual(_scope_from_raw(r["scope_json"]))]
                row = manuals[0] if manuals else rows[0]
            else:
                row = rows[0]

            run_id = str(row["run_id"])
            total = int(row["total"] or 0)
            if total <= 0:
                cancel_empty_queued_runs(data_root=data_root)
                continue
            if done_items > 0 and total > (budget - done_items) and (budget - done_items) < total:
                break

            if comfy_server_override or provider_override:
                con = connect(default_db_path(data_root=data_root))
                try:
                    if comfy_server_override:
                        con.execute(
                            "UPDATE still_tag_runs SET comfy_server=? WHERE run_id=?",
                            (str(comfy_server_override).rstrip("/"), run_id),
                        )
                    if provider_override:
                        con.execute(
                            "UPDATE still_tag_runs SET provider=? WHERE run_id=?",
                            (str(provider_override), run_id),
                        )
                    con.commit()
                finally:
                    con.close()

            try:
                out = process_run(
                    data_root=data_root,
                    run_id=run_id,
                    status_dir=status_dir,
                    front=use_front,
                    batch_n=batch_n,
                    hard_deadline=hard_deadline,
                )
            except Exception as e:
                errors += 1
                run_results.append({"run_id": run_id, "ok": False, "error": str(e)})
                time.sleep(0.2)
                continue

            runs_processed += 1
            dc = int((out.get("run") or {}).get("done_count") or out.get("done_count") or 0)
            if not dc and out.get("ok"):
                r2 = out.get("run") if isinstance(out.get("run"), dict) else {}
                dc = int(r2.get("done_count") or 0)
            done_items += max(0, dc)
            run_results.append(
                {
                    "run_id": run_id,
                    "ok": bool(out.get("ok")),
                    "done_count": dc,
                    "killed": bool(out.get("killed")),
                    "error": out.get("error"),
                }
            )
            if out.get("killed"):
                break
    finally:
        release_out: Optional[Dict[str, Any]] = None
        if occupy:
            try:
                release_out = release_gpu_after_tagging(
                    data_root=data_root,
                    hourly_was_enabled=hourly_was,
                )
            except Exception as e:
                release_out = {"ok": False, "error": str(e)}
        save_tag_session(
            {
                "status": "idle",
                "started_at": session_started,
                "ended_at": _utc_now_iso(),
                "batch_n": batch_n,
                "sec_per_still": sec_est,
                "session_minutes": session_minutes,
                "kill_after_min": kill_after_min,
                "occupied": occupy,
                "hourly_was_enabled": hourly_was,
                "done_items": done_items,
                "runs_processed": runs_processed,
                "release": release_out,
            },
            data_root=data_root,
        )

    return {
        "ok": True,
        "skipped": False,
        "front": use_front,
        "max_inflight": win["max_inflight"],
        "budget": budget,
        "batch_n": batch_n,
        "sec_per_still": sec_est,
        "session_minutes": session_minutes,
        "kill_after_min": kill_after_min,
        "occupied": occupy,
        "occupy": occupy_out,
        "done_items": done_items,
        "runs_processed": runs_processed,
        "errors": errors,
        "window": win,
        "runs": run_results,
    }


def kick_drain(
    *,
    data_root: Path,
    status_dir: Optional[Path] = None,
    force: bool = False,
    respect_schedule: bool = True,
    front: Optional[bool] = None,
    max_items: Optional[int] = None,
    until_minutes: Optional[float] = None,
) -> Dict[str, Any]:
    """Spawn a background drain tick (daemon). Returns immediately."""
    global _drain_thread

    result_box: Dict[str, Any] = {"ok": True, "started": False}

    def _run() -> None:
        try:
            result_box["result"] = drain_backlog(
                data_root=data_root,
                status_dir=status_dir,
                force=force,
                respect_schedule=respect_schedule,
                front=front,
                max_items=max_items,
                until_minutes=until_minutes,
            )
        except Exception as e:
            result_box["result"] = {"ok": False, "error": str(e)}

    with _worker_lock:
        if _drain_thread is not None and _drain_thread.is_alive():
            return {"ok": True, "started": False, "reason": "drain_already_running"}
        t = threading.Thread(target=_run, name="still-tag-drain", daemon=True)
        _drain_thread = t
        t.start()
        result_box["started"] = True
    return result_box


def enrich_still_items(
    items: Sequence[Dict[str, Any]],
    *,
    data_root: Path,
    fp_blocklist: Optional[Sequence[str]] = None,
) -> None:
    """In-place: attach editorial/provisional/effective tags from SQLite (+ legacy JSON fallback)."""
    db_path = default_db_path(data_root=data_root)
    if not db_path.is_file():
        return
    pin_fp = list(fp_blocklist or load_pin().get("fp_blocklist") or [])
    con = connect(db_path)
    try:
        for it in items:
            if not isinstance(it, dict):
                continue
            cid = str(it.get("content_id") or "").strip().lower()
            if not cid:
                continue
            row = con.execute("SELECT * FROM still_tag_items WHERE content_id=?", (cid,)).fetchone()
            if not row:
                continue
            d = _item_dict(row, fp_blocklist=pin_fp)
            it["editorial_tags"] = d["editorial_tags"]
            it["provisional_tags"] = d["provisional_tags"]
            it["effective_tags"] = d["effective_tags"]
            it["tags"] = d["effective_tags"]
            it["tag_status"] = d["tag_status"]
            it["queue_run_id"] = d.get("queue_run_id")
            if d.get("note") is not None:
                it["note"] = d["note"]
    finally:
        con.close()
