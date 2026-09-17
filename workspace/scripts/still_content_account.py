#!/usr/bin/env python3
"""Account input stills by sha256(bytes). Filenames are aliases, not identity.

Walks the Comfy input tree, hashes each unique inode, and records every path
that points at those bytes. Byte-duplicates (same hash, different inodes) are
grouped so a later pass can collapse extras onto one canonical file while
keeping every former path (hardlink).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DB_BASENAME = "still_content_accounting.sqlite"
SCHEMA_VERSION = "1"
STILL_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".jfif", ".avif"}
SKIP_DIR_NAMES = {"__pycache__"}
_SHA256_RE = re.compile(r"([0-9a-f]{64})", re.IGNORECASE)
_DOWNLOAD_COPY_RE = re.compile(r" \(\d+\)\.[^.]+$")


def extract_name_hex(path: str) -> Optional[str]:
    m = _SHA256_RE.search(Path(str(path or "")).name)
    return m.group(1).lower() if m else None


def default_input_root() -> Path:
    env = os.environ.get("COMFYUI_BIND_INPUT_DIR", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p.resolve()
    for cand in (
        Path("/home/yuji/comfyui-runpod-data/input"),
        Path("/workspace/input"),
        Path("/ComfyUI/input"),
    ):
        try:
            if cand.is_dir():
                return cand.resolve()
        except OSError:
            continue
    return Path("/home/yuji/comfyui-runpod-data/input").expanduser()


def default_data_root() -> Path:
    env = os.environ.get("SHAPE_FACTORY_DATA_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    repo = Path(__file__).resolve().parents[2]
    return Path(os.environ.get("SHAPE_FACTORY_DATA_ROOT") or (repo / ".data")).resolve()


def default_db_path(*, data_root: Optional[Path] = None) -> Path:
    root = Path(data_root).expanduser().resolve() if data_root else default_data_root()
    return root / "shape_factory" / DB_BASENAME


def hash_file(path: Path, *, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk_size), b""):
            h.update(block)
    return h.hexdigest()


def _is_skipped_file(path: Path, *, include_factory: bool) -> bool:
    name = path.name
    if name.endswith(":Zone.Identifier") or name.endswith("Zone.Identifier"):
        return True
    parts = set(path.parts)
    if parts & SKIP_DIR_NAMES:
        return True
    if not include_factory and "_factory" in path.parts:
        return True
    return path.suffix.lower() not in STILL_EXTS


def iter_still_paths(root: Path, *, include_factory: bool = True) -> Iterable[Path]:
    root = Path(root)
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
        if not include_factory:
            dirnames[:] = [d for d in dirnames if d != "_factory"]
        base = Path(dirpath)
        for name in filenames:
            p = base / name
            if _is_skipped_file(p, include_factory=include_factory):
                continue
            yield p


def _name_hex_status(name_hex: Optional[str], content_id: Optional[str]) -> str:
    if not content_id:
        return "unreadable" if not name_hex else "unreadable_named"
    if not name_hex:
        return "absent"
    if name_hex == content_id:
        return "match"
    return "mismatch"


def _canonical_key(path: str, *, content_id: str, name_status: str, nlink: int) -> Tuple:
    p = Path(path)
    scrape = int(any(part.endswith("_files") for part in p.parts))
    download_copy = int(bool(_DOWNLOAD_COPY_RE.search(p.name)))
    factory = int("_factory" in p.parts)
    match = int(name_status == "match")
    return (
        scrape,
        download_copy,
        -match,
        -min(int(nlink or 1), 32),
        -factory,
        len(p.name),
        path.lower(),
    )


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path), timeout=60.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        DROP TABLE IF EXISTS paths;
        DROP TABLE IF EXISTS contents;
        DROP TABLE IF EXISTS catalog_paths;
        DROP TABLE IF EXISTS meta;
        CREATE TABLE meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE paths (
          path TEXT PRIMARY KEY,
          root TEXT,
          relpath TEXT,
          device INTEGER,
          inode INTEGER,
          nlink INTEGER,
          size INTEGER,
          mtime REAL,
          content_id TEXT,
          name_hex TEXT,
          name_hex_status TEXT,
          in_catalog INTEGER NOT NULL DEFAULT 0,
          is_factory INTEGER NOT NULL DEFAULT 0,
          error TEXT
        );
        CREATE INDEX idx_paths_cid ON paths(content_id);
        CREATE INDEX idx_paths_inode ON paths(device, inode);
        CREATE TABLE contents (
          content_id TEXT PRIMARY KEY,
          size INTEGER,
          n_paths INTEGER NOT NULL,
          n_inodes INTEGER NOT NULL,
          canonical_path TEXT,
          name_hex_match INTEGER NOT NULL DEFAULT 0,
          name_hex_mismatch INTEGER NOT NULL DEFAULT 0,
          name_hex_absent INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE catalog_paths (
          stored TEXT PRIMARY KEY,
          resolved TEXT,
          content_id TEXT,
          on_disk INTEGER NOT NULL DEFAULT 0,
          name_hex TEXT
        );
        """
    )
    con.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?)",
        ("schema_version", SCHEMA_VERSION),
    )
    con.commit()


