#!/usr/bin/env python3
"""
Persisted output → job construction index (rebuildable).

Canonical jobs stay as ``.job.json``; this SQLite under ``output/_status/`` is for
fast UI joins (rate scrubber bands, replay resolve). See docs/SCALE_INDEX_ARCHITECTURE.md.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

JOB_OUTPUT_INDEX_BASENAME = "job_output_index.sqlite"
JOB_OUTPUT_INDEX_SCHEMA_VERSION = 1

_OUTPUT_SUFFIX_RE = re.compile(r"(?i)_(?:FINAL|PREVIEW)_\d+$")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_job_output_index_path(og_root: Path) -> Path:
    return Path(og_root).expanduser().resolve().parent / "_status" / JOB_OUTPUT_INDEX_BASENAME


def normalize_output_relpath(raw: str, *, output_root: Optional[Path] = None) -> str:
    s = str(raw or "").replace("\\", "/").strip()
    if not s:
        return ""
    if output_root is not None:
        try:
            abs_p = Path(s).expanduser()
            if abs_p.is_absolute():
                root = Path(output_root).expanduser().resolve()
                try:
                    s = str(abs_p.resolve().relative_to(root)).replace("\\", "/")
                except ValueError:
                    pass
        except OSError:
            pass
    s = s.lstrip("/")
    if s.startswith("output/"):
        s = s[len("output/") :]
    return s


def job_key_guess_from_output_basename(name: str) -> str:
    stem = Path(str(name or "").replace("\\", "/")).stem
    if not stem:
        return ""
    return _OUTPUT_SUFFIX_RE.sub("", stem)


def _int(v: Any) -> Optional[int]:
    try:
        if v is None or v == "":
            return None
        n = int(v)
        return n if n >= 0 else None
    except (TypeError, ValueError):
        return None


def _float(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def construction_summary_from_job(job: Dict[str, Any]) -> Dict[str, Any]:
    construction = job.get("construction") if isinstance(job.get("construction"), dict) else {}
    timings = job.get("timings") if isinstance(job.get("timings"), dict) else {}
    workload = timings.get("workload") if isinstance(timings.get("workload"), dict) else {}

    frames_before = _int(construction.get("frames_before"))
    generation = _int(workload.get("frames"))
    if generation is None:
        generation = _int(construction.get("frames_after"))
    output_fc = _int(workload.get("output_frame_count"))
    overlap = _int(workload.get("overlap"))
    if overlap is None:
        overlap = _int(construction.get("overlap"))
    fps = None
    for raw in (workload.get("fps"), workload.get("force_rate"), construction.get("fps")):
        fps = _float(raw)
        if fps is not None:
            break

    parent = str(job.get("parent_output") or construction.get("parent_output") or "").strip() or None
    return {
        "pick_mode": str(job.get("pick_mode") or construction.get("pick_mode") or "").strip() or None,
        "family_slug": str(job.get("family_slug") or "").strip() or None,
        "frames_before": frames_before,
        "generation_frames": generation,
        "output_frame_count": output_fc,
        "overlap": overlap,
        "fps": fps,
        "parent_output": parent,
        "graph_hash": str(job.get("graph_hash") or construction.get("graph_hash") or "").strip() or None,
    }


def extension_range_from_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    if (
        row.get("frames_before") is None
        and row.get("generation_frames") is None
        and row.get("output_frame_count") is None
    ):
        return None
    return {
        "job_key": row.get("job_key"),
        "pick_mode": row.get("pick_mode"),
        "frames_before": row.get("frames_before"),
        "generation_frames": row.get("generation_frames"),
        "output_frame_count": row.get("output_frame_count"),
        "overlap": row.get("overlap"),
        "fps": row.get("fps"),
    }


def open_job_output_index(path: Path) -> sqlite3.Connection:
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS job_output (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_key TEXT NOT NULL,
            output_relpath TEXT NOT NULL,
            output_basename TEXT NOT NULL,
            content_id TEXT,
            family_slug TEXT,
            pick_mode TEXT,
            frames_before INTEGER,
            generation_frames INTEGER,
            output_frame_count INTEGER,
            overlap INTEGER,
            fps REAL,
            parent_output TEXT,
            graph_hash TEXT,
            job_path TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(job_key, output_relpath)
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_jo_relpath ON job_output(output_relpath)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_jo_basename ON job_output(output_basename)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_jo_content ON job_output(content_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_jo_job_key ON job_output(job_key)")
    con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
        (str(JOB_OUTPUT_INDEX_SCHEMA_VERSION),),
    )
    con.commit()
    return con


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def _video_paths_from_job(job: Dict[str, Any]) -> List[str]:
    paths: List[str] = []
    submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    for src in (submit.get("outputs"),):
        if isinstance(src, list):
            paths.extend(str(p) for p in src if str(p).lower().endswith(".mp4"))
    deposit = job.get("deposit") if isinstance(job.get("deposit"), dict) else {}
    vids = deposit.get("videos")
    if isinstance(vids, list):
        paths.extend(str(p) for p in vids if str(p).lower().endswith(".mp4"))
    # Dedupe preserve order
    seen = set()
    out: List[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def upsert_from_job(
    con: sqlite3.Connection,
    job: Dict[str, Any],
    *,
    job_path: Optional[Path] = None,
    output_root: Optional[Path] = None,
    content_ids: Optional[Dict[str, str]] = None,
    commit: bool = True,
) -> int:
    """Upsert one row per mp4 output. Returns number of rows written."""
    if not isinstance(job, dict):
        return 0
    job_key = str(job.get("job_key") or "").strip()
    if not job_key and job_path is not None:
        job_key = job_path.stem.replace(".job", "") if job_path.name.endswith(".job.json") else job_path.stem
        if job_key.endswith(".job"):
            job_key = job_key[: -len(".job")]
    if not job_key:
        return 0

    summary = construction_summary_from_job(job)
    videos = _video_paths_from_job(job)
    if not videos:
        return 0

    now = utc_now()
    n = 0
    for raw in videos:
        rel = normalize_output_relpath(raw, output_root=output_root)
        if not rel:
            continue
        basename = Path(rel).name
        cid = None
        if content_ids:
            cid = content_ids.get(rel) or content_ids.get(basename) or content_ids.get(raw)
        con.execute(
            """
            INSERT INTO job_output (
                job_key, output_relpath, output_basename, content_id,
                family_slug, pick_mode, frames_before, generation_frames,
                output_frame_count, overlap, fps, parent_output, graph_hash,
                job_path, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_key, output_relpath) DO UPDATE SET
                output_basename=excluded.output_basename,
                content_id=COALESCE(excluded.content_id, job_output.content_id),
                family_slug=excluded.family_slug,
                pick_mode=excluded.pick_mode,
                frames_before=excluded.frames_before,
                generation_frames=excluded.generation_frames,
                output_frame_count=excluded.output_frame_count,
                overlap=excluded.overlap,
                fps=excluded.fps,
                parent_output=excluded.parent_output,
                graph_hash=excluded.graph_hash,
                job_path=excluded.job_path,
                updated_at=excluded.updated_at
            """,
            (
                job_key,
                rel,
                basename,
                cid,
                summary.get("family_slug"),
                summary.get("pick_mode"),
                summary.get("frames_before"),
                summary.get("generation_frames"),
                summary.get("output_frame_count"),
                summary.get("overlap"),
                summary.get("fps"),
                summary.get("parent_output"),
                summary.get("graph_hash"),
                str(job_path) if job_path else None,
                now,
            ),
        )
        n += 1
    if commit:
        con.commit()
    return n


def lookup_by_relpath(
    con: sqlite3.Connection,
    relpath: str,
    *,
    output_root: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    rel = normalize_output_relpath(relpath, output_root=output_root)
    if not rel:
        return None
    row = con.execute(
        "SELECT * FROM job_output WHERE output_relpath = ? ORDER BY updated_at DESC LIMIT 1",
        (rel,),
    ).fetchone()
    if row:
        return _row_to_dict(row)
    bn = Path(rel).name
    if bn:
        row = con.execute(
            "SELECT * FROM job_output WHERE output_basename = ? ORDER BY updated_at DESC LIMIT 1",
            (bn,),
        ).fetchone()
        if row:
            return _row_to_dict(row)
    guess = job_key_guess_from_output_basename(bn)
    if guess:
        row = con.execute(
            "SELECT * FROM job_output WHERE job_key = ? ORDER BY updated_at DESC LIMIT 1",
            (guess,),
        ).fetchone()
        if row:
            return _row_to_dict(row)
    return None


def lookup_extension_range(
    con: sqlite3.Connection,
    relpath: str,
    *,
    output_root: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    row = lookup_by_relpath(con, relpath, output_root=output_root)
    return extension_range_from_row(row) if row else None


def rebuild_job_output_index(
    *,
    index_path: Path,
    jobs_root: Path,
    output_root: Optional[Path] = None,
) -> Dict[str, Any]:
    jobs_root = Path(jobs_root).expanduser().resolve()
    index_path = Path(index_path).expanduser().resolve()
    if index_path.is_file():
        index_path.unlink()
    con = open_job_output_index(index_path)
    scanned = 0
    upserted = 0
    try:
        for job_path in sorted(jobs_root.rglob("*.job.json")):
            scanned += 1
            try:
                job = json.loads(job_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(job, dict):
                continue
            upserted += upsert_from_job(
                con,
                job,
                job_path=job_path,
                output_root=output_root,
                commit=False,
            )
        con.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('rebuilt_at', ?)",
            (utc_now(),),
        )
        con.commit()
    finally:
        con.close()
    return {
        "ok": True,
        "index_path": str(index_path),
        "jobs_scanned": scanned,
        "rows_upserted": upserted,
    }


def upsert_job_file(
    job_path: Path,
    *,
    index_path: Path,
    output_root: Optional[Path] = None,
) -> int:
    job_path = Path(job_path).expanduser().resolve()
    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(job, dict):
        return 0
    con = open_job_output_index(index_path)
    try:
        return upsert_from_job(con, job, job_path=job_path, output_root=output_root, commit=True)
    finally:
        con.close()


def add_job_output_index_subparser(sub: Any) -> None:
    p = sub.add_parser(
        "job-output-index",
        help="Rebuild/query persisted output→job construction index",
    )
    sp = p.add_subparsers(dest="job_output_index_cmd", required=True)

    reb = sp.add_parser("rebuild", help="Scan all .job.json into job_output_index.sqlite")
    reb.add_argument("--data-root", default=None, help="Shape-factory data root (jobs under shape_factory/jobs)")
    reb.add_argument("--og-root", default=None, help="og library root (index defaults to ../_status/)")
    reb.add_argument("--output-root", default=None, help="Output bind root for normalizing abs paths")
    reb.add_argument("--index", default=None, help="Explicit sqlite path")
    reb.set_defaults(func=cmd_job_output_index_rebuild)

    look = sp.add_parser("lookup", help="Lookup one output relpath")
    look.add_argument("relpath")
    look.add_argument("--og-root", default=None)
    look.add_argument("--index", default=None)
    look.add_argument("--output-root", default=None)
    look.set_defaults(func=cmd_job_output_index_lookup)


def _resolve_paths(args: argparse.Namespace) -> Tuple[Path, Path, Optional[Path]]:
    from shape_factory_map import resolve_shape_factory_data_root

    repo = Path(__file__).resolve().parents[2]
    data_root = Path(args.data_root).expanduser().resolve() if getattr(args, "data_root", None) else resolve_shape_factory_data_root(repo_root=repo)
    og = Path(args.og_root).expanduser().resolve() if getattr(args, "og_root", None) else None
    if og is None:
        # Prefer sibling of common output binds
        for cand in (
            Path("/home/yuji/comfyui-runpod-data/output/og"),
            repo / "workspace" / "output" / "og",
        ):
            if cand.is_dir():
                og = cand
                break
    if og is None:
        og = data_root / "output" / "og"
    out_root = Path(args.output_root).expanduser().resolve() if getattr(args, "output_root", None) else (og.parent if og else None)
    return data_root, og, out_root


def cmd_job_output_index_rebuild(args: argparse.Namespace) -> int:
    data_root, og, out_root = _resolve_paths(args)
    index = Path(args.index).expanduser().resolve() if args.index else default_job_output_index_path(og)
    jobs_root = data_root / "shape_factory" / "jobs"
    if not jobs_root.is_dir():
        print(f"error: jobs root missing: {jobs_root}", flush=True)
        return 1
    result = rebuild_job_output_index(index_path=index, jobs_root=jobs_root, output_root=out_root)
    print(json.dumps(result, indent=2))
    return 0


def cmd_job_output_index_lookup(args: argparse.Namespace) -> int:
    _data_root, og, out_root = _resolve_paths(args)
    index = Path(args.index).expanduser().resolve() if args.index else default_job_output_index_path(og)
    if not index.is_file():
        print(json.dumps({"ok": False, "error": "index_missing", "path": str(index)}))
        return 1
    con = open_job_output_index(index)
    try:
        row = lookup_by_relpath(con, args.relpath, output_root=out_root)
    finally:
        con.close()
    print(json.dumps({"ok": True, "row": row, "extension_range": extension_range_from_row(row) if row else None}, indent=2))
    return 0
