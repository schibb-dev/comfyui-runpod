#!/usr/bin/env python3
"""Cluster workflow corpus for Phase 2 family discovery.

Scans catalog readable JSONs, template candidates, and user workflow trees;
groups by **topology** fingerprint (id-free node-type multiset + typed edges);
marks clusters covered by enrolled shapes.

Exemplar videos for review are keyed by the same topology fingerprint (PNG
workflow embeds under ``output/og``), not by output basename / brand tokens.

Usage:
  python3 shape_factory_family_discovery.py cluster [--write docs/family_discovery]
  python3 shape_factory_family_discovery.py index-exemplars [--output-root …]
  python3 shape_factory_family_discovery.py backfill-proposals [--write docs/family_discovery]
  python3 shape_factory_family_discovery.py enroll --prop prop_003 --slug MyFamily ...
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from comfy_meta_lib import (  # noqa: E402
    extract_prompt_workflow_from_png_chunks,
    read_png_text_chunks,
)
from shape_factory import load_yaml  # noqa: E402
from shape_factory_vocab import (  # noqa: E402
    format_catalog_stem,
    graph_fingerprint_topology,
    guess_io_from_workflow,
    load_workflow_json,
    parse_catalog_stem,
    validate_shape_document,
)

REPO = Path(__file__).resolve().parents[2]
DEFAULT_DATA = REPO / ".data"
DEFAULT_CATALOG = Path(
    "/home/yuji/comfyui-runpod-data/comfyui_user/default/workflows/generated/catalog"
)
DEFAULT_USER_WF = Path("/home/yuji/comfyui-runpod-data/comfyui_user/default/workflows")
DEFAULT_CANDIDATES = DEFAULT_DATA / "template_candidates"
DEFAULT_OUT = REPO / "docs" / "family_discovery"
DEFAULT_EXEMPLAR_INDEX = DEFAULT_DATA / "shape_factory" / "family_discovery_exemplars.json"
DEFAULT_OUTPUT_ROOT = Path("/home/yuji/comfyui-runpod-data/output")

# Review UI target: enough clips to judge a variation without drowning the operator.
SAMPLE_TARGET = 20

NOISE_NAME_RE = re.compile(r"(?i)(^|[_-])(tune-|tunetest|TUNETEST)")
_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SOURCE_IMAGE_TYPES = frozenset({"LoadImage", "LoadImageWithFilename", "LoadImageWithFilename|pysssss"})
_SOURCE_VIDEO_TYPES = frozenset(
    {
        "VHS_LoadVideo",
        "VHS_LoadVideoPath",
        "LoadVideo",
        "LoadVideoPath",
    }
)


def _utc() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_noise(path: Path) -> bool:
    name = path.name
    if NOISE_NAME_RE.search(name):
        return True
    if name.endswith(".bak") or ".bak." in name or name.endswith(".bak2"):
        return True
    if "bak-before" in name or name.endswith("~"):
        return True
    return False


def _enrolled_fingerprints(shapes_dir: Path) -> Dict[str, Dict[str, Any]]:
    """Map topology fingerprint → enrolled family meta (from shape template when present)."""
    out: Dict[str, Dict[str, Any]] = {}
    for path in sorted(shapes_dir.glob("*.shape.yaml")):
        doc = load_yaml(path)
        slug = str(doc.get("family_slug") or path.name[: -len(".shape.yaml")])
        meta = {
            "family_slug": slug,
            "shape_path": str(path),
            "io_class": doc.get("io_class"),
            "chain_role": doc.get("chain_role"),
            "graph_hash": doc.get("graph_hash"),
        }
        # Historical lite/graph_hash values may still appear on cards; keep as weak keys.
        gh = str(doc.get("graph_hash") or "").strip()
        if gh:
            out[gh] = meta
        tpl = doc.get("template")
        if tpl:
            wf = load_workflow_json(Path(str(tpl)))
            if wf:
                fp = graph_fingerprint_topology(wf)
                out[fp] = meta
                meta["fingerprint"] = fp
    return out


def _iter_workflow_paths(
    *,
    catalog_dir: Path,
    user_dir: Path,
    candidates_dir: Path,
) -> Iterable[Tuple[str, Path]]:
    if catalog_dir.is_dir():
        for p in sorted(catalog_dir.glob("*-readable.json")):
            if _is_noise(p):
                continue
            yield "catalog", p
    if candidates_dir.is_dir():
        for p in sorted(candidates_dir.glob("*.candidate.json")):
            if _is_noise(p):
                continue
            yield "candidate", p
    if user_dir.is_dir():
        for p in sorted(user_dir.rglob("*.json")):
            if _is_noise(p):
                continue
            # skip generated/catalog (already scanned as catalog)
            try:
                rel = p.relative_to(user_dir)
            except ValueError:
                continue
            parts = rel.parts
            if parts and parts[0] == "generated":
                continue
            if any(part.startswith(".") for part in parts):
                continue
            yield "user", p


def _load_candidate_workflow(path: Path) -> Optional[Dict[str, Any]]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    for key in ("workflow", "ui_workflow", "template", "graph"):
        inner = obj.get(key)
        if isinstance(inner, dict) and (inner.get("nodes") or inner.get("links") is not None):
            return inner
    if obj.get("nodes"):
        return obj
    return None


def cluster_corpus(
    *,
    shapes_dir: Path,
    catalog_dir: Path,
    user_dir: Path,
    candidates_dir: Path,
) -> Dict[str, Any]:
    enrolled = _enrolled_fingerprints(shapes_dir)
    clusters: Dict[str, Dict[str, Any]] = {}
    errors: List[Dict[str, str]] = []
    seen_paths: Set[str] = set()

    for source, path in _iter_workflow_paths(
        catalog_dir=catalog_dir, user_dir=user_dir, candidates_dir=candidates_dir
    ):
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen_paths:
            continue
        seen_paths.add(key)
        if source == "candidate":
            wf = _load_candidate_workflow(path)
        else:
            wf = load_workflow_json(path)
        if not wf or not isinstance(wf.get("nodes"), list):
            errors.append({"path": str(path), "source": source, "error": "unreadable_or_not_litegraph"})
            continue
        fp = graph_fingerprint_topology(wf)
        stem_info = parse_catalog_stem(path.name)
        guess = guess_io_from_workflow(wf)
        bucket = clusters.setdefault(
            fp,
            {
                "fingerprint": fp,
                "members": [],
                "covered_by": None,
                "io_guess": guess,
            },
        )
        bucket["members"].append(
            {
                "source": source,
                "path": str(path),
                "name": path.name,
                "stem": stem_info if stem_info.get("ok") else None,
                "node_count": len(wf.get("nodes") or []),
            }
        )
        if fp in enrolled and not bucket["covered_by"]:
            bucket["covered_by"] = enrolled[fp]

    # Also mark covered when shape graph_hash matches even if template path differs
    for fp, bucket in clusters.items():
        if bucket["covered_by"]:
            continue
        # nearest: if any member name matches enrolled family slug as prefix
        for slug_meta in enrolled.values():
            slug = slug_meta["family_slug"]
            for m in bucket["members"]:
                if str(m["name"]).startswith(slug) or slug in str(m["name"]):
                    # weak name match — only if fingerprint equals enrolled hash
                    if fp == slug_meta.get("graph_hash") or fp == slug_meta.get("fingerprint"):
                        bucket["covered_by"] = slug_meta
                        break

    rows = sorted(
        clusters.values(),
        key=lambda b: (-len(b["members"]), b["fingerprint"][:12]),
    )
    uncovered = [b for b in rows if not b.get("covered_by")]
    covered = [b for b in rows if b.get("covered_by")]

    return {
        "schema_version": "comfyui-runpod.family-discovery.v0",
        "generated_at": _utc(),
        "counts": {
            "clusters": len(rows),
            "covered_clusters": len(covered),
            "uncovered_clusters": len(uncovered),
            "workflow_files": sum(len(b["members"]) for b in rows),
            "errors": len(errors),
        },
        "enrolled_families": sorted({m["family_slug"] for m in enrolled.values()}),
        "clusters": rows,
        "errors": errors[:50],
    }


def _sample_videos_for_fingerprint(
    fingerprint: str,
    *,
    exemplar_index: Optional[Dict[str, Any]] = None,
    index_path: Path = DEFAULT_EXEMPLAR_INDEX,
    limit: int = SAMPLE_TARGET,
) -> List[str]:
    """Return up to ``limit`` mp4 paths for this structural fingerprint (not by basename)."""
    fp = str(fingerprint or "").strip()
    if not fp or limit <= 0:
        return []
    idx = exemplar_index
    if idx is None:
        idx = load_exemplar_index(index_path)
    bucket = (idx.get("fingerprints") or {}).get(fp) if isinstance(idx, dict) else None
    paths: List[str] = []
    if isinstance(bucket, list):
        paths = [str(x) for x in bucket]
    elif isinstance(bucket, dict):
        raw = bucket.get("samples") or bucket.get("paths") or []
        if isinstance(raw, list):
            paths = [str(x) for x in raw]
    out: List[str] = []
    for raw in paths:
        p = Path(str(raw))
        if p.is_file():
            out.append(str(p))
        if len(out) >= limit:
            break
    return out


def _exemplar_bucket_paths(bucket: Any) -> List[str]:
    """Normalize v2 list / v3 dict fingerprint buckets to a path list."""
    if isinstance(bucket, list):
        return [str(x) for x in bucket if x]
    if isinstance(bucket, dict):
        raw = bucket.get("paths") or bucket.get("samples") or []
        if isinstance(raw, list):
            return [str(x) for x in raw if x]
    return []


def _match_class_maps(
    *,
    shapes_dir: Path = DEFAULT_DATA / "shapes",
    catalog_dir: Path = DEFAULT_CATALOG,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Topology fingerprint → enrolled / catalog meta (for exemplar annotations)."""
    enrolled = _enrolled_fingerprints(shapes_dir)
    catalog: Dict[str, Dict[str, Any]] = {}
    if catalog_dir.is_dir():
        for path in sorted(catalog_dir.glob("*-readable.json")):
            if _is_noise(path):
                continue
            wf = load_workflow_json(path)
            if not wf or not isinstance(wf.get("nodes"), list):
                continue
            try:
                fp = graph_fingerprint_topology(wf)
            except Exception:
                continue
            catalog.setdefault(fp, {"name": path.name, "path": str(path)})
    return enrolled, catalog