def _normalize_catalog_stored(stored: str, *, input_root: Path) -> str:
    raw = str(stored or "").strip().replace("\\", "/")
    if raw.startswith("/workspace/input/") or raw == "/workspace/input":
        rel = raw[len("/workspace/input/") :] if raw.startswith("/workspace/input/") else ""
        return str((input_root / rel).resolve()) if rel else str(input_root.resolve())
    return raw


def _stat_record(path: Path, *, root: Path) -> Dict[str, Any]:
    st = path.stat()
    try:
        rel = str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        rel = path.name
    return {
        "path": str(path),
        "root": str(root),
        "relpath": rel,
        "device": int(st.st_dev),
        "inode": int(st.st_ino),
        "nlink": int(st.st_nlink),
        "size": int(st.st_size),
        "mtime": float(st.st_mtime),
        "is_factory": int("_factory" in path.parts),
        "name_hex": extract_name_hex(path.name),
    }


def account(
    *,
    input_root: Path,
    data_root: Path,
    db_path: Path,
    workers: int = 8,
    include_factory: bool = True,
) -> Dict[str, Any]:
    t0 = time.time()
    input_root = Path(input_root).expanduser().resolve()
    data_root = Path(data_root).expanduser().resolve()
    records: List[Dict[str, Any]] = []
    stat_errors = 0
    for p in iter_still_paths(input_root, include_factory=include_factory):
        try:
            records.append(_stat_record(p, root=input_root))
        except OSError as exc:
            stat_errors += 1
            records.append(
                {
                    "path": str(p),
                    "root": str(input_root),
                    "relpath": p.name,
                    "device": None,
                    "inode": None,
                    "nlink": 0,
                    "size": 0,
                    "mtime": 0.0,
                    "is_factory": int("_factory" in p.parts),
                    "name_hex": extract_name_hex(p.name),
                    "error": f"stat:{exc}",
                }
            )
    by_inode: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    hash_jobs: List[int] = []
    for i, rec in enumerate(records):
        if rec.get("error"):
            continue
        key = (int(rec["device"]), int(rec["inode"]))
        by_inode[key].append(i)
        if len(by_inode[key]) == 1:
            hash_jobs.append(i)

    hashed: Dict[int, Tuple[Optional[str], Optional[str]]] = {}
    n_workers = max(1, min(int(workers or 8), 32))
    done_hash = 0

    def _job(idx: int) -> Tuple[int, Optional[str], Optional[str]]:
        try:
            return idx, hash_file(Path(records[idx]["path"])), None
        except Exception as exc:
            return idx, None, str(exc).replace("\n", " ")[:240]

    print(
        f"[still-account] files={len(records)} unique_inodes={len(hash_jobs)} workers={n_workers}",
        file=sys.stderr,
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futs = [pool.submit(_job, i) for i in hash_jobs]
        for fut in as_completed(futs):
            idx, cid, err = fut.result()
            hashed[idx] = (cid, err)
            done_hash += 1
            if done_hash == 1 or done_hash % 500 == 0 or done_hash == len(hash_jobs):
                elapsed = max(time.time() - t0, 1e-6)
                rate = done_hash / elapsed
                print(
                    f"[still-account] hashed={done_hash}/{len(hash_jobs)} rate={rate:.1f}/s",
                    file=sys.stderr,
                    flush=True,
                )

    inode_cid: Dict[Tuple[int, int], Tuple[Optional[str], Optional[str]]] = {}
    for idx, (cid, err) in hashed.items():
        rec = records[idx]
        inode_cid[(int(rec["device"]), int(rec["inode"]))] = (cid, err)

    for rec in records:
        if rec.get("error"):
            rec["content_id"] = None
            rec["name_hex_status"] = _name_hex_status(rec.get("name_hex"), None)
            continue
        cid, err = inode_cid.get((int(rec["device"]), int(rec["inode"])), (None, "not_hashed"))
        rec["content_id"] = cid
        if err and not cid:
            rec["error"] = err
        rec["name_hex_status"] = _name_hex_status(rec.get("name_hex"), cid)

    catalog_path = data_root / "shape_factory" / "input_still_catalog.sqlite"
    catalog_stored: List[str] = []
    if catalog_path.is_file():
        cat = sqlite3.connect(str(catalog_path))
        try:
            catalog_stored = [str(r[0] or "") for r in cat.execute("SELECT path FROM stills")]
        finally:
            cat.close()

    disk_paths = {rec["path"] for rec in records}
    resolved_catalog: Dict[str, str] = {}
    catalog_on_disk: set[str] = set()
    for stored in catalog_stored:
        resolved = _normalize_catalog_stored(stored, input_root=input_root)
        try:
            rp = str(Path(resolved).resolve()) if Path(resolved).exists() else resolved
        except OSError:
            rp = resolved
        resolved_catalog[stored] = rp
        if rp in disk_paths or Path(rp).is_file():
            catalog_on_disk.add(rp)

    for rec in records:
        rec["in_catalog"] = int(rec["path"] in catalog_on_disk)

    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for rec in records:
        cid = rec.get("content_id")
        if cid:
            groups[cid].append(rec)

    contents: List[Dict[str, Any]] = []
    for cid, members in groups.items():
        inodes = {(int(m["device"]), int(m["inode"])) for m in members}
        statuses = [str(m.get("name_hex_status") or "") for m in members]
        ranked = sorted(
            members,
            key=lambda m: _canonical_key(
                m["path"],
                content_id=cid,
                name_status=str(m.get("name_hex_status") or ""),
                nlink=int(m.get("nlink") or 1),
            ),
        )
        contents.append(
            {
                "content_id": cid,
                "size": int(ranked[0].get("size") or 0),
                "n_paths": len(members),
                "n_inodes": len(inodes),
                "canonical_path": ranked[0]["path"],
                "name_hex_match": statuses.count("match"),
                "name_hex_mismatch": statuses.count("mismatch"),
                "name_hex_absent": statuses.count("absent"),
            }
        )

    con = connect(db_path)
    try:
        init_db(con)
        con.executemany(
            """
            INSERT INTO paths(path, root, relpath, device, inode, nlink, size, mtime,
              content_id, name_hex, name_hex_status, in_catalog, is_factory, error)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    r["path"],
                    r.get("root") or "",
                    r.get("relpath") or "",
                    r.get("device"),
                    r.get("inode"),
                    int(r.get("nlink") or 0),
                    int(r.get("size") or 0),
                    float(r.get("mtime") or 0.0),
                    r.get("content_id"),
                    r.get("name_hex"),
                    r.get("name_hex_status"),
                    int(r.get("in_catalog") or 0),
                    int(r.get("is_factory") or 0),
                    r.get("error"),
                )
                for r in records
            ],
        )
        con.executemany(
            """
            INSERT INTO contents(content_id, size, n_paths, n_inodes, canonical_path,
              name_hex_match, name_hex_mismatch, name_hex_absent)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            [
                (
                    c["content_id"],
                    c["size"],
                    c["n_paths"],
                    c["n_inodes"],
                    c["canonical_path"],
                    c["name_hex_match"],
                    c["name_hex_mismatch"],
                    c["name_hex_absent"],
                )
                for c in contents
            ],
        )
        path_by_resolved = {r["path"]: r.get("content_id") for r in records}
        cat_rows = []
        for stored, resolved in resolved_catalog.items():
            on_disk = int(Path(resolved).is_file()) if resolved else 0
            cid = path_by_resolved.get(resolved)
            if cid is None and on_disk:
                try:
                    cid = path_by_resolved.get(str(Path(resolved).resolve()))
                except OSError:
                    cid = None
            cat_rows.append(
                (
                    stored,
                    resolved,
                    cid,
                    on_disk,
                    extract_name_hex(stored),
                )
            )
        if cat_rows:
            con.executemany(
                """
                INSERT INTO catalog_paths(stored, resolved, content_id, on_disk, name_hex)
                VALUES (?,?,?,?,?)
                """,
                cat_rows,
            )
        con.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            ("input_root", str(input_root)),
        )
        con.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            ("scanned_at", str(time.time())),
        )
        con.commit()
    finally:
        con.close()

    clip_db = data_root / "shape_factory" / "still_clip_embeddings.sqlite"
    tag_db = data_root / "shape_factory" / "still_tags.sqlite"
    clip_ids: set[str] = set()
    tag_ids: set[str] = set()
    if clip_db.is_file():
        c = sqlite3.connect(str(clip_db))
        try:
            clip_ids = {str(r[0]).lower() for r in c.execute("SELECT content_id FROM embeddings")}
        finally:
            c.close()
    if tag_db.is_file():
        c = sqlite3.connect(str(tag_db))
        try:
            tag_ids = {str(r[0]).lower() for r in c.execute("SELECT content_id FROM still_tag_items")}
        except sqlite3.Error:
            tag_ids = set()
        finally:
            c.close()

    unique_ids = set(groups)
    extra_inode_groups = [c for c in contents if int(c["n_inodes"]) > 1]
    extra_path_groups = [c for c in contents if int(c["n_paths"]) > 1]
    extra_inodes = sum(int(c["n_inodes"]) - 1 for c in extra_inode_groups)
    extra_paths = sum(int(c["n_paths"]) - 1 for c in extra_path_groups)
    unreadable = [r for r in records if not r.get("content_id")]
    name_status_counts: Dict[str, int] = defaultdict(int)
    for r in records:
        name_status_counts[str(r.get("name_hex_status") or "")] += 1
    catalog_missing_disk = sum(1 for _s, rp in resolved_catalog.items() if not Path(rp).is_file())
    disk_not_in_catalog = sum(1 for r in records if not r.get("in_catalog"))

    summary = {
        "ok": True,
        "input_root": str(input_root),
        "db_path": str(db_path),
        "elapsed_s": round(time.time() - t0, 2),
        "files": len(records),
        "unique_inodes": len(by_inode),
        "unique_content_ids": len(unique_ids),
        "unreadable": len(unreadable),
        "stat_errors": stat_errors,
        "name_hex_status": dict(name_status_counts),
        "duplicate_content_n_paths_gt1": len(extra_path_groups),
        "alias_paths_beyond_canonical": extra_paths,
        "duplicate_content_n_inodes_gt1": len(extra_inode_groups),
        "extra_inodes_to_collapse": extra_inodes,
        "already_hardlinked_paths": sum(1 for r in records if int(r.get("nlink") or 1) > 1),
        "catalog_rows": len(catalog_stored),
        "catalog_unique_resolved": len(set(resolved_catalog.values())),
        "catalog_missing_on_disk": catalog_missing_disk,
        "disk_not_in_catalog": disk_not_in_catalog,
        "clip_rows": len(clip_ids),
        "clip_ids_present_on_disk": len(clip_ids & unique_ids),
        "clip_ids_missing_bytes": len(clip_ids - unique_ids),
        "disk_ids_missing_clip": len(unique_ids - clip_ids),
        "tag_rows": len(tag_ids),
        "tag_ids_present_on_disk": len(tag_ids & unique_ids),
        "disk_ids_missing_tags": len(unique_ids - tag_ids),
        "factory_files": sum(int(r.get("is_factory") or 0) for r in records),
    }
    summary_path = db_path.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    summary["summary_path"] = str(summary_path)
    return summary


