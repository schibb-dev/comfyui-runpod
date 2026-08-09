#!/usr/bin/env python3
"""
First-class Clip entities: durable bookmarks onto a parent video asset.

NLE mapping: media/reel = asset; clip = named nominal span; job vhs_window = use.
Clips may overlap; clip_id is stable across in/out edits. No stored full-span row —
absence of a clip preference means the whole parent asset.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import asset_registry as areg

CLIP_ID_PREFIX = "clip_"
MIN_CLIP_GAP_S = 1e-3


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_clip_schema(con: sqlite3.Connection) -> None:
    """Create clips + prefs tables; bump registry schema meta when first applied."""
    try:
        con.execute("PRAGMA busy_timeout=30000")
    except sqlite3.Error:
        pass
    tables = {
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "clips" in tables and "asset_clip_prefs" in tables:
        row = con.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        try:
            ver = int(row["value"]) if row else 0
        except (TypeError, ValueError, KeyError):
            ver = 0
        if ver >= 3:
            return
    dirty = "clips" not in tables or "asset_clip_prefs" not in tables
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS clips (
            clip_id TEXT PRIMARY KEY,
            parent_content_id TEXT NOT NULL,
            mark_in_s REAL NOT NULL,
            mark_out_s REAL NOT NULL,
            label TEXT,
            origin TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_clips_parent ON clips(parent_content_id)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_clips_parent_in ON clips(parent_content_id, mark_in_s)"
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS asset_clip_prefs (
            parent_content_id TEXT PRIMARY KEY,
            default_clip_id TEXT
        )
        """
    )
    row = con.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()
    try:
        ver = int(row["value"]) if row else 0
    except (TypeError, ValueError, KeyError):
        ver = 0
    if ver < 3:
        con.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            ("3",),
        )
        dirty = True
    if dirty:
        con.commit()


def connect_clips(registry_path: Path) -> sqlite3.Connection:
    con = areg.connect(Path(registry_path))
    ensure_clip_schema(con)
    return con


def new_clip_id() -> str:
    return f"{CLIP_ID_PREFIX}{uuid.uuid4().hex}"


def clamp_marks(
    mark_in_s: float,
    mark_out_s: float,
    *,
    duration_s: Optional[float] = None,
) -> Tuple[float, float]:
    tin = max(0.0, float(mark_in_s))
    tout = float(mark_out_s)
    if duration_s is not None and float(duration_s) > 0:
        dur = float(duration_s)
        tin = min(tin, max(0.0, dur - MIN_CLIP_GAP_S))
        tout = min(max(tout, tin + MIN_CLIP_GAP_S), dur)
    if tout <= tin:
        tout = tin + MIN_CLIP_GAP_S
    return tin, tout