def _workflow_from_png(png: Path) -> Optional[Dict[str, Any]]:
    try:
        chunks = read_png_text_chunks(png)
    except Exception:
        return None
    _prompt, wf = extract_prompt_workflow_from_png_chunks(chunks)
    if isinstance(wf, dict) and isinstance(wf.get("nodes"), list):
        return wf
    return None


def _prompt_and_workflow_from_png(png: Path) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    try:
        chunks = read_png_text_chunks(png)
    except Exception:
        return None, None
    prompt, wf = extract_prompt_workflow_from_png_chunks(chunks)
    pr = prompt if isinstance(prompt, dict) else None
    ui = wf if isinstance(wf, dict) and isinstance(wf.get("nodes"), list) else None
    return pr, ui


def _normalize_source_key(raw: str) -> str:
    name = Path(str(raw or "").replace("\\", "/").strip()).name.strip()
    return name.lower() if name else ""


def _primary_source_from_embed(
    prompt: Optional[Dict[str, Any]], workflow: Optional[Dict[str, Any]]
) -> Optional[Dict[str, str]]:
    """Pick primary input media: prefer LoadImage*, else LoadVideo*/VHS_Load*."""
    candidates: List[Tuple[int, str, str]] = []  # priority, kind, path

    def _add(node_type: str, media: str) -> None:
        media = str(media or "").strip()
        if not media:
            return
        nt = str(node_type or "")
        base = nt.split("|", 1)[0]
        if nt in _SOURCE_IMAGE_TYPES or base in _SOURCE_IMAGE_TYPES or "LoadImage" in nt:
            candidates.append((0, "image", media))
        elif nt in _SOURCE_VIDEO_TYPES or base in _SOURCE_VIDEO_TYPES or "LoadVideo" in nt or nt.startswith("VHS_Load"):
            candidates.append((1, "video", media))

    if isinstance(prompt, dict):
        for _nid, node in prompt.items():
            if not isinstance(node, dict):
                continue
            ct = str(node.get("class_type") or "")
            inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
            for key in ("image", "video", "video_path", "path", "filename"):
                val = inputs.get(key)
                if isinstance(val, str) and val.strip():
                    _add(ct, val)
                    break

    if isinstance(workflow, dict):
        for node in workflow.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            nt = str(node.get("type") or node.get("class_type") or "")
            widgets = node.get("widgets_values")
            if isinstance(widgets, list) and widgets and isinstance(widgets[0], str):
                _add(nt, widgets[0])
            elif isinstance(widgets, dict):
                for key in ("image", "video", "video_path"):
                    val = widgets.get(key)
                    if isinstance(val, str) and val.strip():
                        _add(nt, val)
                        break

    if not candidates:
        return None
    candidates.sort(key=lambda t: (t[0], t[2].lower()))
    _prio, kind, media = candidates[0]
    key = _normalize_source_key(media)
    if not key:
        return None
    return {"key": key, "label": Path(media).name, "kind": kind, "raw": media}