def replace_path_with_hardlink(canonical: Path, dest: Path) -> None:
    """Point ``dest`` at ``canonical``'s inode without a nameless gap.

    Creates a temp hardlink in the destination directory, then ``os.replace``s
    it over ``dest``. If the temp link fails, ``dest`` is untouched.
    """
    canonical = Path(canonical)
    dest = Path(dest)
    tmp = dest.with_name(f".{dest.name}.hl-{os.getpid()}-{time.time_ns()}")
    try:
        os.link(canonical, tmp)
        os.replace(tmp, dest)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def apply_hardlinks(*, db_path: Path, dry_run: bool = True) -> Dict[str, Any]:
    """Collapse extra inodes onto the canonical file; keep every path as a hardlink."""
    con = connect(db_path)
    log_path = Path(db_path).with_name("still_content_hardlink.jsonl")
    try:
        groups = list(
            con.execute(
                "SELECT content_id, canonical_path, n_inodes FROM contents WHERE n_inodes > 1"
            )
        )
        actions: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        linked = 0
        skipped = 0
        hash_mismatch = 0
        log_fh = None if dry_run else log_path.open("a", encoding="utf-8")
        try:
            for row in groups:
                cid = str(row["content_id"])
                canonical = Path(str(row["canonical_path"]))
                if not canonical.is_file():
                    errors.append({"content_id": cid, "error": "canonical_missing", "path": str(canonical)})
                    continue
                can_st = canonical.stat()
                try:
                    can_hash = hash_file(canonical)
                except Exception as exc:
                    errors.append({"content_id": cid, "error": f"canonical_hash:{exc}", "path": str(canonical)})
                    continue
                if can_hash != cid:
                    errors.append(
                        {
                            "content_id": cid,
                            "error": "canonical_hash_drift",
                            "path": str(canonical),
                            "got": can_hash,
                        }
                    )
                    continue
                members = list(
                    con.execute(
                        "SELECT path, device, inode FROM paths WHERE content_id=? ORDER BY path",
                        (cid,),
                    )
                )
                for mem in members:
                    p = Path(str(mem["path"]))
                    try:
                        if p.resolve() == canonical.resolve():
                            skipped += 1
                            continue
                    except OSError:
                        pass
                    try:
                        st = p.stat()
                    except OSError as exc:
                        errors.append({"path": str(p), "error": f"stat:{exc}"})
                        continue
                    if (st.st_dev, st.st_ino) == (can_st.st_dev, can_st.st_ino):
                        skipped += 1
                        continue
                    if st.st_dev != can_st.st_dev:
                        errors.append({"path": str(p), "error": "cross_device"})
                        continue
                    if int(st.st_size) != int(can_st.st_size):
                        errors.append({"path": str(p), "error": "size_mismatch", "canonical": str(canonical)})
                        hash_mismatch += 1
                        continue
                    try:
                        dest_hash = hash_file(p)
                    except Exception as exc:
                        errors.append({"path": str(p), "error": f"dest_hash:{exc}"})
                        continue
                    if dest_hash != cid:
                        errors.append(
                            {
                                "path": str(p),
                                "error": "dest_hash_drift",
                                "expected": cid,
                                "got": dest_hash,
                            }
                        )
                        hash_mismatch += 1
                        continue
                    actions.append(
                        {
                            "content_id": cid,
                            "from": str(p),
                            "to": str(canonical),
                        }
                    )
                    if dry_run:
                        continue
                    try:
                        replace_path_with_hardlink(canonical, p)
                        can_st = canonical.stat()
                        linked += 1
                        if log_fh is not None:
                            log_fh.write(
                                json.dumps(
                                    {
                                        "ts": time.time(),
                                        "content_id": cid,
                                        "alias": str(p),
                                        "canonical": str(canonical),
                                    }
                                )
                                + "\n"
                            )
                    except OSError as exc:
                        errors.append({"path": str(p), "error": f"link:{exc}"})
        finally:
            if log_fh is not None:
                log_fh.close()
        return {
            "ok": not errors,
            "dry_run": dry_run,
            "groups": len(groups),
            "would_link": len(actions) if dry_run else linked,
            "linked": 0 if dry_run else linked,
            "already_same_inode": skipped,
            "hash_mismatch": hash_mismatch,
            "errors": errors[:50],
            "error_count": len(errors),
            "sample": actions[:12],
            "log_path": None if dry_run else str(log_path),
        }
    finally:
        con.close()


