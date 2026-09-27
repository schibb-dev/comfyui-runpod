"""Durable parent→child lineage adjacency for fast descendant counts.

Built from ``discovery_lineage_edges.json``; refreshed when edges are persisted.
Does not replace the JSON graph — it is a query projection.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_SHA256_RE = re.compile(r"[0-9a-f]{64}", re.I)
_lock = threading.RLock()
# path → (mtime, size, built_at_monotonic)
_META: Dict[str, Tuple[float, int, float]] = {}

DB_BASENAME = "discovery_lineage_adjacency.sqlite"


def adjacency_db_path(edges_path: Path) -> Path:
    return Path(edges_path).with_name(DB_BASENAME)


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path), timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS edges (
          parent_group_id TEXT NOT NULL,
          child_group_id TEXT NOT NULL,
          via_source_raw TEXT NOT NULL DEFAULT '',
          evidence TEXT,
          PRIMARY KEY (parent_group_id, child_group_id, via_source_raw)
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_adj_parent ON edges(parent_group_id)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_adj_child ON edges(child_group_id)"
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS node_stats (
          group_id TEXT PRIMARY KEY,
          direct_child_count INTEGER NOT NULL DEFAULT 0,
          descendant_count INTEGER NOT NULL DEFAULT 0,
          updated_at REAL NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS key_alias (
          parent_key TEXT NOT NULL,
          group_id TEXT NOT NULL,
          PRIMARY KEY (parent_key, group_id)
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_adj_key ON key_alias(parent_key)"
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        )
        """
    )
    return con


def match_keys_for_media(rel_or_name: str) -> Set[str]:
    raw = str(rel_or_name or "").strip().replace("\\", "/").lstrip("/")
    if not raw:
        return set()
    bn = Path(raw).name
    stem = Path(bn).stem
    keys = {raw.lower(), bn.lower()}
    if stem:
        keys.add(stem.lower())
    if bn:
        keys.add(f"input/{bn.lower()}")
    hit = _SHA256_RE.search(bn) or _SHA256_RE.search(raw)
    if hit:
        keys.add(hit.group(0).lower())
    p = raw
    while p.lower().startswith("output/"):
        p = p[7:]
        if p:
            keys.add(p.lower())
    return {k for k in keys if len(k) >= 4}


def workspace_input_group_id(norm_in: str) -> str:
    n = str(norm_in or "").strip().replace("\\", "/").lstrip("/")
    bn = Path(n).name
    return f"input:{bn}" if bn else f"input:{n}"


def _load_edges_doc(edges_path: Path) -> Dict[str, Any]:
    if not edges_path.is_file():
        return {"edges": []}
    try:
        doc = json.loads(edges_path.read_text(encoding="utf-8"))
    except Exception:
        return {"edges": []}
    return doc if isinstance(doc, dict) else {"edges": []}


