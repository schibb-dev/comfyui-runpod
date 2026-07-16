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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

VARIANTS_REGISTRY = "vision_slice_variants.json"
LEGACY_NDJSON = "vision_slice_captions.ndjson"
VARIANT_PREFIX = "vision_slice_captions__"
VARIANT_SUFFIX = ".ndjson"
VARIANT_MANIFEST_PREFIX = "vision_slice_manifest__"
VARIANT_MANIFEST_SUFFIX = ".json"
QUALITY_NDJSON = "vision_slice_quality.ndjson"


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


def variant_manifest_name(variant_id: str) -> str:
    return f"{VARIANT_MANIFEST_PREFIX}{sanitize_variant_id(variant_id)}{VARIANT_MANIFEST_SUFFIX}"


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    xs = sorted(values)
    n = len(xs)
    mid = n // 2
    if n % 2:
        return float(xs[mid])
    return float(xs[mid - 1] + xs[mid]) / 2.0


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return float(sum(values)) / float(len(values))


def _parse_utc(s: Any) -> Optional[datetime]:
    if not isinstance(s, str) or not s.strip():
        return None
    t = s.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_variant_run_manifest(status_dir: Path, variant_id: str) -> Optional[Dict[str, Any]]:
    path = Path(status_dir) / variant_manifest_name(variant_id)
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def compute_caption_quality_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Text/tag distributions for one variant's NDJSON rows."""
    n = len(rows)
    char_lens: List[float] = []
    tag_counts: List[float] = []
    empty = 0
    for r in rows:
        cap = str(r.get("caption") or "").strip()
        if not cap:
            empty += 1
            char_lens.append(0.0)
        else:
            char_lens.append(float(len(cap)))
        tags = r.get("tags")
        if isinstance(tags, list):
            tag_counts.append(float(len(tags)))
        elif cap:
            # PromptGen tags often land as comma-separated caption text.
            parts = [p.strip() for p in cap.split(",") if p.strip()]
            tag_counts.append(float(len(parts)) if len(parts) >= 3 else 0.0)
        else:
            tag_counts.append(0.0)
    return {
        "n": n,
        "empty_count": empty,
        "empty_rate": round(empty / n, 4) if n else 0.0,
        "mean_chars": round(_mean(char_lens) or 0.0, 1) if n else None,
        "median_chars": round(_median(char_lens) or 0.0, 1) if n else None,
        "mean_tags": round(_mean(tag_counts) or 0.0, 2) if n else None,
        "median_tags": round(_median(tag_counts) or 0.0, 2) if n else None,
    }


def infer_variant_run_status(
    *,
    caption_count: int,
    frame_count: Optional[int],
    finished_utc: Any,
    ndjson_mtime: Optional[float],
    started_utc: Any = None,
    manifest_status: Any = None,
) -> str:
    """
    complete | running | idle

    Prefer explicit manifest ``status: running``. Otherwise compare caption_count
    to frame_count. Missing finished_utc alone is not enough — many older
    registries lack per-variant end stamps.
    """
    if str(manifest_status or "").lower() == "running":
        return "running"
    expected = int(frame_count) if isinstance(frame_count, (int, float)) and frame_count else None
    finished = _parse_utc(finished_utc)
    started = _parse_utc(started_utc)
    if caption_count <= 0:
        if str(manifest_status or "").lower() == "running":
            return "running"
        if started is not None and finished is None:
            age = (datetime.now(timezone.utc) - started).total_seconds()
            if age < 6 * 3600:
                return "running"
        if expected and finished and ndjson_mtime is not None:
            if ndjson_mtime > finished.timestamp() - 1.0:
                return "running"
        return "idle"
    if expected is not None and caption_count < expected:
        return "running"
    if finished is None and started is not None:
        age = (datetime.now(timezone.utc) - started).total_seconds()
        if age < 6 * 3600 and (expected is None or caption_count < expected):
            # Mid-run progress marker (finished_utc explicitly null).
            return "running"
    if finished is not None and ndjson_mtime is not None and ndjson_mtime > finished.timestamp() + 2.0:
        return "running"
    return "complete"


