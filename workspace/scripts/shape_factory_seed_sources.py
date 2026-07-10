#!/usr/bin/env python3
"""
Recover source stills for seeded (job-less) shape-factory outputs.

Many older i2v outputs (e.g. X-KNEEL) were deposited into pools before job
tracking existed, so the factory map has no ``source_still`` binding for them.
Their originating still is still embedded in the output's ComfyUI prompt
(``LoadImage``), so we can recover it from the companion PNG (or the mp4 tags).

The recovered mapping is persisted to ``_status/factory_seed_sources.json`` so
it is durable and reusable (map serve-time + heuristics), and only computed once
per output.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SEED_SOURCES_BASENAME = "factory_seed_sources.json"
SEED_SOURCES_SCHEMA_VERSION = 1

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".avif", ".gif", ".tiff", ".tif", ".jfif"}
_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_rel(s: str) -> str:
    return str(s or "").replace("\\", "/").strip().lstrip("/")


def default_seed_sources_path(og_root: Path) -> Path:
    return og_root.resolve().parent / "_status" / SEED_SOURCES_BASENAME


def load_seed_sources(path: Path) -> Dict[str, Any]:
    """Return the ``by_output_relpath`` table (empty on any error)."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(doc, dict):
        return {}
    table = doc.get("by_output_relpath")
    return table if isinstance(table, dict) else {}


