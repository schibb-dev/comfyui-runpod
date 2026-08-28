#!/usr/bin/env python3
"""
Still auto-tagger store + batch runner (PromptGen-large via Comfy).

SQLite at <data_root>/shape_factory/still_tags.sqlite — not a monolith JSON blob.
Gallery enqueues runs; a background worker submits Comfy Florence jobs and writes events.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from vision_tag_judgment_tags import parse_danbooru_tags

SCHEMA_VERSION = 1
DB_BASENAME = "still_tags.sqlite"
DEFAULT_MODEL_PIN = "MiaoshouAI/Florence-2-large-PromptGen-v2.0"
# Same weights/task as V3a day-one; x2 cohort had similar F1 and stronger important-tag recall.
DEFAULT_PIN_POLICY = "cohort_x2_pg_large_tags"
DEFAULT_TASK = "prompt_gen_tags"
DEFAULT_LIMIT = 12
DEFAULT_COMFY_SERVER = "http://127.0.0.1:8188"
_SHA256_RE = re.compile(r"([0-9a-f]{64})", re.IGNORECASE)

_worker_lock = threading.Lock()
_worker_thread: Optional[threading.Thread] = None


def _utc_now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _json_loads_list(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if not raw:
        return []
    try:
        val = json.loads(str(raw))
    except Exception:
        return []
    if not isinstance(val, list):
        return []
    return [str(x).strip() for x in val if str(x).strip()]


def _dedupe(tags: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for t in tags:
        n = str(t or "").strip().lower()
        if not n or n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def extract_content_id(path: str) -> Optional[str]:
    m = _SHA256_RE.search(Path(str(path or "")).name)
    return m.group(1).lower() if m else None


def default_db_path(*, data_root: Optional[Path] = None) -> Path:
    env = os.environ.get("STILL_TAGS_DB_PATH", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if data_root is None:
        repo = Path(__file__).resolve().parents[2]
        data_root = Path(os.environ.get("SHAPE_FACTORY_DATA_ROOT") or (repo / ".data"))
    return Path(data_root).expanduser().resolve() / "shape_factory" / DB_BASENAME


def default_pin_path(*, status_dir: Optional[Path] = None) -> Path:
    env = os.environ.get("VISION_V3A_TAG_PIN_PATH", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if status_dir is not None:
        return Path(status_dir).expanduser().resolve() / "vision_v3a_tag_pin.json"
    for cand in (
        Path("/home/yuji/comfyui-runpod-data/output/_status/vision_v3a_tag_pin.json"),
        Path("/workspace/output/_status/vision_v3a_tag_pin.json"),
    ):
        if cand.is_file():
            return cand
    return Path("/home/yuji/comfyui-runpod-data/output/_status/vision_v3a_tag_pin.json")


def load_pin(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or default_pin_path()
    out: Dict[str, Any] = {
        "model_pin": DEFAULT_MODEL_PIN,
        "pin_policy": DEFAULT_PIN_POLICY,
        "fp_blocklist": [],
        "path": str(p),
    }
    env_policy = os.environ.get("STILL_TAG_PIN_POLICY", "").strip()
    if env_policy:
        out["pin_policy"] = env_policy
    if not p.is_file():
        return out
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return out
    # Still tagger defaults to x2 PromptGen-large; pin file still supplies FP blocklist.
    # Override with STILL_TAG_PIN_POLICY if set; otherwise keep DEFAULT_PIN_POLICY.
    if not env_policy:
        out["pin_policy"] = DEFAULT_PIN_POLICY
    out["model_pin"] = DEFAULT_MODEL_PIN
    out["fp_blocklist"] = _dedupe([str(x) for x in (doc.get("fp_blocklist") or [])])
    out["path"] = str(p)
    return out


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path), timeout=60.0, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS still_tag_items (
          content_id TEXT PRIMARY KEY,
          editorial_tags TEXT NOT NULL DEFAULT '[]',
          note TEXT,
          provisional_tags TEXT NOT NULL DEFAULT '[]',
          provisional_model_pin TEXT,
          provisional_pin_policy TEXT,
          provisional_run_id TEXT,
          provisional_tagged_at TEXT,
          provisional_raw_caption TEXT,
          suppressed_tags TEXT NOT NULL DEFAULT '[]',
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS still_tag_runs (
          run_id TEXT PRIMARY KEY,
          status TEXT NOT NULL,
          scope_json TEXT NOT NULL,
          enqueued_at TEXT NOT NULL,
          started_at TEXT,
          finished_at TEXT,
          total INT NOT NULL DEFAULT 0,
          done_count INT NOT NULL DEFAULT 0,
          error_count INT NOT NULL DEFAULT 0,
          skipped_count INT NOT NULL DEFAULT 0,
          pin_policy TEXT,
          model_pin TEXT,
          provider TEXT,
          comfy_server TEXT,
          detail TEXT
        );
        CREATE TABLE IF NOT EXISTS still_tag_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id TEXT NOT NULL,
          ts TEXT NOT NULL,
          kind TEXT NOT NULL,
          content_id TEXT,
          message TEXT,
          payload_json TEXT
        );
        CREATE INDEX IF NOT EXISTS still_tag_events_run ON still_tag_events(run_id, id);
        """
    )
    con.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    con.commit()