def build_exemplar_index(
    *,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    index_path: Path = DEFAULT_EXEMPLAR_INDEX,
    per_fp: int = SAMPLE_TARGET,
    stride: int = 1,
    years: Optional[List[str]] = None,
    shapes_dir: Path = DEFAULT_DATA / "shapes",
    catalog_dir: Path = DEFAULT_CATALOG,
) -> Dict[str, Any]:
    """
    Scan ``output/og/**/*.png`` workflow embeds → topology fingerprint → sibling ``.mp4``.

    Naming is ignored: differently named dumps with the same graph topology share a bucket.
    Stores **all** paths per fingerprint (gallery), plus a short ``samples`` preview list.
    """
    og = Path(output_root) / "og"
    year_prefixes = tuple(years) if years else None
    pngs: List[Path] = []
    if og.is_dir():
        for day in sorted(og.iterdir()):
            if not day.is_dir() or day.name.startswith("_"):
                continue
            if year_prefixes and not any(day.name.startswith(y) for y in year_prefixes):
                continue
            if not _DATE_DIR_RE.match(day.name):
                continue
            for p in day.glob("*.png"):
                pngs.append(p)
            hourly = day / "hourly"
            if hourly.is_dir():
                for p in hourly.glob("*.png"):
                    pngs.append(p)

    stride = max(1, int(stride))
    scanned = pngs[::stride]
    buckets: Dict[str, List[Tuple[float, str]]] = defaultdict(list)
    seen_mp4: Dict[str, Set[str]] = defaultdict(set)
    # path → source meta (for gallery enrichment)
    path_sources: Dict[str, Dict[str, str]] = {}
    # source_key → list of (mtime, mp4, fingerprint)
    source_rows: Dict[str, List[Tuple[float, str, str]]] = defaultdict(list)
    source_meta: Dict[str, Dict[str, str]] = {}
    stats = {
        "png_candidates": len(pngs),
        "png_scanned": len(scanned),
        "with_workflow": 0,
        "with_mp4": 0,
        "with_source": 0,
        "errors": 0,
    }

    for png in scanned:
        prompt, wf = _prompt_and_workflow_from_png(png)
        if not wf:
            continue
        try:
            fp = graph_fingerprint_topology(wf)
        except Exception:
            stats["errors"] += 1
            continue
        stats["with_workflow"] += 1
        mp4 = png.with_suffix(".mp4")
        if not mp4.is_file():
            continue
        try:
            key = str(mp4.resolve())
        except Exception:
            key = str(mp4)
        if key in seen_mp4[fp]:
            continue
        seen_mp4[fp].add(key)
        try:
            mtime = mp4.stat().st_mtime
        except Exception:
            mtime = 0.0
        buckets[fp].append((mtime, key))
        stats["with_mp4"] += 1

        src = _primary_source_from_embed(prompt, wf)
        if src:
            stats["with_source"] += 1
            path_sources[key] = src
            sk = src["key"]
            source_meta.setdefault(sk, {"key": sk, "label": src["label"], "kind": src["kind"]})
            source_rows[sk].append((mtime, key, fp))

    enrolled, catalog = _match_class_maps(shapes_dir=shapes_dir, catalog_dir=catalog_dir)
    sample_n = max(1, int(per_fp))
    fingerprints: Dict[str, Dict[str, Any]] = {}
    class_counts = {"enrolled": 0, "catalog_only": 0, "unmatched": 0}
    for fp, rows in buckets.items():
        rows.sort(key=lambda t: t[0], reverse=True)
        paths = [path for _, path in rows]
        if fp in enrolled:
            match_class = "enrolled"
            label = enrolled[fp].get("family_slug")
        elif fp in catalog:
            match_class = "catalog_only"
            label = catalog[fp].get("name")
        else:
            match_class = "unmatched"
            label = None
        class_counts[match_class] = class_counts.get(match_class, 0) + 1
        fingerprints[fp] = {
            "fingerprint": fp,
            "match_class": match_class,
            "label": label,
            "total_count": len(paths),
            "samples": paths[:sample_n],
            "paths": paths,
            "path_sources": {p: path_sources[p] for p in paths if p in path_sources},
        }

    sources: Dict[str, Dict[str, Any]] = {}
    for sk, rows in source_rows.items():
        rows.sort(key=lambda t: t[0], reverse=True)
        meta = source_meta.get(sk) or {"key": sk, "label": sk, "kind": "unknown"}
        by_fp: Dict[str, int] = defaultdict(int)
        out_paths: List[str] = []
        for _mt, path, fp in rows:
            out_paths.append(path)
            by_fp[fp] += 1
        sources[sk] = {
            **meta,
            "total_count": len(out_paths),
            "bucket_count": len(by_fp),
            "by_fingerprint": dict(sorted(by_fp.items(), key=lambda kv: -kv[1])),
            "paths": out_paths,
            "samples": out_paths[:sample_n],
        }

    payload = {
        "schema_version": "comfyui-runpod.family-discovery-exemplars.v4",
        "fingerprint_kind": "topology",
        "generated_at": _utc(),
        "output_root": str(output_root),
        "per_fp": int(per_fp),
        "stride": stride,
        "years": list(year_prefixes) if year_prefixes else None,
        "counts": {
            **stats,
            "fingerprints": len(fingerprints),
            "exemplars": sum(int(b.get("total_count") or 0) for b in fingerprints.values()),
            "sample_exemplars": sum(len(b.get("samples") or []) for b in fingerprints.values()),
            "sources": len(sources),
            "by_match_class": class_counts,
        },
        "fingerprints": fingerprints,
        "sources": sources,
    }
    index_path = Path(index_path)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    payload["index_path"] = str(index_path)
    return payload