def enrich_variant_stats(
    *,
    status_dir: Path,
    meta: Dict[str, Any],
    rows: List[Dict[str, Any]],
    nd_path: Path,
) -> Dict[str, Any]:
    """Attach run progress + quality stats onto a variant meta dict."""
    vid = sanitize_variant_id(str(meta.get("id") or "default"))
    run = load_variant_run_manifest(status_dir, vid) or {}
    caption_count = len(rows)
    frame_count = run.get("frame_count")
    if not isinstance(frame_count, (int, float)):
        frame_count = meta.get("frame_count")
    error_count = run.get("error_count")
    if not isinstance(error_count, (int, float)):
        error_count = None
    started_utc = run.get("started_utc")
    finished_utc = run.get("finished_utc")
    timing = run.get("timing") if isinstance(run.get("timing"), dict) else None
    nd_mtime = nd_path.stat().st_mtime if nd_path.is_file() else None
    status = infer_variant_run_status(
        caption_count=caption_count,
        frame_count=int(frame_count) if isinstance(frame_count, (int, float)) else None,
        finished_utc=finished_utc,
        ndjson_mtime=nd_mtime,
        started_utc=started_utc,
        manifest_status=run.get("status"),
    )
    expected = int(frame_count) if isinstance(frame_count, (int, float)) and frame_count else None
    pct = None
    if expected and expected > 0:
        pct = round(min(1.0, caption_count / float(expected)) * 100.0, 1)

    wall_s = None
    caps_per_min = None
    if timing:
        if isinstance(timing.get("wall_s"), (int, float)):
            wall_s = float(timing["wall_s"])
        if isinstance(timing.get("captions_per_min_steady"), (int, float)):
            caps_per_min = float(timing["captions_per_min_steady"])
    if wall_s is None and started_utc:
        start_dt = _parse_utc(started_utc)
        end_dt = _parse_utc(finished_utc) or datetime.now(timezone.utc)
        if start_dt is not None:
            wall_s = max(0.0, (end_dt - start_dt).total_seconds())
    if caps_per_min is None and wall_s and wall_s > 0 and caption_count > 0 and status == "complete":
        caps_per_min = round(caption_count / (wall_s / 60.0), 2)
    elif caps_per_min is None and wall_s and wall_s > 0 and caption_count > 0 and status == "running":
        caps_per_min = round(caption_count / (wall_s / 60.0), 2)

    quality = compute_caption_quality_stats(rows)
    out = dict(meta)
    out.update(
        {
            "id": vid,
            "caption_count": caption_count,
            "frame_count": expected,
            "error_count": int(error_count) if error_count is not None else None,
            "started_utc": started_utc,
            "finished_utc": finished_utc if status == "complete" else None,
            "status": status,
            "progress_pct": pct,
            "wall_s": round(wall_s, 1) if isinstance(wall_s, float) else None,
            "captions_per_min": round(caps_per_min, 2) if isinstance(caps_per_min, float) else None,
            "timing": timing,
            "quality": quality,
        }
    )
    return out


def build_review_stats(variants_out: List[Dict[str, Any]], *, slice_count: int) -> Dict[str, Any]:
    """Roll-up for the UI stats strip."""
    complete = sum(1 for v in variants_out if v.get("status") == "complete")
    running = sum(1 for v in variants_out if v.get("status") == "running")
    idle = sum(1 for v in variants_out if v.get("status") == "idle")
    expected_frames = 0
    for v in variants_out:
        fc = v.get("frame_count")
        if isinstance(fc, (int, float)):
            expected_frames = max(expected_frames, int(fc))
    if expected_frames <= 0:
        expected_frames = int(slice_count or 0)
    done_max = max((int(v.get("caption_count") or 0) for v in variants_out), default=0)
    any_running = running > 0
    return {
        "variant_count": len(variants_out),
        "complete_count": complete,
        "running_count": running,
        "idle_count": idle,
        "expected_frames": expected_frames or None,
        "max_caption_count": done_max,
        "slice_count": slice_count,
        "any_running": any_running,
        "poll_suggested_ms": 8000 if any_running else None,
    }


def load_quality_by_slice(status_dir: Path) -> Dict[Tuple[str, float, float, str], Dict[str, Any]]:
    """Map slice keys → classical quality dict from ``vision_slice_quality.ndjson``."""
    path = Path(status_dir) / QUALITY_NDJSON
    out: Dict[Tuple[str, float, float, str], Dict[str, Any]] = {}
    for row in load_slice_rows(path):
        q = row.get("quality")
        if not isinstance(q, dict):
            continue
        key = _slice_key(row)
        if key[0] == "":
            continue
        out[key] = {
            "sharpness": q.get("sharpness"),
            "convergence": q.get("convergence"),
            "artifacting": q.get("artifacting"),
            "exposure": q.get("exposure"),
            "contrast": q.get("contrast"),
        }
    return out