def save_seed_sources(path: Path, table: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "version": SEED_SOURCES_SCHEMA_VERSION,
        "updated_at": _utc_now(),
        "count": len(table),
        "by_output_relpath": table,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    tmp.replace(path)


def source_still_relpath(basename: str) -> str:
    bn = Path(str(basename or "")).name
    return f"input/{bn}" if bn else ""


def _extract_prompt(media_abs: Path, *, ffprobe: Optional[str]) -> tuple[Optional[Dict[str, Any]], str]:
    """(prompt_dict, evidence) from the companion PNG, else the mp4 container tags."""
    try:
        from correlate_output_ratings import extract_prompt_mp4, extract_prompt_png
    except ImportError:
        return None, ""
    png = media_abs.with_suffix(".png")
    if png.is_file():
        pr = extract_prompt_png(png)
        if isinstance(pr, dict) and pr:
            return pr, "png_load_image"
    if media_abs.suffix.lower() in _VIDEO_EXTS and media_abs.is_file() and ffprobe:
        pr = extract_prompt_mp4(media_abs, ffprobe=ffprobe)
        if isinstance(pr, dict) and pr:
            return pr, "mp4_load_image"
    return None, ""


def infer_source_still(media_abs: Path, *, ffprobe: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Return ``{"source_basename", "source_raw", "evidence"}`` for the first image
    source in the output's embedded prompt, or ``None`` if not recoverable.
    """
    try:
        from correlate_output_ratings import (
            extract_source_paths_from_prompt,
            normalize_source_basename,
        )
    except ImportError:
        return None
    prompt, evidence = _extract_prompt(media_abs, ffprobe=ffprobe)
    if not isinstance(prompt, dict):
        return None
    for raw in extract_source_paths_from_prompt(prompt):
        bn = normalize_source_basename(raw)
        if bn and Path(bn).suffix.lower() in _IMAGE_EXTS:
            return {"source_basename": bn, "source_raw": raw, "evidence": evidence}
    return None


def build_seed_sources(
    *,
    output_relpaths: List[str],
    output_root: Path,
    seed_sources_path: Path,
    ffprobe: Optional[str] = None,
    workspace_input_exists: Optional[Any] = None,
    abs_by_key: Optional[Dict[str, Path]] = None,
    refresh: bool = False,
) -> Dict[str, Any]:
    """
    Infer + persist source stills for the given output relpaths.

    ``workspace_input_exists`` (optional callable ``rel -> bool``) filters to
    stills that are actually servable under ``input/``. ``abs_by_key`` lets the
    caller supply already-known absolute media paths (avoids re-resolution).
    """
    table = {} if refresh else dict(load_seed_sources(seed_sources_path))
    output_root = output_root.resolve()
    scanned = 0
    resolved = 0
    negative = 0
    for rel in output_relpaths:
        key = _norm_rel(rel)
        if not key:
            continue
        if not refresh and key in table:
            continue
        media_abs = (abs_by_key or {}).get(key) or _resolve_output_abs(key, output_root)
        if media_abs is None or not Path(media_abs).exists():
            continue
        scanned += 1
        info = infer_source_still(media_abs, ffprobe=ffprobe)
        if not info:
            table[key] = {"source_still_relpath": None, "updated_at": _utc_now()}
            negative += 1
            continue
        src_rel = source_still_relpath(info["source_basename"])
        if workspace_input_exists is not None and src_rel and not workspace_input_exists(src_rel):
            table[key] = {
                "source_still_relpath": None,
                "source_basename": info["source_basename"],
                "missing_input": True,
                "updated_at": _utc_now(),
            }
            negative += 1
            continue
        table[key] = {
            "source_still_relpath": src_rel,
            "source_basename": info["source_basename"],
            "evidence": info["evidence"],
            "updated_at": _utc_now(),
        }
        resolved += 1
    save_seed_sources(seed_sources_path, table)
    return {
        "ok": True,
        "path": str(seed_sources_path),
        "total": len(table),
        "scanned": scanned,
        "resolved": resolved,
        "negative": negative,
    }


def _resolve_output_abs(relpath: str, output_root: Path) -> Optional[Path]:
    rel = _norm_rel(relpath)
    if not rel:
        return None
    if rel.startswith("output/"):
        rel = rel[len("output/") :]
    for cand in (output_root / rel, output_root.parent / rel, output_root / "output" / rel):
        if cand.is_file():
            return cand.resolve()
    return None


def _collect_seed_deposit_paths(
    data_root: Path,
    output_root: Path,
    *,
    workspace_root: Optional[Path],
    family_filter: Optional[str] = None,
) -> Dict[str, Path]:
    """Return {output_relpath: abs_mp4} for job-less deposit members across families."""
    import sys

    d = str(Path(__file__).resolve().parent)
    if d not in sys.path:
        sys.path.insert(0, d)
    from shape_factory_map import resolve_output_relpath  # type: ignore

    out: Dict[str, Path] = {}
    pools_root = data_root / "pools"
    if not pools_root.is_dir():
        return out
    for fam_dir in sorted(pools_root.iterdir()):
        if not fam_dir.is_dir():
            continue
        if family_filter and fam_dir.name != family_filter:
            continue
        idx = fam_dir / "index.json"
        if not idx.is_file():
            continue
        try:
            doc = json.loads(idx.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for spec in (doc.get("pools") or {}).values() if isinstance(doc.get("pools"), dict) else []:
            if not isinstance(spec, dict):
                continue
            for m in spec.get("members") or []:
                if not isinstance(m, dict) or m.get("job_key"):
                    continue
                p = str(m.get("path") or "")
                if not p.lower().endswith(".mp4"):
                    continue
                rel = resolve_output_relpath(p, output_root, workspace_root=workspace_root)
                if not rel:
                    continue
                key = _norm_rel(rel)
                # index.json stores host absolute paths; resolve to the actual
                # file under this process's output_root (host or container).
                media_abs = _resolve_output_abs(key, output_root)
                if media_abs is not None:
                    out[key] = media_abs
    return out


def cmd_seed_sources_build(args: "Any") -> int:
    import sys

    d = str(Path(__file__).resolve().parent)
    if d not in sys.path:
        sys.path.insert(0, d)
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    output_root = Path(args.output_root).expanduser().resolve()
    workspace_root = Path(args.workspace_root).expanduser().resolve() if args.workspace_root else None
    if args.data_root:
        data_root = Path(args.data_root).expanduser().resolve()
    else:
        data_root = resolve_shape_factory_data_root(repo_root=Path(args.repo_root).expanduser().resolve())
    og_root = output_root / "og"
    if not og_root.is_dir():
        og_root = output_root / "output" / "og"
    seed_path = Path(args.out).expanduser().resolve() if args.out else default_seed_sources_path(og_root)

    abs_by_key = _collect_seed_deposit_paths(
        data_root, output_root, workspace_root=workspace_root, family_filter=args.family
    )
    if not abs_by_key:
        print("[seed-sources] no job-less deposit members found", file=__import__("sys").stderr)

    def input_exists(rel: str) -> bool:
        if workspace_root is None:
            return True
        return (workspace_root / rel).is_file()

    result = build_seed_sources(
        output_relpaths=list(abs_by_key.keys()),
        output_root=output_root,
        seed_sources_path=seed_path,
        ffprobe=shutil.which("ffprobe"),
        workspace_input_exists=input_exists,
        abs_by_key=abs_by_key,
        refresh=bool(args.refresh),
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


def add_seed_sources_subparser(sub: "Any") -> None:
    p = sub.add_parser("seed-sources", help="Recover source stills for seeded (job-less) outputs")
    p_sub = p.add_subparsers(dest="seed_sources_cmd", required=True)
    build = p_sub.add_parser("build", help="Infer + persist factory_seed_sources.json")
    build.add_argument("--output-root", default="/home/yuji/comfyui-runpod-data/output")
    build.add_argument("--workspace-root", default="/home/yuji/comfyui-runpod-data")
    build.add_argument("--data-root", default=None, help="Shape-factory .data root (default: repo .data)")
    build.add_argument("--repo-root", default="/home/yuji/src/comfyui-runpod")
    build.add_argument("--family", default=None, help="Only this family slug (dir under pools/)")
    build.add_argument("--out", default=None, help="Write map here (default: _status/factory_seed_sources.json)")
    build.add_argument("--refresh", action="store_true", help="Recompute all (ignore existing cache)")
    build.set_defaults(func=cmd_seed_sources_build)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Shape-factory seed source recovery")
    _sub = ap.add_subparsers(dest="cmd", required=True)
    add_seed_sources_subparser(_sub)
    _args = ap.parse_args()
    raise SystemExit(_args.func(_args))
