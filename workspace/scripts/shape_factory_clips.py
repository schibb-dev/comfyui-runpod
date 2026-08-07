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