def _recompute_stats(con: sqlite3.Connection) -> None:
    """Refresh direct + transitive descendant counts for every parent."""
    fwd: Dict[str, Set[str]] = {}
    for row in con.execute("SELECT parent_group_id, child_group_id FROM edges"):
        p = str(row["parent_group_id"] or "").strip()
        c = str(row["child_group_id"] or "").strip()
        if not p or not c or p == c:
            continue
        fwd.setdefault(p, set()).add(c)
    now = time.time()
    con.execute("DELETE FROM node_stats")
    rows: List[Tuple[str, int, int, float]] = []
    for parent, kids in fwd.items():
        direct = len(kids)
        seen: Set[str] = set()
        stack = list(kids)
        while stack:
            cur = stack.pop()
            if cur in seen or cur == parent:
                continue
            seen.add(cur)
            for ch in fwd.get(cur, ()):
                if ch not in seen:
                    stack.append(ch)
        rows.append((parent, direct, len(seen), now))
    if rows:
        con.executemany(
            """
            INSERT OR REPLACE INTO node_stats
              (group_id, direct_child_count, descendant_count, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            rows,
        )


def _rebuild_locked(con: sqlite3.Connection, edges_path: Path) -> Dict[str, Any]:
    doc = _load_edges_doc(edges_path)
    edges = doc.get("edges") if isinstance(doc.get("edges"), list) else []
    con.execute("DELETE FROM edges")
    con.execute("DELETE FROM key_alias")
    edge_rows: List[Tuple[str, str, str, Optional[str]]] = []
    alias_rows: List[Tuple[str, str]] = []
    seen_alias: Set[Tuple[str, str]] = set()
    for e in edges:
        if not isinstance(e, dict):
            continue
        parent = str(e.get("parent_group_id") or "").strip()
        child = str(e.get("child_group_id") or "").strip()
        if not parent or not child:
            continue
        via = str(e.get("via_source_raw") or "")
        evidence = str(e.get("evidence") or "") or None
        edge_rows.append((parent, child, via, evidence))
        # Aliases so stills/clips can resolve without knowing group_id.
        for src in (
            via,
            str(e.get("resolved_parent_relpath") or ""),
            parent[6:] if parent.startswith("input:") else "",
        ):
            for key in match_keys_for_media(src):
                pair = (key, parent)
                if pair not in seen_alias:
                    seen_alias.add(pair)
                    alias_rows.append(pair)
        if parent.startswith("input:"):
            bn = parent.split(":", 1)[-1].lower()
            for key in (bn, f"input/{bn}", parent.lower()):
                pair = (key, parent)
                if len(key) >= 4 and pair not in seen_alias:
                    seen_alias.add(pair)
                    alias_rows.append(pair)
    if edge_rows:
        con.executemany(
            """
            INSERT OR REPLACE INTO edges
              (parent_group_id, child_group_id, via_source_raw, evidence)
            VALUES (?, ?, ?, ?)
            """,
            edge_rows,
        )
    if alias_rows:
        con.executemany(
            "INSERT OR REPLACE INTO key_alias(parent_key, group_id) VALUES (?, ?)",
            alias_rows,
        )
    _recompute_stats(con)
    try:
        st = edges_path.stat()
        mtime, size = float(st.st_mtime), int(st.st_size)
    except OSError:
        mtime, size = 0.0, 0
    con.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('edges_mtime', ?)",
        (str(mtime),),
    )
    con.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('edges_size', ?)",
        (str(size),),
    )
    con.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('built_at', ?)",
        (str(time.time()),),
    )
    con.commit()
    return {
        "ok": True,
        "edge_count": len(edge_rows),
        "alias_count": len(alias_rows),
        "parent_count": con.execute("SELECT COUNT(*) FROM node_stats").fetchone()[0],
    }


def ensure_adjacency(
    edges_path: Path,
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """Rebuild adjacency DB if missing or edges JSON changed."""
    edges_path = Path(edges_path)
    db_path = adjacency_db_path(edges_path)
    try:
        st = edges_path.stat()
        mtime, size = float(st.st_mtime), int(st.st_size)
    except OSError:
        mtime, size = 0.0, 0
    cache_key = str(db_path.resolve()) if db_path.parent.exists() else str(db_path)
    with _lock:
        hit = _META.get(cache_key)
        if (
            not force
            and hit
            and hit[0] == mtime
            and hit[1] == size
            and db_path.is_file()
        ):
            return {"ok": True, "cached": True, "edge_mtime": mtime}
        con = _connect(db_path)
        try:
            if not force and db_path.is_file():
                row = con.execute(
                    "SELECT value FROM meta WHERE key='edges_mtime'"
                ).fetchone()
                row2 = con.execute(
                    "SELECT value FROM meta WHERE key='edges_size'"
                ).fetchone()
                try:
                    prev_m = float(row["value"]) if row else -1.0
                    prev_s = int(float(row2["value"])) if row2 else -1
                except (TypeError, ValueError, KeyError):
                    prev_m, prev_s = -1.0, -1
                if prev_m == mtime and prev_s == size:
                    _META[cache_key] = (mtime, size, time.monotonic())
                    return {"ok": True, "cached": True, "edge_mtime": mtime}
            out = _rebuild_locked(con, edges_path)
            _META[cache_key] = (mtime, size, time.monotonic())
            out["cached"] = False
            return out
        finally:
            con.close()


def ingest_edge_rows(
    edges_path: Path,
    rows: Sequence[Dict[str, Any]],
) -> int:
    """Incremental upsert after graph persist; recomputes stats for touched parents."""
    if not rows:
        return 0
    edges_path = Path(edges_path)
    ensure_adjacency(edges_path)
    db_path = adjacency_db_path(edges_path)
    added = 0
    touched: Set[str] = set()
    with _lock:
        con = _connect(db_path)
        try:
            for e in rows:
                if not isinstance(e, dict):
                    continue
                parent = str(e.get("parent_group_id") or "").strip()
                child = str(e.get("child_group_id") or "").strip()
                if not parent or not child:
                    continue
                via = str(e.get("via_source_raw") or "")
                evidence = str(e.get("evidence") or "") or None
                cur = con.execute(
                    """
                    INSERT OR IGNORE INTO edges
                      (parent_group_id, child_group_id, via_source_raw, evidence)
                    VALUES (?, ?, ?, ?)
                    """,
                    (parent, child, via, evidence),
                )
                if cur.rowcount:
                    added += 1
                touched.add(parent)
                for src in (via, str(e.get("resolved_parent_relpath") or "")):
                    for key in match_keys_for_media(src):
                        con.execute(
                            "INSERT OR IGNORE INTO key_alias(parent_key, group_id) VALUES (?, ?)",
                            (key, parent),
                        )
            if touched:
                # Full recompute is cheap at current scale (~ms).
                _recompute_stats(con)
            try:
                st = edges_path.stat()
                con.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES ('edges_mtime', ?)",
                    (str(float(st.st_mtime)),),
                )
                con.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES ('edges_size', ?)",
                    (str(int(st.st_size)),),
                )
            except OSError:
                pass
            con.commit()
            _META.pop(str(db_path.resolve()), None)
        finally:
            con.close()
    return added


def _stats_for_group_ids(
    con: sqlite3.Connection, group_ids: Iterable[str]
) -> Dict[str, Dict[str, int]]:
    ids = [str(g).strip() for g in group_ids if str(g).strip()]
    if not ids:
        return {}
    out: Dict[str, Dict[str, int]] = {}
    # Chunk IN queries
    for i in range(0, len(ids), 400):
        chunk = ids[i : i + 400]
        ph = ",".join("?" for _ in chunk)
        for row in con.execute(
            f"""
            SELECT group_id, direct_child_count, descendant_count
            FROM node_stats
            WHERE group_id IN ({ph})
            """,
            chunk,
        ):
            gid = str(row["group_id"])
            out[gid] = {
                "direct_child_count": int(row["direct_child_count"] or 0),
                "descendant_count": int(row["descendant_count"] or 0),
            }
    return out


def resolve_group_ids_for_keys(
    con: sqlite3.Connection, keys: Iterable[str]
) -> Set[str]:
    ks = sorted({str(k).strip().lower() for k in keys if str(k).strip()})
    if not ks:
        return set()
    found: Set[str] = set()
    for i in range(0, len(ks), 400):
        chunk = ks[i : i + 400]
        ph = ",".join("?" for _ in chunk)
        for row in con.execute(
            f"SELECT DISTINCT group_id FROM key_alias WHERE parent_key IN ({ph})",
            chunk,
        ):
            found.add(str(row["group_id"]))
    # Synthetic input: ids
    for k in ks:
        bn = Path(k).name
        if bn:
            found.add(f"input:{bn}")
    return found


def counts_for_media_refs(
    edges_path: Path,
    refs: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Batch descendant counts for stills/clips.

    Each ref may include ``relpath``, ``content_id``, ``group_id``, ``basename``.
    """
    ensure_adjacency(edges_path)
    db_path = adjacency_db_path(edges_path)
    out: List[Dict[str, Any]] = []
    with _lock:
        con = _connect(db_path)
        try:
            for ref in refs:
                if not isinstance(ref, dict):
                    continue
                keys: Set[str] = set()
                gid_hint = str(ref.get("group_id") or "").strip()
                for field in ("relpath", "basename", "media_relpath", "path", "name"):
                    v = ref.get(field)
                    if isinstance(v, str) and v.strip():
                        keys |= match_keys_for_media(v)
                cid = str(ref.get("content_id") or "").strip().lower()
                if cid:
                    keys.add(cid)
                gids = set()
                if gid_hint:
                    gids.add(gid_hint)
                gids |= resolve_group_ids_for_keys(con, keys)
                stats = _stats_for_group_ids(con, gids)
                direct = 0
                desc = 0
                best_gid = gid_hint or None
                for gid, st in stats.items():
                    d = int(st.get("descendant_count") or 0)
                    if d >= desc:
                        desc = d
                        direct = int(st.get("direct_child_count") or 0)
                        best_gid = gid
                out.append(
                    {
                        "relpath": ref.get("relpath") or ref.get("media_relpath"),
                        "content_id": cid or None,
                        "group_id": best_gid,
                        "direct_child_count": direct,
                        "descendant_count": desc,
                    }
                )
        finally:
            con.close()
    return out


def counts_map_by_relpath(
    edges_path: Path,
    relpaths: Sequence[str],
) -> Dict[str, Dict[str, int]]:
    refs = [{"relpath": r} for r in relpaths if str(r).strip()]
    rows = counts_for_media_refs(edges_path, refs)
    out: Dict[str, Dict[str, int]] = {}
    for row, ref in zip(rows, refs):
        key = str(ref.get("relpath") or "").replace("\\", "/").strip()
        if not key:
            continue
        out[key] = {
            "direct_child_count": int(row.get("direct_child_count") or 0),
            "descendant_count": int(row.get("descendant_count") or 0),
        }
        # also basename key
        bn = Path(key).name
        if bn:
            out[bn] = out[key]
    return out
