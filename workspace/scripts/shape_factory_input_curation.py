#!/usr/bin/env python3
"""Input curation registry + source_still merge helpers."""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCHEMA_VERSION = "v1"
_SHA256_RE = re.compile(r"([0-9a-f]{64})", re.IGNORECASE)


def _utc_now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _dedupe_strs(values: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for raw in values:
        v = str(raw or "").strip()
        if not v or v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _extract_content_id(path: str) -> Optional[str]:
    m = _SHA256_RE.search(Path(str(path or "")).name)
    if not m:
        return None
    return m.group(1).lower()


def _normalize_abs_path(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except Exception:
        return path.expanduser()


def collections_path(data_root: Path) -> Path:
    return data_root / "shape_factory" / "input_collections.json"


def bindings_path(data_root: Path) -> Path:
    return data_root / "shape_factory" / "input_collection_bindings.json"


def _coerce_collection_items(raw_items: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(raw_items, list):
        return out
    for item in raw_items:
        if isinstance(item, str):
            path = item.strip()
            if not path:
                continue
            out.append({"path": path})
            continue
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or item.get("abs_path") or item.get("relpath") or "").strip()
        if not path:
            continue
        rec = {
            "path": path,
            "added_at": str(item.get("added_at") or "").strip() or None,
            "note": str(item.get("note") or "").strip() or None,
            "content_id": str(item.get("content_id") or "").strip().lower() or _extract_content_id(path),
        }
        out.append(rec)
    deduped: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for rec in out:
        key = str(rec.get("path") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(rec)
    return deduped


def _normalize_collections_doc(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    rows_raw = raw.get("collections")
    if rows_raw is None and isinstance(raw.get("items"), list):
        rows_raw = raw.get("items")
    rows: List[Dict[str, Any]] = []
    for ent in rows_raw or []:
        if not isinstance(ent, dict):
            continue
        cid = str(ent.get("id") or ent.get("collection_id") or "").strip()
        name = str(ent.get("name") or "").strip()
        if not cid:
            cid = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-") or f"collection-{len(rows)+1}"
        if not name:
            name = cid
        rows.append(
            {
                "id": cid,
                "name": name,
                "description": str(ent.get("description") or "").strip() or None,
                "created_at": str(ent.get("created_at") or "").strip() or None,
                "updated_at": str(ent.get("updated_at") or "").strip() or None,
                "items": _coerce_collection_items(ent.get("items")),
            }
        )
    rows.sort(key=lambda r: str(r.get("name") or r.get("id") or "").lower())
    return {
        "schema_version": SCHEMA_VERSION,
        "collections": rows,
        "updated_at": str(raw.get("updated_at") or "").strip() or None,
    }


def _normalize_bindings_doc(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    fams_raw = raw.get("families")
    if fams_raw is None and isinstance(raw.get("bindings"), dict):
        fams_raw = raw.get("bindings")
    families: Dict[str, List[str]] = {}
    if isinstance(fams_raw, dict):
        for fam, vals in fams_raw.items():
            slug = str(fam or "").strip()
            if not slug:
                continue
            if isinstance(vals, list):
                families[slug] = _dedupe_strs([str(v) for v in vals])
            elif isinstance(vals, str):
                families[slug] = _dedupe_strs([vals])
    return {
        "schema_version": SCHEMA_VERSION,
        "families": families,
        "updated_at": str(raw.get("updated_at") or "").strip() or None,
    }


def load_collections(data_root: Path, *, fallback_paths: Optional[Sequence[Path]] = None) -> Dict[str, Any]:
    candidates = [collections_path(data_root)]
    for p in fallback_paths or []:
        if p not in candidates:
            candidates.append(p)
    raw: Any = {}
    for path in candidates:
        try:
            raw = _read_json(path)
            break
        except Exception:
            raw = {}
    return _normalize_collections_doc(raw)


def save_collections(path: Path, doc: Dict[str, Any]) -> Dict[str, Any]:
    norm = _normalize_collections_doc(doc)
    norm["updated_at"] = _utc_now_iso()
    _write_json(path, norm)
    return norm


def load_bindings(data_root: Path, *, fallback_paths: Optional[Sequence[Path]] = None) -> Dict[str, Any]:
    candidates = [bindings_path(data_root)]
    for p in fallback_paths or []:
        if p not in candidates:
            candidates.append(p)
    raw: Any = {}
    for path in candidates:
        try:
            raw = _read_json(path)
            break
        except Exception:
            raw = {}
    return _normalize_bindings_doc(raw)


def save_bindings(path: Path, doc: Dict[str, Any]) -> Dict[str, Any]:
    norm = _normalize_bindings_doc(doc)
    norm["updated_at"] = _utc_now_iso()
    _write_json(path, norm)
    return norm


def collections_by_id(doc: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for ent in doc.get("collections") or []:
        if not isinstance(ent, dict):
            continue
        cid = str(ent.get("id") or "").strip()
        if cid:
            out[cid] = ent
    return out


def merged_source_stills(
    *,
    family_slug: str,
    base_members: Sequence[Path],
    data_root: Path,
    workspace_root: Optional[Path] = None,
    output_root: Optional[Path] = None,
    fallback_collections_paths: Optional[Sequence[Path]] = None,
    fallback_bindings_paths: Optional[Sequence[Path]] = None,
) -> Dict[str, Any]:
    """Merge pool stills with family-attached collection stills."""
    bound = load_bindings(data_root, fallback_paths=fallback_bindings_paths).get("families") or {}
    attached_ids = _dedupe_strs((bound.get(str(family_slug)) or []) if isinstance(bound, dict) else [])
    if not attached_ids:
        return {
            "members": [Path(p).expanduser().resolve() for p in base_members],
            "attached_count": 0,
            "added_count": 0,
            "missing_count": 0,
            "deduped_count": 0,
            "attached_collection_ids": [],
        }
    collections_doc = load_collections(data_root, fallback_paths=fallback_collections_paths)
    by_id = collections_by_id(collections_doc)

    # Resolve lazily to avoid importing shape_factory_map during tests that mock everything else.
    from shape_factory_map import resolve_existing_path  # type: ignore

    merged: List[Path] = []
    seen_path: set[str] = set()
    seen_content: set[str] = set()
    deduped = 0

    def push(path_obj: Path) -> bool:
        nonlocal deduped
        resolved = _normalize_abs_path(path_obj)
        pkey = str(resolved)
        cid = _extract_content_id(pkey)
        if pkey in seen_path or (cid and cid in seen_content):
            deduped += 1
            return False
        seen_path.add(pkey)
        if cid:
            seen_content.add(cid)
        merged.append(resolved)
        return True

    for p in base_members:
        push(_normalize_abs_path(p))

    added = 0
    missing = 0
    for cid in attached_ids:
        coll = by_id.get(cid)
        if not isinstance(coll, dict):
            continue
        for item in coll.get("items") or []:
            if not isinstance(item, dict):
                continue
            raw = str(item.get("path") or "").strip()
            if not raw:
                continue
            try:
                resolved = resolve_existing_path(
                    raw,
                    output_root=Path(output_root).expanduser().resolve() if output_root else data_root,
                    data_root=data_root,
                    workspace_root=Path(workspace_root).expanduser().resolve() if workspace_root else data_root.parent,
                )
            except Exception:
                missing += 1
                continue
            if push(resolved):
                added += 1

    return {
        "members": merged,
        "attached_count": len(attached_ids),
        "added_count": added,
        "missing_count": missing,
        "deduped_count": deduped,
        "attached_collection_ids": attached_ids,
    }


def list_catalog_stills(
    *,
    data_root: Path,
    q: str = "",
    limit: int = 200,
    offset: int = 0,
    scan: bool = False,
) -> Dict[str, Any]:
    from input_still_catalog import default_catalog_path, default_input_root, scan_input_stills  # type: ignore

    if scan:
        scan_input_stills(input_root=default_input_root(), catalog_path=default_catalog_path(data_root=data_root))
    cat = default_catalog_path(data_root=data_root)
    if not cat.is_file():
        return {"ok": True, "catalog_path": str(cat), "items": [], "count": 0, "total": 0}
    lim = max(1, min(2000, int(limit or 200)))
    off = max(0, int(offset or 0))
    where = " WHERE 1=1 "
    args: List[Any] = []
    qn = str(q or "").strip().lower()
    if qn:
        where += " AND lower(path) LIKE ? "
        args.append(f"%{qn}%")
    total = 0
    rows: List[sqlite3.Row] = []
    con = sqlite3.connect(str(cat), timeout=30.0)
    con.row_factory = sqlite3.Row
    try:
        total = int(con.execute(f"SELECT COUNT(*) FROM stills {where}", tuple(args)).fetchone()[0])
        rows = con.execute(
            f"""
            SELECT path, size, mtime, first_seen, last_seen
            FROM stills
            {where}
            ORDER BY first_seen DESC, mtime DESC
            LIMIT ? OFFSET ?
            """,
            (*args, lim, off),
        ).fetchall()
    finally:
        con.close()
    items: List[Dict[str, Any]] = []
    for r in rows:
        p = _normalize_abs_path(Path(str(r["path"])))
        if not p.is_file():
            continue
        items.append(
            {
                "path": str(p),
                "basename": p.name,
                "size": int(r["size"] or 0),
                "mtime": float(r["mtime"] or 0.0),
                "first_seen": float(r["first_seen"] or 0.0),
                "last_seen": float(r["last_seen"] or 0.0),
                "content_id": _extract_content_id(str(p)),
            }
        )
    return {
        "ok": True,
        "catalog_path": str(cat),
        "items": items,
        "count": len(items),
        "total": total,
        "limit": lim,
        "offset": off,
    }


def choose_writable_path(primary: Path, fallbacks: Sequence[Path]) -> Path:
    candidates = [primary, *list(fallbacks or [])]
    for cand in candidates:
        try:
            cand.parent.mkdir(parents=True, exist_ok=True)
            probe = cand.parent / f".__w_{os.getpid()}_{int(_dt.datetime.utcnow().timestamp())}"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return cand
        except Exception:
            continue
    return primary