def _rollup_quality_means(qs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not qs:
        return None
    keys = ("sharpness", "convergence", "artifacting", "exposure", "contrast")
    out: Dict[str, Any] = {"frame_count": len(qs)}
    for k in keys:
        vals = [float(q[k]) for q in qs if isinstance(q.get(k), (int, float))]
        if not vals:
            continue
        vals_sorted = sorted(vals)
        n = len(vals_sorted)
        mean = sum(vals_sorted) / n
        p10 = vals_sorted[max(0, int(0.1 * (n - 1)))]
        p90 = vals_sorted[min(n - 1, int(0.9 * (n - 1)))]
        out[k] = {
            "mean": round(mean, 4),
            "p10": round(p10, 4),
            "p90": round(p90, 4),
            "n": n,
        }
    return out


def corpus_quality_stats(assets: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Mean of per-asset quality means across the corpus."""
    keys = ("sharpness", "convergence", "artifacting", "exposure", "contrast")
    buckets: Dict[str, List[float]] = {k: [] for k in keys}
    n_assets = 0
    for a in assets:
        q = a.get("quality")
        if not isinstance(q, dict):
            continue
        n_assets += 1
        for k in keys:
            m = q.get(k)
            if isinstance(m, dict) and isinstance(m.get("mean"), (int, float)):
                buckets[k].append(float(m["mean"]))
    if n_assets <= 0:
        return None
    out: Dict[str, Any] = {"asset_count": n_assets}
    for k, vals in buckets.items():
        if vals:
            out[k] = round(sum(vals) / len(vals), 4)
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
    quality_by_key = load_quality_by_slice(status_dir)

    # key -> slice shell + captions_by_variant
    merged: Dict[Tuple[str, float, float, str], Dict[str, Any]] = {}
    total_rows = 0

    for meta in variant_metas:
        vid = sanitize_variant_id(str(meta.get("id") or "default"))
        nd_name = str(meta.get("ndjson") or variant_ndjson_name(vid))
        nd_path = status_dir / nd_name
        rows = load_slice_rows(nd_path)
        total_rows += len(rows)
        base_meta = {
            "id": vid,
            "label": meta.get("label") or vid,
            "model_pin": meta.get("model_pin") or None,
            "task": meta.get("task"),
            "provider": meta.get("provider"),
            "run_id": meta.get("run_id"),
            "ndjson": nd_name,
        }
        variants_out.append(
            enrich_variant_stats(
                status_dir=status_dir,
                meta=base_meta,
                rows=rows,
                nd_path=nd_path,
            )
        )
        # Prefer pin/task from live rows / run manifest when registry is stale.
        enriched = variants_out[-1]
        run = load_variant_run_manifest(status_dir, vid) or {}
        if not enriched.get("model_pin"):
            enriched["model_pin"] = run.get("model_pin")
        if not enriched.get("task"):
            enriched["task"] = run.get("task")
        if not enriched.get("run_id"):
            enriched["run_id"] = run.get("run_id")
        if rows and not enriched.get("model_pin"):
            enriched["model_pin"] = rows[0].get("model_pin")
        if rows and not enriched.get("task"):
            enriched["task"] = rows[0].get("task")
        if rows and not enriched.get("provider"):
            enriched["provider"] = rows[0].get("provider")
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
                    "excerpt_index": r.get("excerpt_index"),
                    "excerpt_video_relpath": r.get("excerpt_video_relpath"),
                    "excerpt_local_t": r.get("excerpt_local_t"),
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
            if shell.get("excerpt_video_relpath") is None and r.get("excerpt_video_relpath"):
                shell["excerpt_video_relpath"] = r.get("excerpt_video_relpath")
            if shell.get("excerpt_index") is None and r.get("excerpt_index") is not None:
                shell["excerpt_index"] = r.get("excerpt_index")
            if shell.get("excerpt_local_t") is None and r.get("excerpt_local_t") is not None:
                shell["excerpt_local_t"] = r.get("excerpt_local_t")

    by_asset: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for shell in merged.values():
        by_asset[str(shell["asset_relpath"])].append(shell)

    assets: List[Dict[str, Any]] = []
    for rel in sorted(by_asset.keys()):
        slices_raw = by_asset[rel]
        slices_raw.sort(
            key=lambda x: (
                int(x.get("excerpt_index") or 0),
                0 if x.get("slice") == "window" else 1,
                float(x.get("t0") or 0),
            )
        )
        excerpts_by_idx: Dict[int, Dict[str, Any]] = {}
        slices: List[Dict[str, Any]] = []
        for s in slices_raw:
            primary = ""
            for v in variants_out:
                cap = (s.get("captions") or {}).get(v["id"], {}).get("caption") or ""
                if cap:
                    primary = cap
                    break
            ex_idx = s.get("excerpt_index")
            ex_rel = s.get("excerpt_video_relpath")
            if ex_idx is not None and ex_rel:
                ei = int(ex_idx)
                if ei not in excerpts_by_idx:
                    excerpts_by_idx[ei] = {
                        "index": ei,
                        "video_relpath": ex_rel,
                        "video_url": _files_url(str(ex_rel)),
                        "source_t0": None,
                        "source_t1": None,
                    }
                # Prefer whole-row bounds for excerpt source span; else expand from windows.
                if s.get("slice") == "whole":
                    excerpts_by_idx[ei]["source_t0"] = s.get("t0")
                    excerpts_by_idx[ei]["source_t1"] = s.get("t1")
                else:
                    local = s.get("excerpt_local_t")
                    frame_t = s.get("frame_t")
                    if (
                        excerpts_by_idx[ei].get("source_t0") is None
                        and isinstance(local, (int, float))
                        and isinstance(frame_t, (int, float))
                    ):
                        excerpts_by_idx[ei]["source_t0"] = round(float(frame_t) - float(local), 6)
                    t0 = s.get("t0")
                    t1 = s.get("t1")
                    if (
                        excerpts_by_idx[ei].get("source_t0") is None
                        and isinstance(t0, (int, float))
                    ):
                        excerpts_by_idx[ei]["source_t0"] = float(t0)
                    if isinstance(t1, (int, float)):
                        prev1 = excerpts_by_idx[ei].get("source_t1")
                        if prev1 is None or float(t1) > float(prev1):
                            excerpts_by_idx[ei]["source_t1"] = float(t1)
            slices.append(
                {
                    "t0": s.get("t0"),
                    "t1": s.get("t1"),
                    "frame_t": s.get("frame_t"),
                    "slice": s.get("slice") or "window",
                    "caption": primary,
                    "captions": s.get("captions") or {},
                    "quality": quality_by_key.get(
                        (
                            str(s.get("asset_relpath") or ""),
                            float(s.get("t0") or 0.0),
                            float(s.get("t1") or 0.0),
                            str(s.get("slice") or "window"),
                        )
                    ),
                    "excerpt_index": ex_idx,
                    "excerpt_video_relpath": ex_rel,
                    "excerpt_video_url": _files_url(str(ex_rel)) if ex_rel else None,
                    "excerpt_local_t": s.get("excerpt_local_t"),
                }
            )
        basename = Path(rel.replace("\\", "/")).name
        excerpts = [excerpts_by_idx[i] for i in sorted(excerpts_by_idx.keys())]
        slice_qs = [sl["quality"] for sl in slices if isinstance(sl.get("quality"), dict)]
        assets.append(
            {
                "asset_relpath": rel,
                "basename": basename,
                "video_url": _files_url(rel),
                "excerpts": excerpts,
                "slice_count": len(slices),
                "has_whole": any(x.get("slice") == "whole" for x in slices),
                "quality": _rollup_quality_means(slice_qs),
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

    stats = build_review_stats(variants_out, slice_count=len(merged))
    cq = corpus_quality_stats(assets)
    if cq:
        stats["video_quality"] = cq
    # If running variants lack frame_count, backfill expected from roll-up.
    if stats.get("expected_frames"):
        for v in variants_out:
            if not v.get("frame_count") and v.get("status") == "running":
                v["frame_count"] = stats["expected_frames"]
                done = int(v.get("caption_count") or 0)
                exp = int(stats["expected_frames"])
                if exp > 0:
                    v["progress_pct"] = round(min(1.0, done / float(exp)) * 100.0, 1)

    return {
        "ok": True,
        "manifest_path": str(man_path) if man_path.is_file() else None,
        "manifest": manifest,
        "variants": variants_out,
        "stats": stats,
        "quality_ndjson": str(status_dir / QUALITY_NDJSON)
        if (status_dir / QUALITY_NDJSON).is_file()
        else None,
        "asset_count": len(assets),
        "caption_count": total_rows,
        "slice_count": len(merged),
        "assets": assets,
    }
