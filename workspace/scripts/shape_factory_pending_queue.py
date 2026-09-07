"""Factory pending queue: FIFO by default, operator-reorderable.

Drain (``submit --pending-only``) and Workbench share this order. Rank lives on
``submit.pending_rank`` (0 = next to drain). Jobs without a rank sort by
``created_at`` among themselves after any ranked jobs of the same age band —
``compact_pending_ranks`` writes missing ranks so the order is stable.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any, Optional

PENDING_RANK_KEY = "pending_rank"
PENDING_QUEUE_STATUSES = frozenset({"", "pending", "editing", "draft", "deposited", "error", "failed"})
_BLOCKED_STATUSES = frozenset(
    {"queued", "running", "submitted", "complete", "completed", "abandoned"}
)


def jobs_dir_from_data_root(data_root: Path) -> Path:
    return Path(data_root).expanduser().resolve() / "shape_factory" / "jobs"


def jobs_dir_from_job_path(job_path: Path) -> Path:
    """``…/jobs/<family>/<key>.job.json`` → ``…/jobs``."""
    p = Path(job_path).expanduser().resolve()
    if p.parent.parent.name == "jobs":
        return p.parent.parent
    if p.parent.name == "jobs":
        return p.parent
    return p.parent.parent


def _created_ts(job: dict[str, Any], path: Optional[Path] = None) -> float:
    raw = job.get("created_at")
    if isinstance(raw, str) and raw.strip():
        text = raw.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return _dt.datetime.fromisoformat(text).timestamp()
        except Exception:
            pass
    if path is not None:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0
    return 0.0


def _coerce_rank(raw: Any) -> Optional[int]:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and float(raw).is_integer():
        return int(raw)
    if isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
        return int(raw.strip())
    return None


def job_pending_rank(job: dict[str, Any]) -> Optional[int]:
    top = _coerce_rank(job.get(PENDING_RANK_KEY))
    if top is not None:
        return top
    submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    return _coerce_rank(submit.get(PENDING_RANK_KEY))


def is_pending_queue_job(job: dict[str, Any]) -> bool:
    """True when the job belongs on the factory pending queue (not on Comfy)."""
    submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    pid = str(submit.get("prompt_id") or "").strip()
    if pid:
        return False
    status = str(submit.get("status") or "").strip().lower()
    if status in _BLOCKED_STATUSES:
        return False
    if status == "abandoned":
        return False
    if status in {"error", "failed"}:
        try:
            from shape_factory import job_pending_submit

            return bool(job_pending_submit(job))
        except Exception:
            return True
    if status and status not in PENDING_QUEUE_STATUSES:
        return False
    return True


def pending_queue_sort_key(job: dict[str, Any], path: Optional[Path] = None) -> tuple[int, float, str]:
    """Lower sorts first (FIFO). Explicit rank wins; else created_at."""
    rank = job_pending_rank(job)
    created = _created_ts(job, path)
    key = str(job.get("job_key") or (path.name if path is not None else ""))
    if rank is not None:
        return (0, float(rank), key)
    return (1, created, key)


def iter_pending_queue_job_files(jobs_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    root = Path(jobs_dir).expanduser().resolve()
    if not root.is_dir():
        return []
    rows: list[tuple[Path, dict[str, Any]]] = []
    for path in root.rglob("*.job.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(job, dict) or not is_pending_queue_job(job):
            continue
        rows.append((path, job))
    rows.sort(key=lambda pair: pending_queue_sort_key(pair[1], pair[0]))
    return rows


def _write_job(path: Path, job: dict[str, Any]) -> None:
    try:
        from shape_factory import atomic_write_json

        atomic_write_json(path, job)
    except Exception:
        path.write_text(
            json.dumps(job, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def compact_pending_ranks(*, jobs_dir: Path) -> list[dict[str, Any]]:
    """Rewrite ``pending_rank`` to 0..n-1 in FIFO order. Returns the compact list."""
    rows = iter_pending_queue_job_files(jobs_dir)
    out: list[dict[str, Any]] = []
    for index, (path, job) in enumerate(rows):
        submit = job.get("submit") if isinstance(job.get("submit"), dict) else None
        if submit is None:
            submit = {}
            job["submit"] = submit
        status = str(submit.get("status") or "").strip().lower()
        if status not in {"editing", "error", "failed"}:
            submit["status"] = "pending"
        if job_pending_rank(job) != index:
            submit[PENDING_RANK_KEY] = index
            _write_job(path, job)
        else:
            submit[PENDING_RANK_KEY] = index
        out.append(
            {
                "job_key": str(job.get("job_key") or path.stem.replace(".job", "")),
                "job_path": str(path),
                "pending_rank": index,
                "status": str(submit.get("status") or "pending"),
            }
        )
    return out


def enqueue_pending_job(
    job_path: Path,
    *,
    jobs_dir: Optional[Path] = None,
    position: str = "append",
) -> dict[str, Any]:
    """Place (or re-place) a job on the pending queue. ``position`` is append|front."""
    path = Path(job_path).expanduser().resolve()
    root = Path(jobs_dir).expanduser().resolve() if jobs_dir else jobs_dir_from_job_path(path)
    job = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(job, dict):
        raise ValueError(f"not a job document: {path}")
    submit = job.get("submit") if isinstance(job.get("submit"), dict) else None
    if submit is None:
        submit = {}
        job["submit"] = submit
    submit.pop("prompt_id", None)
    if str(submit.get("status") or "").strip().lower() != "editing":
        submit["status"] = "pending"
    pos = str(position or "append").strip().lower()
    if pos in {"front", "next", "head"}:
        pos = "front"
    else:
        pos = "append"
    # Drop any stale rank so compact + insert sees a clean list, then insert.
    submit.pop(PENDING_RANK_KEY, None)
    _write_job(path, job)

    others = [(p, j) for p, j in iter_pending_queue_job_files(root) if p.resolve() != path]
    ordered = [(path, job)] + others if pos == "front" else others + [(path, job)]
    for index, (p, j) in enumerate(ordered):
        sub = j.get("submit") if isinstance(j.get("submit"), dict) else None
        if sub is None:
            sub = {}
            j["submit"] = sub
        if str(sub.get("status") or "").strip().lower() not in {"editing", "error", "failed"}:
            sub["status"] = "pending"
        sub[PENDING_RANK_KEY] = index
        _write_job(p, j)
    rank = 0 if pos == "front" else max(0, len(ordered) - 1)
    return {
        "ok": True,
        "job_key": str(job.get("job_key") or path.stem.replace(".job", "")),
        "job_path": str(path),
        "pending_rank": rank,
        "pending_count": len(ordered),
        "position": pos,
        "status": "pending",
    }


def move_pending_job(
    *,
    jobs_dir: Path,
    job_key: str,
    delta: int,
) -> dict[str, Any]:
    """Move one job by ``delta`` slots (−1 = toward drain / earlier)."""
    key = str(job_key or "").strip()
    if not key:
        raise ValueError("job_key is required")
    try:
        step = int(delta)
    except (TypeError, ValueError) as exc:
        raise ValueError("delta must be an integer") from exc
    if step == 0:
        return {"ok": True, "job_key": key, "moved": False, "queue": compact_pending_ranks(jobs_dir=jobs_dir)}

    rows = compact_pending_ranks(jobs_dir=jobs_dir)
    keys = [str(r["job_key"]) for r in rows]
    try:
        idx = keys.index(key)
    except ValueError as exc:
        raise ValueError(f"not_pending:{key}") from exc
    dest = max(0, min(len(keys) - 1, idx + step))
    if dest == idx:
        return {"ok": True, "job_key": key, "moved": False, "pending_rank": idx, "queue": rows}
    keys.insert(dest, keys.pop(idx))
    return reorder_pending_jobs(jobs_dir=jobs_dir, job_keys=keys)


def reorder_pending_jobs(*, jobs_dir: Path, job_keys: list[str]) -> dict[str, Any]:
    """Set the pending queue to ``job_keys`` order (must include every pending key)."""
    wanted = [str(k).strip() for k in job_keys if str(k or "").strip()]
    rows = iter_pending_queue_job_files(jobs_dir)
    by_key = {str(j.get("job_key") or p.stem.replace(".job", "")): (p, j) for p, j in rows}
    current = list(by_key.keys())
    missing = [k for k in wanted if k not in by_key]
    if missing:
        raise ValueError(f"unknown_or_not_pending:{','.join(missing)}")
    extras = [k for k in current if k not in wanted]
    ordered_keys = wanted + extras
    for index, key in enumerate(ordered_keys):
        path, job = by_key[key]
        submit = job.get("submit") if isinstance(job.get("submit"), dict) else None
        if submit is None:
            submit = {}
            job["submit"] = submit
        submit[PENDING_RANK_KEY] = index
        if str(submit.get("status") or "").strip().lower() not in {"editing", "error", "failed"}:
            submit["status"] = "pending"
        _write_job(path, job)
    queue = compact_pending_ranks(jobs_dir=jobs_dir)
    rank = next((r["pending_rank"] for r in queue if r["job_key"] == wanted[0]), None) if wanted else None
    return {"ok": True, "moved": True, "job_key": wanted[0] if wanted else None, "pending_rank": rank, "queue": queue}


def attach_pending_queue_meta(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stamp ``pending_rank`` / ``pending_index`` / ``pending_count`` on work-product rows."""
    pending = [it for it in items if isinstance(it, dict) and is_pending_queue_item(it)]
    pending.sort(key=lambda it: pending_queue_sort_key(it))
    n = len(pending)
    for index, it in enumerate(pending):
        it["pending_rank"] = job_pending_rank(it) if job_pending_rank(it) is not None else index
        it["pending_index"] = index
        it["pending_count"] = n
    return items