def load_exemplar_index(index_path: Path = DEFAULT_EXEMPLAR_INDEX) -> Dict[str, Any]:
    path = Path(index_path)
    if not path.is_file():
        return {"fingerprints": {}, "ok": False, "error": "missing", "path": str(path)}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"fingerprints": {}, "ok": False, "error": str(e), "path": str(path)}
    if not isinstance(obj, dict):
        return {"fingerprints": {}, "ok": False, "error": "invalid", "path": str(path)}
    if not isinstance(obj.get("fingerprints"), dict):
        obj["fingerprints"] = {}
    obj["ok"] = True
    obj["path"] = str(path)
    return obj


def _next_prop_id(existing_ids: Set[str]) -> str:
    n = 1
    while True:
        pid = f"prop_{n:03d}"
        if pid not in existing_ids:
            return pid
        n += 1
        if n > 999:
            raise RuntimeError("prop id space exhausted (prop_001–prop_999)")


def _io_guess_from_sample_mp4(mp4_path: str) -> Dict[str, Any]:
    png = Path(mp4_path).with_suffix(".png")
    if not png.is_file():
        return {}
    wf = _workflow_from_png(png)
    if not wf:
        return {}
    try:
        return guess_io_from_workflow(wf) or {}
    except Exception:
        return {}


