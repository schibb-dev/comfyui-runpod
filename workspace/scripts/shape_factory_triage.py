#!/usr/bin/env python3
"""Triage passes: review sessions separate from mutable disposition."""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from shape_factory_disposition import is_retired_disposition, lookup_output_disposition
from shape_factory_ratings import _atomic_write_json_doc, utc_now

TRIAGE_SCHEMA_VERSION = 1
TRIAGE_INDEX_SCHEMA = "comfyui-runpod.triage-index.v0"


def default_triage_index_path(og_root: Path) -> Path:
    return og_root.resolve().parent / "_status" / "triage_index.json"


def _init_triage_doc() -> Dict[str, Any]:
    return {
        "version": TRIAGE_SCHEMA_VERSION,
        "schema": TRIAGE_INDEX_SCHEMA,
        "updated_at": utc_now(),
        "by_output_relpath": {},
    }


def _load_or_init_triage_doc(path: Path) -> Dict[str, Any]:
    if path.is_file():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                doc.setdefault("by_output_relpath", {})
                return doc
        except (OSError, json.JSONDecodeError):
            pass
    return _init_triage_doc()


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    raw = str(ts).strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def lookup_output_triage(output_path: str, triage_doc: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Resolve triage row by path variants (mirrors disposition lookup)."""
    table = (triage_doc or {}).get("by_output_relpath") or {}
    if not isinstance(table, dict):
        return None
    raw = str(output_path or "").strip().replace("\\", "/")
    if not raw:
        return None
    keys = [raw, Path(raw).name]
    if "/output/output/" in raw:
        keys.append(re.sub(r"^.*?/output/output/", "output/", raw))
    if "/og/" in raw:
        tail = raw.split("/og/", 1)[-1]
        keys.append(f"output/og/{tail.rstrip('/')}")
        keys.append(f"og/{tail.rstrip('/')}")
    expanded: list[str] = []
    for key in keys:
        key = key.strip().replace("\\", "/")
        if not key:
            continue
        expanded.append(key)
        for suffix in (".mp4", ".MP4", ".png", ".PNG", ".webm", ".WEBM"):
            if key.endswith(suffix):
                expanded.append(key[: -len(suffix)])
    seen: set[str] = set()
    for key in expanded:
        if not key or key in seen:
            continue
        seen.add(key)
        row = table.get(key)
        if isinstance(row, dict):
            return row
    return None


def _discovery_keys_for_relpath(media_relpath: str, og_root: Path, media_abs: Path) -> tuple[str, str]:
    from correlate_output_ratings import output_relpath_keys_from_xmp

    xmp_like = media_abs.with_suffix(".XMP")
    try:
        short_key, discovery_key = output_relpath_keys_from_xmp(xmp_like, og_root)
    except ValueError:
        short_key = ""
        discovery_key = str(media_relpath or "").replace("\\", "/")
    return short_key, discovery_key


def triage_for_item(
    item: dict[str, Any],
    triage_doc: Optional[Dict[str, Any]],
    *,
    disposition_doc: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not triage_doc:
        return {
            "needs_triage": True,
            "last_triaged_at": None,
            "triage_pass_count": 0,
            "last_disposition_at_triage": None,
        }
    rel = str(item.get("relpath") or item.get("video_relpath") or "").strip().replace("\\", "/")
    row = lookup_output_triage(rel, triage_doc) or {}
    last = row.get("last_triaged_at")
    return {
        "needs_triage": needs_triage_item(item, triage_doc=triage_doc, disposition_doc=disposition_doc),
        "last_triaged_at": last,
        "triage_pass_count": int(row.get("pass_count") or 0),
        "last_disposition_at_triage": row.get("last_disposition_at_triage"),
    }


def needs_triage_item(
    item: dict[str, Any],
    *,
    triage_doc: Optional[Dict[str, Any]],
    disposition_doc: Optional[Dict[str, Any]] = None,
) -> bool:
    rel = str(item.get("relpath") or item.get("video_relpath") or "").strip().replace("\\", "/")
    if disposition_doc:
        disp_row = lookup_output_disposition(rel, disposition_doc)
        if disp_row:
            markers = disp_row.get("markers") or []
            if is_retired_disposition(markers if isinstance(markers, list) else []):
                return False

    triage_row = lookup_output_triage(rel, triage_doc or {})
    if not triage_row:
        return True

    last_triaged = triage_row.get("last_triaged_at")
    if not last_triaged:
        return True

    if disposition_doc is None:
        return False

    disp_row = lookup_output_disposition(rel, disposition_doc)
    disp_updated = disp_row.get("updated_at") if isinstance(disp_row, dict) else None
    if not disp_updated:
        return False

    dt_triaged = _parse_iso(str(last_triaged))
    dt_disp = _parse_iso(str(disp_updated))
    if dt_triaged is None or dt_disp is None:
        return str(disp_updated) > str(last_triaged)
    return dt_disp > dt_triaged


def record_triage_pass(
    *,
    media_abs: Path,
    media_relpath: str,
    og_root: Path,
    triage_index_path: Path,
    disposition_doc: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    media_abs = Path(media_abs)
    if not media_abs.is_file():
        raise FileNotFoundError(str(media_abs))
    rel = str(media_relpath or "").strip().replace("\\", "/")
    if not rel:
        raise ValueError("missing relpath")

    og_root = Path(og_root).resolve()
    short_key, discovery_key = _discovery_keys_for_relpath(rel, og_root, media_abs)
    doc = _load_or_init_triage_doc(triage_index_path)
    table = doc.setdefault("by_output_relpath", {})

    row: Dict[str, Any] = {}
    for k in (discovery_key, short_key):
        if k and isinstance(table.get(k), dict):
            row = copy.deepcopy(table[k])
            break

    pass_count = int(row.get("pass_count") or 0) + 1
    now = utc_now()
    disp_updated = None
    if disposition_doc:
        disp_row = lookup_output_disposition(rel, disposition_doc)
        if isinstance(disp_row, dict):
            disp_updated = disp_row.get("updated_at")

    row = {
        "short_key": short_key,
        "last_triaged_at": now,
        "pass_count": pass_count,
        "last_disposition_at_triage": disp_updated,
    }
    for k in (discovery_key, short_key):
        if k:
            table[k] = row
    doc["updated_at"] = now
    _atomic_write_json_doc(triage_index_path, doc)

    return {
        "ok": True,
        "relpath": rel,
        "last_triaged_at": now,
        "pass_count": pass_count,
        "last_disposition_at_triage": disp_updated,
        "needs_triage": False,
        "discovery_key": discovery_key,
        "short_key": short_key,
    }


def has_entry_disposition(
    relpath: str,
    disposition_doc: Optional[Dict[str, Any]],
    catalog: Optional[Dict[str, Any]] = None,
) -> bool:
    if not disposition_doc:
        return False
    row = lookup_output_disposition(relpath, disposition_doc)
    if not row:
        return False
    markers = row.get("markers") or []
    if not isinstance(markers, list):
        return False
    entry_ids = {str(m["id"]) for m in (catalog or {}).get("markers") or [] if isinstance(m, dict) and m.get("kind") == "entry"}
    if not entry_ids:
        entry_ids = {"refine", "investigate", "extract", "advance", "retire", "park"}
    return any(str(m) in entry_ids for m in markers)
