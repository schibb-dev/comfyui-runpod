#!/usr/bin/env python3
"""Durable work instances linked to source assets and factory jobs (bucket model Phase 2A)."""

from __future__ import annotations

import copy
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from shape_factory_heuristics import _og_group_id_from_relpath
from shape_factory_ratings import _atomic_write_json_doc, utc_now

WORK_ITEMS_SCHEMA = "comfyui-runpod.work-items.v0"
WORK_ITEMS_VERSION = 1

WORK_STATUSES = frozenset({"draft", "queued", "running", "done", "failed", "cancelled"})
TERMINAL_STATUSES = frozenset({"done", "failed", "cancelled"})
OPEN_STATUSES = frozenset({"draft", "queued", "running"})
# Priority can be reshaped only before Comfy has picked the work up.
PRIORITY_MUTABLE_STATUSES = frozenset({"draft", "queued"})
PRIORITIES = frozenset({"normal", "front"})
POOLS = frozenset({"extend", "vary", "refine_backlog", "extract", "investigate"})

# Default cooldown for identical pool+recipe idempotency keys (seconds).
DEFAULT_IDEMPOTENCY_COOLDOWN_S = 3600

# Crockford Base32 (ULID alphabet).
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# Disposition step_id → (pool, disposition_entry, default_priority)
STEP_ROUTE_MAP: Dict[str, Tuple[str, str, str]] = {
    "advance.extend": ("extend", "advance", "normal"),
    "advance.vary": ("vary", "advance", "front"),
    # Queue now is priority, not a pool — map to vary/replay with front priority.
    "advance.queue_now": ("vary", "advance", "front"),
    "refine.aspect": ("refine_backlog", "refine", "normal"),
    "refine.quality": ("refine_backlog", "refine", "normal"),
    "refine.edit": ("refine_backlog", "refine", "normal"),
    "extract.frame": ("extract", "extract", "normal"),
    "extract.clip": ("extract", "extract", "normal"),
    "extract.reference": ("extract", "extract", "normal"),
    "investigate.to_extract": ("investigate", "investigate", "normal"),
    "investigate.to_advance": ("investigate", "investigate", "normal"),
    "investigate.to_refine": ("investigate", "investigate", "normal"),
    "investigate.to_retire": ("investigate", "investigate", "normal"),
}

# Hooks that enqueue factory work (create/update work items on run-step).
FACTORY_ENQUEUE_HOOKS = frozenset({"replay", "replay_front", "extend"})


def default_work_items_index_path(og_root: Path) -> Path:
    return og_root.resolve().parent / "_status" / "work_items_index.json"


def new_work_id() -> str:
    """Time-sortable ULID-style id with ``wi:`` prefix."""
    ms = int(time.time() * 1000)
    chars: List[str] = []
    for _ in range(10):
        chars.append(_CROCKFORD[ms & 31])
        ms >>= 5
    chars.reverse()
    rand = secrets.randbits(80)
    for _ in range(16):
        chars.append(_CROCKFORD[rand & 31])
        rand >>= 5
    return "wi:" + "".join(chars)


def _init_doc() -> Dict[str, Any]:
    return {
        "version": WORK_ITEMS_VERSION,
        "schema": WORK_ITEMS_SCHEMA,
        "updated_at": utc_now(),
        "items": [],
    }


def load_work_items_doc(path: Path) -> Dict[str, Any]:
    if path.is_file():
        try:
            import json

            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                items = doc.get("items")
                if not isinstance(items, list):
                    doc["items"] = []
                doc.setdefault("schema", WORK_ITEMS_SCHEMA)
                doc.setdefault("version", WORK_ITEMS_VERSION)
                return doc
        except (OSError, ValueError):
            pass
    return _init_doc()


def save_work_items_doc(path: Path, doc: Dict[str, Any]) -> None:
    out = copy.deepcopy(doc) if isinstance(doc, dict) else _init_doc()
    out["schema"] = WORK_ITEMS_SCHEMA
    out["version"] = WORK_ITEMS_VERSION
    out["updated_at"] = utc_now()
    if not isinstance(out.get("items"), list):
        out["items"] = []
    _atomic_write_json_doc(path, out)


def normalize_priority(value: Any) -> str:
    p = str(value or "normal").strip().lower()
    return p if p in PRIORITIES else "normal"


