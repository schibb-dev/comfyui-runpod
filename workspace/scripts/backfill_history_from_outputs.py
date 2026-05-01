#!/usr/bin/env python3
"""
Backfill per-run history.json from filesystem outputs when ComfyUI /history is missing.

Why:
- ComfyUI writes output media (mp4/png/...) to disk, but history.json is normally
  collected later by polling GET /history/<prompt_id>.
- If ComfyUI is restarted (or history is otherwise unavailable), runs can have
  outputs but no history.json.

This script writes a minimal "dummy" history.json that the Experiments UI can
consume, based on media files found under the experiment folder.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


MEDIA_EXTS = {".mp4", ".png", ".webp", ".jpg", ".jpeg"}


def _utc_iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _read_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def _read_json_dict(p: Path) -> Dict[str, Any]:
    try:
        obj = _read_json(p)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _write_json(p: Path, obj: Any, *, indent: int = 2) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=indent, ensure_ascii=False), encoding="utf-8")


def _resolve_workspace_root(base: Path) -> Path:
    """
    Match experiments_ui_server.py workspace root resolution.
    """
    base = base.resolve()
    if (base / "output").exists() and (base / "experiments_ui").exists() and (base / "scripts").exists():
        return base
    if (base / "workspace" / "output").exists() and (base / "workspace" / "experiments_ui").exists() and (base / "workspace" / "scripts").exists():
        return (base / "workspace").resolve()
    return base


def _iter_experiment_dirs(experiments_root: Path) -> List[Path]:
    if not experiments_root.is_dir():
        return []
    out: List[Path] = []
    for child in experiments_root.iterdir():
        if child.is_dir() and (child / "manifest.json").exists():
            out.append(child)
    out.sort(key=lambda p: p.name)
    return out


def _iter_run_dirs(exp_dir: Path) -> List[Path]:
    runs = exp_dir / "runs"
    if not runs.is_dir():
        return []
    out = [p for p in runs.iterdir() if p.is_dir() and p.name.startswith("run_") and (p / "prompt.json").exists()]
    out.sort(key=lambda p: p.name)
    return out


def _prompt_id_from_submit(submit_path: Path) -> Optional[str]:
    obj = _read_json_dict(submit_path) if submit_path.exists() else {}
    pid = obj.get("prompt_id")
    return pid.strip() if isinstance(pid, str) and pid.strip() else None


def _queue_prompt_ids(server: str) -> Tuple[set[str], set[str]]:
    server = server.rstrip("/")
    raw = urllib.request.urlopen(f"{server}/queue", timeout=10).read().decode("utf-8", "replace")
    q = json.loads(raw)
    pending: set[str] = set()
    running: set[str] = set()
    if isinstance(q, dict):
        for key, out in (("queue_pending", pending), ("queue_running", running)):
            arr = q.get(key)
            if not isinstance(arr, list):
                continue
            for it in arr:
                if isinstance(it, list) and len(it) >= 2 and isinstance(it[1], str) and it[1].strip():
                    out.add(it[1].strip())
    return pending, running


def _history_by_prompt_id(server: str, prompt_id: str) -> Optional[Dict[str, Any]]:
    """
    Return history dict if available; otherwise None.
    """
    server = server.rstrip("/")
    try:
        raw = urllib.request.urlopen(f"{server}/history/{prompt_id}", timeout=10).read().decode("utf-8", "replace")
        obj = json.loads(raw)
    except Exception:
        return None
    if isinstance(obj, dict) and obj:
        return obj
    return None


def _index_media_for_experiment(exp_dir: Path) -> Dict[str, List[Path]]:
    """
    Returns run_id -> list of media paths for files named like:
      run_###_*.{mp4,png,webp,jpg,jpeg}
    """
    out: Dict[str, List[Path]] = {}
    try:
        for p in exp_dir.rglob("*"):
            try:
                if not p.is_file():
                    continue
            except Exception:
                continue
            if p.suffix.lower() not in MEDIA_EXTS:
                continue
            name = p.name
            if not name.startswith("run_"):
                continue
            # run_id is prefix up to first "_"
            i = name.find("_", len("run_"))
            if i <= 0:
                continue
            run_id = name[:i]
            out.setdefault(run_id, []).append(p)
    except Exception:
        return out

    for k in list(out.keys()):
        out[k].sort(key=lambda p: (p.suffix.lower(), p.name))
    return out


def _rel_subfolder_and_filename(*, output_root: Path, path: Path) -> Optional[Tuple[str, str]]:
    try:
        rel = path.resolve().relative_to(output_root.resolve())
    except Exception:
        return None
    rel_posix = str(rel).replace("\\", "/")
    parent = str(rel.parent).replace("\\", "/")
    return (parent if parent != "." else "", path.name)


def _dummy_history_record(
    *,
    output_root: Path,
    media_paths: Sequence[Path],
    prompt_id: Optional[str],
    exp_id: str,
    run_id: str,
    embed_prompt_json: Optional[Dict[str, Any]] = None,
    embed_workflow_json: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    for p in media_paths:
        r = _rel_subfolder_and_filename(output_root=output_root, path=p)
        if r is None:
            continue
        subfolder, filename = r
        items.append(
            {
                "filename": filename,
                "subfolder": subfolder,
                "type": "output",
                "format": None,
                "frame_rate": None,
                "workflow": None,
                "fullpath": str(p),
            }
        )

    record: Dict[str, Any] = {
        # Mimic ComfyUI history record fields the UI cares about.
        "outputs": {
            # A synthetic node bucket.
            "fs": {
                "fs": items,
            }
        },
        "status": {
            # Use success so we don't mark this as a failed run.
            "status_str": "success",
            "completed": True,
            "messages": [
                {
                    "type": "backfilled",
                    "message": "history.json backfilled from filesystem outputs (ComfyUI /history unavailable)",
                }
            ],
        },
        "backfill": {
            "schema": 1,
            "at": _utc_iso(time.time()),
            "exp_id": exp_id,
            "run_id": run_id,
            "prompt_id": prompt_id,
            "media_count": len(items),
        },
    }

    if isinstance(embed_prompt_json, dict) and embed_prompt_json:
        record["prompt"] = embed_prompt_json
    if isinstance(embed_workflow_json, dict) and embed_workflow_json:
        record["workflow"] = embed_workflow_json

    return record


def _maybe_extract_embedded_comfy_metadata(media_paths: Sequence[Path]) -> Tuple[Optional[Any], Optional[Any]]:
    """
    Best-effort extract (prompt, workflow) from:
    - PNG text chunks, if present
    - MP4 container tags, if present
    """
    try:
        from comfy_meta_lib import (
            extract_prompt_workflow_from_png_chunks,
            extract_prompt_workflow_from_tags,
            ffprobe_format_tags,
            read_png_text_chunks,
        )
    except Exception:
        return None, None

    # Prefer PNG: usually exact resolved prompt/workflow.
    for p in media_paths:
        if p.suffix.lower() != ".png":
            continue
        try:
            chunks = read_png_text_chunks(p)
            prompt_obj, workflow_obj = extract_prompt_workflow_from_png_chunks(chunks)
            if prompt_obj is not None or workflow_obj is not None:
                return prompt_obj, workflow_obj
        except Exception:
            continue

    # Fallback: container tags (mp4/mov/mkv/webm)
    for p in media_paths:
        if p.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm"}:
            continue
        try:
            tags = ffprobe_format_tags(p)
            prompt_obj, workflow_obj = extract_prompt_workflow_from_tags(tags)
            if prompt_obj is not None or workflow_obj is not None:
                return prompt_obj, workflow_obj
        except Exception:
            continue

    return None, None


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill run history.json from filesystem outputs.")
    ap.add_argument("--experiments-root", default="", help="Experiments root (default: workspace/output/output/experiments)")
    ap.add_argument("--server", default="http://127.0.0.1:8188", help="ComfyUI server URL (used to avoid backfilling active runs)")
    ap.add_argument("--limit-experiments", type=int, default=0, help="Only process first N experiments (0=all)")
    ap.add_argument("--newest-first", action="store_true", help="Process newest experiment folders first (by name desc)")
    ap.add_argument("--min-age-seconds", type=float, default=300.0, help="Only backfill runs whose submit.json is older than this (default: 300s)")
    ap.add_argument("--dry-run", action="store_true", help="Scan and report but do not write history.json")
    ap.add_argument("--indent", type=int, default=2)
    ap.add_argument("--try-fetch-history", action="store_true", help="If /history/<prompt_id> is available, write real history instead of dummy")
    ap.add_argument("--extract-media-metadata", action="store_true", help="Try extracting prompt/workflow from PNG/MP4 metadata and include in dummy history")
    args = ap.parse_args()

    here = Path(__file__).resolve()
    # .../<repo>/workspace/scripts/backfill_history_from_outputs.py -> repo root is parents[2]
    repo_root = here.parents[2]
    workspace_root = _resolve_workspace_root(repo_root / "workspace")
    output_root = (workspace_root / "output").resolve()
    exp_root = Path(args.experiments_root) if args.experiments_root else (workspace_root / "output" / "output" / "experiments")
    exp_root = exp_root.resolve()

    if not exp_root.is_dir():
        raise SystemExit(f"experiments root not found: {exp_root}")

    try:
        q_pending, q_running = _queue_prompt_ids(str(args.server))
    except Exception:
        q_pending, q_running = set(), set()

    exp_dirs = _iter_experiment_dirs(exp_root)
    if args.newest_first:
        exp_dirs.sort(key=lambda p: p.name, reverse=True)
    if args.limit_experiments and args.limit_experiments > 0:
        exp_dirs = exp_dirs[: int(args.limit_experiments)]

    now = time.time()
    scanned = 0
    eligible = 0
    written = 0
    wrote_real = 0

    for exp_dir in exp_dirs:
        mf = _read_json_dict(exp_dir / "manifest.json")
        exp_id = mf.get("exp_id") if isinstance(mf.get("exp_id"), str) and mf["exp_id"].strip() else exp_dir.name

        media_index = _index_media_for_experiment(exp_dir)
        if not media_index:
            continue

        for run_dir in _iter_run_dirs(exp_dir):
            scanned += 1
            run_id = run_dir.name
            hist_path = run_dir / "history.json"
            if hist_path.exists():
                continue

            media_paths = media_index.get(run_id, [])
            if not media_paths:
                continue

            submit_path = run_dir / "submit.json"
            pid = _prompt_id_from_submit(submit_path)

            # Don't touch active queue items.
            if pid and (pid in q_pending or pid in q_running):
                continue

            # Age gate: if we have submit.json, require it to be older than min_age_seconds.
            if submit_path.exists():
                try:
                    age = now - float(submit_path.stat().st_mtime)
                except Exception:
                    age = float("inf")
                if age < float(args.min_age_seconds):
                    continue

            eligible += 1

            if args.dry_run:
                continue

            # Try to fetch real history if requested and possible.
            if args.try_fetch_history and pid:
                hist = _history_by_prompt_id(str(args.server), pid)
                if isinstance(hist, dict) and hist:
                    _write_json(hist_path, hist, indent=int(args.indent))
                    written += 1
                    wrote_real += 1
                    continue

            embed_prompt = None
            embed_workflow = None
            if args.extract_media_metadata:
                embed_prompt, embed_workflow = _maybe_extract_embedded_comfy_metadata(media_paths)

            key = pid or run_id
            dummy = {key: _dummy_history_record(output_root=output_root, media_paths=media_paths, prompt_id=pid, exp_id=exp_id, run_id=run_id, embed_prompt_json=embed_prompt if isinstance(embed_prompt, dict) else None, embed_workflow_json=embed_workflow if isinstance(embed_workflow, dict) else None)}
            _write_json(hist_path, dummy, indent=int(args.indent))
            written += 1

    print(f"experiments_root: {exp_root}")
    print(f"output_root: {output_root}")
    print(f"server: {str(args.server).rstrip('/')}")
    print(f"runs_scanned: {scanned}")
    print(f"runs_eligible(outputs+no history+not queued): {eligible}")
    print(f"history_written: {written}")
    print(f"history_written_real: {wrote_real}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