def ensure_db(db_path: Path) -> Path:
    con = connect(db_path)
    try:
        init_db(con)
    finally:
        con.close()
    return db_path


def migrate_editorial_from_json(db_path: Path, json_path: Path) -> int:
    """One-shot import of G1 input_still_tags.json editorial tags."""
    if not json_path.is_file():
        return 0
    try:
        doc = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    items = doc.get("items") if isinstance(doc.get("items"), dict) else {}
    if not items:
        return 0
    con = connect(db_path)
    try:
        init_db(con)
        n = 0
        now = _utc_now_iso()
        for cid, meta in items.items():
            key = str(cid or "").strip().lower()
            if not key:
                continue
            tags: List[str] = []
            note = None
            if isinstance(meta, dict):
                tags = _dedupe([str(t) for t in (meta.get("tags") or [])])
                note = str(meta.get("note") or "").strip() or None
            elif isinstance(meta, list):
                tags = _dedupe([str(t) for t in meta])
            row = con.execute("SELECT content_id FROM still_tag_items WHERE content_id=?", (key,)).fetchone()
            if row:
                continue
            con.execute(
                """
                INSERT INTO still_tag_items(
                  content_id, editorial_tags, note, provisional_tags, suppressed_tags, updated_at
                ) VALUES (?, ?, ?, '[]', '[]', ?)
                """,
                (key, _json_dumps(tags), note, now),
            )
            n += 1
        con.commit()
        return n
    finally:
        con.close()


def effective_tags_for_row(row: sqlite3.Row, *, fp_blocklist: Optional[Sequence[str]] = None) -> List[str]:
    editorial = _json_loads_list(row["editorial_tags"])
    provisional = _json_loads_list(row["provisional_tags"])
    suppressed = set(_json_loads_list(row["suppressed_tags"]))
    fp = set(_dedupe(list(fp_blocklist or [])))
    out: List[str] = []
    seen: set[str] = set()
    for t in editorial:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    for t in provisional:
        if t in seen or t in suppressed or t in fp:
            continue
        seen.add(t)
        out.append(t)
    return out


def get_item(con: sqlite3.Connection, content_id: str) -> Optional[Dict[str, Any]]:
    cid = str(content_id or "").strip().lower()
    if not cid:
        return None
    row = con.execute("SELECT * FROM still_tag_items WHERE content_id=?", (cid,)).fetchone()
    if not row:
        return None
    return _item_dict(row)