def normalize_status(value: Any) -> str:
    s = str(value or "draft").strip().lower()
    return s if s in WORK_STATUSES else "draft"


def apply_priority_reshape(item: Dict[str, Any], desired: str) -> Dict[str, Any]:
    """
    Mutate ``item`` priority in place when safe.

    Returns flags: ``changed``, ``upgraded``, ``demoted``, ``skipped_running``,
    ``skipped_terminal``. Never mutates ``running`` (or terminal) items.
    """
    desired_n = normalize_priority(desired)
    cur = normalize_priority(item.get("priority"))
    st = normalize_status(item.get("status"))
    if desired_n == cur:
        return {
            "changed": False,
            "upgraded": False,
            "demoted": False,
            "skipped_running": False,
            "skipped_terminal": False,
        }
    if st == "running":
        return {
            "changed": False,
            "upgraded": False,
            "demoted": False,
            "skipped_running": True,
            "skipped_terminal": False,
        }
    if st in TERMINAL_STATUSES or st not in PRIORITY_MUTABLE_STATUSES:
        return {
            "changed": False,
            "upgraded": False,
            "demoted": False,
            "skipped_running": False,
            "skipped_terminal": st in TERMINAL_STATUSES,
        }
    item["priority"] = desired_n
    item["updated_at"] = utc_now()
    return {
        "changed": True,
        "upgraded": desired_n == "front" and cur != "front",
        "demoted": desired_n == "normal" and cur == "front",
        "skipped_running": False,
        "skipped_terminal": False,
    }


def set_work_item_priority(
    work_id: str,
    *,
    priority: str,
    work_items_index_path: Path,
) -> Dict[str, Any]:
    """Set priority with safe reshape rules (skip running / terminal)."""
    doc = load_work_items_doc(work_items_index_path)
    row = find_item_by_id(doc, work_id)
    if row is None:
        raise FileNotFoundError(f"work item not found: {work_id}")
    flags = apply_priority_reshape(row, priority)
    if flags.get("changed"):
        save_work_items_doc(work_items_index_path, doc)
    return {"ok": True, "item": copy.deepcopy(row), **flags}


def normalize_pool(value: Any) -> str:
    p = str(value or "").strip().lower()
    return p if p in POOLS else p or "vary"


def route_for_step(step_id: str) -> Optional[Tuple[str, str, str]]:
    sid = str(step_id or "").strip()
    return STEP_ROUTE_MAP.get(sid)


def build_idempotency_key(
    *,
    pool: str,
    source_group_id: str,
    factory_family: str = "",
    recipe: str = "",
) -> str:
    parts = [
        str(pool or "").strip().lower() or "pool",
        str(source_group_id or "").strip() or "unknown",
        str(factory_family or "").strip() or "-",
        str(recipe or "").strip() or "-",
    ]
    return ":".join(parts)


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    raw = str(ts or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _within_cooldown(item: Dict[str, Any], *, cooldown_s: int, now: Optional[datetime] = None) -> bool:
    if cooldown_s <= 0:
        return False
    created = _parse_iso(str(item.get("created_at") or item.get("updated_at") or ""))
    if created is None:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    ref = now or datetime.now(timezone.utc)
    return (ref - created).total_seconds() < cooldown_s


def find_item_by_id(doc: Dict[str, Any], work_id: str) -> Optional[Dict[str, Any]]:
    wid = str(work_id or "").strip()
    if not wid:
        return None
    for row in doc.get("items") or []:
        if isinstance(row, dict) and str(row.get("work_id") or "") == wid:
            return row
    return None


def find_open_by_idempotency(
    doc: Dict[str, Any],
    idempotency_key: str,
    *,
    cooldown_s: int = DEFAULT_IDEMPOTENCY_COOLDOWN_S,
) -> Optional[Dict[str, Any]]:
    key = str(idempotency_key or "").strip()
    if not key:
        return None
    for row in doc.get("items") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("idempotency_key") or "") != key:
            continue
        status = normalize_status(row.get("status"))
        if status in TERMINAL_STATUSES:
            continue
        if _within_cooldown(row, cooldown_s=cooldown_s):
            return row
    return None