def _row_to_clip(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    d = dict(row)
    d["mark_in_s"] = float(d["mark_in_s"])
    d["mark_out_s"] = float(d["mark_out_s"])
    d["label"] = (str(d.get("label") or "").strip() or None)
    d["origin"] = (str(d.get("origin") or "").strip() or None)
    d["notes"] = (str(d.get("notes") or "").strip() or None)
    return d


def get_clip(con: sqlite3.Connection, clip_id: str) -> Optional[Dict[str, Any]]:
    cid = str(clip_id or "").strip()
    if not cid:
        return None
    row = con.execute("SELECT * FROM clips WHERE clip_id=?", (cid,)).fetchone()
    return _row_to_clip(row)


def list_clips_for_parent(
    con: sqlite3.Connection,
    parent_content_id: str,
) -> List[Dict[str, Any]]:
    pid = str(parent_content_id or "").strip()
    if not pid:
        return []
    rows = con.execute(
        """
        SELECT * FROM clips
        WHERE parent_content_id=?
        ORDER BY mark_in_s ASC, created_at ASC
        """,
        (pid,),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = _row_to_clip(r)
        if d:
            out.append(d)
    return out


def list_clips_library(
    con: sqlite3.Connection,
    *,
    limit: int = 100,
    offset: int = 0,
    origin: Optional[str] = None,
    q: Optional[str] = None,
    defaults_only: bool = False,
) -> Dict[str, Any]:
    """Browse clips across parents, joined to asset current_relpath when known."""
    lim = max(1, min(int(limit or 100), 500))
    off = max(0, int(offset or 0))
    origin_raw = str(origin or "").strip()
    origin_empty = origin_raw in ("(none)", "__empty__", "__none__")
    origin_f = None if origin_empty else (origin_raw or None)
    q_raw = str(q or "").strip()
    q_f = q_raw.lower() if q_raw else None
    like = f"%{q_f}%" if q_f else None

    where = ["1=1"]
    params: List[Any] = []
    if origin_empty:
        where.append("(c.origin IS NULL OR TRIM(c.origin) = '')")
    elif origin_f:
        where.append("c.origin = ?")
        params.append(origin_f)
    if like is not None:
        where.append(
            "("
            "lower(IFNULL(c.label,'')) LIKE ? OR "
            "lower(IFNULL(c.notes,'')) LIKE ? OR "
            "lower(IFNULL(c.clip_id,'')) LIKE ? OR "
            "lower(IFNULL(a.current_relpath,'')) LIKE ?"
            ")"
        )
        params.extend([like, like, like, like])
    if defaults_only:
        where.append("p.default_clip_id = c.clip_id")

    where_sql = " AND ".join(where)
    from_sql = """
        FROM clips c
        LEFT JOIN assets a ON a.content_id = c.parent_content_id
        LEFT JOIN asset_clip_prefs p ON p.parent_content_id = c.parent_content_id
    """
    total = int(
        con.execute(
            f"SELECT COUNT(*) AS n {from_sql} WHERE {where_sql}",
            tuple(params),
        ).fetchone()["n"]
    )
    rows = con.execute(
        f"""
        SELECT
            c.*,
            a.current_relpath AS media_relpath,
            a.kind AS asset_kind,
            a.ext AS asset_ext,
            a.mtime AS asset_mtime,
            CASE WHEN p.default_clip_id = c.clip_id THEN 1 ELSE 0 END AS is_default
        {from_sql}
        WHERE {where_sql}
        ORDER BY c.updated_at DESC, c.created_at DESC
        LIMIT ? OFFSET ?
        """,
        tuple(params + [lim, off]),
    ).fetchall()

    clips: List[Dict[str, Any]] = []
    for r in rows:
        d = _row_to_clip(r)
        if not d:
            continue
        rel = str(r["media_relpath"] or "").strip() or None
        d["media_relpath"] = rel
        d["media_basename"] = Path(rel).name if rel else None
        d["asset_kind"] = str(r["asset_kind"] or "").strip() or None
        d["asset_ext"] = str(r["asset_ext"] or "").strip() or None
        try:
            d["asset_mtime"] = float(r["asset_mtime"]) if r["asset_mtime"] is not None else None
        except (TypeError, ValueError):
            d["asset_mtime"] = None
        d["is_default"] = bool(int(r["is_default"] or 0))
        d["duration_s"] = max(0.0, float(d["mark_out_s"]) - float(d["mark_in_s"]))
        if rel:
            d["media_url"] = "/files/" + rel.replace("\\", "/")
        else:
            d["media_url"] = None
        clips.append(d)

    origin_counts: Dict[str, int] = {}
    for r in con.execute(
        "SELECT IFNULL(origin, '') AS origin, COUNT(*) AS n FROM clips GROUP BY IFNULL(origin, '')"
    ).fetchall():
        key = str(r["origin"] or "").strip() or "(none)"
        origin_counts[key] = int(r["n"])

    return {
        "ok": True,
        "clips": clips,
        "count": len(clips),
        "total": total,
        "limit": lim,
        "offset": off,
        "origin_counts": origin_counts,
        "filters": {
            "origin": "(none)" if origin_empty else origin_f,
            "q": q_raw or None,
            "defaults_only": bool(defaults_only),
        },
    }


def create_clip(
    con: sqlite3.Connection,
    *,
    parent_content_id: str,
    mark_in_s: float,
    mark_out_s: float,
    label: Optional[str] = None,
    origin: Optional[str] = None,
    notes: Optional[str] = None,
    duration_s: Optional[float] = None,
    clip_id: Optional[str] = None,
) -> Dict[str, Any]:
    parent = str(parent_content_id or "").strip()
    if not parent:
        raise ValueError("missing_parent_content_id")
    tin, tout = clamp_marks(mark_in_s, mark_out_s, duration_s=duration_s)
    cid = str(clip_id or "").strip() or new_clip_id()
    now = _utc_now()
    lab = (str(label or "").strip() or "Clip")[:200]
    con.execute(
        """
        INSERT INTO clips(
            clip_id, parent_content_id, mark_in_s, mark_out_s,
            label, origin, notes, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            cid,
            parent,
            tin,
            tout,
            lab,
            (str(origin).strip() if origin else None),
            (str(notes).strip() if notes else None),
            now,
            now,
        ),
    )
    con.commit()
    clip = get_clip(con, cid)
    assert clip is not None
    return clip


def update_clip(
    con: sqlite3.Connection,
    clip_id: str,
    *,
    mark_in_s: Optional[float] = None,
    mark_out_s: Optional[float] = None,
    label: Optional[str] = None,
    notes: Optional[str] = None,
    duration_s: Optional[float] = None,
) -> Dict[str, Any]:
    existing = get_clip(con, clip_id)
    if existing is None:
        raise KeyError(f"clip_not_found:{clip_id}")
    tin = float(existing["mark_in_s"] if mark_in_s is None else mark_in_s)
    tout = float(existing["mark_out_s"] if mark_out_s is None else mark_out_s)
    tin, tout = clamp_marks(tin, tout, duration_s=duration_s)
    lab = existing.get("label")
    if label is not None:
        lab = (str(label).strip() or "Clip")[:200]
    nts = existing.get("notes")
    if notes is not None:
        nts = str(notes).strip() or None
    now = _utc_now()
    con.execute(
        """
        UPDATE clips
        SET mark_in_s=?, mark_out_s=?, label=?, notes=?, updated_at=?
        WHERE clip_id=?
        """,
        (tin, tout, lab, nts, now, str(clip_id).strip()),
    )
    con.commit()
    clip = get_clip(con, clip_id)
    assert clip is not None
    return clip


def delete_clip(con: sqlite3.Connection, clip_id: str) -> bool:
    cid = str(clip_id or "").strip()
    if not cid:
        return False
    # Clear default prefs pointing at this clip.
    con.execute(
        "UPDATE asset_clip_prefs SET default_clip_id=NULL WHERE default_clip_id=?",
        (cid,),
    )
    cur = con.execute("DELETE FROM clips WHERE clip_id=?", (cid,))
    con.commit()
    return int(cur.rowcount or 0) > 0


def get_default_clip_id(
    con: sqlite3.Connection,
    parent_content_id: str,
) -> Optional[str]:
    pid = str(parent_content_id or "").strip()
    if not pid:
        return None
    row = con.execute(
        "SELECT default_clip_id FROM asset_clip_prefs WHERE parent_content_id=?",
        (pid,),
    ).fetchone()
    if row is None:
        return None
    cid = str(row["default_clip_id"] or "").strip()
    if not cid:
        return None
    # Drop stale defaults.
    if get_clip(con, cid) is None:
        con.execute(
            "UPDATE asset_clip_prefs SET default_clip_id=NULL WHERE parent_content_id=?",
            (pid,),
        )
        con.commit()
        return None
    return cid


def set_default_clip(
    con: sqlite3.Connection,
    parent_content_id: str,
    clip_id: Optional[str],
) -> Optional[str]:
    """Set or clear the editorial default clip for a parent asset. Returns new default id."""
    parent = str(parent_content_id or "").strip()
    if not parent:
        raise ValueError("missing_parent_content_id")
    cid = str(clip_id or "").strip() or None
    if cid:
        clip = get_clip(con, cid)
        if clip is None:
            raise KeyError(f"clip_not_found:{cid}")
        if str(clip["parent_content_id"]) != parent:
            raise ValueError("clip_parent_mismatch")
    con.execute(
        """
        INSERT INTO asset_clip_prefs(parent_content_id, default_clip_id)
        VALUES(?,?)
        ON CONFLICT(parent_content_id) DO UPDATE SET default_clip_id=excluded.default_clip_id
        """,
        (parent, cid),
    )
    con.commit()
    return cid


def marks_to_vhs_window(
    *,
    mark_in_s: float,
    mark_out_s: float,
    duration_s: float,
    fps: float,
    frame_count: Optional[int] = None,
) -> Dict[str, Any]:
    """Convert clip/use marks into VHS skip/cap via existing queue helpers."""
    from shape_factory_queue import trim_seconds_to_vhs_window

    return trim_seconds_to_vhs_window(
        mark_in=float(mark_in_s),
        mark_out=float(mark_out_s),
        duration_s=float(duration_s) if duration_s > 0 else 0.0,
        fps=float(fps) if fps > 0 else 18.0,
        frame_count=frame_count,
    )


def resolve_job_use_window(
    *,
    job: Optional[Dict[str, Any]] = None,
    source_clip_id: Optional[str] = None,
    parent_content_id: Optional[str] = None,
    media_meta: Optional[Dict[str, Any]] = None,
    con: Optional[sqlite3.Connection] = None,
    registry_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Resolve this run's use window.

    Order: explicit job vhs_window → source_clip_id → parent default_clip → full file.
    Never reads catalog template skip/cap.
    """
    meta = dict(media_meta or {})
    fps = float(meta.get("fps") or 18.0)
    if fps <= 0:
        fps = 18.0
    duration = float(meta.get("duration") or 0.0)
    try:
        frame_count = int(meta.get("frame_count") or 0)
    except (TypeError, ValueError):
        frame_count = 0
    if duration <= 0 and frame_count > 0 and fps > 0:
        duration = frame_count / fps
    if frame_count <= 0 and duration > 0 and fps > 0:
        frame_count = max(1, int(round(duration * fps)))

    job = job if isinstance(job, dict) else {}
    win = job.get("vhs_window") if isinstance(job.get("vhs_window"), dict) else {}
    explicit_marks = (
        win.get("mark_in") is not None
        or win.get("mark_out") is not None
        or win.get("skip_first_frames") is not None
        or win.get("frame_load_cap") is not None
    )

    own_con = False
    if con is None and registry_path is not None:
        con = connect_clips(Path(registry_path))
        own_con = True

    try:
        if explicit_marks:
            if win.get("mark_in") is not None or win.get("mark_out") is not None:
                tin = float(win["mark_in"] if win.get("mark_in") is not None else 0.0)
                tout = float(
                    win["mark_out"]
                    if win.get("mark_out") is not None
                    else (duration if duration > 0 else tin)
                )
                tin, tout = clamp_marks(tin, tout, duration_s=duration or None)
                vhs = marks_to_vhs_window(
                    mark_in_s=tin,
                    mark_out_s=tout,
                    duration_s=duration,
                    fps=fps,
                    frame_count=frame_count or None,
                )
                return {
                    "source": "use",
                    "clip_id": str(win.get("clip_id") or job.get("source_clip_id") or "").strip()
                    or None,
                    "mark_in": tin,
                    "mark_out": tout,
                    "skip_first_frames": int(vhs["skip_first_frames"]),
                    "frame_load_cap": int(vhs["frame_load_cap"]),
                    "fps": fps,
                    "frame_count": frame_count,
                    "duration": duration,
                }
            # skip/cap only
            from shape_factory_queue import clamp_vhs_load_window

            try:
                req_skip = int(win.get("skip_first_frames") or 0)
            except (TypeError, ValueError):
                req_skip = 0
            try:
                req_cap = int(win.get("frame_load_cap") or 0)
            except (TypeError, ValueError):
                req_cap = 0
            if frame_count > 0:
                skip, cap, _ = clamp_vhs_load_window(
                    skip_first_frames=req_skip,
                    frame_load_cap=req_cap,
                    frame_count=frame_count,
                )
            else:
                skip, cap = max(0, req_skip), max(0, req_cap)
            # Near-empty after skip → full file
            if frame_count > 0 and skip >= frame_count:
                skip, cap = 0, 0
            remaining = max(0, frame_count - skip) if frame_count > 0 else 0
            if frame_count > 0 and remaining > 0 and remaining < 2 and req_skip > 0:
                skip, cap = 0, 0
            mark_in = skip / fps if fps > 0 else 0.0
            if cap > 0 and fps > 0:
                mark_out = mark_in + (cap / fps)
            else:
                mark_out = duration if duration > 0 else mark_in
            return {
                "source": "use",
                "clip_id": str(win.get("clip_id") or job.get("source_clip_id") or "").strip()
                or None,
                "mark_in": mark_in,
                "mark_out": mark_out,
                "skip_first_frames": int(skip),
                "frame_load_cap": int(cap),
                "fps": fps,
                "frame_count": frame_count,
                "duration": duration,
            }

        clip_id = str(
            source_clip_id
            or job.get("source_clip_id")
            or (win.get("clip_id") if win else "")
            or ""
        ).strip() or None

        parent = str(parent_content_id or "").strip() or None
        if not parent:
            bindings = job.get("bindings") if isinstance(job.get("bindings"), dict) else {}
            src = bindings.get("source_video")
            if isinstance(src, dict):
                parent = str(src.get("content_id") or "").strip() or None

        clip: Optional[Dict[str, Any]] = None
        seed_source = "full"
        if con is not None and clip_id:
            clip = get_clip(con, clip_id)
            if clip:
                seed_source = "clip"
        if clip is None and con is not None and parent:
            default_id = get_default_clip_id(con, parent)
            if default_id:
                clip = get_clip(con, default_id)
                if clip:
                    clip_id = default_id
                    seed_source = "default_clip"
            if clip is None:
                siblings = list_clips_for_parent(con, parent)
                if siblings:
                    clip = siblings[0]
                    clip_id = str(clip["clip_id"])
                    seed_source = "clip"

        if clip is not None:
            tin, tout = clamp_marks(
                float(clip["mark_in_s"]),
                float(clip["mark_out_s"]),
                duration_s=duration or None,
            )
            vhs = marks_to_vhs_window(
                mark_in_s=tin,
                mark_out_s=tout,
                duration_s=duration,
                fps=fps,
                frame_count=frame_count or None,
            )
            skip = int(vhs["skip_first_frames"])
            cap = int(vhs["frame_load_cap"])
            if frame_count > 0 and skip >= frame_count:
                return {
                    "source": "full",
                    "clip_id": None,
                    "mark_in": 0.0,
                    "mark_out": duration if duration > 0 else 0.0,
                    "skip_first_frames": 0,
                    "frame_load_cap": 0,
                    "fps": fps,
                    "frame_count": frame_count,
                    "duration": duration,
                    "message": f"clip window empty on this media; fell back to full",
                }
            return {
                "source": seed_source,
                "clip_id": clip_id,
                "mark_in": tin,
                "mark_out": tout,
                "skip_first_frames": skip,
                "frame_load_cap": cap,
                "fps": fps,
                "frame_count": frame_count,
                "duration": duration,
            }

        return {
            "source": "full",
            "clip_id": None,
            "mark_in": 0.0,
            "mark_out": duration if duration > 0 else 0.0,
            "skip_first_frames": 0,
            "frame_load_cap": 0,
            "fps": fps,
            "frame_count": frame_count,
            "duration": duration,
        }
    finally:
        if own_con and con is not None:
            con.close()


def import_trims_presets_as_clips(
    con: sqlite3.Connection,
    *,
    parent_content_id: str,
    trims_doc: Dict[str, Any],
    duration_s: Optional[float] = None,
    origin: str = "trims_import",
) -> List[Dict[str, Any]]:
    """
    Create clips from a ``*.trims.json`` document (nontrivial presets only).

    Does not delete existing clips. Skips presets that closely match an existing
    clip on the same parent (within 50ms).
    """
    parent = str(parent_content_id or "").strip()
    if not parent:
        return []
    existing = list_clips_for_parent(con, parent)
    created: List[Dict[str, Any]] = []
    contexts = trims_doc.get("contexts") if isinstance(trims_doc, dict) else None
    if not isinstance(contexts, dict):
        return []
    for _ctx, body in contexts.items():
        if not isinstance(body, dict):
            continue
        presets = body.get("presets")
        if not isinstance(presets, list):
            continue
        for p in presets:
            if not isinstance(p, dict):
                continue
            try:
                tin = float(p.get("in"))
                tout = float(p.get("out"))
            except (TypeError, ValueError):
                continue
            if tout - tin < MIN_CLIP_GAP_S:
                continue
            tin, tout = clamp_marks(tin, tout, duration_s=duration_s)
            if any(
                abs(float(e["mark_in_s"]) - tin) < 0.05
                and abs(float(e["mark_out_s"]) - tout) < 0.05
                for e in existing + created
            ):
                continue
            label = str(p.get("label") or "Clip").strip() or "Clip"
            clip = create_clip(
                con,
                parent_content_id=parent,
                mark_in_s=tin,
                mark_out_s=tout,
                label=label,
                origin=origin,
                duration_s=duration_s,
            )
            created.append(clip)
            # First imported nontrivial preset can become default if none set.
            if get_default_clip_id(con, parent) is None and created:
                set_default_clip(con, parent, clip["clip_id"])
    return created


_VIDEO_EXTS = (".mp4", ".webm", ".mov", ".mkv", ".avi")


def media_path_for_trims_sidecar(sidecar: Path) -> Optional[Path]:
    """Map ``foo.trims.json`` (from ``Path.with_suffix('.trims.json')``) back to ``foo.mp4`` etc."""
    name = Path(sidecar).name
    if not name.endswith(".trims.json"):
        return None
    stem = name[: -len(".trims.json")]
    parent = Path(sidecar).parent
    for ext in _VIDEO_EXTS:
        cand = parent / f"{stem}{ext}"
        if cand.is_file():
            return cand
    return None


def backfill_clips_from_trims_sidecars(
    *,
    output_root: Path,
    registry_path: Optional[Path] = None,
    apply: bool = False,
) -> Dict[str, Any]:
    """
    One-shot walk: import nontrivial ``*.trims.json`` presets as Clip rows.

    Idempotent via ``import_trims_presets_as_clips`` near-duplicate skip.
    """
    from shape_factory_queue import _probe_media_frame_meta

    out = Path(output_root).expanduser().resolve()
    og = out / "og" if (out / "og").is_dir() else out
    reg = Path(registry_path).expanduser().resolve() if registry_path else areg.default_registry_path(og)

    sidecars = sorted(out.rglob("*.trims.json"))
    summary: Dict[str, Any] = {
        "ok": True,
        "apply": bool(apply),
        "output_root": str(out),
        "registry": str(reg),
        "sidecars": len(sidecars),
        "missing_media": 0,
        "parents": 0,
        "clips_created": 0,
        "skipped_empty": 0,
        "errors": [],
    }
    if not apply:
        # Dry-run: count only (no DB writes / probes beyond existence).
        for sc in sidecars:
            if media_path_for_trims_sidecar(sc) is None:
                summary["missing_media"] += 1
        return summary

    con = connect_clips(reg)
    seen_parents: set[str] = set()
    try:
        for sc in sidecars:
            media = media_path_for_trims_sidecar(sc)
            if media is None:
                summary["missing_media"] += 1
                continue
            try:
                rel = str(media.resolve().relative_to(out)).replace("\\", "/")
            except ValueError:
                rel = media.name
            try:
                duration = float((_probe_media_frame_meta(media) or {}).get("duration") or 0.0)
                existing = areg.by_relpath(con, rel)
                if existing and existing.get("content_id"):
                    parent = str(existing["content_id"])
                else:
                    parent = areg.register(con, media, relpath=rel, kind="video", with_dims=False)
                doc = json.loads(sc.read_text(encoding="utf-8"))
                created = import_trims_presets_as_clips(
                    con,
                    parent_content_id=parent,
                    trims_doc=doc if isinstance(doc, dict) else {},
                    duration_s=duration or None,
                    origin="trims_backfill",
                )
                seen_parents.add(parent)
                summary["clips_created"] += len(created)
                if not created:
                    summary["skipped_empty"] += 1
            except Exception as exc:  # noqa: BLE001
                summary["errors"].append({"sidecar": str(sc), "detail": str(exc)})
                summary["ok"] = False
    finally:
        con.close()
    summary["parents"] = len(seen_parents)
    return summary


# Family/template-style skips baked into many GEX/GEX2 graphs — not editorial clips
# when paired with frame_load_cap == 0.
_TEMPLATE_SKIP_DEFAULTS = frozenset({47, 57, 85})


def _vhs_window_from_widgets(widgets: Any) -> Optional[Tuple[str, int, int]]:
    """Return (video_path, skip_first_frames, frame_load_cap) or None."""
    video, skip, cap = "", 0, 0
    if isinstance(widgets, dict):
        video = str(widgets.get("video") or "").strip()
        try:
            skip = int(widgets.get("skip_first_frames") or 0)
        except (TypeError, ValueError):
            skip = 0
        try:
            cap = int(widgets.get("frame_load_cap") or 0)
        except (TypeError, ValueError):
            cap = 0
    elif isinstance(widgets, list) and widgets:
        video = str(widgets[0] or "").strip()
        if len(widgets) >= 7:
            try:
                cap = int(widgets[5] or 0)
            except (TypeError, ValueError):
                cap = 0
            try:
                skip = int(widgets[6] or 0)
            except (TypeError, ValueError):
                skip = 0
    else:
        return None
    if skip < 0:
        skip = 0
    if cap < 0:
        cap = 0
    if skip <= 0 and cap <= 0:
        return None
    return video, skip, cap


def is_editorial_vhs_window(skip: int, cap: int, *, include_template_skips: bool = False) -> bool:
    """True when the window looks like an intentional span, not a bare template skip."""
    if cap > 0:
        return True
    if include_template_skips:
        return skip > 0 or cap > 0
    return skip > 0 and skip not in _TEMPLATE_SKIP_DEFAULTS


def _resolve_workflow_video_path(
    video_path: str,
    *,
    output_root: Path,
    data_root: Path,
) -> Optional[Path]:
    """Locate the source media for an embedded workflow video reference."""
    raw = str(video_path or "").replace("\\", "/").strip()
    if not raw:
        return None
    out = output_root.expanduser().resolve()
    data = data_root.expanduser().resolve()
    name = Path(raw).name

    candidates: List[Path] = []
    p = Path(raw)
    if p.is_absolute():
        candidates.append(p)
    candidates.extend(
        [
            data / raw.lstrip("/"),
            out / raw.lstrip("/"),
            out / name,
        ]
    )
    if "output/output/" in raw:
        candidates.append(data / raw.replace("output/output/", "output/", 1).lstrip("/"))
        candidates.append(out / raw.split("output/output/", 1)[-1])
    if raw.startswith("output/"):
        candidates.append(data / raw)
        candidates.append(out / raw[len("output/") :])

    seen: set[str] = set()
    for c in candidates:
        key = str(c)
        if key in seen:
            continue
        seen.add(key)
        try:
            if c.is_file():
                return c.resolve()
        except OSError:
            continue

    # Basename search under output (og first).
    hits: List[Path] = []
    for root in (out / "og", out):
        if not root.is_dir():
            continue
        try:
            for hit in root.rglob(name):
                if hit.is_file():
                    hits.append(hit.resolve())
                    if len(hits) >= 8:
                        break
        except OSError:
            continue
        if hits:
            break
    if not hits:
        return None
    og_hits = [h for h in hits if "/output/og/" in str(h).replace("\\", "/")]
    pool = og_hits or hits
    return pool[0]


def _score_workflow_window(
    *,
    wf_reuse: int,
    as_job_source: int,
    src_workflow_count: int,
    basename: str,
) -> float:
    score = float(wf_reuse) * 3.0
    score += float(src_workflow_count) * 0.5
    score += float(as_job_source) * 2.0
    if "Kneel" in basename or basename.startswith("X-"):
        score += 1.5
    return score


def backfill_clips_from_workflows(
    *,
    workflows_root: Path,
    output_root: Path,
    data_root: Optional[Path] = None,
    jobs_root: Optional[Path] = None,
    registry_path: Optional[Path] = None,
    apply: bool = False,
    top: int = 100,
    include_template_skips: bool = False,
    set_default: bool = True,
) -> Dict[str, Any]:
    """
    Farm Clip bookmarks from VHS windows embedded in saved UI workflows.

    Hard gate: source video must resolve on disk (trim without parent is dropped).
    Default filter keeps editorial windows (cap>0 or non-template skip); ranks by
    workflow reuse + later factory ``source_video`` use.
    """
    from collections import Counter, defaultdict

    from shape_factory_queue import _probe_media_frame_meta

    wf_root = Path(workflows_root).expanduser().resolve()
    out = Path(output_root).expanduser().resolve()
    data = Path(data_root).expanduser().resolve() if data_root else out.parent
    jobs = Path(jobs_root).expanduser().resolve() if jobs_root else None
    og = out / "og" if (out / "og").is_dir() else out
    reg = Path(registry_path).expanduser().resolve() if registry_path else areg.default_registry_path(og)

    summary: Dict[str, Any] = {
        "ok": True,
        "apply": bool(apply),
        "workflows_root": str(wf_root),
        "output_root": str(out),
        "registry": str(reg),
        "top": int(top),
        "include_template_skips": bool(include_template_skips),
        "files_scanned": 0,
        "vhs_nodes_nontrivial": 0,
        "editorial_instances": 0,
        "unique_editorial": 0,
        "unresolved_source": 0,
        "clips_created": 0,
        "clips_skipped_dup": 0,
        "defaults_set": 0,
        "candidates": [],
        "unresolved_samples": [],
        "errors": [],
    }
    if not wf_root.is_dir():
        summary["ok"] = False
        summary["errors"].append({"detail": f"workflows_root_missing:{wf_root}"})
        return summary

    # Optional gravity: basename -> count as factory source_video.
    as_source: Counter[str] = Counter()
    if jobs and jobs.is_dir():
        for jp in jobs.rglob("*.job.json"):
            try:
                job = json.loads(jp.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            binds = job.get("bindings") if isinstance(job, dict) and isinstance(job.get("bindings"), dict) else {}
            for slot, spec in binds.items():
                if "video" not in str(slot).lower():
                    continue
                path = ""
                if isinstance(spec, dict):
                    path = str(spec.get("path") or spec.get("relpath") or "")
                elif isinstance(spec, str):
                    path = spec
                name = Path(path.replace("\\", "/")).name
                if name:
                    as_source[name] += 1

    # Aggregate unique (basename, skip, cap).
    agg: Dict[Tuple[str, int, int], Dict[str, Any]] = {}
    src_wfs: Dict[str, set[str]] = defaultdict(set)

    for p in sorted(wf_root.rglob("*.json")):
        if ".validate." in p.name:
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        nodes = doc.get("nodes") if isinstance(doc, dict) else None
        if not isinstance(nodes, list):
            continue
        summary["files_scanned"] += 1
        for node in nodes:
            if not isinstance(node, dict):
                continue
            ntype = str(node.get("type") or "")
            if ntype not in ("VHS_LoadVideoPath", "VHS_LoadVideo"):
                continue
            parsed = _vhs_window_from_widgets(node.get("widgets_values"))
            if not parsed:
                continue
            video, skip, cap = parsed
            summary["vhs_nodes_nontrivial"] += 1
            if not is_editorial_vhs_window(skip, cap, include_template_skips=include_template_skips):
                continue
            summary["editorial_instances"] += 1
            bname = Path(video.replace("\\", "/")).name or video
            key = (bname, int(skip), int(cap))
            src_wfs[bname].add(p.name)
            row = agg.get(key)
            if row is None:
                agg[key] = {
                    "basename": bname,
                    "video": video,
                    "skip": int(skip),
                    "cap": int(cap),
                    "wf_reuse": 1,
                    "sample_workflow": p.name,
                }
            else:
                row["wf_reuse"] = int(row["wf_reuse"]) + 1

    summary["unique_editorial"] = len(agg)

    scored: List[Dict[str, Any]] = []
    for key, row in agg.items():
        bname = str(row["basename"])
        resolved = _resolve_workflow_video_path(str(row["video"]), output_root=out, data_root=data)
        if resolved is None:
            summary["unresolved_source"] += 1
            if len(summary["unresolved_samples"]) < 20:
                summary["unresolved_samples"].append(
                    {
                        "basename": bname,
                        "video": row["video"],
                        "skip": row["skip"],
                        "cap": row["cap"],
                        "wf_reuse": row["wf_reuse"],
                    }
                )
            continue
        score = _score_workflow_window(
            wf_reuse=int(row["wf_reuse"]),
            as_job_source=int(as_source.get(bname, 0)),
            src_workflow_count=len(src_wfs.get(bname, ())),
            basename=bname,
        )
        scored.append(
            {
                **row,
                "resolved_path": str(resolved),
                "as_job_source": int(as_source.get(bname, 0)),
                "src_workflow_count": len(src_wfs.get(bname, ())),
                "score": round(score, 2),
            }
        )

    scored.sort(key=lambda r: (-float(r["score"]), -int(r["wf_reuse"]), str(r["basename"])))
    limit = max(0, int(top))
    chosen = scored[:limit] if limit else scored
    summary["candidates"] = [
        {
            "basename": c["basename"],
            "skip": c["skip"],
            "cap": c["cap"],
            "wf_reuse": c["wf_reuse"],
            "as_job_source": c["as_job_source"],
            "score": c["score"],
            "resolved_path": c["resolved_path"],
            "sample_workflow": c["sample_workflow"],
        }
        for c in chosen
    ]
    summary["candidates_total_resolvable"] = len(scored)
    summary["candidates_selected"] = len(chosen)

    if not apply:
        return summary

    con = connect_clips(reg)
    parents_defaulted: set[str] = set()
    try:
        for c in chosen:
            media = Path(str(c["resolved_path"]))
            if not media.is_file():
                summary["unresolved_source"] += 1
                continue
            try:
                try:
                    rel = str(media.resolve().relative_to(out)).replace("\\", "/")
                except ValueError:
                    try:
                        rel = str(media.resolve().relative_to(data)).replace("\\", "/")
                    except ValueError:
                        rel = media.name
                meta = _probe_media_frame_meta(media) or {}
                fps = float(meta.get("fps") or 18.0)
                if fps <= 0:
                    fps = 18.0
                duration = float(meta.get("duration") or 0.0)
                frame_count = int(meta.get("frame_count") or 0)
                if duration <= 0 and frame_count > 0:
                    duration = frame_count / fps
                skip = int(c["skip"])
                cap = int(c["cap"])
                mark_in = skip / fps
                if cap > 0:
                    mark_out = mark_in + (cap / fps)
                else:
                    mark_out = duration if duration > mark_in else mark_in + max(1.0 / fps, 0.05)
                if mark_out <= mark_in + MIN_CLIP_GAP_S:
                    summary["clips_skipped_dup"] += 1
                    continue

                existing = areg.by_relpath(con, rel)
                if existing and existing.get("content_id"):
                    parent = str(existing["content_id"])
                else:
                    parent = areg.register(con, media, relpath=rel, kind="video", with_dims=False)
                if not parent:
                    summary["errors"].append({"path": str(media), "detail": "register_failed"})
                    summary["ok"] = False
                    continue

                # Near-duplicate skip (50ms), same as trims import.
                existing_clips = list_clips_for_parent(con, parent)
                if any(
                    abs(float(e["mark_in_s"]) - mark_in) < 0.05
                    and abs(float(e["mark_out_s"]) - mark_out) < 0.05
                    for e in existing_clips
                ):
                    summary["clips_skipped_dup"] += 1
                    continue

                label = f"wf skip{skip}" + (f" cap{cap}" if cap > 0 else "")
                notes = (
                    f"workflow_import score={c['score']} reuse={c['wf_reuse']} "
                    f"job_src={c['as_job_source']} sample={c['sample_workflow']}"
                )
                clip = create_clip(
                    con,
                    parent_content_id=parent,
                    mark_in_s=mark_in,
                    mark_out_s=mark_out,
                    label=label,
                    origin="workflow_import",
                    notes=notes,
                    duration_s=duration or None,
                )
                summary["clips_created"] += 1

                if (
                    set_default
                    and parent not in parents_defaulted
                    and get_default_clip_id(con, parent) is None
                    and int(c["cap"]) > 0
                ):
                    set_default_clip(con, parent, clip["clip_id"])
                    parents_defaulted.add(parent)
                    summary["defaults_set"] += 1
            except Exception as exc:  # noqa: BLE001
                summary["errors"].append({"path": str(c.get("resolved_path")), "detail": str(exc)})
                summary["ok"] = False
    finally:
        con.close()
    return summary


def iter_vhs_windows_from_png_embed(png_path: Path) -> List[Dict[str, Any]]:
    """
    Yield editorial-candidate VHS windows from a companion PNG's embedded
    API prompt and/or UI workflow. Asset-centric: video path is the parent reel.
    """
    from comfy_meta_lib import extract_prompt_workflow_from_png_chunks, read_png_text_chunks

    out: List[Dict[str, Any]] = []
    seen: set[Tuple[str, int, int]] = set()
    try:
        chunks = read_png_text_chunks(Path(png_path))
        prompt, workflow = extract_prompt_workflow_from_png_chunks(chunks)
    except Exception:
        return out

    def add(video: str, skip: int, cap: int, via: str) -> None:
        video = str(video or "").strip()
        if not video:
            return
        if skip <= 0 and cap <= 0:
            return
        bname = Path(video.replace("\\", "/")).name
        key = (bname, int(skip), int(cap))
        if key in seen:
            return
        seen.add(key)
        out.append(
            {
                "basename": bname,
                "video": video,
                "skip": int(skip),
                "cap": int(cap),
                "via": via,
                "png": str(png_path),
            }
        )

    if isinstance(prompt, dict):
        for node in prompt.values():
            if not isinstance(node, dict):
                continue
            ct = str(node.get("class_type") or "")
            if "VHS_LoadVideo" not in ct:
                continue
            inp = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
            try:
                skip = int(inp.get("skip_first_frames") or 0)
            except (TypeError, ValueError):
                skip = 0
            try:
                cap = int(inp.get("frame_load_cap") or 0)
            except (TypeError, ValueError):
                cap = 0
            add(str(inp.get("video") or ""), skip, cap, "api_prompt")

    if isinstance(workflow, dict) and isinstance(workflow.get("nodes"), list):
        for node in workflow["nodes"]:
            if not isinstance(node, dict):
                continue
            ntype = str(node.get("type") or "")
            if "VHS_LoadVideo" not in ntype:
                continue
            parsed = _vhs_window_from_widgets(node.get("widgets_values"))
            if not parsed:
                continue
            video, skip, cap = parsed
            add(video, skip, cap, "ui_workflow")

    return out


def backfill_clips_from_companion_pngs(
    *,
    output_root: Path,
    data_root: Optional[Path] = None,
    jobs_root: Optional[Path] = None,
    registry_path: Optional[Path] = None,
    apply: bool = False,
    top: int = 150,
    include_template_skips: bool = False,
    set_default: bool = True,
    max_pngs: int = 0,
) -> Dict[str, Any]:
    """
    Farm Clip bookmarks from VHS windows embedded in companion PNGs (og outputs).

    The PNG is only the discovery surface — the Clip is stored on the resolved
    source video asset. Skips bare template skips unless ``include_template_skips``.
    """
    from collections import Counter, defaultdict

    from shape_factory_queue import _probe_media_frame_meta

    out = Path(output_root).expanduser().resolve()
    data = Path(data_root).expanduser().resolve() if data_root else out.parent
    jobs = Path(jobs_root).expanduser().resolve() if jobs_root else None
    og = out / "og" if (out / "og").is_dir() else out
    reg = Path(registry_path).expanduser().resolve() if registry_path else areg.default_registry_path(og)

    summary: Dict[str, Any] = {
        "ok": True,
        "apply": bool(apply),
        "output_root": str(out),
        "scan_root": str(og),
        "registry": str(reg),
        "top": int(top),
        "include_template_skips": bool(include_template_skips),
        "pngs_scanned": 0,
        "pngs_with_embed": 0,
        "window_instances": 0,
        "editorial_instances": 0,
        "unique_editorial": 0,
        "unresolved_source": 0,
        "clips_created": 0,
        "clips_skipped_dup": 0,
        "defaults_set": 0,
        "candidates": [],
        "unresolved_samples": [],
        "errors": [],
    }

    as_source: Counter[str] = Counter()
    if jobs and jobs.is_dir():
        for jp in jobs.rglob("*.job.json"):
            try:
                job = json.loads(jp.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            binds = job.get("bindings") if isinstance(job, dict) and isinstance(job.get("bindings"), dict) else {}
            for slot, spec in binds.items():
                if "video" not in str(slot).lower():
                    continue
                path = ""
                if isinstance(spec, dict):
                    path = str(spec.get("path") or spec.get("relpath") or "")
                elif isinstance(spec, str):
                    path = spec
                name = Path(path.replace("\\", "/")).name
                if name:
                    as_source[name] += 1

    agg: Dict[Tuple[str, int, int], Dict[str, Any]] = {}
    png_hits: Dict[Tuple[str, int, int], set[str]] = defaultdict(set)

    png_iter = sorted(og.rglob("*.png"))
    if max_pngs and max_pngs > 0:
        png_iter = png_iter[: int(max_pngs)]

    for png in png_iter:
        summary["pngs_scanned"] += 1
        windows = iter_vhs_windows_from_png_embed(png)
        if not windows:
            continue
        summary["pngs_with_embed"] += 1
        for w in windows:
            summary["window_instances"] += 1
            skip = int(w["skip"])
            cap = int(w["cap"])
            if not is_editorial_vhs_window(skip, cap, include_template_skips=include_template_skips):
                continue
            summary["editorial_instances"] += 1
            key = (str(w["basename"]), skip, cap)
            png_hits[key].add(Path(w["png"]).name)
            row = agg.get(key)
            if row is None:
                agg[key] = {
                    "basename": w["basename"],
                    "video": w["video"],
                    "skip": skip,
                    "cap": cap,
                    "png_reuse": 1,
                    "sample_png": Path(w["png"]).name,
                    "via": w.get("via"),
                }
            else:
                row["png_reuse"] = int(row["png_reuse"]) + 1

    summary["unique_editorial"] = len(agg)

    scored: List[Dict[str, Any]] = []
    for key, row in agg.items():
        bname = str(row["basename"])
        resolved = _resolve_workflow_video_path(str(row["video"]), output_root=out, data_root=data)
        if resolved is None:
            summary["unresolved_source"] += 1
            if len(summary["unresolved_samples"]) < 20:
                summary["unresolved_samples"].append(
                    {
                        "basename": bname,
                        "video": row["video"],
                        "skip": row["skip"],
                        "cap": row["cap"],
                        "png_reuse": row["png_reuse"],
                    }
                )
            continue
        # Score: how many outputs' companion PNGs reused this window + factory gravity.
        score = float(row["png_reuse"]) * 3.0
        score += float(len(png_hits.get(key, ()))) * 0.25
        score += float(as_source.get(bname, 0)) * 2.0
        if "Kneel" in bname or bname.startswith("X-"):
            score += 1.5
        scored.append(
            {
                **row,
                "resolved_path": str(resolved),
                "as_job_source": int(as_source.get(bname, 0)),
                "distinct_pngs": len(png_hits.get(key, ())),
                "score": round(score, 2),
            }
        )

    scored.sort(key=lambda r: (-float(r["score"]), -int(r["png_reuse"]), str(r["basename"])))
    limit = max(0, int(top))
    chosen = scored[:limit] if limit else scored
    summary["candidates"] = [
        {
            "basename": c["basename"],
            "skip": c["skip"],
            "cap": c["cap"],
            "png_reuse": c["png_reuse"],
            "as_job_source": c["as_job_source"],
            "score": c["score"],
            "resolved_path": c["resolved_path"],
            "sample_png": c["sample_png"],
        }
        for c in chosen
    ]
    summary["candidates_total_resolvable"] = len(scored)
    summary["candidates_selected"] = len(chosen)

    if not apply:
        return summary

    con = connect_clips(reg)
    parents_defaulted: set[str] = set()
    meta_cache: Dict[str, Dict[str, Any]] = {}
    try:
        for c in chosen:
            media = Path(str(c["resolved_path"]))
            if not media.is_file():
                summary["unresolved_source"] += 1
                continue
            try:
                try:
                    rel = str(media.resolve().relative_to(out)).replace("\\", "/")
                except ValueError:
                    try:
                        rel = str(media.resolve().relative_to(data)).replace("\\", "/")
                    except ValueError:
                        rel = media.name
                cache_key = str(media.resolve())
                if cache_key not in meta_cache:
                    meta_cache[cache_key] = _probe_media_frame_meta(media) or {}
                meta = meta_cache[cache_key]
                fps = float(meta.get("fps") or 18.0)
                if fps <= 0:
                    fps = 18.0
                duration = float(meta.get("duration") or 0.0)
                frame_count = int(meta.get("frame_count") or 0)
                if duration <= 0 and frame_count > 0:
                    duration = frame_count / fps
                skip = int(c["skip"])
                cap = int(c["cap"])
                mark_in = skip / fps
                if cap > 0:
                    mark_out = mark_in + (cap / fps)
                else:
                    mark_out = duration if duration > mark_in else mark_in + max(1.0 / fps, 0.05)
                if mark_out <= mark_in + MIN_CLIP_GAP_S:
                    summary["clips_skipped_dup"] += 1
                    continue

                existing = areg.by_relpath(con, rel)
                if existing and existing.get("content_id"):
                    parent = str(existing["content_id"])
                else:
                    parent = areg.register(con, media, relpath=rel, kind="video", with_dims=False)
                if not parent:
                    summary["errors"].append({"path": str(media), "detail": "register_failed"})
                    summary["ok"] = False
                    continue

                existing_clips = list_clips_for_parent(con, parent)
                if any(
                    abs(float(e["mark_in_s"]) - mark_in) < 0.05
                    and abs(float(e["mark_out_s"]) - mark_out) < 0.05
                    for e in existing_clips
                ):
                    summary["clips_skipped_dup"] += 1
                    continue

                label = f"png skip{skip}" + (f" cap{cap}" if cap > 0 else "")
                notes = (
                    f"png_embed_import score={c['score']} png_reuse={c['png_reuse']} "
                    f"job_src={c['as_job_source']} sample={c['sample_png']}"
                )
                clip = create_clip(
                    con,
                    parent_content_id=parent,
                    mark_in_s=mark_in,
                    mark_out_s=mark_out,
                    label=label,
                    origin="png_embed_import",
                    notes=notes,
                    duration_s=duration or None,
                )
                summary["clips_created"] += 1

                if (
                    set_default
                    and parent not in parents_defaulted
                    and get_default_clip_id(con, parent) is None
                    and int(c["cap"]) > 0
                ):
                    set_default_clip(con, parent, clip["clip_id"])
                    parents_defaulted.add(parent)
                    summary["defaults_set"] += 1
            except Exception as exc:  # noqa: BLE001
                summary["errors"].append({"path": str(c.get("resolved_path")), "detail": str(exc)})
                summary["ok"] = False
    finally:
        con.close()
    return summary
