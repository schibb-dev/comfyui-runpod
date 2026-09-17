#!/usr/bin/env python3
"""Still identity: content_id is sha256(bytes). Filenames are aliases.

Reads ``still_content_accounting.sqlite`` produced by ``still_content_account.py``.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

ACCOUNTING_BASENAME = "still_content_accounting.sqlite"
_HOST_INPUT_PREFIX = "/home/yuji/comfyui-runpod-data/input"
_WORKSPACE_INPUT_PREFIX = "/workspace/input"

_CACHE: Optional[Tuple[str, float, "StillIdentity"]] = None


def accounting_db_path(data_root: Path) -> Path:
    return Path(data_root).expanduser().resolve() / "shape_factory" / ACCOUNTING_BASENAME


def _path_aliases(path: str) -> List[str]:
    raw = str(path or "").replace("\\", "/").rstrip("/")
    out = [raw]
    if raw.startswith(_HOST_INPUT_PREFIX):
        out.append(_WORKSPACE_INPUT_PREFIX + raw[len(_HOST_INPUT_PREFIX) :])
    elif raw.startswith(_WORKSPACE_INPUT_PREFIX):
        out.append(_HOST_INPUT_PREFIX + raw[len(_WORKSPACE_INPUT_PREFIX) :])
    env = (os.environ.get("COMFYUI_BIND_INPUT_DIR") or "").strip().replace("\\", "/").rstrip("/")
    if env and raw.startswith(_HOST_INPUT_PREFIX):
        out.append(env + raw[len(_HOST_INPUT_PREFIX) :])
    if env and raw.startswith(_WORKSPACE_INPUT_PREFIX):
        out.append(env + raw[len(_WORKSPACE_INPUT_PREFIX) :])
    seen: set[str] = set()
    uniq: List[str] = []
    for p in out:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


class StillIdentity:
    def __init__(self) -> None:
        self.by_path: Dict[str, str] = {}
        self.by_name_hex: Dict[str, str] = {}
        self.canonical: Dict[str, str] = {}
        self.size: Dict[str, int] = {}

    def content_id_for_path(self, path: str) -> Optional[str]:
        raw = str(path or "").strip().replace("\\", "/")
        if not raw:
            return None
        for cand in _path_aliases(raw):
            hit = self.by_path.get(cand)
            if hit:
                return hit
        try:
            resolved = str(Path(raw).expanduser().resolve())
        except OSError:
            resolved = raw
        for cand in _path_aliases(resolved):
            hit = self.by_path.get(cand)
            if hit:
                return hit
        return None

    def canonical_path(self, content_id: str) -> Optional[Path]:
        cid = str(content_id or "").strip().lower()
        stored = self.canonical.get(cid)
        if not stored:
            return None
        for cand in _path_aliases(stored):
            p = Path(cand)
            try:
                if p.is_file():
                    return p
            except OSError:
                continue
        return Path(stored)


def load_identity(data_root: Path, *, refresh: bool = False) -> Optional[StillIdentity]:
    global _CACHE
    db = accounting_db_path(data_root)
    if not db.is_file():
        return None
    mtime = db.stat().st_mtime
    key = str(db)
    if not refresh and _CACHE and _CACHE[0] == key and _CACHE[1] == mtime:
        return _CACHE[2]
    ident = StillIdentity()
    con = sqlite3.connect(str(db), timeout=30.0)
    try:
        for path, cid, name_hex in con.execute(
            "SELECT path, content_id, name_hex FROM paths WHERE content_id IS NOT NULL"
        ):
            cid_s = str(cid).lower()
            ident.by_path[str(path)] = cid_s
            for alias in _path_aliases(str(path)):
                ident.by_path[alias] = cid_s
            if name_hex:
                ident.by_name_hex.setdefault(str(name_hex).lower(), cid_s)
        for cid, canon, size in con.execute(
            "SELECT content_id, canonical_path, size FROM contents"
        ):
            cid_s = str(cid).lower()
            ident.canonical[cid_s] = str(canon)
            ident.size[cid_s] = int(size or 0)
    finally:
        con.close()
    _CACHE = (key, mtime, ident)
    return ident


def iter_unique_stills(
    data_root: Path,
    *,
    min_size: int = 1,
) -> List[Tuple[str, Path]]:
    """Unique byte hashes with a live canonical file, newest first."""
    ident = load_identity(data_root)
    if ident is None:
        return []
    rows: List[Tuple[float, str, Path]] = []
    for cid in ident.canonical:
        if int(ident.size.get(cid) or 0) < int(min_size):
            continue
        p = ident.canonical_path(cid)
        if p is None or not p.is_file():
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0
        rows.append((mtime, cid, p))
    rows.sort(key=lambda t: (-t[0], t[1]))
    return [(cid, path) for _mt, cid, path in rows]