def backfill_proposals_from_buckets(
    *,
    out_dir: Path = DEFAULT_OUT,
    exemplar_index_path: Path = DEFAULT_EXEMPLAR_INDEX,
    sample_limit: int = SAMPLE_TARGET,
    include_enrolled: bool = False,
    match_classes: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Create/update ``prop_*`` cards from exemplar video buckets (not workflow clusters).

    Preserves operator fields on existing cards for the same fingerprint.
    Default classes: ``unmatched`` + ``catalog_only`` (skip enrolled unless requested).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    exemplar_idx = load_exemplar_index(exemplar_index_path)
    fps_map = exemplar_idx.get("fingerprints") if isinstance(exemplar_idx.get("fingerprints"), dict) else {}

    wanted = {str(x).strip().lower() for x in (match_classes or ("unmatched", "catalog_only")) if str(x).strip()}
    if include_enrolled:
        wanted.add("enrolled")

    # Load existing props → fingerprint map (preserve judgments).
    existing_by_fp: Dict[str, Dict[str, Any]] = {}
    existing_ids: Set[str] = set()
    for path in sorted(out_dir.glob("prop_*.json")):
        try:
            card = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(card, dict):
            continue
        pid = str(card.get("id") or path.stem).strip()
        existing_ids.add(pid)
        fp = str(card.get("fingerprint") or "").strip()
        if fp and fp not in existing_by_fp:
            existing_by_fp[fp] = card

    buckets: List[Dict[str, Any]] = []
    for fp, bucket in fps_map.items():
        paths = _exemplar_bucket_paths(bucket)
        if isinstance(bucket, dict):
            match_class = str(bucket.get("match_class") or "unmatched")
            label = bucket.get("label")
            total = int(bucket.get("total_count") or len(paths))
        else:
            match_class = "unmatched"
            label = None
            total = len(paths)
        if total <= 0:
            continue
        if match_class not in wanted:
            continue
        buckets.append(
            {
                "fingerprint": str(fp),
                "match_class": match_class,
                "label": label,
                "total_count": total,
                "paths": paths,
            }
        )
    buckets.sort(key=lambda b: (-int(b["total_count"]), str(b["match_class"]), str(b["fingerprint"])))

    created = 0
    updated = 0
    index_rows: List[Dict[str, Any]] = []
    preserve_keys = (
        "status",
        "proposed_family_slug",
        "nearest_enrolled",
        "operator_decision",
        "operator_notes",
        "enrolled_at",
        "enrolled_shape",
        "quarantine_notes",
    )

    for b in buckets:
        fp = b["fingerprint"]
        paths: List[str] = list(b["paths"] or [])
        samples = paths[: max(1, int(sample_limit))]
        guess = _io_guess_from_sample_mp4(samples[0]) if samples else {}
        rep_path = samples[0] if samples else None
        rep = {
            "source": "og_embed",
            "path": rep_path,
            "name": Path(rep_path).name if rep_path else None,
            "stem": None,
            "node_count": None,
        }
        members = [
            {
                "source": "og_embed",
                "path": p,
                "name": Path(p).name,
                "stem": None,
                "node_count": None,
            }
            for p in samples[:12]
        ]

        prev = existing_by_fp.get(fp)
        if prev:
            prop_id = str(prev.get("id") or "").strip()
            if not prop_id:
                prop_id = _next_prop_id(existing_ids)
                existing_ids.add(prop_id)
            updated += 1
        else:
            prop_id = _next_prop_id(existing_ids)
            existing_ids.add(prop_id)
            created += 1
            prev = {}

        card: Dict[str, Any] = {
            "id": prop_id,
            "status": "pending_review",
            "proposed_family_slug": None,
            "fingerprint": fp,
            "match_class": b["match_class"],
            "label": b.get("label"),
            "video_count": b["total_count"],
            "io_guess": guess.get("io_class"),
            "primary_input_guess": guess.get("primary_input"),
            "input_profile_guess": guess.get("input_profile"),
            "chain_role_guess": guess.get("chain_role_guess"),
            "member_count": len(members),
            "representative": rep,
            "members": members,
            "sample_videos": samples,
            "sample_source": "fingerprint_exemplars",
            "source": "bucket_backfill",
            "quarantine_notes": [],
            "nearest_enrolled": None,
            "operator_decision": None,
            "operator_notes": None,
        }
        for k in preserve_keys:
            if prev.get(k) is not None:
                card[k] = prev[k]

        path = out_dir / f"{prop_id}.json"
        path.write_text(json.dumps(card, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        md_lines = [
            f"# {prop_id}",
            "",
            f"- **status:** `{card.get('status')}`",
            f"- **IO guess:** `{card.get('io_guess')}` · profile `{card.get('input_profile_guess')}` · role `{card.get('chain_role_guess')}`",
            f"- **fingerprint:** `{fp[:16]}…`",
            f"- **match_class:** `{card.get('match_class')}`",
            f"- **video_count:** {card.get('video_count')}",
            f"- **label:** `{card.get('label') or '—'}`",
            f"- **representative:** `{rep.get('name') or '—'}`",
            "",
            "## Sample videos (by fingerprint)",
            "",
        ]
        md_lines.extend(f"- `{v}`" for v in samples)
        md_lines.extend(
            [
                "",
                "## Operator gate",
                "",
                "- [ ] new family — set `proposed_family_slug`",
                "- [ ] merge into existing — note target slug",
                "- [ ] skip",
                "",
            ]
        )
        (out_dir / f"{prop_id}.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")

        index_rows.append(
            {
                "id": prop_id,
                "io_guess": card.get("io_guess"),
                "members": card.get("member_count"),
                "representative": rep.get("name"),
                "status": card.get("status"),
                "fingerprint": fp,
                "match_class": card.get("match_class"),
                "video_count": card.get("video_count"),
                "sample_count": len(samples),
                "sample_target": int(sample_limit),
            }
        )

    # Keep any existing non-bucket props that weren't rewritten? Prefer INDEX = bucket-backed only.
    # Still list orphans (old cluster props with no bucket) at the end as pending with 0 videos —
    # operator can skip. For clarity, INDEX is only backfilled rows (sorted by video_count).
    index_rows.sort(
        key=lambda r: (
            0 if str(r.get("status") or "") == "pending_review" else 1,
            -int(r.get("video_count") or 0),
            str(r.get("id") or ""),
        )
    )

    summary = {
        "schema_version": "comfyui-runpod.family-discovery-index.v1",
        "generated_at": _utc(),
        "source": "bucket_backfill",
        "cluster_report": "cluster_report.json",
        "proposals": index_rows,
        "covered_clusters": None,
        "uncovered_clusters": None,
        "bucket_proposal_counts": {
            "created": created,
            "updated": updated,
            "total": len(index_rows),
            "match_classes": sorted(wanted),
        },
        "exemplar_index": str(exemplar_index_path),
        "review_instructions": (
            "Proposals are backfilled from og video buckets (topology fingerprints with mp4s). "
            "Watch samples, set status to new_family|merge|skip, then enroll via CLI."
        ),
    }
    (out_dir / "INDEX.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    review_lines = [
        "# Family discovery — operator review",
        "",
        f"Generated `{summary['generated_at']}` from **video buckets** (not workflow-only clusters).",
        "",
        f"- Proposals: **{len(index_rows)}** (created {created}, updated {updated})",
        f"- Classes: {', '.join(sorted(wanted))}",
        f"- Samples: up to **{sample_limit}** per prop from exemplar index",
        "",
        "## Proposal index",
        "",
        "| id | class | videos | samples | representative | status |",
        "|----|-------|--------|---------|----------------|--------|",
    ]
    for row in index_rows:
        review_lines.append(
            f"| {row['id']} | {row.get('match_class') or '—'} | {row.get('video_count') or 0} | "
            f"{row.get('sample_count') or 0} | `{row.get('representative') or '—'}` | {row.get('status')} |"
        )
    review_lines.append("")
    (out_dir / "REVIEW.md").write_text("\n".join(review_lines) + "\n", encoding="utf-8")

    return {
        "ok": True,
        "created": created,
        "updated": updated,
        "total": len(index_rows),
        "out_dir": str(out_dir),
        "match_classes": sorted(wanted),
    }


def write_proposal_cards(
    report: Dict[str, Any],
    out_dir: Path,
    *,
    output_root: Path,
    max_props: int = 40,
    exemplar_index_path: Path = DEFAULT_EXEMPLAR_INDEX,
    sample_limit: int = SAMPLE_TARGET,
) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    uncovered = [c for c in report["clusters"] if not c.get("covered_by")]

    def rank(c: Dict[str, Any]) -> Tuple[int, int, str]:
        sources = {m["source"] for m in c["members"]}
        weight = (2 if "catalog" in sources else 0) + (1 if "candidate" in sources else 0)
        return (-weight, -len(c["members"]), c["fingerprint"])

    uncovered.sort(key=rank)
    written: List[Path] = []
    index_rows: List[Dict[str, Any]] = []
    exemplar_idx = load_exemplar_index(exemplar_index_path)

    for i, cluster in enumerate(uncovered[:max_props], start=1):
        prop_id = f"prop_{i:03d}"
        guess = cluster.get("io_guess") or {}
        members = cluster["members"]
        rep = members[0]
        fp = str(cluster.get("fingerprint") or "")
        videos = _sample_videos_for_fingerprint(
            fp, exemplar_index=exemplar_idx, limit=sample_limit
        )
        card = {
            "id": prop_id,
            "status": "pending_review",
            "proposed_family_slug": None,
            "fingerprint": fp,
            "io_guess": guess.get("io_class"),
            "primary_input_guess": guess.get("primary_input"),
            "input_profile_guess": guess.get("input_profile"),
            "chain_role_guess": guess.get("chain_role_guess"),
            "member_count": len(members),
            "representative": rep,
            "members": members[:12],
            "sample_videos": videos,
            "sample_source": "fingerprint_exemplars",
            "quarantine_notes": [],
            "nearest_enrolled": None,
            "operator_decision": None,
            "operator_notes": None,
        }
        path = out_dir / f"{prop_id}.json"
        path.write_text(json.dumps(card, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        md = out_dir / f"{prop_id}.md"
        lines = [
            f"# {prop_id}",
            "",
            f"- **status:** `{card['status']}`",
            f"- **IO guess:** `{card['io_guess']}` · profile `{card['input_profile_guess']}` · role `{card['chain_role_guess']}`",
            f"- **fingerprint:** `{fp[:16]}…`" if fp else "- **fingerprint:** —",
            f"- **members:** {card['member_count']}",
            f"- **representative:** `{rep['path']}`",
            "",
            "## Sample videos (by fingerprint, not output name)",
            "",
        ]
        if videos:
            lines.extend(f"- `{v}`" for v in videos)
        else:
            lines.append(
                "_none in exemplar index — run "
                "`shape_factory_family_discovery.py index-exemplars`_"
            )
        lines.extend(["", "## Members", ""])
        for m in members[:12]:
            lines.append(f"- [{m['source']}] `{m['path']}`")
        lines.extend(
            [
                "",
                "## Operator gate",
                "",
                "- [ ] new family — set `proposed_family_slug`",
                "- [ ] merge into existing — note target slug",
                "- [ ] skip",
                "",
            ]
        )
        md.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.extend([path, md])
        index_rows.append(
            {
                "id": prop_id,
                "io_guess": card["io_guess"],
                "members": card["member_count"],
                "representative": rep["name"],
                "status": card["status"],
                "fingerprint": fp,
                "sample_count": len(videos),
            }
        )

    summary = {
        "schema_version": "comfyui-runpod.family-discovery-index.v0",
        "generated_at": _utc(),
        "cluster_report": "cluster_report.json",
        "proposals": index_rows,
        "covered_clusters": report["counts"]["covered_clusters"],
        "uncovered_clusters": report["counts"]["uncovered_clusters"],
        "exemplar_index": str(exemplar_index_path),
        "review_instructions": (
            "For each prop_NNN: watch fingerprint-matched sample videos (not basename "
            "cousins), then set status to new_family|merge|skip and fill "
            "proposed_family_slug or merge target. Then run: "
            "python3 shape_factory_family_discovery.py enroll --prop prop_NNN ..."
        ),
    }
    (out_dir / "INDEX.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out_dir / "cluster_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out_dir / "REVIEW.md").write_text(
        "\n".join(
            [
                "# Family discovery — operator review",
                "",
                f"Generated `{summary['generated_at']}`.",
                "",
                f"- Covered clusters (already enrolled): **{summary['covered_clusters']}**",
                f"- Uncovered clusters with proposals: **{len(index_rows)}** "
                f"(of {summary['uncovered_clusters']} uncovered)",
                f"- Sample videos: up to **{sample_limit}** per prop via fingerprint "
                f"exemplar index (`{Path(exemplar_index_path).name}`), not output naming.",
                "",
                "## How to review",
                "",
                "**UI (preferred):** open Experiments UI → Workflow Explorer → **Family review** tab.",
                "",
                "**Manual / CLI:**",
                "",
                "1. Open each `prop_NNN.md` and watch listed sample videos.",
                "2. Decide: **new family** / **merge** into an enrolled slug / **skip**.",
                "3. Edit the matching `prop_NNN.json`: set `status`, `proposed_family_slug` "
                "(or `nearest_enrolled` for merge), and `operator_notes`.",
                "4. For approved new families, run enroll.",
                "",
                "## Proposal index",
                "",
                "| id | IO | members | samples | representative | status |",
                "|----|----|---------|---------|----------------|--------|",
            ]
            + [
                f"| {r['id']} | {r.get('io_guess') or '—'} | {r['members']} | "
                f"{r.get('sample_count', 0)} | `{r['representative']}` | {r['status']} |"
                for r in index_rows
            ]
            + ["", "No families are auto-enrolled. Naming is the human gate.", ""]
        ),
        encoding="utf-8",
    )
    written.append(out_dir / "REVIEW.md")
    return written



def enroll_from_prop(
    *,
    prop_path: Path,
    slug: str,
    shapes_dir: Path,
    pools_dir: Path,
    catalog_dir: Path,
    io_class: Optional[str] = None,
    chain_role: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Scaffold shape+pools for an approved proposal (does not auto-fix templates)."""
    card = json.loads(prop_path.read_text(encoding="utf-8"))
    if card.get("status") not in {"new_family", "approved"}:
        raise RuntimeError(f"{prop_path.name} status must be new_family/approved (got {card.get('status')!r})")
    rep = card.get("representative") or {}
    src = Path(str(rep.get("path") or ""))
    if not src.is_file():
        raise RuntimeError(f"representative missing: {src}")
    io = io_class or card.get("io_guess") or "I2V"
    role = chain_role or card.get("chain_role_guess") or "standalone"
    profile = {
        "I2V": "still_prompt",
        "V2V": "video_prompt",
        "VI2V": "video_identity_still_prompt",
    }.get(str(io).upper(), "still_prompt")
    primary = "still" if profile == "still_prompt" else "video"
    shape_id = {
        "still_prompt": "wan-i2v-still+prompt",
        "video_prompt": "wan-v2v-source+prompt",
        "video_identity_still_prompt": "wan-vi2v-source+identity_still+prompt",
    }[profile]

    now = datetime.now(tz=timezone.utc)
    stem = format_catalog_stem(
        slug,
        date=now.strftime("%Y-%m-%d"),
        time=now.strftime("%H%M%S"),
        io_class=io,
        seq=1,
    )
    catalog_name = f"{stem}-readable.json"
    catalog_path = catalog_dir / catalog_name
    shape_path = shapes_dir / f"{slug}.shape.yaml"
    family_pools = pools_dir / slug
    pools_path = family_pools / "pools.yaml"

    wf = load_workflow_json(src) if src.suffix == ".json" and "candidate" not in src.name else None
    if wf is None and "candidate" in src.name:
        wf = _load_candidate_workflow(src)
    if wf is None:
        wf = load_workflow_json(src)
    if not wf:
        raise RuntimeError(f"cannot load workflow from {src}")

    fp = graph_fingerprint_topology(wf)
    # Minimal requires — operator must wire node_ids before generate
    if profile == "still_prompt":
        requires = [
            {
                "slot": "source_still",
                "role": "A",
                "media": "image",
                "binding": {"type": "load_image", "node_id": 88, "field": "image"},
            },
            {
                "slot": "prompt_profile",
                "role": "C",
                "media": "text",
                "binding": {
                    "type": "prompt_bundle",
                    "positive": {"node_id": 408, "widget_index": 0},
                    "negative": {"node_id": 409, "widget_index": 0},
                },
            },
        ]
    elif profile == "video_identity_still_prompt":
        requires = [
            {
                "slot": "source_video",
                "role": "B",
                "media": "video",
                "binding": {"type": "vhs_load_video_path", "node_id": 377, "field": "video"},
            },
            {
                "slot": "identity_anchor",
                "role": "A",
                "media": "image",
                "binding": {"type": "load_image", "node_id": 500, "field": "image"},
            },
            {
                "slot": "prompt_profile",
                "role": "C",
                "media": "text",
                "binding": {
                    "type": "prompt_bundle",
                    "positive": {"node_id": 380, "widget_index": 0},
                    "negative": {"node_id": 17, "widget_index": 0},
                },
            },
        ]
    else:
        requires = [
            {
                "slot": "source_video",
                "role": "B",
                "media": "video",
                "binding": {"type": "vhs_load_video_path", "node_id": 377, "field": "video"},
            },
            {
                "slot": "prompt_profile",
                "role": "C",
                "media": "text",
                "binding": {
                    "type": "prompt_bundle",
                    "positive": {"node_id": 380, "widget_index": 0},
                    "negative": {"node_id": 17, "widget_index": 0},
                },
            },
        ]

    shape_doc = {
        "schema_version": "comfyui-runpod.shape.v0",
        "shape_id": shape_id,
        "family_slug": slug,
        "primary_input": primary,
        "input_profile": profile,
        "chain_role": role,
        "io_class": str(io).upper(),
        "graph_hash": fp,
        "template": str(catalog_path),
        "requires": requires,
        "produces": [
            {
                "slot": "final_video",
                "role": "X",
                "media": "video",
                "binding": {"node_id": 80 if profile != "still_prompt" else 398, "node_type": "VHS_VideoCombine"},
            }
        ],
        "deposits": {"final_video": {"to_pool": f"pool:{slug}_X_og"}},
        "output_prefix_root": f"og/%date:yyyy-MM-dd%/{slug}_shape",
        "rules": [],
    }

    result = {
        "slug": slug,
        "catalog_path": str(catalog_path),
        "shape_path": str(shape_path),
        "pools_path": str(pools_path),
        "stem": stem,
        "dry_run": dry_run,
        "validate_errors": validate_shape_document(shape_doc, check_start_image=False),
    }
    if dry_run:
        return result

    catalog_dir.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Re-validate with template on disk
    result["validate_errors"] = validate_shape_document(shape_doc, check_start_image=True)

    import yaml

    shape_path.write_text(yaml.safe_dump(shape_doc, sort_keys=False), encoding="utf-8")
    family_pools.mkdir(parents=True, exist_ok=True)
    (family_pools / "prompts").mkdir(exist_ok=True)
    pools_doc = {
        "schema_version": "comfyui-runpod.pools.v0",
        "shape": str(shape_path.resolve()),
        "pools": {},
        "deposit_pools": {
            f"{slug}_X_og": {
                "slot": "final_video",
                "description": f"{slug} final_video",
                "seed_members": [],
            }
        },
    }
    for req in requires:
        slot = req["slot"]
        pools_doc["pools"][slot] = {"slot": slot, "members": []}
    pools_path.write_text(yaml.safe_dump(pools_doc, sort_keys=False), encoding="utf-8")

    card["status"] = "enrolled"
    card["proposed_family_slug"] = slug
    card["enrolled_at"] = _utc()
    card["enrolled_shape"] = str(shape_path)
    prop_path.write_text(json.dumps(card, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("cluster", help="Cluster corpus and write proposal cards")
    c.add_argument("--shapes-dir", type=Path, default=DEFAULT_DATA / "shapes")
    c.add_argument("--catalog-dir", type=Path, default=DEFAULT_CATALOG)
    c.add_argument("--user-dir", type=Path, default=DEFAULT_USER_WF)
    c.add_argument("--candidates-dir", type=Path, default=DEFAULT_CANDIDATES)
    c.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    c.add_argument("--write", type=Path, default=DEFAULT_OUT)
    c.add_argument("--max-props", type=int, default=40)
    c.add_argument("--exemplar-index", type=Path, default=DEFAULT_EXEMPLAR_INDEX)
    c.add_argument("--sample-limit", type=int, default=SAMPLE_TARGET)

    ix = sub.add_parser(
        "index-exemplars",
        help="Build fingerprint→mp4 exemplar index from output/og PNG embeds (ignores filenames)",
    )
    ix.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    ix.add_argument("--index", type=Path, default=DEFAULT_EXEMPLAR_INDEX)
    ix.add_argument("--per-fp", type=int, default=SAMPLE_TARGET)
    ix.add_argument("--stride", type=int, default=1, help="Scan every Nth PNG (1=all)")
    ix.add_argument(
        "--years",
        nargs="*",
        default=None,
        help="Optional date-dir prefixes e.g. 2025 2026 (default: all)",
    )

    bp = sub.add_parser(
        "backfill-proposals",
        help="Create/update prop_* cards from og video buckets (preserves judgments)",
    )
    bp.add_argument("--write", type=Path, default=DEFAULT_OUT)
    bp.add_argument("--exemplar-index", type=Path, default=DEFAULT_EXEMPLAR_INDEX)
    bp.add_argument("--sample-limit", type=int, default=SAMPLE_TARGET)
    bp.add_argument(
        "--include-enrolled",
        action="store_true",
        help="Also create proposals for enrolled-matching buckets",
    )
    bp.add_argument(
        "--classes",
        nargs="*",
        default=None,
        help="Match classes to include (default: unmatched catalog_only)",
    )

    e = sub.add_parser("enroll", help="Scaffold shape+pools from an approved prop card")
    e.add_argument("--prop", required=True, help="prop id (prop_001) or path to prop JSON")
    e.add_argument("--slug", required=True, help="family_slug to enroll")
    e.add_argument("--discovery-dir", type=Path, default=DEFAULT_OUT)
    e.add_argument("--shapes-dir", type=Path, default=DEFAULT_DATA / "shapes")
    e.add_argument("--pools-dir", type=Path, default=DEFAULT_DATA / "pools")
    e.add_argument("--catalog-dir", type=Path, default=DEFAULT_CATALOG)
    e.add_argument("--io-class", default=None)
    e.add_argument("--chain-role", default=None)
    e.add_argument("--dry-run", action="store_true")

    v = sub.add_parser("validate-shapes", help="Validate enrolled shape vocabulary + start_image")
    v.add_argument("--shapes-dir", type=Path, default=DEFAULT_DATA / "shapes")

    args = ap.parse_args(argv)
    if args.cmd == "cluster":
        report = cluster_corpus(
            shapes_dir=args.shapes_dir,
            catalog_dir=args.catalog_dir,
            user_dir=args.user_dir,
            candidates_dir=args.candidates_dir,
        )
        write_proposal_cards(
            report,
            args.write,
            output_root=args.output_root,
            max_props=args.max_props,
            exemplar_index_path=args.exemplar_index,
            sample_limit=args.sample_limit,
        )
        print(json.dumps(report["counts"], indent=2))
        print(f"wrote proposals under {args.write}")
        return 0

    if args.cmd == "index-exemplars":
        payload = build_exemplar_index(
            output_root=args.output_root,
            index_path=args.index,
            per_fp=args.per_fp,
            stride=args.stride,
            years=args.years,
        )
        print(json.dumps(payload.get("counts"), indent=2))
        print(f"wrote {payload.get('index_path')}")
        return 0

    if args.cmd == "backfill-proposals":
        result = backfill_proposals_from_buckets(
            out_dir=args.write,
            exemplar_index_path=args.exemplar_index,
            sample_limit=args.sample_limit,
            include_enrolled=bool(args.include_enrolled),
            match_classes=args.classes,
        )
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    if args.cmd == "enroll":
        prop = Path(args.prop)
        if not prop.is_file():
            prop = args.discovery_dir / f"{args.prop}.json"
        result = enroll_from_prop(
            prop_path=prop,
            slug=args.slug,
            shapes_dir=args.shapes_dir,
            pools_dir=args.pools_dir,
            catalog_dir=args.catalog_dir,
            io_class=args.io_class,
            chain_role=args.chain_role,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, indent=2))
        return 0

    if args.cmd == "validate-shapes":
        bad = 0
        for path in sorted(args.shapes_dir.glob("*.shape.yaml")):
            doc = load_yaml(path)
            errs = validate_shape_document(doc, check_start_image=True)
            if errs:
                bad += 1
                print(f"FAIL {path.name}")
                for e in errs:
                    print(f"  - {e}")
            else:
                print(f"OK   {path.name}")
        return 1 if bad else 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
