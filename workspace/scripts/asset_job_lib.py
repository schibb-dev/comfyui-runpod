#!/usr/bin/env python3
"""
Asset enrichment job queue primitives (V2 stub framework).

Discovery (and other producers) append JSONL records; ``asset_job_worker`` drains
them. v1 handlers are stubs — no GPU / no heavy compute.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_catalog_path(repo_root: Optional[Path] = None) -> Path:
    root = repo_root or Path(__file__).resolve().parents[2]
    return root / "workspace" / "asset_job_catalog.yaml"


def default_queue_path(status_dir: Path) -> Path:
    return Path(status_dir) / "asset_job_queue.jsonl"


def default_worker_state_path(status_dir: Path) -> Path:
    return Path(status_dir) / "asset_job_worker_state.json"


def job_state_path(status_dir: Path, job_type: str) -> Path:
    return Path(status_dir) / "enrichment" / f"{job_type}_state.json"


def load_catalog(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or default_catalog_path()
    if yaml is None:
        raise RuntimeError("PyYAML required to load asset_job_catalog.yaml")
    if not path.is_file():
        return {"version": 1, "jobs": {}}
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(doc, dict):
        raise RuntimeError(f"catalog is not a mapping: {path}")
    doc.setdefault("version", 1)
    doc.setdefault("jobs", {})
    return doc


def catalog_job(catalog: Dict[str, Any], job_type: str) -> Optional[Dict[str, Any]]:
    jobs = catalog.get("jobs") if isinstance(catalog.get("jobs"), dict) else {}
    row = jobs.get(job_type)
    return row if isinstance(row, dict) else None


def active_job_types(catalog: Dict[str, Any], *, include_stub: bool = True) -> List[str]:
    """Job types that may be enqueued / drained (active or stub)."""
    out: List[str] = []
    jobs = catalog.get("jobs") if isinstance(catalog.get("jobs"), dict) else {}
    for name, row in jobs.items():
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "").strip().lower()
        if status == "active":
            out.append(str(name))
        elif include_stub and status == "stub":
            out.append(str(name))
    return out


def make_idempotency_key(job_type: str, group_id: str, content_hash: str = "") -> str:
    prefix = (content_hash or "")[:12]
    return f"{job_type}:{group_id}:{prefix or 'na'}"


def enqueue_job(queue_path: Path, record: Dict[str, Any]) -> Dict[str, Any]:
    """Append one JSONL job record. Returns the written row (with defaults filled)."""
    queue_path = Path(queue_path)
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    row = dict(record)
    row.setdefault("job_id", str(uuid.uuid4()))
    row.setdefault("ts", utc_now())
    if not row.get("idempotency_key"):
        asset = row.get("asset") if isinstance(row.get("asset"), dict) else {}
        row["idempotency_key"] = make_idempotency_key(
            str(row.get("job_type") or ""),
            str(asset.get("group_id") or asset.get("relpath") or ""),
            str(asset.get("sha256") or ""),
        )
    with queue_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def load_worker_state(path: Path) -> Dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        return {"schema": 1, "offset": 0, "seen_keys": [], "updated_at": None}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": 1, "offset": 0, "seen_keys": [], "updated_at": None}
    if not isinstance(doc, dict):
        return {"schema": 1, "offset": 0, "seen_keys": [], "updated_at": None}
    doc.setdefault("schema", 1)
    doc.setdefault("offset", 0)
    doc.setdefault("seen_keys", [])
    return doc


def save_worker_state(path: Path, state: Dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = dict(state)
    state["updated_at"] = utc_now()
    # Cap seen_keys so the state file stays small.
    seen = state.get("seen_keys") if isinstance(state.get("seen_keys"), list) else []
    if len(seen) > 5000:
        state["seen_keys"] = seen[-4000:]
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def read_batch(
    queue_path: Path,
    state: Dict[str, Any],
    *,
    job_types: Optional[Iterable[str]] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """
    Read up to ``limit`` unread queue rows matching ``job_types``.

    Advances a provisional ``offset`` in the returned rows' metadata via
    ``_batch_end_offset`` on the last item (caller commits via ``commit_cursor``).
    """
    queue_path = Path(queue_path)
    if not queue_path.is_file():
        return []
    want: Optional[Set[str]] = set(job_types) if job_types is not None else None
    seen: Set[str] = {
        str(k) for k in (state.get("seen_keys") if isinstance(state.get("seen_keys"), list) else [])
    }
    offset = int(state.get("offset") or 0)
    out: List[Dict[str, Any]] = []
    with queue_path.open("r", encoding="utf-8") as f:
        f.seek(offset)
        while len(out) < max(1, int(limit)):
            pos = f.tell()
            line = f.readline()
            if not line:
                break
            new_offset = f.tell()
            if not line.strip():
                offset = new_offset
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                offset = new_offset
                continue
            if not isinstance(row, dict):
                offset = new_offset
                continue
            jt = str(row.get("job_type") or "")
            if want is not None and jt not in want:
                offset = new_offset
                continue
            key = str(row.get("idempotency_key") or "")
            if key and key in seen:
                offset = new_offset
                continue
            row = dict(row)
            row["_queue_offset_start"] = pos
            row["_queue_offset_end"] = new_offset
            out.append(row)
            if key:
                seen.add(key)
            offset = new_offset
    if out:
        out[-1]["_batch_end_offset"] = offset
        out[-1]["_batch_seen_keys"] = sorted(seen)
    return out


def commit_cursor(state: Dict[str, Any], batch: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Update state offset + seen keys after a successful batch."""
    if not batch:
        return state
    last = batch[-1]
    end = last.get("_batch_end_offset")
    if isinstance(end, int):
        state["offset"] = end
    keys = last.get("_batch_seen_keys")
    if isinstance(keys, list):
        state["seen_keys"] = [str(k) for k in keys]
    else:
        seen = list(state.get("seen_keys") or [])
        for row in batch:
            k = str(row.get("idempotency_key") or "")
            if k and k not in seen:
                seen.append(k)
        state["seen_keys"] = seen
    return state


def run_stub_handler(job_type: str, batch: List[Dict[str, Any]], *, status_dir: Path) -> Dict[str, Any]:
    """Record would_run for a stub job type; no side effects beyond state JSON."""
    path = job_state_path(status_dir, job_type)
    path.parent.mkdir(parents=True, exist_ok=True)
    prev = {}
    if path.is_file():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prev = {}
    doc = {
        "schema": 1,
        "job_type": job_type,
        "status": "stub",
        "would_run": True,
        "batch_size": len(batch),
        "job_ids": [r.get("job_id") for r in batch],
        "idempotency_keys": [r.get("idempotency_key") for r in batch],
        "last_run_utc": utc_now(),
        "runs": int(prev.get("runs") or 0) + 1,
    }
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return doc


def queue_depth(queue_path: Path) -> int:
    path = Path(queue_path)
    if not path.is_file():
        return 0
    n = 0
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                n += 1
    return n