def list_work_items(
    doc: Dict[str, Any],
    *,
    source_relpath: Optional[str] = None,
    source_group_id: Optional[str] = None,
    pool: Optional[str] = None,
    status: Optional[Sequence[str]] = None,
    include_terminal: bool = True,
) -> List[Dict[str, Any]]:
    rel = str(source_relpath or "").strip().replace("\\", "/")
    gid = str(source_group_id or "").strip()
    pool_f = str(pool or "").strip().lower() or None
    status_set: Optional[Set[str]] = None
    if status is not None:
        status_set = {normalize_status(s) for s in status}

    out: List[Dict[str, Any]] = []
    for row in doc.get("items") or []:
        if not isinstance(row, dict):
            continue
        st = normalize_status(row.get("status"))
        if not include_terminal and st in TERMINAL_STATUSES:
            continue
        if status_set is not None and st not in status_set:
            continue
        if pool_f and normalize_pool(row.get("pool")) != pool_f:
            continue
        if rel:
            row_rel = str(row.get("source_relpath") or "").replace("\\", "/")
            if row_rel != rel and Path(row_rel).name != Path(rel).name:
                continue
        if gid and str(row.get("source_group_id") or "") != gid:
            continue
        out.append(copy.deepcopy(row))
    out.sort(key=lambda r: str(r.get("updated_at") or r.get("created_at") or ""), reverse=True)
    return out


