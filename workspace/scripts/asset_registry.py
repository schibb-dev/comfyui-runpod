#!/usr/bin/env python3
"""
Content-addressed asset registry (SQLite).

Stable identity for images/videos via sha256 of file bytes, so assets survive
moves/renames. Foundation for job backfill, asset relocation, and image reorg.

Schema is created on demand; ``phash`` is reserved for future perceptual-hash
near-duplicate detection (kept nullable so it can be filled without migration).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

REGISTRY_BASENAME = "asset_registry.sqlite"
REGISTRY_SCHEMA_VERSION = 2

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".avif", ".gif", ".tiff", ".tif", ".jfif"}
_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".avi"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_registry_path(og_root: Path) -> Path:
    return og_root.resolve().parent / "_status" / REGISTRY_BASENAME


def kind_for_ext(ext: str) -> str:
    e = str(ext or "").lower()
    if not e.startswith("."):
        e = "." + e
    if e in _IMAGE_EXTS:
        return "image"
    if e in _VIDEO_EXTS:
        return "video"
    return "other"


def hash_file(path: Path, *, chunk_size: int = 1 << 20) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(chunk_size), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return None


def image_dims(path: Path) -> tuple[Optional[int], Optional[int]]:
    try:
        from PIL import Image

        with Image.open(path) as im:
            return int(im.width), int(im.height)
    except Exception:
        return None, None


def connect(registry_path: Path) -> sqlite3.Connection:
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(registry_path), timeout=30.0)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=30000")
    except sqlite3.Error:
        pass
    tables = {
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "assets" in tables and "meta" in tables:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(assets)")}
        if "mtime" in cols:
            # Hot path: schema already applied — no DDL/commit (avoids lock storms).
            return con

    dirty = "assets" not in tables or "meta" not in tables
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS assets (
            content_id TEXT PRIMARY KEY,
            size INTEGER,
            mtime REAL,
            ext TEXT,
            kind TEXT,
            width INTEGER,
            height INTEGER,
            current_relpath TEXT,
            first_seen TEXT,
            last_seen TEXT,
            status TEXT DEFAULT 'present',
            phash TEXT,
            moved_history TEXT DEFAULT '[]',
            refs TEXT DEFAULT '[]'
        )
        """
    )
    # Migrate pre-v2 registries that lack the mtime hash-cache column.
    cols = {r["name"] for r in con.execute("PRAGMA table_info(assets)")}
    if "mtime" not in cols:
        con.execute("ALTER TABLE assets ADD COLUMN mtime REAL")
        dirty = True
    con.execute("CREATE INDEX IF NOT EXISTS idx_assets_relpath ON assets(current_relpath)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_assets_ext ON assets(ext)")
    con.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
    )
    row = con.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()
    try:
        ver = int(row["value"]) if row else 0
    except (TypeError, ValueError, KeyError):
        ver = 0
    # Never decrease: clip schema (and others) may bump past REGISTRY_SCHEMA_VERSION.
    if ver < REGISTRY_SCHEMA_VERSION:
        con.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(REGISTRY_SCHEMA_VERSION),),
        )
        dirty = True
    if dirty:
        con.commit()
    return con


def _norm_rel(s: str) -> str:
    return str(s or "").replace("\\", "/").strip().lstrip("/")


