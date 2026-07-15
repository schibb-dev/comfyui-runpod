#!/usr/bin/env python3
"""
Vision V1 — package slice-caption NDJSON for the Experiments UI review page.

Supports multiple comparative variants:
  vision_slice_captions__<variant>.ndjson
  vision_slice_variants.json  (registry / labels)
Legacy single file vision_slice_captions.ndjson is treated as variant ``base_caption``.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

VARIANTS_REGISTRY = "vision_slice_variants.json"
LEGACY_NDJSON = "vision_slice_captions.ndjson"
VARIANT_PREFIX = "vision_slice_captions__"
VARIANT_SUFFIX = ".ndjson"


def _files_url(rel: str) -> str:
    return "/files/" + urllib.parse.quote(_normalize_rel(rel), safe="/")


def _normalize_rel(rel: str) -> str:
    s = str(rel or "").replace("\\", "/").strip()
    while s.startswith("./"):
        s = s[2:]
    return s.lstrip("/")


def sanitize_variant_id(raw: str) -> str:
    t = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(raw or "").strip())
    return t.strip("._") or "default"


def variant_ndjson_name(variant_id: str) -> str:
    return f"{VARIANT_PREFIX}{sanitize_variant_id(variant_id)}{VARIANT_SUFFIX}"


def load_slice_rows(ndjson_path: Path) -> List[Dict[str, Any]]:
    if not ndjson_path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    for line in ndjson_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("asset_relpath"):
            rows.append(obj)
    return rows


def _slice_key(row: Dict[str, Any]) -> Tuple[str, float, float, str]:
    return (
        str(row.get("asset_relpath") or ""),
        float(row.get("t0") or 0.0),
        float(row.get("t1") or 0.0),
        str(row.get("slice") or "window"),
    )


def load_variants_registry(status_dir: Path) -> Dict[str, Any]:
    path = status_dir / VARIANTS_REGISTRY
    if not path.is_file():
        return {"schema": 1, "variants": []}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": 1, "variants": []}
    if not isinstance(doc, dict):
        return {"schema": 1, "variants": []}
    if not isinstance(doc.get("variants"), list):
        doc["variants"] = []
    return doc


def register_variant(
    status_dir: Path,
    *,
    variant_id: str,
    label: str,
    model_pin: str,
    task: str,
    provider: str,
    run_id: str,
    ndjson_name: str,
    caption_count: int,
) -> Path:
    status_dir = Path(status_dir)
    status_dir.mkdir(parents=True, exist_ok=True)
    doc = load_variants_registry(status_dir)
    vid = sanitize_variant_id(variant_id)
    variants = [v for v in doc["variants"] if isinstance(v, dict) and v.get("id") != vid]
    variants.append(
        {
            "id": vid,
            "label": label or vid,
            "model_pin": model_pin,
            "task": task,
            "provider": provider,
            "run_id": run_id,
            "ndjson": ndjson_name,
            "caption_count": caption_count,
        }
    )
    # Stable order: base first, then by label
    def sort_key(v: Dict[str, Any]) -> Tuple[int, str]:
        i = str(v.get("id") or "")
        return (0 if i == "base_caption" else 1, str(v.get("label") or i))

    variants.sort(key=sort_key)
    doc["schema"] = 1
    doc["variants"] = variants
    path = status_dir / VARIANTS_REGISTRY
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def discover_variant_files(status_dir: Path) -> List[Dict[str, Any]]:
    """Merge registry + on-disk ``vision_slice_captions__*.ndjson`` (+ legacy)."""
    status_dir = Path(status_dir)
    reg = load_variants_registry(status_dir)
    by_id: Dict[str, Dict[str, Any]] = {}
    for v in reg.get("variants") or []:
        if isinstance(v, dict) and v.get("id"):
            by_id[str(v["id"])] = dict(v)

    for p in sorted(status_dir.glob(f"{VARIANT_PREFIX}*{VARIANT_SUFFIX}")):
        mid = p.name[len(VARIANT_PREFIX) : -len(VARIANT_SUFFIX)]
        vid = sanitize_variant_id(mid)
        if vid not in by_id:
            by_id[vid] = {"id": vid, "label": vid, "ndjson": p.name}
        else:
            by_id[vid]["ndjson"] = p.name

    legacy = status_dir / LEGACY_NDJSON
    if legacy.is_file() and "base_caption" not in by_id and not (status_dir / variant_ndjson_name("base_caption")).is_file():
        by_id["base_caption"] = {
            "id": "base_caption",
            "label": "base · Florence-2-base · caption",
            "ndjson": LEGACY_NDJSON,
            "model_pin": "microsoft/Florence-2-base",
            "task": "caption",
            "provider": "comfy_florence2",
        }

    out = list(by_id.values())

    def sort_key(v: Dict[str, Any]) -> Tuple[int, str]:
        i = str(v.get("id") or "")
        return (0 if i == "base_caption" else 1, str(v.get("label") or i))

    out.sort(key=sort_key)
    return out


def list_vision_slice_review(
    *,
    status_dir: Path,
    manifest_name: str = "vision_slice_manifest.json",
) -> Dict[str, Any]:
    """
    Group captions by asset+time window; attach per-variant caption text for comparison.
    """
    status_dir = Path(status_dir)
    variant_metas = discover_variant_files(status_dir)
    variants_out: List[Dict[str, Any]] = []

    # key -> slice shell + captions_by_variant
    merged: Dict[Tuple[str, float, float, str], Dict[str, Any]] = {}
    total_rows = 0

    for meta in variant_metas:
        vid = sanitize_variant_id(str(meta.get("id") or "default"))
        nd_name = str(meta.get("ndjson") or variant_ndjson_name(vid))
        nd_path = status_dir / nd_name
        rows = load_slice_rows(nd_path)
        total_rows += len(rows)
        variants_out.append(
            {
                "id": vid,
                "label": meta.get("label") or vid,
                "model_pin": meta.get("model_pin"),
                "task": meta.get("task"),
                "provider": meta.get("provider"),
                "run_id": meta.get("run_id"),
                "ndjson": nd_name,
                "caption_count": len(rows),
            }
        )
        for r in rows:
            key = _slice_key(r)
            if key[0] == "":
                continue
            shell = merged.get(key)
            if shell is None:
                shell = {
                    "asset_relpath": key[0],
                    "t0": key[1],
                    "t1": key[2],
                    "frame_t": r.get("frame_t"),
                    "slice": key[3],
                    "captions": {},
                }
                merged[key] = shell
            shell["captions"][vid] = {
                "caption": r.get("caption") or "",
                "tags": r.get("tags") if isinstance(r.get("tags"), list) else [],
                "provider": r.get("provider"),
                "model_pin": r.get("model_pin"),
                "run_id": r.get("run_id"),
                "task": r.get("task") or meta.get("task"),
            }
            if shell.get("frame_t") is None and r.get("frame_t") is not None:
                shell["frame_t"] = r.get("frame_t")

    by_asset: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for shell in merged.values():
        by_asset[str(shell["asset_relpath"])].append(shell)

    assets: List[Dict[str, Any]] = []
    for rel in sorted(by_asset.keys()):
        slices_raw = by_asset[rel]
        slices_raw.sort(key=lambda x: (0 if x.get("slice") == "window" else 1, float(x.get("t0") or 0)))
        slices: List[Dict[str, Any]] = []
        for s in slices_raw:
            # Primary caption for compact view: first variant that has text
            primary = ""
            for v in variants_out:
                cap = (s.get("captions") or {}).get(v["id"], {}).get("caption") or ""
                if cap:
                    primary = cap
                    break
            slices.append(
                {
                    "t0": s.get("t0"),
                    "t1": s.get("t1"),
                    "frame_t": s.get("frame_t"),
                    "slice": s.get("slice") or "window",
                    "caption": primary,
                    "captions": s.get("captions") or {},
                }
            )
        basename = Path(rel.replace("\\", "/")).name
        assets.append(
            {
                "asset_relpath": rel,
                "basename": basename,
                "video_url": _files_url(rel),
                "slice_count": len(slices),
                "has_whole": any(x.get("slice") == "whole" for x in slices),
                "slices": slices,
            }
        )

    man_path = status_dir / manifest_name
    manifest: Optional[Dict[str, Any]] = None
    if man_path.is_file():
        try:
            doc = json.loads(man_path.read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                manifest = {
                    "run_id": doc.get("run_id"),
                    "provider": doc.get("provider"),
                    "model_pin": doc.get("model_pin"),
                    "caption_count": doc.get("caption_count"),
                    "asset_count": doc.get("asset_count"),
                    "finished_utc": doc.get("finished_utc"),
                    "note": doc.get("note"),
                }
        except (OSError, json.JSONDecodeError):
            manifest = None

    return {
        "ok": True,
        "manifest_path": str(man_path) if man_path.is_file() else None,
        "manifest": manifest,
        "variants": variants_out,
        "asset_count": len(assets),
        "caption_count": total_rows,
        "slice_count": len(merged),
        "assets": assets,
    }