def is_pending_queue_item(item: dict[str, Any]) -> bool:
    """Work-product or job-shaped dict."""
    pid = str(item.get("prompt_id") or "").strip()
    if pid:
        return False
    status = str(item.get("status") or "").strip().lower()
    if status in _BLOCKED_STATUSES:
        return False
    submit = item.get("submit") if isinstance(item.get("submit"), dict) else {}
    if str(submit.get("prompt_id") or "").strip():
        return False
    if status in {"error", "failed"}:
        return True
    if not status or status in PENDING_QUEUE_STATUSES:
        return True
    return is_pending_queue_job(item)


def parse_queue_destination(
    body: Optional[dict[str, Any]] = None,
    *,
    destination: str = "",
    skip_submit: bool = False,
) -> str:
    src = body if isinstance(body, dict) else {}
    raw = str(destination or src.get("destination") or "").strip().lower()
    if raw in {"pending", "comfy"}:
        return raw
    if skip_submit or src.get("skip_submit") or src.get("generate_only"):
        return "pending"
    return "comfy"


def job_is_hourly_queue_item(job: dict[str, Any], path: Optional[Path] = None) -> bool:
    """True when this pending-queue row is an hourly planner product."""
    try:
        from shape_factory_work_products import job_is_hourly_product

        return bool(job_is_hourly_product(job, path))
    except Exception:
        key = str(job.get("job_key") or "").strip()
        if key.startswith("hourly__"):
            return True
        if path is not None and Path(path).name.startswith("hourly__"):
            return True
        return False