def work_items_for_item(item: Dict[str, Any], doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Enrichment fields for discovery library rows."""
    if not doc or not isinstance(item, dict):
        return {}
    rel = str(item.get("relpath") or item.get("video_relpath") or "").strip()
    gid = str(item.get("group_id") or "").strip()
    rows = list_work_items(doc, source_relpath=rel or None, source_group_id=gid or None)
    if not rows and gid:
        rows = list_work_items(doc, source_group_id=gid)
    open_rows = [r for r in rows if normalize_status(r.get("status")) in OPEN_STATUSES]
    return {
        "work_items": rows[:12],
        "work_items_open": open_rows[:8],
        "work_items_open_count": len(open_rows),
        "work_items_total_count": len(rows),
    }


def create_work_item(
    *,
    source_relpath: str,
    pool: str,
    disposition_entry: str,
    disposition_step: str = "",
    priority: str = "normal",
    status: str = "draft",
    factory_job_key: Optional[str] = None,
    factory_family: str = "",
    recipe: str = "",
    source_group_id: Optional[str] = None,
    error: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    work_items_index_path: Path,
    cooldown_s: int = DEFAULT_IDEMPOTENCY_COOLDOWN_S,
    force_new: bool = False,
) -> Dict[str, Any]:
    """
    Create (or reuse within cooldown) a work instance.

    Returns ``{"ok": True, "item": ..., "created": bool, "reused": bool}``.
    """
    rel = str(source_relpath or "").strip().replace("\\", "/")
    if not rel:
        raise ValueError("missing source_relpath")
    pool_n = normalize_pool(pool)
    if pool_n not in POOLS:
        raise ValueError(f"unknown pool: {pool}")
    entry = str(disposition_entry or "").strip()
    if not entry:
        raise ValueError("missing disposition_entry")
    gid = str(source_group_id or "").strip() or (_og_group_id_from_relpath(rel) or "")
    family = str(factory_family or "").strip()
    recipe_s = str(recipe or disposition_step or "").strip()
    idem = str(idempotency_key or "").strip() or build_idempotency_key(
        pool=pool_n,
        source_group_id=gid or Path(rel).stem,
        factory_family=family,
        recipe=recipe_s,
    )

    doc = load_work_items_doc(work_items_index_path)
    if not force_new:
        existing = find_open_by_idempotency(doc, idem, cooldown_s=cooldown_s)
        if existing is not None:
            # Promote/demote priority on reuse when safe (never while running).
            flags = apply_priority_reshape(existing, priority)
            if flags.get("changed"):
                save_work_items_doc(work_items_index_path, doc)
            return {
                "ok": True,
                "item": copy.deepcopy(existing),
                "created": False,
                "reused": True,
                **flags,
            }

    now = utc_now()
    item: Dict[str, Any] = {
        "work_id": new_work_id(),
        "source_relpath": rel,
        "source_group_id": gid or None,
        "pool": pool_n,
        "priority": normalize_priority(priority),
        "disposition_entry": entry,
        "disposition_step": str(disposition_step or "").strip() or None,
        "status": normalize_status(status),
        "created_at": now,
        "updated_at": now,
        "factory_job_key": str(factory_job_key).strip() if factory_job_key else None,
        "factory_family": family or None,
        "child_relpaths": [],
        "error": str(error).strip() if error else None,
        "idempotency_key": idem,
    }
    items = doc.setdefault("items", [])
    if not isinstance(items, list):
        doc["items"] = []
        items = doc["items"]
    items.append(item)
    save_work_items_doc(work_items_index_path, doc)
    return {"ok": True, "item": copy.deepcopy(item), "created": True, "reused": False}


def update_work_item(
    work_id: str,
    *,
    work_items_index_path: Path,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    factory_job_key: Optional[str] = None,
    factory_family: Optional[str] = None,
    error: Optional[str] = None,
    child_relpaths: Optional[Iterable[str]] = None,
    clear_error: bool = False,
) -> Dict[str, Any]:
    doc = load_work_items_doc(work_items_index_path)
    row = find_item_by_id(doc, work_id)
    if row is None:
        raise FileNotFoundError(f"work item not found: {work_id}")
    if status is not None:
        row["status"] = normalize_status(status)
    if priority is not None:
        row["priority"] = normalize_priority(priority)
    if factory_job_key is not None:
        row["factory_job_key"] = str(factory_job_key).strip() or None
    if factory_family is not None:
        row["factory_family"] = str(factory_family).strip() or None
    if clear_error:
        row["error"] = None
    elif error is not None:
        row["error"] = str(error).strip() or None
    if child_relpaths is not None:
        kids: List[str] = []
        seen: Set[str] = set()
        for c in child_relpaths:
            s = str(c or "").strip().replace("\\", "/")
            if s and s not in seen:
                seen.add(s)
                kids.append(s)
        row["child_relpaths"] = kids
    row["updated_at"] = utc_now()
    save_work_items_doc(work_items_index_path, doc)
    return {"ok": True, "item": copy.deepcopy(row)}


def cancel_work_item(
    work_id: str,
    *,
    work_items_index_path: Path,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    doc = load_work_items_doc(work_items_index_path)
    row = find_item_by_id(doc, work_id)
    if row is None:
        raise FileNotFoundError(f"work item not found: {work_id}")
    st = normalize_status(row.get("status"))
    if st == "running":
        # Do not cancel work that Comfy has already picked up.
        return {
            "ok": True,
            "item": copy.deepcopy(row),
            "already_terminal": False,
            "skipped_running": True,
            "cancelled": False,
        }
    if st in ("done", "cancelled"):
        return {
            "ok": True,
            "item": copy.deepcopy(row),
            "already_terminal": True,
            "skipped_running": False,
            "cancelled": False,
        }
    row["status"] = "cancelled"
    if reason:
        row["error"] = str(reason).strip()
    row["updated_at"] = utc_now()
    save_work_items_doc(work_items_index_path, doc)
    return {
        "ok": True,
        "item": copy.deepcopy(row),
        "already_terminal": False,
        "skipped_running": False,
        "cancelled": True,
    }


def record_run_step_work_item(
    *,
    source_relpath: str,
    step_id: str,
    hook: str,
    hook_result: Optional[Dict[str, Any]],
    work_items_index_path: Path,
    factory_family: str = "",
    recipe: str = "",
    priority_override: Optional[str] = None,
    cooldown_s: int = DEFAULT_IDEMPOTENCY_COOLDOWN_S,
) -> Optional[Dict[str, Any]]:
    """
    Create/update a work item after a disposition run-step.

    Only factory-enqueue hooks (replay / replay_front / extend) write rows.
    Returns the create/update payload, or None when the step is not work-tracked.
    """
    route = route_for_step(step_id)
    if route is None:
        return None
    pool, entry, default_priority = route
    hook_n = str(hook or "").strip().lower()
    if hook_n not in FACTORY_ENQUEUE_HOOKS:
        # Still allow explicit create for mapped steps that don't enqueue (e.g. open_trim)
        # — skip those for run-step auto-tracking.
        return None

    result = hook_result if isinstance(hook_result, dict) else {}
    ok = result.get("ok", True) is not False and not result.get("error") and not result.get("reason")
    job_key = str(result.get("job_key") or "").strip() or None
    family = str(result.get("family_slug") or factory_family or "").strip()
    priority = normalize_priority(priority_override or default_priority)
    if priority_override is None and (result.get("front") or hook_n == "replay_front"):
        priority = "front"

    status = "queued" if ok and job_key else ("failed" if not ok else "draft")
    err = None
    if not ok:
        err = str(result.get("error") or result.get("reason") or result.get("detail") or "hook_failed")

    created = create_work_item(
        source_relpath=source_relpath,
        pool=pool,
        disposition_entry=entry,
        disposition_step=step_id,
        priority=priority,
        status=status if status != "queued" else "draft",
        factory_job_key=None,
        factory_family=family,
        recipe=recipe or step_id,
        work_items_index_path=work_items_index_path,
        cooldown_s=cooldown_s,
        error=err if status == "failed" else None,
    )
    item = created.get("item") or {}
    wid = str(item.get("work_id") or "")
    if not wid:
        return created

    # Apply terminal enqueue fields after create/reuse.
    updated = update_work_item(
        wid,
        work_items_index_path=work_items_index_path,
        status=status,
        priority=priority,
        factory_job_key=job_key,
        factory_family=family or None,
        error=err,
        clear_error=ok,
    )
    return {
        "ok": True,
        "item": updated.get("item"),
        "created": bool(created.get("created")),
        "reused": bool(created.get("reused")),
        "from_run_step": True,
    }


def create_routes_batch(
    *,
    source_relpath: str,
    routes: Sequence[Dict[str, Any]],
    work_items_index_path: Path,
    queue_now: bool = False,
    cooldown_s: int = DEFAULT_IDEMPOTENCY_COOLDOWN_S,
) -> Dict[str, Any]:
    """
    Advance multi-route commit (Phase 2B API surface).

    Each route dict: ``{pool|step_id, priority?, factory_family?, recipe?}``.
    ``queue_now=True`` (Now) → ``priority: front`` for all routes.
    ``queue_now=False`` (Later) → ``priority: normal`` for all routes (overrides
    step defaults such as advance.vary→front), and demotes reusable open items
    when safe.
    """
    rel = str(source_relpath or "").strip().replace("\\", "/")
    if not rel:
        raise ValueError("missing source_relpath")
    if not routes:
        raise ValueError("missing routes")

    results: List[Dict[str, Any]] = []
    upgraded = 0
    demoted = 0
    skipped_running = 0
    for raw in routes:
        if not isinstance(raw, dict):
            continue
        step_id = str(raw.get("step_id") or raw.get("disposition_step") or "").strip()
        pool = str(raw.get("pool") or "").strip()
        entry = str(raw.get("disposition_entry") or "").strip()
        # Now/Later are explicit: ignore step default priority unless caller sets priority.
        if raw.get("priority"):
            priority = normalize_priority(raw.get("priority"))
        else:
            priority = "front" if queue_now else "normal"
        if step_id and not pool:
            mapped = route_for_step(step_id)
            if mapped:
                pool, entry, _default_pri = mapped
        if not pool:
            raise ValueError(f"route missing pool/step_id: {raw}")
        if not entry:
            # Infer entry from pool.
            entry = {
                "extend": "advance",
                "vary": "advance",
                "refine_backlog": "refine",
                "extract": "extract",
                "investigate": "investigate",
            }.get(normalize_pool(pool), "advance")
        out = create_work_item(
            source_relpath=rel,
            pool=pool,
            disposition_entry=entry,
            disposition_step=step_id,
            priority=priority,
            status="draft",
            factory_family=str(raw.get("factory_family") or "").strip(),
            recipe=str(raw.get("recipe") or step_id or "").strip(),
            work_items_index_path=work_items_index_path,
            cooldown_s=cooldown_s,
            force_new=bool(raw.get("force_new")),
        )
        if out.get("upgraded"):
            upgraded += 1
        if out.get("demoted"):
            demoted += 1
        if out.get("skipped_running"):
            skipped_running += 1
        results.append(out)
    return {
        "ok": True,
        "source_relpath": rel,
        "items": [x.get("item") for x in results if x.get("item")],
        "results": results,
        "count": len(results),
        "upgraded": upgraded,
        "demoted": demoted,
        "skipped_running": skipped_running,
        "queue_now": bool(queue_now),
    }