def duplicate_samples(db_path: Path, *, limit: int = 12) -> List[Dict[str, Any]]:
    con = connect(db_path)
    try:
        rows = con.execute(
            """
            SELECT content_id, n_paths, n_inodes, canonical_path, size
            FROM contents
            WHERE n_inodes > 1
            ORDER BY n_inodes DESC, n_paths DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        out = []
        for row in rows:
            aliases = [
                str(r[0])
                for r in con.execute(
                    "SELECT path FROM paths WHERE content_id=? ORDER BY path",
                    (row["content_id"],),
                )
            ]
            out.append(
                {
                    "content_id": row["content_id"],
                    "size": row["size"],
                    "n_paths": row["n_paths"],
                    "n_inodes": row["n_inodes"],
                    "canonical_path": row["canonical_path"],
                    "aliases": aliases,
                }
            )
        return out
    finally:
        con.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Account input stills by sha256(bytes)")
    sub = p.add_subparsers(dest="cmd", required=True)
    acc = sub.add_parser("scan", help="Hash stills and write accounting sqlite")
    acc.add_argument("--input-root", type=Path, default=None)
    acc.add_argument("--data-root", type=Path, default=None)
    acc.add_argument("--db", type=Path, default=None)
    acc.add_argument("--workers", type=int, default=8)
    acc.add_argument("--skip-factory", action="store_true")
    hl = sub.add_parser("hardlink-dups", help="Collapse extra inodes; keep all paths")
    hl.add_argument("--db", type=Path, default=None)
    hl.add_argument("--data-root", type=Path, default=None)
    hl.add_argument("--apply", action="store_true")
    sm = sub.add_parser("summary", help="Print last scan summary + duplicate samples")
    sm.add_argument("--db", type=Path, default=None)
    sm.add_argument("--data-root", type=Path, default=None)
    args = p.parse_args(list(argv) if argv is not None else None)
    data_root = Path(args.data_root).expanduser().resolve() if getattr(args, "data_root", None) else default_data_root()
    db_path = Path(args.db).expanduser().resolve() if getattr(args, "db", None) and args.db else default_db_path(data_root=data_root)
    if args.cmd == "scan":
        out = account(
            input_root=Path(args.input_root).expanduser().resolve() if args.input_root else default_input_root(),
            data_root=data_root,
            db_path=db_path,
            workers=int(args.workers or 8),
            include_factory=not bool(args.skip_factory),
        )
        print(json.dumps(out, indent=2))
        return 0
    if args.cmd == "hardlink-dups":
        out = apply_hardlinks(db_path=db_path, dry_run=not bool(args.apply))
        print(json.dumps(out, indent=2))
        return 0 if out.get("ok") else 1
    if args.cmd == "summary":
        summary_path = db_path.with_suffix(".summary.json")
        doc: Dict[str, Any] = {}
        if summary_path.is_file():
            doc = json.loads(summary_path.read_text(encoding="utf-8"))
        doc["duplicates"] = duplicate_samples(db_path)
        print(json.dumps(doc, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
