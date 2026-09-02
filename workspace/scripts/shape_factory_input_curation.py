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


def still_tags_path(data_root: Path) -> Path:
    return data_root / "shape_factory" / "input_still_tags.json"


def _normalize_still_tags_doc(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    items_raw = raw.get("items")
    if items_raw is None and isinstance(raw.get("tags"), dict):
        items_raw = raw.get("tags")
    items: Dict[str, Dict[str, Any]] = {}
    if isinstance(items_raw, dict):
        for cid, meta in items_raw.items():
            key = str(cid or "").strip().lower()
            if not key:
                continue
            tags: List[str] = []
            note = None
            if isinstance(meta, dict):
                tags = _dedupe_strs([str(t) for t in (meta.get("tags") or [])])
                note = str(meta.get("note") or "").strip() or None
            elif isinstance(meta, list):
                tags = _dedupe_strs([str(t) for t in meta])
            elif isinstance(meta, str) and meta.strip():
                tags = [meta.strip()]
            items[key] = {"tags": tags, "note": note, "updated_at": None}
            if isinstance(meta, dict) and meta.get("updated_at"):
                items[key]["updated_at"] = str(meta.get("updated_at"))
    return {"schema_version": SCHEMA_VERSION, "items": items, "updated_at": str(raw.get("updated_at") or "").strip() or None}


def load_still_tags(data_root: Path, *, fallback_paths: Optional[Sequence[Path]] = None) -> Dict[str, Any]:
    candidates = [still_tags_path(data_root)]
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
    return _normalize_still_tags_doc(raw)


def save_still_tags(path: Path, doc: Dict[str, Any]) -> Dict[str, Any]:
    norm = _normalize_still_tags_doc(doc)
    norm["updated_at"] = _utc_now_iso()
    _write_json(path, norm)
    return norm


def still_tags_for(data_root: Path, content_id: Optional[str]) -> Dict[str, Any]:
    cid = str(content_id or "").strip().lower()
    if not cid:
        return {"tags": [], "note": None}
    doc = load_still_tags(data_root)
    items = doc.get("items") if isinstance(doc.get("items"), dict) else {}
    meta = items.get(cid) if isinstance(items, dict) else None
    if not isinstance(meta, dict):
        return {"tags": [], "note": None}
    return {
        "tags": list(meta.get("tags") or []),
        "note": meta.get("note"),
        "updated_at": meta.get("updated_at"),
    }


def upsert_still_tags(
    data_root: Path,
    *,
    content_id: str,
    tags: Optional[Sequence[str]] = None,
    note: Optional[str] = None,
    write_path: Optional[Path] = None,
    fallback_paths: Optional[Sequence[Path]] = None,
) -> Dict[str, Any]:
    cid = str(content_id or "").strip().lower()
    if not cid:
        raise ValueError("missing_content_id")
    path = write_path or still_tags_path(data_root)
    doc = load_still_tags(data_root, fallback_paths=fallback_paths)
    items = doc.get("items") if isinstance(doc.get("items"), dict) else {}
    cur = items.get(cid) if isinstance(items.get(cid), dict) else {"tags": [], "note": None}
    next_tags = _dedupe_strs([str(t) for t in tags]) if tags is not None else list(cur.get("tags") or [])
    next_note = str(note).strip() if note is not None else cur.get("note")
    items[cid] = {
        "tags": next_tags,
        "note": (str(next_note).strip() or None) if next_note is not None else None,
        "updated_at": _utc_now_iso(),
    }
    doc["items"] = items
    return save_still_tags(path, doc)


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


def _normalize_still_appetite_filter(raw: str) -> str:
    text = str(raw or "").strip().lower().replace("-", "_")
    if text in {"", "all", "*"}:
        return ""
    if text in {"any", "marked", "set", "has"}:
        return "any"
    if text in {"none", "unmarked", "clear", "unset"}:
        return "none"
    if text in {"more", "fast_track", "less", "neutral"}:
        return text
    return ""


def _normalize_still_sort(raw: str) -> str:
    text = str(raw or "").strip().lower().replace("-", "_")
    if text in {"appetite", "appetite_first", "appetite_desc"}:
        return "appetite"
    return "newest"


def _still_appetite_lookup_maps(
    appetite_doc: Optional[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Map basename / input-rel / raw key → appetite row for still gallery joins."""
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(appetite_doc, dict):
        return out
    table = appetite_doc.get("by_output_relpath")
    if not isinstance(table, dict):
        return out
    for key, row in table.items():
        if not isinstance(row, dict):
            continue
        state = str(row.get("appetite") or "").strip()
        if not state:
            continue
        k = str(key or "").replace("\\", "/").strip()
        if not k:
            continue
        low = k.lower()
        # Prefer input/ image marks; skip obvious output videos.
        if low.endswith((".mp4", ".webm", ".mov")):
            continue
        if "/og/" in low or low.startswith("og/") or "/wip/" in low or low.startswith("wip/"):
            continue
        packed = {
            "appetite": state,
            "appetite_facet": str(row.get("facet") or row.get("appetite_facet") or "source").strip() or "source",
            "updated_at": row.get("updated_at"),
        }
        out[k] = packed
        bn = Path(k).name
        if bn:
            out[bn] = packed
            out[f"input/{bn}"] = packed
        if low.startswith("input/"):
            out[k[len("input/") :]] = packed
        elif "/input/" in low:
            rel = k.split("/input/", 1)[-1].lstrip("/")
            if rel:
                out[rel] = packed
                out[f"input/{rel}"] = packed
    return out


def _attach_still_appetite(
    item: Dict[str, Any],
    appetite_by_key: Dict[str, Dict[str, Any]],
) -> None:
    if not appetite_by_key or not isinstance(item, dict):
        return
    keys: List[str] = []
    rel = str(item.get("relpath") or "").replace("\\", "/").strip()
    bn = str(item.get("basename") or "").strip() or Path(str(item.get("path") or "")).name
    raw_path = str(item.get("path") or "").replace("\\", "/").strip()
    if rel:
        keys.append(rel)
        if not rel.lower().startswith("input/"):
            keys.append(f"input/{rel}")
    if bn:
        keys.append(f"input/{bn}")
        keys.append(bn)
    if raw_path:
        keys.append(raw_path)
        if "/input/" in raw_path:
            keys.append("input/" + raw_path.split("/input/", 1)[-1].lstrip("/"))
    seen: set[str] = set()
    for key in keys:
        key = str(key or "").strip().replace("\\", "/")
        if not key or key in seen:
            continue
        seen.add(key)
        hit = appetite_by_key.get(key)
        if isinstance(hit, dict) and hit.get("appetite"):
            item["appetite"] = hit.get("appetite")
            item["appetite_facet"] = hit.get("appetite_facet") or "source"
            return


def _still_appetite_matches(state: str, filt: str) -> bool:
    if not filt:
        return True
    s = str(state or "").strip()
    if filt == "any":
        return s in {"more", "fast_track", "less", "neutral"}
    if filt == "none":
        return s not in {"more", "fast_track", "less", "neutral"}
    return s == filt


def list_catalog_stills(
    *,
    data_root: Path,
    q: str = "",
    limit: int = 200,
    offset: int = 0,
    scan: bool = False,
    tag: str = "",
    appetite: str = "",
    sort: str = "newest",
    appetite_doc: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from input_still_catalog import (  # type: ignore
        default_catalog_path,
        default_input_root,
        is_download_copy_name,
        resolve_catalog_still_path,
        scan_input_stills,
        still_relpath_for_comfy,
    )

    input_root = default_input_root()
    if scan:
        scan_input_stills(input_root=input_root, catalog_path=default_catalog_path(data_root=data_root))
    cat = default_catalog_path(data_root=data_root)
    appetite_filt = _normalize_still_appetite_filter(appetite)
    sort_mode = _normalize_still_sort(sort)
    appetite_by_key = _still_appetite_lookup_maps(appetite_doc) if (appetite_filt or sort_mode == "appetite" or appetite_doc) else {}
    need_appetite_join = bool(appetite_filt or sort_mode == "appetite" or appetite_by_key)
    if not cat.is_file():
        return {
            "ok": True,
            "catalog_path": str(cat),
            "input_root": str(input_root),
            "items": [],
            "count": 0,
            "total": 0,
            "appetite": appetite_filt or None,
            "sort": sort_mode,
        }
    lim = max(1, min(2000, int(limit or 200)))
    off = max(0, int(offset or 0))
    where = " WHERE 1=1 "
    args: List[Any] = []
    qn = str(q or "").strip().lower()
    if qn:
        where += " AND lower(path) LIKE ? "
        args.append(f"%{qn}%")
    tag_n = str(tag or "").strip().lower()
    tags_doc = load_still_tags(data_root)
    tags_items = tags_doc.get("items") if isinstance(tags_doc.get("items"), dict) else {}
    tagged_ids: Optional[set[str]] = None
    if tag_n:
        tagged_ids = set()
        for cid, meta in tags_items.items():
            tags = []
            if isinstance(meta, dict):
                tags = meta.get("tags") or []
            elif isinstance(meta, list):
                tags = meta
            if any(str(t).strip().lower() == tag_n for t in tags):
                tagged_ids.add(str(cid).strip().lower())
        # Also match SQLite effective / provisional tags when present.
        try:
            from vision_still_tags import connect, default_db_path, effective_tags_for_row, ensure_db  # type: ignore

            dbp = default_db_path(data_root=data_root)
            if dbp.is_file():
                con_tags = connect(dbp)
                try:
                    for row in con_tags.execute("SELECT * FROM still_tag_items"):
                        eff = effective_tags_for_row(row)
                        if any(t == tag_n for t in eff):
                            tagged_ids.add(str(row["content_id"]).lower())
                finally:
                    con_tags.close()
            else:
                ensure_db(dbp)
        except Exception:
            pass

    total = 0
    items: List[Dict[str, Any]] = []
    skipped_missing = 0
    skipped_download_copies = 0
    seen_resolved: set[str] = set()
    sql_offset = 0
    batch = 400
    # Positive appetite filters are tiny vs the catalog — resolve marks first.
    positive_appetite = appetite_filt in {"any", "more", "fast_track", "less", "neutral"}
    # Appetite filter/sort needs a full filtered set before paging so totals stay honest.
    collect_all = bool(appetite_filt or sort_mode == "appetite")
    need = None if collect_all else (off + lim)
    exhausted = False

    def _push_resolved(resolved: Path, *, catalog_path: str = "", mtime: float = 0.0, first_seen: float = 0.0, last_seen: float = 0.0, size: int = 0) -> bool:
        nonlocal skipped_download_copies
        if is_download_copy_name(resolved.name):
            skipped_download_copies += 1
            return False
        resolved_key = str(resolved)
        if resolved_key in seen_resolved:
            skipped_download_copies += 1
            return False
        seen_resolved.add(resolved_key)
        content_id = _extract_content_id(str(resolved)) or _extract_content_id(catalog_path)
        if tagged_ids is not None and (not content_id or content_id not in tagged_ids):
            return False
        if qn:
            hay = f"{resolved} {catalog_path} {resolved.name}".lower()
            if qn not in hay:
                return False
        relpath = still_relpath_for_comfy(resolved, input_root=input_root)
        meta = tags_items.get(str(content_id or "").lower()) if content_id else None
        tags_list: List[str] = []
        note = None
        if isinstance(meta, dict):
            tags_list = list(meta.get("tags") or [])
            note = meta.get("note")
        item = {
            "path": str(resolved),
            "catalog_path": catalog_path or str(resolved),
            "basename": resolved.name,
            "relpath": relpath,
            "url": "/files/" + relpath.replace("\\", "/"),
            "thumb_url": "/files/" + relpath.replace("\\", "/"),
            "size": int(size or 0),
            "mtime": float(mtime or 0.0),
            "first_seen": float(first_seen or 0.0),
            "last_seen": float(last_seen or 0.0),
            "content_id": content_id,
            "tags": tags_list,
            "note": note,
        }
        if need_appetite_join:
            _attach_still_appetite(item, appetite_by_key)
            if not _still_appetite_matches(str(item.get("appetite") or ""), appetite_filt):
                return False
        try:
            st = resolved.stat()
            if not item["mtime"]:
                item["mtime"] = float(st.st_mtime)
            if not item["size"]:
                item["size"] = int(st.st_size)
            if not item["first_seen"]:
                item["first_seen"] = float(st.st_mtime)
            if not item["last_seen"]:
                item["last_seen"] = float(st.st_mtime)
        except OSError:
            pass
        items.append(item)
        return True

    if positive_appetite and isinstance(appetite_doc, dict):
        table = appetite_doc.get("by_output_relpath")
        if isinstance(table, dict):
            for key, row in table.items():
                if not isinstance(row, dict):
                    continue
                state = str(row.get("appetite") or "").strip()
                if not _still_appetite_matches(state, appetite_filt):
                    continue
                k = str(key or "").replace("\\", "/").strip()
                if not k:
                    continue
                low = k.lower()
                if low.endswith((".mp4", ".webm", ".mov")):
                    continue
                if "/og/" in low or low.startswith("og/") or "/wip/" in low or low.startswith("wip/"):
                    continue
                candidates = [k]
                if low.startswith("input/"):
                    candidates.append(k.split("/", 1)[-1])
                bn = Path(k).name
                if bn:
                    candidates.append(bn)
                    candidates.append(f"input/{bn}")
                resolved = None
                for cand in candidates:
                    resolved = resolve_catalog_still_path(cand, input_root=input_root)
                    if resolved is not None:
                        break
                    try:
                        p = Path(cand).expanduser()
                        if not p.is_file() and low.startswith("input/"):
                            p = input_root / cand.split("/", 1)[-1]
                        elif not p.is_file() and bn:
                            p = input_root / bn
                        if p.is_file():
                            resolved = p.resolve()
                            break
                    except OSError:
                        continue
                if resolved is None:
                    skipped_missing += 1
                    continue
                _push_resolved(resolved, catalog_path=k)
        if sort_mode == "appetite" or appetite_filt:
            items.sort(
                key=lambda it: (
                    -_appetite_rank(str(it.get("appetite") or "")),
                    -float(it.get("first_seen") or 0.0),
                    -float(it.get("mtime") or 0.0),
                    str(it.get("basename") or ""),
                )
            )
        try:
            from vision_still_tags import enrich_still_items  # type: ignore

            enrich_still_items(items, data_root=data_root)
        except Exception:
            pass
        total = len(items)
        page = items[off : off + lim]
        return {
            "ok": True,
            "catalog_path": str(cat),
            "input_root": str(input_root),
            "items": page,
            "count": len(page),
            "total": total,
            "resolved_total": total,
            "skipped_missing": skipped_missing,
            "skipped_download_copies": skipped_download_copies,
            "limit": lim,
            "offset": off,
            "next_offset": off + len(page),
            "has_more": off + len(page) < total,
            "tag": tag_n or None,
            "appetite": appetite_filt or None,
            "sort": sort_mode,
        }

    con = sqlite3.connect(str(cat), timeout=30.0)
    con.row_factory = sqlite3.Row
    try:
        total = int(con.execute(f"SELECT COUNT(*) FROM stills {where}", tuple(args)).fetchone()[0])
        # Walk catalog rows until we can fill offset+limit of *resolved* stills
        # (or the full matching set when appetite filter/sort is active).
        while (need is None or len(items) < need) and not exhausted:
            rows = con.execute(
                f"""
                SELECT path, size, mtime, first_seen, last_seen
                FROM stills
                {where}
                ORDER BY first_seen DESC, mtime DESC
                LIMIT ? OFFSET ?
                """,
                (*args, batch, sql_offset),
            ).fetchall()
            if not rows:
                exhausted = True
                break
            sql_offset += len(rows)
            if len(rows) < batch:
                exhausted = True
            for r in rows:
                resolved = resolve_catalog_still_path(str(r["path"]), input_root=input_root)
                if resolved is None:
                    skipped_missing += 1
                    continue
                if not _push_resolved(
                    resolved,
                    catalog_path=str(r["path"]),
                    mtime=float(r["mtime"] or 0.0),
                    first_seen=float(r["first_seen"] or 0.0),
                    last_seen=float(r["last_seen"] or 0.0),
                    size=int(r["size"] or 0),
                ):
                    continue
                if need is not None and len(items) >= need:
                    break
    finally:
        con.close()

    try:
        from vision_still_tags import enrich_still_items  # type: ignore

        enrich_still_items(items, data_root=data_root)
    except Exception:
        pass

    if sort_mode == "appetite":
        items.sort(
            key=lambda it: (
                -_appetite_rank(str(it.get("appetite") or "")),
                -float(it.get("first_seen") or 0.0),
                -float(it.get("mtime") or 0.0),
                str(it.get("basename") or ""),
            )
        )

    if collect_all:
        total = len(items)
        page = items[off : off + lim]
        has_more = off + len(page) < total
    else:
        # Attach appetite onto the page even when not filtering/sorting by it.
        if appetite_by_key:
            for it in items:
                if "appetite" not in it:
                    _attach_still_appetite(it, appetite_by_key)
        page = items[off : off + lim]
        has_more = len(page) >= lim and (not exhausted or len(items) > off + lim)

    return {
        "ok": True,
        "catalog_path": str(cat),
        "input_root": str(input_root),
        "items": page,
        "count": len(page),
        "total": total,
        "resolved_total": len(items),
        "skipped_missing": skipped_missing,
        "skipped_download_copies": skipped_download_copies,
        "limit": lim,
        "offset": off,
        "next_offset": off + len(page),
        "has_more": bool(has_more),
        "tag": tag_n or None,
        "appetite": appetite_filt or None,
        "sort": sort_mode,
    }


def _appetite_rank(state: str) -> float:
    # Mirror shape_factory_ratings.APPETITE_SCORE without a hard import cycle.
    return {"less": 1.0, "neutral": 2.5, "more": 4.0, "fast_track": 5.0}.get(str(state or "").strip(), 0.0)


def _job_output_path_keys(job: Dict[str, Any]) -> List[str]:
    keys: List[str] = []
    submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    outs = submit.get("outputs") if isinstance(submit.get("outputs"), list) else []
    if not outs and isinstance(job.get("outputs"), list):
        outs = job.get("outputs") or []
    for o in outs:
        if isinstance(o, dict):
            for k in ("relpath", "path", "basename"):
                v = str(o.get(k) or "").strip().replace("\\", "/")
                if v:
                    keys.append(v)
                    keys.append(Path(v).name)
        elif isinstance(o, str) and o.strip():
            keys.append(o.strip().replace("\\", "/"))
            keys.append(Path(o.strip()).name)
    deposit = str(job.get("deposit_path") or job.get("output_path") or "").strip().replace("\\", "/")
    if deposit:
        keys.append(deposit)
        keys.append(Path(deposit).name)
    return _dedupe_strs(keys)


def _job_source_still_path(job: Dict[str, Any]) -> str:
    bindings = job.get("bindings") if isinstance(job.get("bindings"), dict) else {}
    for slot in ("source_still", "identity_anchor"):
        row = bindings.get(slot)
        if isinstance(row, dict):
            p = str(row.get("path") or "").strip()
            if p:
                return p
        elif isinstance(row, str) and row.strip():
            return row.strip()
    return ""


def list_appetite_source_seeds(
    *,
    family_slug: str,
    appetite_doc: Dict[str, Any],
    jobs: Sequence[Dict[str, Any]],
    limit: int = 40,
    min_states: Sequence[str] = ("more", "fast_track"),
    facets: Sequence[str] = ("source", "both"),
) -> Dict[str, Any]:
    """
    Suggest source_still paths for curation from high-appetite family outputs.

    Appetite is a "do more WITH this" signal on outputs. Facet ``source`` / ``both``
    credits the material — those stills are the ones worth attaching to pools.
    """
    wanted_states = {str(s).strip() for s in min_states if str(s).strip()}
    wanted_facets = {str(f).strip() for f in facets if str(f).strip()} or {"source", "both"}
    table = appetite_doc.get("by_output_relpath") if isinstance(appetite_doc, dict) else {}
    if not isinstance(table, dict):
        table = {}

    # Index appetite rows by basename + full key for cheap job matching.
    appetite_by_key: Dict[str, Dict[str, Any]] = {}
    for key, row in table.items():
        if not isinstance(row, dict):
            continue
        state = str(row.get("appetite") or "").strip()
        if state not in wanted_states:
            continue
        facet = str(row.get("facet") or row.get("appetite_facet") or "both").strip() or "both"
        if facet not in wanted_facets:
            continue
        k = str(key or "").strip().replace("\\", "/")
        if not k:
            continue
        packed = {"appetite": state, "facet": facet, "updated_at": row.get("updated_at"), "key": k}
        appetite_by_key[k] = packed
        appetite_by_key[Path(k).name] = packed

    slug = str(family_slug or "").strip()
    seeds: List[Dict[str, Any]] = []
    seen_still: set[str] = set()
    seen_content: set[str] = set()

    for job in jobs:
        if not isinstance(job, dict):
            continue
        if slug and str(job.get("family_slug") or "").strip() != slug:
            continue
        still = _job_source_still_path(job)
        if not still:
            continue
        matched: Optional[Dict[str, Any]] = None
        for ok in _job_output_path_keys(job):
            hit = appetite_by_key.get(ok) or appetite_by_key.get(Path(ok).name)
            if hit:
                matched = hit
                break
        if not matched:
            continue
        still_key = still
        cid = _extract_content_id(still)
        if still_key in seen_still or (cid and cid in seen_content):
            continue
        seen_still.add(still_key)
        if cid:
            seen_content.add(cid)
        seeds.append(
            {
                "path": still,
                "basename": Path(still).name,
                "content_id": cid,
                "appetite": matched.get("appetite"),
                "facet": matched.get("facet"),
                "updated_at": matched.get("updated_at"),
                "job_key": job.get("job_key"),
                "output_key": matched.get("key"),
            }
        )

    seeds.sort(
        key=lambda s: (
            _appetite_rank(str(s.get("appetite") or "")),
            str(s.get("updated_at") or ""),
        ),
        reverse=True,
    )
    lim = max(1, min(200, int(limit)))
    page = seeds[:lim]
    return {
        "ok": True,
        "family_slug": slug,
        "count": len(page),
        "total": len(seeds),
        "items": page,
        "min_states": sorted(wanted_states),
        "facets": sorted(wanted_facets),
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