def count_hourly_pending_jobs(jobs_dir: Path) -> int:
    """Hourly jobs currently sitting on the factory pending FIFO (including editing)."""
    return sum(
        1
        for path, job in iter_pending_queue_job_files(jobs_dir)
        if job_is_hourly_queue_item(job, path)
    )


def apply_hourly_pending_drain_floor(
    paths: list[Path],
    *,
    pending_hourly_min: int,
    hourly_pending_count: Optional[int] = None,
) -> list[Path]:
    """Keep hourlies on the FIFO until ``pending_hourly_min`` remain.

    Non-hourly Submit jobs always pass. Hourlies drain only while remaining
    hourly pending would stay above the floor. ``hourly_pending_count`` should
    be the full pending-queue hourly count (including editing / held rows).
    """
    min_keep = max(0, int(pending_hourly_min))
    if min_keep <= 0 or not paths:
        return list(paths)
    rows: list[tuple[Path, dict[str, Any]]] = []
    for path in paths:
        try:
            job = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(job, dict):
            rows.append((Path(path), job))
    if hourly_pending_count is None:
        hourly_count = sum(1 for path, job in rows if job_is_hourly_queue_item(job, path))
    else:
        hourly_count = max(0, int(hourly_pending_count))
    taken_hourly = 0
    out: list[Path] = []
    for path, job in rows:
        if job_is_hourly_queue_item(job, path):
            if hourly_count - taken_hourly <= min_keep:
                continue
            taken_hourly += 1
        out.append(path)
    return out


def parse_pending_position(body: Optional[dict[str, Any]] = None, *, position: str = "") -> str:
    src = body if isinstance(body, dict) else {}
    raw = str(position or src.get("pending_position") or src.get("position") or "").strip().lower()
    if raw in {"front", "next", "head"}:
        return "front"
    return "append"
