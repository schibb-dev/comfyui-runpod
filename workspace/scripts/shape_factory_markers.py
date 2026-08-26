#!/usr/bin/env python3
"""
Work-product markers: lean content_id-keyed facts (not disposition / ratings / asset_tags).

Store: ``{output}/_status/work_product_markers.sqlite``
Keys must be namespaced (``decode.vae``, ``note.review``). Values are small strings.
Sources: ``scan`` | ``job`` | ``human``. Human wins on overwrite conflicts.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

MARKERS_BASENAME = "work_product_markers.sqlite"
MARKERS_SCHEMA_VERSION = 1
DECODE_VAE_KEY = "decode.vae"

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
_SOURCES = frozenset({"scan", "job", "human"})
_SOURCE_RANK = {"scan": 0, "job": 1, "human": 2}
_MAX_VALUE_LEN = 512


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_markers_path(og_root: Path) -> Path:
    return og_root.resolve().parent / "_status" / MARKERS_BASENAME


def markers_path_for_output_root(output_root: Path) -> Path:
    """``<output_root>/_status/work_product_markers.sqlite`` (flat library layout)."""
    return Path(output_root).resolve() / "_status" / MARKERS_BASENAME


def validate_key(key: str) -> str:
    k = str(key or "").strip().lower()
    if not _KEY_RE.match(k):
        raise ValueError(
            f"invalid marker key {key!r}: expect namespaced form like decode.vae"
        )
    return k


def validate_value(value: Any) -> str:
    if value is None:
        raise ValueError("marker value required")
    if isinstance(value, (dict, list)):
        raise ValueError("marker value must be a small string (not nested JSON)")
    text = str(value).strip()
    if not text:
        raise ValueError("marker value must be non-empty")
    if len(text) > _MAX_VALUE_LEN:
        raise ValueError(f"marker value too long (max {_MAX_VALUE_LEN})")
    return text


def validate_source(source: str) -> str:
    s = str(source or "").strip().lower()
    if s not in _SOURCES:
        raise ValueError(f"invalid source {source!r}: expect scan|job|human")
    return s


def connect(db_path: Path) -> sqlite3.Connection:
    path = Path(db_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=30.0)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=30000")
    except sqlite3.Error:
        pass
    _ensure_schema(con)
    return con


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS markers (
            content_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            source TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (content_id, key)
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS markers_by_key_value ON markers(key, value)"
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    con.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
        (str(MARKERS_SCHEMA_VERSION),),
    )
    con.commit()


def get_marker(
    con: sqlite3.Connection, content_id: str, key: str
) -> Optional[Dict[str, Any]]:
    cid = str(content_id or "").strip()
    if not cid:
        return None
    k = validate_key(key)
    row = con.execute(
        "SELECT content_id, key, value, source, updated_at FROM markers "
        "WHERE content_id=? AND key=?",
        (cid, k),
    ).fetchone()
    return dict(row) if row else None


def list_for(
    con: sqlite3.Connection, content_id: str
) -> Dict[str, Dict[str, Any]]:
    cid = str(content_id or "").strip()
    if not cid:
        return {}
    rows = con.execute(
        "SELECT content_id, key, value, source, updated_at FROM markers "
        "WHERE content_id=? ORDER BY key",
        (cid,),
    ).fetchall()
    return {str(r["key"]): dict(r) for r in rows}


def markers_map(con: sqlite3.Connection, content_id: str) -> Dict[str, str]:
    return {k: str(v["value"]) for k, v in list_for(con, content_id).items()}


def query_by_key(
    con: sqlite3.Connection,
    key: str,
    *,
    value: Optional[str] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    k = validate_key(key)
    lim = max(1, min(5000, int(limit)))
    if value is not None:
        v = validate_value(value)
        rows = con.execute(
            "SELECT content_id, key, value, source, updated_at FROM markers "
            "WHERE key=? AND value=? ORDER BY updated_at DESC LIMIT ?",
            (k, v, lim),
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT content_id, key, value, source, updated_at FROM markers "
            "WHERE key=? ORDER BY updated_at DESC LIMIT ?",
            (k, lim),
        ).fetchall()
    return [dict(r) for r in rows]


def _can_overwrite(existing_source: Optional[str], new_source: str) -> bool:
    if existing_source is None:
        return True
    old = str(existing_source).strip().lower()
    new = validate_source(new_source)
    if old not in _SOURCE_RANK:
        return True
    # human wins; scan/job may overwrite each other and prior non-human
    if _SOURCE_RANK[old] > _SOURCE_RANK[new]:
        return False
    return True


def set_marker(
    con: sqlite3.Connection,
    content_id: str,
    key: str,
    value: Any,
    *,
    source: str = "human",
    force: bool = False,
) -> Dict[str, Any]:
    """
    Upsert one marker. Returns the stored row (or existing row if blocked).

    ``human`` overwrites ``scan``/``job``. ``scan``/``job`` do not overwrite ``human``
    unless ``force=True``.
    """
    cid = str(content_id or "").strip()
    if not cid or len(cid) < 8:
        raise ValueError("content_id required")
    k = validate_key(key)
    v = validate_value(value)
    src = validate_source(source)
    existing = get_marker(con, cid, k)
    if existing and not force and not _can_overwrite(existing.get("source"), src):
        return {**existing, "unchanged": True, "blocked": True}
    now = utc_now()
    con.execute(
        """
        INSERT INTO markers(content_id, key, value, source, updated_at)
        VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(content_id, key) DO UPDATE SET
            value=excluded.value,
            source=excluded.source,
            updated_at=excluded.updated_at
        """,
        (cid, k, v, src, now),
    )
    con.commit()
    return {
        "content_id": cid,
        "key": k,
        "value": v,
        "source": src,
        "updated_at": now,
        "unchanged": False,
        "blocked": False,
    }


def delete_marker(con: sqlite3.Connection, content_id: str, key: str) -> bool:
    cid = str(content_id or "").strip()
    k = validate_key(key)
    cur = con.execute(
        "DELETE FROM markers WHERE content_id=? AND key=?", (cid, k)
    )
    con.commit()
    return cur.rowcount > 0


def classify_decode_vae(prompt: Any) -> Optional[str]:
    """
    Classify VAE decode mode from a Comfy API prompt graph.

    Returns ``tiled`` if any node is VAEDecodeTiled, ``plain`` if any VAEDecode
    (and no tiled), else None.
    """
    if not isinstance(prompt, dict):
        return None
    saw_plain = False
    saw_tiled = False
    for node in prompt.values():
        if not isinstance(node, dict):
            continue
        ct = str(node.get("class_type") or "").strip()
        if ct == "VAEDecodeTiled":
            saw_tiled = True
        elif ct == "VAEDecode":
            saw_plain = True
    if saw_tiled:
        return "tiled"
    if saw_plain:
        return "plain"
    return None


def _job_output_relpaths(job: Dict[str, Any], *, output_root: Optional[Path] = None) -> List[str]:
    paths: List[str] = []
    for p in job.get("outputs") or []:
        if p:
            paths.append(str(p))
    submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    for p in submit.get("outputs") or []:
        if p:
            paths.append(str(p))
    deposit = job.get("deposit") if isinstance(job.get("deposit"), dict) else {}
    for p in deposit.get("videos") or []:
        if p:
            paths.append(str(p))
    out: List[str] = []
    seen: set[str] = set()
    root = output_root.resolve() if output_root else None
    for raw in paths:
        s = str(raw or "").strip().replace("\\", "/")
        if not s or s in seen:
            continue
        if root is not None:
            try:
                p = Path(s)
                if p.is_absolute():
                    rel = p.resolve().relative_to(root).as_posix()
                    s = rel
            except (ValueError, OSError):
                pass
        seen.add(s)
        out.append(s)
    return out


def _load_api_prompt(job: Dict[str, Any], job_path: Path) -> Optional[Dict[str, Any]]:
    try:
        from shape_factory_work_products import _prompt_doc_for_job

        return _prompt_doc_for_job(job, job_path)
    except Exception:
        pass
    sibling = job_path.with_name(job_path.name.replace(".job.json", ".prompt.json"))
    candidates = [sibling]
    submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    for raw in (submit.get("prompt_path"), job.get("prompt_path")):
        text = str(raw or "").strip()
        if text:
            candidates.append(Path(text).expanduser())
    for path in candidates:
        if not path.is_file():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(doc, dict):
            return doc
    return None


def _resolve_content_id(
    *,
    relpath: str,
    abs_hint: Optional[Path],
    registry_con: Any,
    register: bool,
) -> Optional[str]:
    import asset_registry as areg

    rel = str(relpath or "").strip().replace("\\", "/")
    if not rel:
        return None
    existing = areg.by_relpath(registry_con, rel)
    if existing and existing.get("content_id"):
        return str(existing["content_id"])
    # Basename fallback when path moved.
    bn_hits = areg.by_basename(registry_con, Path(rel).name)
    if len(bn_hits) == 1 and bn_hits[0].get("content_id"):
        return str(bn_hits[0]["content_id"])
    if not register:
        return None
    path = abs_hint
    if path is None or not path.is_file():
        return None
    cid = areg.register(registry_con, path, relpath=rel)
    return str(cid) if cid else None


def scan_decode_vae(
    *,
    jobs_root: Path,
    output_root: Path,
    markers_db: Optional[Path] = None,
    registry_path: Optional[Path] = None,
    apply: bool = False,
    register_assets: bool = True,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Scan factory jobs' ``.prompt.json`` graphs and stamp ``decode.vae`` markers.
    """
    import asset_registry as areg
    from shape_factory import default_asset_registry_path

    jobs_root = Path(jobs_root).expanduser().resolve()
    output_root = Path(output_root).expanduser().resolve()
    db_path = Path(markers_db) if markers_db else markers_path_for_output_root(output_root)
    reg_path = (
        Path(registry_path)
        if registry_path
        else default_asset_registry_path(output_root)
    )

    stats: Dict[str, Any] = {
        "jobs_scanned": 0,
        "jobs_with_decode": 0,
        "outputs_considered": 0,
        "marked": 0,
        "blocked": 0,
        "skipped_no_output": 0,
        "skipped_no_content_id": 0,
        "apply": bool(apply),
        "by_value": {"tiled": 0, "plain": 0},
        "samples": [],
    }

    markers_con = connect(db_path) if apply else None
    reg_con = areg.connect(reg_path)

    job_paths = sorted(jobs_root.rglob("*.job.json")) if jobs_root.is_dir() else []
    if limit is not None:
        job_paths = job_paths[: max(0, int(limit))]

    try:
        for job_path in job_paths:
            try:
                job = json.loads(job_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(job, dict):
                continue
            stats["jobs_scanned"] += 1
            prompt = _load_api_prompt(job, job_path)
            kind = classify_decode_vae(prompt)
            if kind is None:
                continue
            stats["jobs_with_decode"] += 1
            stats["by_value"][kind] = int(stats["by_value"].get(kind) or 0) + 1
            rels = _job_output_relpaths(job, output_root=output_root)
            if not rels:
                stats["skipped_no_output"] += 1
                continue
            job_key = str(job.get("job_key") or job_path.stem)
            for rel in rels:
                stats["outputs_considered"] += 1
                abs_path = (output_root / rel).resolve() if not Path(rel).is_absolute() else Path(rel)
                if not abs_path.is_file():
                    # Try as already-absolute under output
                    cand = Path(rel)
                    abs_path = cand if cand.is_file() else abs_path
                cid = _resolve_content_id(
                    relpath=rel if not Path(rel).is_absolute() else abs_path.name,
                    abs_hint=abs_path if abs_path.is_file() else None,
                    registry_con=reg_con,
                    register=register_assets and abs_path.is_file(),
                )
                if not cid and abs_path.is_file():
                    # Prefer rel under output_root when registering
                    try:
                        rel_use = abs_path.resolve().relative_to(output_root).as_posix()
                    except ValueError:
                        rel_use = abs_path.name
                    cid = _resolve_content_id(
                        relpath=rel_use,
                        abs_hint=abs_path,
                        registry_con=reg_con,
                        register=register_assets,
                    )
                if not cid:
                    stats["skipped_no_content_id"] += 1
                    continue
                sample = {
                    "job_key": job_key,
                    "content_id": cid,
                    "relpath": rel,
                    "decode.vae": kind,
                }
                if len(stats["samples"]) < 20:
                    stats["samples"].append(sample)
                if not apply or markers_con is None:
                    stats["marked"] += 1
                    continue
                row = set_marker(
                    markers_con, cid, DECODE_VAE_KEY, kind, source="scan"
                )
                if row.get("blocked"):
                    stats["blocked"] += 1
                else:
                    stats["marked"] += 1
        if apply and reg_con is not None:
            try:
                reg_con.commit()
            except sqlite3.Error:
                pass
    finally:
        if markers_con is not None:
            markers_con.close()
        reg_con.close()

    stats["markers_db"] = str(db_path)
    stats["registry"] = str(reg_path)
    return stats


def attach_markers_to_work_products(
    items: Sequence[Dict[str, Any]],
    *,
    output_root: Path,
    markers_db: Optional[Path] = None,
    registry_path: Optional[Path] = None,
) -> None:
    """Mutate work-product items in place: set ``content_id`` + ``markers`` when known."""
    import asset_registry as areg
    from shape_factory import default_asset_registry_path

    if not items:
        return
    output_root = Path(output_root).expanduser().resolve()
    db_path = Path(markers_db) if markers_db else markers_path_for_output_root(output_root)
    if not db_path.is_file():
        for it in items:
            if isinstance(it, dict):
                it.setdefault("markers", {})
        return
    reg_path = (
        Path(registry_path)
        if registry_path
        else default_asset_registry_path(output_root)
    )
    markers_con = connect(db_path)
    reg_con = areg.connect(reg_path) if Path(reg_path).is_file() else None
    try:
        for it in items:
            if not isinstance(it, dict):
                continue
            rel = str(it.get("output_relpath") or "").strip().replace("\\", "/")
            cid = str(it.get("content_id") or "").strip() or None
            if not cid and rel and reg_con is not None:
                row = areg.by_relpath(reg_con, rel)
                if row and row.get("content_id"):
                    cid = str(row["content_id"])
                else:
                    hits = areg.by_basename(reg_con, Path(rel).name)
                    if len(hits) == 1 and hits[0].get("content_id"):
                        cid = str(hits[0]["content_id"])
            if cid:
                it["content_id"] = cid
                it["markers"] = markers_map(markers_con, cid)
            else:
                it.setdefault("markers", {})
    finally:
        markers_con.close()
        if reg_con is not None:
            reg_con.close()


# --- CLI ---


def _resolve_og_and_output(args: argparse.Namespace) -> Tuple[Path, Path, Path]:
    """Return (og_root, output_root, markers_db)."""
    root = Path(getattr(args, "root", "") or "/home/yuji/comfyui-runpod-data/output/og")
    root = root.expanduser().resolve()
    if root.name == "og" and root.parent.name != "_status":
        og_root = root
        output_root = root.parent
    elif (root / "og").is_dir():
        output_root = root
        og_root = root / "og"
    else:
        og_root = root
        output_root = root.parent if root.name == "og" else root
    db = Path(args.db).expanduser().resolve() if getattr(args, "db", None) else default_markers_path(og_root)
    return og_root, output_root, db


def cmd_markers_get(args: argparse.Namespace) -> int:
    _og, _out, db = _resolve_og_and_output(args)
    cid = str(args.content_id or "").strip()
    if not cid:
        print("error: --content-id required", file=sys.stderr)
        return 1
    con = connect(db)
    try:
        if args.key:
            row = get_marker(con, cid, args.key)
            print(json.dumps({"ok": row is not None, "marker": row}, indent=2))
            return 0 if row else 1
        rows = list_for(con, cid)
        print(
            json.dumps(
                {
                    "ok": True,
                    "content_id": cid,
                    "markers": {k: v["value"] for k, v in rows.items()},
                    "rows": list(rows.values()),
                },
                indent=2,
            )
        )
        return 0
    finally:
        con.close()


def cmd_markers_set(args: argparse.Namespace) -> int:
    _og, _out, db = _resolve_og_and_output(args)
    try:
        con = connect(db)
        try:
            row = set_marker(
                con,
                args.content_id,
                args.key,
                args.value,
                source=str(args.source or "human"),
                force=bool(args.force),
            )
        finally:
            con.close()
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": not row.get("blocked"), "saved": row}, indent=2))
    return 0 if not row.get("blocked") else 2


def cmd_markers_list(args: argparse.Namespace) -> int:
    _og, _out, db = _resolve_og_and_output(args)
    con = connect(db)
    try:
        rows = query_by_key(
            con,
            args.key,
            value=args.value,
            limit=int(args.limit or 500),
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        con.close()
    print(json.dumps({"ok": True, "count": len(rows), "rows": rows}, indent=2))
    return 0


def cmd_markers_scan_decode(args: argparse.Namespace) -> int:
    _og, output_root, db = _resolve_og_and_output(args)
    jobs_root = Path(
        args.jobs_root or "/home/yuji/src/comfyui-runpod/.data/shape_factory/jobs"
    ).expanduser().resolve()
    stats = scan_decode_vae(
        jobs_root=jobs_root,
        output_root=output_root,
        markers_db=db,
        apply=bool(args.apply),
        register_assets=not bool(args.no_register),
        limit=int(args.limit) if args.limit is not None else None,
    )
    print(json.dumps({"ok": True, **stats}, indent=2))
    return 0


def add_markers_subparser(sub: argparse._SubParsersAction) -> None:
    markers = sub.add_parser(
        "markers",
        help="Work-product markers (content_id facts; not disposition/tags)",
    )
    markers_sub = markers.add_subparsers(dest="markers_cmd", required=True)

    get_p = markers_sub.add_parser("get", help="Get markers for a content_id")
    get_p.add_argument("--root", default="/home/yuji/comfyui-runpod-data/output/og")
    get_p.add_argument("--db", default=None)
    get_p.add_argument("--content-id", required=True)
    get_p.add_argument("--key", default=None)
    get_p.set_defaults(func=cmd_markers_get)

    set_p = markers_sub.add_parser("set", help="Set one marker")
    set_p.add_argument("--root", default="/home/yuji/comfyui-runpod-data/output/og")
    set_p.add_argument("--db", default=None)
    set_p.add_argument("--content-id", required=True)
    set_p.add_argument("--key", required=True)
    set_p.add_argument("--value", required=True)
    set_p.add_argument("--source", default="human", choices=sorted(_SOURCES))
    set_p.add_argument("--force", action="store_true", help="Overwrite even human rows")
    set_p.set_defaults(func=cmd_markers_set)

    list_p = markers_sub.add_parser("list", help="List markers by key[/value]")
    list_p.add_argument("--root", default="/home/yuji/comfyui-runpod-data/output/og")
    list_p.add_argument("--db", default=None)
    list_p.add_argument("--key", required=True)
    list_p.add_argument("--value", default=None)
    list_p.add_argument("--limit", type=int, default=500)
    list_p.set_defaults(func=cmd_markers_list)

    scan_p = markers_sub.add_parser(
        "scan-decode",
        help="Scan job prompts for VAEDecode / VAEDecodeTiled → decode.vae",
    )
    scan_p.add_argument("--root", default="/home/yuji/comfyui-runpod-data/output/og")
    scan_p.add_argument("--db", default=None)
    scan_p.add_argument(
        "--jobs-root",
        default="/home/yuji/src/comfyui-runpod/.data/shape_factory/jobs",
    )
    scan_p.add_argument("--apply", action="store_true", help="Write markers (default: dry-run)")
    scan_p.add_argument("--no-register", action="store_true", help="Do not register missing assets")
    scan_p.add_argument("--limit", type=int, default=None, help="Max jobs to scan")
    scan_p.set_defaults(func=cmd_markers_scan_decode)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Work-product markers")
    sub = ap.add_subparsers(dest="cmd", required=True)
    add_markers_subparser(sub)
    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
