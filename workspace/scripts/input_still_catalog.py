#!/usr/bin/env python3
"""
Thin catalog of Comfy input stills for hourly / pool membership.

Stores path + size + mtime + first_seen (no content hash). Incremental: a directory
whose mtime is unchanged skips re-statting files in that directory (subdirs are
still visited). After the first scan, newly seen files get first_seen=now so
copy-in mtimes on WSL do not hide fresh drops.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

STILL_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
SKIP_DIR_NAMES = {"_factory", "__pycache__"}
CATALOG_BASENAME = "input_still_catalog.sqlite"

# Windows / browser accidental re-download: ``foo (1).jpeg``, ``foo (2).png``.
_DOWNLOAD_COPY_RE = re.compile(r"^(?P<stem>.+?)(?: \(\d+\))+(?P<ext>\.[^.]+)$")
_DOWNLOAD_COPY_NOEXT_RE = re.compile(r"^(?P<stem>.+?)(?: \(\d+\))+$")


def strip_download_copy_suffix(path_or_name: str) -> str:
    """Drop `` (1)`` / `` (2)`` / … before the extension (or at end if no ext).

    Accidental duplicate downloads are almost always byte-identical to the
    original; treat the copy name as the canonical basename for lookup/id.
    Preserves any parent directory prefix.
    """
    raw = str(path_or_name or "").strip().replace("\\", "/")
    if not raw:
        return ""
    if "/" in raw:
        parent, base = raw.rsplit("/", 1)
        parent_prefix = parent + "/"
    else:
        base = raw
        parent_prefix = ""
    m = _DOWNLOAD_COPY_RE.match(base)
    if m:
        return f"{parent_prefix}{m.group('stem')}{m.group('ext')}"
    m = _DOWNLOAD_COPY_NOEXT_RE.match(base)
    if m:
        return f"{parent_prefix}{m.group('stem')}"
    return raw


def download_copy_name_candidates(path_or_name: str) -> List[str]:
    """Original name first, then canonical (no `` (N)``) when different."""
    raw = str(path_or_name or "").strip().replace("\\", "/")
    if not raw:
        return []
    canon = strip_download_copy_suffix(raw)
    if canon and canon != raw:
        return [raw, canon]
    return [raw]


def is_download_copy_name(path_or_name: str) -> bool:
    """True when basename looks like an accidental `` (N)`` re-download."""
    raw = str(path_or_name or "").strip().replace("\\", "/")
    if not raw:
        return False
    base = raw.rsplit("/", 1)[-1]
    return bool(base) and strip_download_copy_suffix(base) != base


def default_catalog_path(*, data_root: Optional[Path] = None) -> Path:
    env = os.environ.get("HOURLY_INPUT_STILL_CATALOG_PATH", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if data_root is None:
        repo = Path(__file__).resolve().parents[2]
        data_root = Path(os.environ.get("SHAPE_FACTORY_DATA_ROOT") or (repo / ".data"))
    return (Path(data_root).expanduser().resolve() / "shape_factory" / CATALOG_BASENAME)


def default_input_root() -> Path:
    env = os.environ.get("COMFYUI_BIND_INPUT_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    # Container bind is usually /workspace/input; host clone uses the data root.
    for cand in (
        Path("/workspace/input"),
        Path("/ComfyUI/input"),
        Path("/home/yuji/comfyui-runpod-data/input"),
    ):
        try:
            if cand.is_dir():
                return cand.resolve()
        except OSError:
            continue
    return Path("/home/yuji/comfyui-runpod-data/input").expanduser().resolve()


def resolve_catalog_still_path(stored: str, *, input_root: Optional[Path] = None) -> Optional[Path]:
    """Map a catalog abs path onto a file that exists now (host↔container remaps)."""
    raw = str(stored or "").strip()
    if not raw:
        return None
    root = (input_root or default_input_root()).expanduser()
    try:
        root = root.resolve()
    except OSError:
        pass
    p = Path(raw).expanduser()
    try:
        if p.is_file():
            # Prefer the canonical sibling over an accidental `` (1)`` re-download.
            if is_download_copy_name(p.name):
                canon = strip_download_copy_suffix(p.name)
                if canon and canon != p.name:
                    sibling = p.parent / canon
                    try:
                        if sibling.is_file():
                            return sibling.resolve()
                    except OSError:
                        pass
            return p.resolve()
    except OSError:
        pass
    name = p.name
    if not name:
        return None
    name_candidates = download_copy_name_candidates(name)
    # Rewrite …/input/<rel> → live input_root/<rel>
    parts = list(p.parts)
    if "input" in parts:
        idx = parts.index("input")
        rel = Path(*parts[idx + 1 :]) if idx + 1 < len(parts) else Path(name)
        for cand_name in download_copy_name_candidates(rel.as_posix()):
            cand = root / cand_name
            try:
                if cand.is_file():
                    # Prefer canonical when both the copy and original exist under root.
                    if is_download_copy_name(cand.name):
                        canon = strip_download_copy_suffix(cand.name)
                        sibling = cand.parent / canon
                        try:
                            if sibling.is_file():
                                return sibling.resolve()
                        except OSError:
                            pass
                    return cand.resolve()
            except OSError:
                pass
    for cand_name in name_candidates:
        cand = root / cand_name
        try:
            if cand.is_file():
                if is_download_copy_name(cand.name):
                    canon = strip_download_copy_suffix(cand.name)
                    sibling = cand.parent / canon
                    try:
                        if sibling.is_file():
                            return sibling.resolve()
                    except OSError:
                        pass
                return cand.resolve()
        except OSError:
            continue
    return None


def still_relpath_for_comfy(path: Path, *, input_root: Optional[Path] = None) -> str:
    """Best-effort ``input/…`` relpath for /files and LoadImage."""
    root = (input_root or default_input_root()).expanduser()
    try:
        root = root.resolve()
        resolved = path.expanduser().resolve()
        rel = resolved.relative_to(root).as_posix()
        canon = strip_download_copy_suffix(rel)
        if canon and canon != rel:
            try:
                if (root / canon).is_file():
                    return f"input/{canon}"
            except OSError:
                pass
            # Sole `` (1)`` copy: keep the real name so /files can serve it.
            return f"input/{rel}"
        return f"input/{rel}"
    except Exception:
        name = path.name
        canon = strip_download_copy_suffix(name)
        return f"input/{canon or name}"


def connect(catalog_path: Path) -> sqlite3.Connection:
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(catalog_path), timeout=30.0)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=30000")
    except sqlite3.Error:
        pass
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS stills (
            path TEXT PRIMARY KEY,
            size INTEGER,
            mtime REAL,
            ext TEXT,
            first_seen REAL NOT NULL,
            last_seen REAL NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_stills_first_seen ON stills(first_seen DESC)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_stills_ext ON stills(ext)")
    con.execute("CREATE TABLE IF NOT EXISTS dirs (path TEXT PRIMARY KEY, mtime REAL)")
    con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    con.commit()
    return con


def _meta_get(con: sqlite3.Connection, key: str) -> Optional[str]:
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def _meta_set(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def _skip_dir_name(name: str) -> bool:
    n = str(name or "").strip().lower()
    if not n or n.startswith("."):
        return True
    if n in SKIP_DIR_NAMES:
        return True
    if n.endswith("_files"):
        return True
    return False


def _is_still_file(name: str) -> bool:
    lower = str(name or "").lower()
    if not lower or lower.startswith("."):
        return False
    if ":zone.identifier" in lower:
        return False
    return Path(lower).suffix in STILL_EXTS


def scan_input_stills(
    *,
    input_root: Optional[Path] = None,
    catalog_path: Optional[Path] = None,
    now_ts: Optional[float] = None,
) -> Dict[str, Any]:
    """Walk ``input_root`` incrementally and upsert still rows. Returns scan stats."""
    root = (input_root or default_input_root()).expanduser().resolve()
    cat = (catalog_path or default_catalog_path()).expanduser().resolve()
    now = float(now_ts) if now_ts is not None else time.time()
    stats: Dict[str, Any] = {
        "ok": False,
        "input_root": str(root),
        "catalog": str(cat),
        "inserted": 0,
        "updated": 0,
        "removed": 0,
        "dirs_skipped": 0,
        "dirs_scanned": 0,
        "bootstrapped": False,
    }
    if not root.is_dir():
        stats["error"] = "input_root_missing"
        return stats

    con = connect(cat)
    try:
        already = _meta_get(con, "bootstrapped") == "1"
        stats["bootstrapped"] = already
        dir_mtime: Dict[str, float] = {
            str(r["path"]): float(r["mtime"] or 0.0)
            for r in con.execute("SELECT path, mtime FROM dirs")
        }

        def visit(dir_path: Path) -> None:
            try:
                entries = list(os.scandir(dir_path))
                d_mtime = float(dir_path.stat().st_mtime)
            except OSError:
                return
            key = str(dir_path)
            cached = dir_mtime.get(key)
            files_changed = cached is None or abs(cached - d_mtime) > 1e-3
            if files_changed:
                stats["dirs_scanned"] += 1
            else:
                stats["dirs_skipped"] += 1
            seen: List[str] = []
            for ent in entries:
                try:
                    is_dir = ent.is_dir(follow_symlinks=False)
                    is_file = ent.is_file(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir:
                    if not _skip_dir_name(ent.name):
                        visit(Path(ent.path).resolve())
                    continue
                if not files_changed or not is_file or not _is_still_file(ent.name):
                    continue
                # Accidental re-downloads: ignore ``foo (1).jpeg`` when ``foo.jpeg`` exists.
                if is_download_copy_name(ent.name):
                    canon_name = strip_download_copy_suffix(ent.name)
                    try:
                        if canon_name and (Path(ent.path).parent / canon_name).is_file():
                            continue
                    except OSError:
                        pass
                try:
                    st = ent.stat(follow_symlinks=False)
                except OSError:
                    continue
                abs_path = str(Path(ent.path).resolve())
                seen.append(abs_path)
                ext = Path(ent.name).suffix.lower()
                row = con.execute("SELECT path, first_seen FROM stills WHERE path=?", (abs_path,)).fetchone()
                if row is None:
                    first = float(st.st_mtime) if not already else now
                    if first > now:
                        first = now
                    con.execute(
                        """
                        INSERT INTO stills(path, size, mtime, ext, first_seen, last_seen)
                        VALUES(?,?,?,?,?,?)
                        """,
                        (abs_path, int(st.st_size), float(st.st_mtime), ext, first, now),
                    )
                    stats["inserted"] += 1
                else:
                    con.execute(
                        """
                        UPDATE stills SET size=?, mtime=?, ext=?, last_seen=? WHERE path=?
                        """,
                        (int(st.st_size), float(st.st_mtime), ext, now, abs_path),
                    )
                    stats["updated"] += 1
            if files_changed:
                prefix = key.rstrip("/") + "/"
                existing = [
                    str(r["path"])
                    for r in con.execute(
                        "SELECT path FROM stills WHERE path LIKE ?",
                        (prefix + "%",),
                    )
                ]
                for old in existing:
                    parent = str(Path(old).parent)
                    if parent != key:
                        continue
                    if old not in seen:
                        con.execute("DELETE FROM stills WHERE path=?", (old,))
                        stats["removed"] += 1
                con.execute(
                    "INSERT INTO dirs(path, mtime) VALUES(?, ?) ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime",
                    (key, d_mtime),
                )

        visit(root)
        if not already:
            _meta_set(con, "bootstrapped", "1")
        _meta_set(con, "last_scan_at", str(int(now)))
        con.commit()
        stats["ok"] = True
        stats["count"] = int(con.execute("SELECT COUNT(*) FROM stills").fetchone()[0])
        stats["bootstrapped"] = True
    finally:
        con.close()
    return stats


def list_recent_stills(
    *,
    catalog_path: Optional[Path] = None,
    exts: Optional[Sequence[str]] = None,
    limit: int = 200,
) -> List[Path]:
    """Newest-first by first_seen. Drops rows whose files are gone (cheap check on the window)."""
    cat = (catalog_path or default_catalog_path()).expanduser().resolve()
    if not cat.is_file():
        return []
    want = {e.lower() if str(e).startswith(".") else f".{e.lower()}" for e in (exts or STILL_EXTS)}
    lim = max(1, int(limit or 200))
    con = connect(cat)
    try:
        placeholders = ",".join("?" * len(want))
        rows = con.execute(
            f"""
            SELECT path FROM stills
            WHERE ext IN ({placeholders})
              AND path NOT LIKE '%/_factory/%'
            ORDER BY first_seen DESC
            LIMIT ?
            """,
            (*sorted(want), lim * 2),
        ).fetchall()
    finally:
        con.close()
    out: List[Path] = []
    for row in rows:
        p = Path(str(row["path"]))
        if not p.is_file():
            continue
        out.append(p)
        if len(out) >= lim:
            break
    return out


def load_first_seen_map(catalog_path: Optional[Path] = None) -> Dict[str, float]:
    cat = (catalog_path or default_catalog_path()).expanduser().resolve()
    if not cat.is_file():
        return {}
    con = connect(cat)
    try:
        return {str(r["path"]): float(r["first_seen"]) for r in con.execute("SELECT path, first_seen FROM stills")}
    finally:
        con.close()


def glob_ext_from_pattern(pattern: str) -> Optional[str]:
    """Return ``.png`` from ``.../input/**/*.png`` (or None if not an input-still glob)."""
    text = str(pattern or "").replace("\\", "/").strip()
    if not text:
        return None
    lower = text.lower()
    if "/input/" not in lower and not lower.startswith("input/"):
        return None
    name = Path(lower).name
    if not name.startswith("*") and "*." not in name:
        # e.g. **/*.png → name is *.png
        pass
    suffix = Path(name.replace("*", "x")).suffix
    if suffix in STILL_EXTS:
        return suffix
    return None