def register(
    con: sqlite3.Connection,
    abs_path: Path,
    *,
    relpath: str,
    kind: Optional[str] = None,
    refs: Optional[Iterable[str]] = None,
    with_dims: bool = True,
    force_rehash: bool = False,
) -> Optional[str]:
    """
    Upsert one asset by content hash. Returns content_id (or None if unreadable).

    Efficiency: a file already registered at ``relpath`` whose ``size`` + ``mtime``
    are unchanged is treated as identical and its cached ``content_id`` is reused
    without rehashing (pass ``force_rehash=True`` to override).
    """
    abs_path = Path(abs_path)
    try:
        st = abs_path.stat()
    except OSError:
        return None
    rel = _norm_rel(relpath)
    size = int(st.st_size)
    mtime = float(st.st_mtime)
    now = _utc_now()
    new_refs = sorted({str(r) for r in (refs or []) if str(r).strip()})

    # Fast path: unchanged file at this relpath -> reuse cached hash, skip rehash.
    if not force_rehash:
        cached = con.execute(
            "SELECT * FROM assets WHERE current_relpath = ?", (rel,)
        ).fetchone()
        if (
            cached is not None
            and cached["size"] == size
            and cached["mtime"] is not None
            and abs(float(cached["mtime"]) - mtime) < 1e-6
        ):
            cid = cached["content_id"]
            merged = sorted(set(_json_list(cached["refs"])) | set(new_refs))
            con.execute(
                "UPDATE assets SET last_seen=?, status='present', refs=? WHERE content_id=?",
                (now, json.dumps(merged), cid),
            )
            con.commit()
            return cid

    content_id = hash_file(abs_path)
    if content_id is None:
        return None
    ext = abs_path.suffix.lower()
    kind = kind or kind_for_ext(ext)
    width = height = None
    if with_dims and kind == "image":
        width, height = image_dims(abs_path)

    row = con.execute("SELECT * FROM assets WHERE content_id = ?", (content_id,)).fetchone()
    if row is None:
        con.execute(
            """
            INSERT INTO assets(content_id, size, mtime, ext, kind, width, height,
                current_relpath, first_seen, last_seen, status, phash, moved_history, refs)
            VALUES(?,?,?,?,?,?,?,?,?,?, 'present', NULL, '[]', ?)
            """,
            (content_id, size, mtime, ext, kind, width, height, rel, now, now, json.dumps(new_refs)),
        )
    else:
        history = _json_list(row["moved_history"])
        prev_rel = row["current_relpath"]
        if prev_rel and prev_rel != rel and prev_rel not in history:
            history.append(prev_rel)
        merged_refs = sorted(set(_json_list(row["refs"])) | set(new_refs))
        con.execute(
            """
            UPDATE assets SET current_relpath=?, size=?, mtime=?, last_seen=?, status='present',
                moved_history=?, refs=?, width=COALESCE(?, width), height=COALESCE(?, height)
            WHERE content_id=?
            """,
            (rel, size, mtime, now, json.dumps(history), json.dumps(merged_refs), width, height, content_id),
        )
    con.commit()
    return content_id


def add_ref(con: sqlite3.Connection, content_id: str, ref: str) -> None:
    row = con.execute("SELECT refs FROM assets WHERE content_id=?", (content_id,)).fetchone()
    if row is None:
        return
    refs = sorted(set(_json_list(row["refs"])) | {str(ref)})
    con.execute("UPDATE assets SET refs=? WHERE content_id=?", (json.dumps(refs), content_id))
    con.commit()


def by_content_id(con: sqlite3.Connection, content_id: str) -> Optional[Dict[str, Any]]:
    row = con.execute("SELECT * FROM assets WHERE content_id=?", (content_id,)).fetchone()
    return _row_to_dict(row)


def by_relpath(con: sqlite3.Connection, relpath: str) -> Optional[Dict[str, Any]]:
    row = con.execute(
        "SELECT * FROM assets WHERE current_relpath=?", (_norm_rel(relpath),)
    ).fetchone()
    return _row_to_dict(row)


def by_basename(con: sqlite3.Connection, basename: str) -> List[Dict[str, Any]]:
    bn = Path(str(basename or "")).name
    if not bn:
        return []
    rows = con.execute(
        "SELECT * FROM assets WHERE current_relpath LIKE ?", (f"%/{bn}",)
    ).fetchall()
    # Also match when relpath is just the basename (no dir).
    rows += con.execute(
        "SELECT * FROM assets WHERE current_relpath=?", (bn,)
    ).fetchall()
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = _row_to_dict(r)
        if d and d["content_id"] not in seen:
            seen.add(d["content_id"])
            out.append(d)
    return out


def stats(con: sqlite3.Connection) -> Dict[str, Any]:
    total = con.execute("SELECT COUNT(*) AS n FROM assets").fetchone()["n"]
    by_kind = {
        r["kind"]: r["n"]
        for r in con.execute("SELECT kind, COUNT(*) AS n FROM assets GROUP BY kind").fetchall()
    }
    missing = con.execute("SELECT COUNT(*) AS n FROM assets WHERE status='missing'").fetchone()["n"]
    return {"total": total, "by_kind": by_kind, "missing": missing}


def _json_list(raw: Any) -> List[str]:
    if not raw:
        return []
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
        return [str(x) for x in v] if isinstance(v, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    d = dict(row)
    d["moved_history"] = _json_list(d.get("moved_history"))
    d["refs"] = _json_list(d.get("refs"))
    return d