def _item_dict(row: sqlite3.Row, *, fp_blocklist: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    editorial = _json_loads_list(row["editorial_tags"])
    provisional = _json_loads_list(row["provisional_tags"])
    suppressed = _json_loads_list(row["suppressed_tags"])
    return {
        "content_id": row["content_id"],
        "editorial_tags": editorial,
        "tags": editorial,  # alias for G1 UI
        "note": row["note"],
        "provisional_tags": provisional,
        "provisional_model_pin": row["provisional_model_pin"],
        "provisional_pin_policy": row["provisional_pin_policy"],
        "provisional_run_id": row["provisional_run_id"],
        "provisional_tagged_at": row["provisional_tagged_at"],
        "suppressed_tags": suppressed,
        "effective_tags": effective_tags_for_row(row, fp_blocklist=fp_blocklist),
        "updated_at": row["updated_at"],
    }


def upsert_editorial(
    con: sqlite3.Connection,
    *,
    content_id: str,
    tags: Optional[Sequence[str]] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    cid = str(content_id or "").strip().lower()
    if not cid:
        raise ValueError("missing_content_id")
    now = _utc_now_iso()
    cur = con.execute("SELECT * FROM still_tag_items WHERE content_id=?", (cid,)).fetchone()
    if cur:
        next_tags = _dedupe(tags) if tags is not None else _json_loads_list(cur["editorial_tags"])
        next_note = str(note).strip() if note is not None else cur["note"]
        con.execute(
            """
            UPDATE still_tag_items
            SET editorial_tags=?, note=?, updated_at=?
            WHERE content_id=?
            """,
            (_json_dumps(next_tags), (str(next_note).strip() or None) if next_note else None, now, cid),
        )
    else:
        next_tags = _dedupe(tags or [])
        next_note = str(note).strip() if note is not None else None
        con.execute(
            """
            INSERT INTO still_tag_items(
              content_id, editorial_tags, note, provisional_tags, suppressed_tags, updated_at
            ) VALUES (?, ?, ?, '[]', '[]', ?)
            """,
            (cid, _json_dumps(next_tags), next_note or None, now),
        )
    con.commit()
    return get_item(con, cid) or {}


def upsert_provisional(
    con: sqlite3.Connection,
    *,
    content_id: str,
    tags: Sequence[str],
    model_pin: str,
    pin_policy: str,
    run_id: str,
    raw_caption: Optional[str] = None,
) -> None:
    cid = str(content_id or "").strip().lower()
    if not cid:
        raise ValueError("missing_content_id")
    now = _utc_now_iso()
    tags_j = _json_dumps(_dedupe(tags))
    cur = con.execute("SELECT content_id FROM still_tag_items WHERE content_id=?", (cid,)).fetchone()
    if cur:
        con.execute(
            """
            UPDATE still_tag_items SET
              provisional_tags=?,
              provisional_model_pin=?,
              provisional_pin_policy=?,
              provisional_run_id=?,
              provisional_tagged_at=?,
              provisional_raw_caption=?,
              updated_at=?
            WHERE content_id=?
            """,
            (tags_j, model_pin, pin_policy, run_id, now, raw_caption, now, cid),
        )
    else:
        con.execute(
            """
            INSERT INTO still_tag_items(
              content_id, editorial_tags, note, provisional_tags,
              provisional_model_pin, provisional_pin_policy, provisional_run_id,
              provisional_tagged_at, provisional_raw_caption, suppressed_tags, updated_at
            ) VALUES (?, '[]', NULL, ?, ?, ?, ?, ?, ?, '[]', ?)
            """,
            (cid, tags_j, model_pin, pin_policy, run_id, now, raw_caption, now),
        )
    con.commit()


def append_event(
    con: sqlite3.Connection,
    *,
    run_id: str,
    kind: str,
    content_id: Optional[str] = None,
    message: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> int:
    cur = con.execute(
        """
        INSERT INTO still_tag_events(run_id, ts, kind, content_id, message, payload_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            _utc_now_iso(),
            kind,
            content_id,
            message,
            _json_dumps(payload) if payload is not None else None,
        ),
    )
    con.commit()
    return int(cur.lastrowid)


def get_run(con: sqlite3.Connection, run_id: str) -> Optional[Dict[str, Any]]:
    row = con.execute("SELECT * FROM still_tag_runs WHERE run_id=?", (run_id,)).fetchone()
    if not row:
        return None
    return {
        "run_id": row["run_id"],
        "status": row["status"],
        "scope": json.loads(row["scope_json"] or "{}"),
        "enqueued_at": row["enqueued_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "total": int(row["total"] or 0),
        "done_count": int(row["done_count"] or 0),
        "error_count": int(row["error_count"] or 0),
        "skipped_count": int(row["skipped_count"] or 0),
        "pin_policy": row["pin_policy"],
        "model_pin": row["model_pin"],
        "provider": row["provider"],
        "comfy_server": row["comfy_server"],
        "detail": row["detail"],
    }


def list_events(
    con: sqlite3.Connection, *, run_id: str, after_id: int = 0, limit: int = 200
) -> List[Dict[str, Any]]:
    rows = con.execute(
        """
        SELECT id, run_id, ts, kind, content_id, message, payload_json
        FROM still_tag_events
        WHERE run_id=? AND id>?
        ORDER BY id ASC
        LIMIT ?
        """,
        (run_id, int(after_id or 0), max(1, min(2000, int(limit or 200)))),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        payload = None
        if r["payload_json"]:
            try:
                payload = json.loads(r["payload_json"])
            except Exception:
                payload = None
        out.append(
            {
                "id": int(r["id"]),
                "run_id": r["run_id"],
                "ts": r["ts"],
                "kind": r["kind"],
                "content_id": r["content_id"],
                "message": r["message"],
                "payload": payload,
            }
        )
    return out


def content_ids_missing_provisional(con: sqlite3.Connection, candidates: Sequence[str]) -> List[str]:
    out: List[str] = []
    for cid in candidates:
        key = str(cid or "").strip().lower()
        if not key:
            continue
        row = con.execute(
            "SELECT provisional_tags FROM still_tag_items WHERE content_id=?", (key,)
        ).fetchone()
        if not row or not _json_loads_list(row["provisional_tags"]):
            out.append(key)
    return out


def resolve_targets(
    *,
    data_root: Path,
    content_ids: Optional[Sequence[str]] = None,
    collection_id: Optional[str] = None,
    only_missing: bool = True,
    limit: int = DEFAULT_LIMIT,
    force: bool = False,
) -> List[Dict[str, Any]]:
    """
    Return list of {content_id, path, relpath} to tag.
    """
    from input_still_catalog import (  # type: ignore
        default_catalog_path,
        default_input_root,
        resolve_catalog_still_path,
        still_relpath_for_comfy,
    )
    from shape_factory_input_curation import load_collections  # type: ignore

    lim = max(1, int(limit or DEFAULT_LIMIT))
    input_root = default_input_root()
    wanted: List[str] = []

    if content_ids:
        wanted = [str(c).strip().lower() for c in content_ids if str(c).strip()]
    elif collection_id:
        cols = load_collections(data_root)
        coll = None
        for c in cols.get("collections") or []:
            if isinstance(c, dict) and str(c.get("id") or "") == str(collection_id):
                coll = c
                break
        if not coll:
            return []
        for it in coll.get("items") or []:
            path = ""
            if isinstance(it, dict):
                path = str(it.get("path") or "")
                cid = str(it.get("content_id") or "").strip().lower() or extract_content_id(path)
            else:
                path = str(it)
                cid = extract_content_id(path)
            if cid:
                wanted.append(cid)
    else:
        # Catalog walk: newest first.
        cat = default_catalog_path(data_root=data_root)
        if cat.is_file():
            con_cat = sqlite3.connect(str(cat), timeout=30.0)
            try:
                rows = con_cat.execute(
                    "SELECT path FROM stills ORDER BY first_seen DESC, mtime DESC LIMIT ?",
                    (max(lim * 20, 400),),
                ).fetchall()
            finally:
                con_cat.close()
            for (path,) in rows:
                cid = extract_content_id(str(path))
                if cid:
                    wanted.append(cid)

    # Dedupe preserve order
    seen: set[str] = set()
    ordered: List[str] = []
    for cid in wanted:
        if cid in seen:
            continue
        seen.add(cid)
        ordered.append(cid)

    db_path = default_db_path(data_root=data_root)
    ensure_db(db_path)
    con = connect(db_path)
    try:
        if only_missing and not force:
            ordered = content_ids_missing_provisional(con, ordered)
        ordered = ordered[:lim]
    finally:
        con.close()

    # Resolve paths via catalog
    cat = default_catalog_path(data_root=data_root)
    path_by_cid: Dict[str, str] = {}
    if cat.is_file():
        con_cat = sqlite3.connect(str(cat), timeout=30.0)
        try:
            for cid in ordered:
                # Match filename containing hash
                row = con_cat.execute(
                    "SELECT path FROM stills WHERE lower(path) LIKE ? LIMIT 1",
                    (f"%{cid}%",),
                ).fetchone()
                if row:
                    path_by_cid[cid] = str(row[0])
        finally:
            con_cat.close()

    out: List[Dict[str, Any]] = []
    for cid in ordered:
        stored = path_by_cid.get(cid)
        resolved = resolve_catalog_still_path(stored, input_root=input_root) if stored else None
        if resolved is None:
            # Fallback: search input_root by hash substring
            hits = list(input_root.rglob(f"*{cid}*")) if input_root.is_dir() else []
            hits = [h for h in hits if h.is_file()]
            resolved = hits[0] if hits else None
        if resolved is None:
            out.append({"content_id": cid, "path": None, "relpath": None, "missing": True})
            continue
        rel = still_relpath_for_comfy(resolved, input_root=input_root)
        out.append(
            {
                "content_id": cid,
                "path": str(resolved),
                "relpath": rel,
                "missing": False,
            }
        )
    return out


def enqueue_run(
    *,
    data_root: Path,
    content_ids: Optional[Sequence[str]] = None,
    collection_id: Optional[str] = None,
    only_missing: bool = True,
    limit: int = DEFAULT_LIMIT,
    force: bool = False,
    provider: str = "comfy",
    comfy_server: Optional[str] = None,
    dry_run: bool = False,
    pin_path: Optional[Path] = None,
    status_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    db_path = default_db_path(data_root=data_root)
    ensure_db(db_path)
    # Best-effort migrate G1 JSON once
    migrate_editorial_from_json(db_path, data_root / "shape_factory" / "input_still_tags.json")

    pin = load_pin(pin_path or default_pin_path(status_dir=status_dir))
    server = (comfy_server or os.environ.get("VISION_COMFY_SERVER") or DEFAULT_COMFY_SERVER).rstrip("/")
    if dry_run:
        provider = "dry-run"

    targets = resolve_targets(
        data_root=data_root,
        content_ids=content_ids,
        collection_id=collection_id,
        only_missing=only_missing,
        limit=limit,
        force=force,
    )
    runnable = [t for t in targets if not t.get("missing")]
    skipped = [t for t in targets if t.get("missing")]

    run_id = f"still_tag_{_utc_now_iso().replace(':', '').replace('-', '')}_{uuid.uuid4().hex[:8]}"
    scope = {
        "content_ids": [t["content_id"] for t in targets],
        "collection_id": collection_id,
        "only_missing": only_missing,
        "limit": limit,
        "force": force,
        "targets": targets,
    }
    con = connect(db_path)
    try:
        con.execute(
            """
            INSERT INTO still_tag_runs(
              run_id, status, scope_json, enqueued_at, total, done_count, error_count, skipped_count,
              pin_policy, model_pin, provider, comfy_server, detail
            ) VALUES (?, 'queued', ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                _json_dumps(scope),
                _utc_now_iso(),
                len(runnable),
                len(skipped),
                pin["pin_policy"],
                pin["model_pin"],
                provider,
                server,
                None,
            ),
        )
        append_event(
            con,
            run_id=run_id,
            kind="enqueued",
            message=f"enqueued {len(runnable)} (skipped_missing={len(skipped)})",
            payload={"total": len(runnable), "skipped": len(skipped)},
        )
        con.commit()
    finally:
        con.close()

    return {
        "ok": True,
        "run_id": run_id,
        "enqueued": len(runnable),
        "skipped": len(skipped),
        "db_path": str(db_path),
        "model_pin": pin["model_pin"],
        "pin_policy": pin["pin_policy"],
        "provider": provider,
        "comfy_server": server,
    }


def process_run(
    *,
    data_root: Path,
    run_id: str,
    status_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    db_path = default_db_path(data_root=data_root)
    con = connect(db_path)
    try:
        run = get_run(con, run_id)
        if not run:
            return {"ok": False, "error": "run_not_found", "run_id": run_id}
        if run["status"] in ("done", "error", "cancelled"):
            return {"ok": True, "run": run, "already_finished": True}

        pin = load_pin(default_pin_path(status_dir=status_dir))
        fp = set(pin.get("fp_blocklist") or [])
        scope = run.get("scope") or {}
        targets = [t for t in (scope.get("targets") or []) if isinstance(t, dict) and not t.get("missing")]
        provider = str(run.get("provider") or "comfy")
        server = str(run.get("comfy_server") or DEFAULT_COMFY_SERVER)
        model_pin = str(run.get("model_pin") or pin["model_pin"])
        pin_policy = str(run.get("pin_policy") or pin["pin_policy"])

        con.execute(
            "UPDATE still_tag_runs SET status=?, started_at=? WHERE run_id=?",
            ("running", _utc_now_iso(), run_id),
        )
        append_event(con, run_id=run_id, kind="started", message=f"provider={provider} server={server}")
        con.commit()

        from vision_slice_runner import CaptionRequest, make_runner  # type: ignore

        runner = make_runner(
            provider=provider,
            runner_label=provider,
            comfy_server=server,
            model_pin=model_pin,
            dry_run=provider in ("dry-run", "dry_run"),
            task=DEFAULT_TASK,
            max_new_tokens=256,
            image_mode="upload",
        )

        ndjson_path: Optional[Path] = None
        if status_dir is not None:
            status_dir = Path(status_dir)
            status_dir.mkdir(parents=True, exist_ok=True)
            ndjson_path = status_dir / "vision_still_tags.ndjson"

        done = 0
        errors = 0
        try:
            for t in targets:
                cid = str(t.get("content_id") or "")
                path = Path(str(t.get("path") or ""))
                try:
                    if provider in ("dry-run", "dry_run"):
                        # Emit parseable tags so store + UI can be exercised without GPU.
                        caption = (
                            "1girl, long hair, looking at viewer, solo, simple background, "
                            f"tag_smoke_{cid[:8]}"
                        )
                        raw = {"dry_run": True}
                        model_used = "dry-run"
                    else:
                        if not path.is_file():
                            raise FileNotFoundError(f"missing still: {path}")
                        result = runner.caption(
                            CaptionRequest(
                                image_path=path,
                                asset_relpath=str(t.get("relpath") or path.name),
                                meta={"content_id": cid},
                            )
                        )
                        caption = result.caption
                        raw = result.raw
                        model_used = result.model_pin

                    tags = [x for x in parse_danbooru_tags(caption, max_tags=64) if x not in fp]
                    if not tags and provider in ("dry-run", "dry_run"):
                        tags = _dedupe(caption.split(","))

                    upsert_provisional(
                        con,
                        content_id=cid,
                        tags=tags,
                        model_pin=model_used,
                        pin_policy=pin_policy,
                        run_id=run_id,
                        raw_caption=caption,
                    )
                    done += 1
                    con.execute(
                        "UPDATE still_tag_runs SET done_count=? WHERE run_id=?",
                        (done, run_id),
                    )
                    append_event(
                        con,
                        run_id=run_id,
                        kind="item_done",
                        content_id=cid,
                        message=f"{len(tags)} tags",
                        payload={"tags": tags[:24], "tag_count": len(tags)},
                    )
                    if ndjson_path is not None:
                        row = {
                            "schema": 1,
                            "content_id": cid,
                            "relpath": t.get("relpath"),
                            "tags": tags,
                            "caption": caption,
                            "model_pin": model_used,
                            "pin_policy": pin_policy,
                            "run_id": run_id,
                            "provider": provider,
                            "raw": raw,
                            "ts": _utc_now_iso(),
                        }
                        with ndjson_path.open("a", encoding="utf-8") as f:
                            f.write(json.dumps(row, ensure_ascii=False) + "\n")
                except Exception as e:
                    errors += 1
                    con.execute(
                        "UPDATE still_tag_runs SET error_count=? WHERE run_id=?",
                        (errors, run_id),
                    )
                    append_event(
                        con,
                        run_id=run_id,
                        kind="item_error",
                        content_id=cid,
                        message=str(e)[:500],
                    )
            status = "done" if errors == 0 or done > 0 else "error"
            detail = None if errors == 0 else f"{errors} item error(s)"
            con.execute(
                """
                UPDATE still_tag_runs
                SET status=?, finished_at=?, done_count=?, error_count=?, detail=?
                WHERE run_id=?
                """,
                (status, _utc_now_iso(), done, errors, detail, run_id),
            )
            append_event(
                con,
                run_id=run_id,
                kind="finished",
                message=f"status={status} done={done} errors={errors}",
                payload={"done": done, "errors": errors, "status": status},
            )
            con.commit()
            return {"ok": True, "run": get_run(con, run_id)}
        finally:
            try:
                runner.close()
            except Exception:
                pass
    finally:
        con.close()


def kick_worker(*, data_root: Path, status_dir: Optional[Path] = None) -> None:
    """Ensure a background thread is draining queued runs."""
    global _worker_thread

    def _loop() -> None:
        db_path = default_db_path(data_root=data_root)
        ensure_db(db_path)
        while True:
            con = connect(db_path)
            try:
                row = con.execute(
                    """
                    SELECT run_id FROM still_tag_runs
                    WHERE status='queued'
                    ORDER BY enqueued_at ASC
                    LIMIT 1
                    """
                ).fetchone()
            finally:
                con.close()
            if not row:
                break
            try:
                process_run(data_root=data_root, run_id=str(row["run_id"]), status_dir=status_dir)
            except Exception:
                # process_run should record errors; keep draining
                time.sleep(0.2)

    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return
        t = threading.Thread(target=_loop, name="still-tag-worker", daemon=True)
        _worker_thread = t
        t.start()


def enrich_still_items(
    items: Sequence[Dict[str, Any]],
    *,
    data_root: Path,
    fp_blocklist: Optional[Sequence[str]] = None,
) -> None:
    """In-place: attach editorial/provisional/effective tags from SQLite (+ legacy JSON fallback)."""
    db_path = default_db_path(data_root=data_root)
    if not db_path.is_file():
        return
    pin_fp = list(fp_blocklist or load_pin().get("fp_blocklist") or [])
    con = connect(db_path)
    try:
        for it in items:
            if not isinstance(it, dict):
                continue
            cid = str(it.get("content_id") or "").strip().lower()
            if not cid:
                continue
            row = con.execute("SELECT * FROM still_tag_items WHERE content_id=?", (cid,)).fetchone()
            if not row:
                continue
            d = _item_dict(row, fp_blocklist=pin_fp)
            it["editorial_tags"] = d["editorial_tags"]
            it["provisional_tags"] = d["provisional_tags"]
            it["effective_tags"] = d["effective_tags"]
            it["tags"] = d["effective_tags"]
            if d.get("note") is not None:
                it["note"] = d["note"]
    finally:
        con.close()
