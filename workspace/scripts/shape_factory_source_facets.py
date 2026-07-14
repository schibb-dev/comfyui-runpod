#!/usr/bin/env python3
"""
Structured similarity facets on seed *input* videos (not output prompt tags).

Axes (v1): appearance | expression | identity
  - Hold one axis per hourly tick (rotate); randomize sources within that family.
  - Identity is a similarity degree, not lineage (editorial subject/cluster labels;
    face embeddings are a later provider).

Store: ``<output>/_status/source_facets.json`` keyed by source basename.
Catalog: YAML editorial bootstrap (``source_facet_catalog.yaml``).
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

FACETS_SCHEMA_VERSION = 1
SOURCE_FACETS_BASENAME = "source_facets.json"
HOLD_AXES: Tuple[str, ...] = ("appearance", "expression", "identity")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_facet_value(raw: str) -> str:
    t = str(raw or "").strip().lower()
    t = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in t)
    return t.strip("_")


def source_key(path_or_name: str) -> str:
    """Canonical key for a source video: basename without directory."""
    return Path(str(path_or_name or "").replace("\\", "/")).name


def default_source_facets_path(og_root: Path) -> Path:
    return og_root.resolve().parent / "_status" / SOURCE_FACETS_BASENAME


def default_catalog_path(repo_or_workspace: Optional[Path] = None) -> Path:
    """Prefer workspace/source_facet_catalog.yaml under the repo."""
    if repo_or_workspace is not None:
        base = Path(repo_or_workspace)
        for cand in (
            base / "source_facet_catalog.yaml",
            base / "workspace" / "source_facet_catalog.yaml",
        ):
            if cand.is_file():
                return cand.resolve()
    here = Path(__file__).resolve()
    # workspace/scripts -> workspace/source_facet_catalog.yaml
    ws = here.parents[1] / "source_facet_catalog.yaml"
    if ws.is_file():
        return ws.resolve()
    return (here.parents[2] / "workspace" / "source_facet_catalog.yaml").resolve()


def hold_axis_for_cursor(cursor: int, *, axes: Sequence[str] = HOLD_AXES) -> str:
    seq = [a for a in axes if a]
    if not seq:
        return "appearance"
    return seq[int(cursor) % len(seq)]


def load_source_facets(path: Path) -> Dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": FACETS_SCHEMA_VERSION, "by_source_key": {}}
    if not isinstance(doc, dict):
        return {"version": FACETS_SCHEMA_VERSION, "by_source_key": {}}
    by = doc.get("by_source_key")
    if not isinstance(by, dict):
        doc["by_source_key"] = {}
    return doc


def lookup_source_facets(
    path_or_name: str,
    facets_doc: Optional[Dict[str, Any]],
) -> Dict[str, List[str]]:
    """Return ``{axis: [values...]}`` for a source path/basename (empty if unknown)."""
    if not facets_doc:
        return {}
    by = facets_doc.get("by_source_key") or {}
    if not isinstance(by, dict):
        return {}
    row = by.get(source_key(path_or_name))
    if not isinstance(row, dict):
        return {}
    facets = row.get("facets") if isinstance(row.get("facets"), dict) else {}
    out: Dict[str, List[str]] = {}
    for axis, vals in facets.items():
        axis_n = normalize_facet_value(str(axis))
        if not axis_n:
            continue
        cleaned: List[str] = []
        if isinstance(vals, str):
            vals = [vals]
        if isinstance(vals, list):
            for v in vals:
                nv = normalize_facet_value(str(v))
                if nv and nv not in cleaned:
                    cleaned.append(nv)
        if cleaned:
            out[axis_n] = cleaned
    return out


def facet_values(facets: Dict[str, List[str]], axis: str) -> Set[str]:
    return set(facets.get(normalize_facet_value(axis)) or [])


def shares_facet_axis(
    a: Dict[str, List[str]],
    b: Dict[str, List[str]],
    axis: str,
) -> bool:
    """True when both have at least one overlapping value on ``axis``."""
    return bool(facet_values(a, axis) & facet_values(b, axis))


def filter_sources_by_hold_axis(
    candidates: Iterable[str],
    *,
    seed_source: str,
    hold_axis: str,
    facets_doc: Optional[Dict[str, Any]],
) -> Tuple[List[str], Dict[str, Any]]:
    """
    Prefer candidates that share ``hold_axis`` values with the seed source.

    If the seed has no values on that axis, or no candidates match, return the
    full candidate list (unconstrained fallback).
    """
    cand_list = [str(c) for c in candidates if str(c or "").strip()]
    seed_facets = lookup_source_facets(seed_source, facets_doc)
    hold_vals = sorted(facet_values(seed_facets, hold_axis))
    meta: Dict[str, Any] = {
        "hold_axis": normalize_facet_value(hold_axis),
        "hold_values": hold_vals,
        "candidate_count_unfiltered": len(cand_list),
        "facet_constrained": False,
    }
    if not hold_vals or not facets_doc:
        meta["candidate_count"] = len(cand_list)
        meta["fallback"] = "no_seed_facet" if not hold_vals else "no_facets_doc"
        return cand_list, meta

    matched = [
        c
        for c in cand_list
        if shares_facet_axis(seed_facets, lookup_source_facets(c, facets_doc), hold_axis)
    ]
    if matched:
        meta["candidate_count"] = len(matched)
        meta["facet_constrained"] = True
        return matched, meta

    meta["candidate_count"] = len(cand_list)
    meta["fallback"] = "no_family_match"
    return cand_list, meta


def _normalize_row_facets(raw: Any) -> Dict[str, List[str]]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, List[str]] = {}
    for axis, vals in raw.items():
        axis_n = normalize_facet_value(str(axis))
        if axis_n not in HOLD_AXES and axis_n not in ("appearance", "expression", "identity"):
            # Allow only known axes in v1 catalog rows (still store if listed).
            pass
        cleaned: List[str] = []
        seq = vals if isinstance(vals, list) else ([vals] if vals is not None else [])
        for v in seq:
            nv = normalize_facet_value(str(v))
            if nv and nv not in cleaned:
                cleaned.append(nv)
        if cleaned:
            out[axis_n] = cleaned
    return out


def build_source_facets_from_catalog(
    catalog: Dict[str, Any],
    *,
    provider: str = "editorial",
) -> Dict[str, Any]:
    """Build ``source_facets.json`` document from a YAML catalog dict."""
    by_source_key: Dict[str, Dict[str, Any]] = {}
    sources = catalog.get("sources") if isinstance(catalog.get("sources"), dict) else {}
    # Also accept list form: [{key, facets}, ...]
    if not sources and isinstance(catalog.get("sources"), list):
        for item in catalog["sources"]:
            if not isinstance(item, dict):
                continue
            key = source_key(str(item.get("key") or item.get("source") or item.get("basename") or ""))
            if not key:
                continue
            sources[key] = item

    for key_raw, row in sources.items():
        key = source_key(str(key_raw))
        if not key:
            continue
        if not isinstance(row, dict):
            continue
        facets = _normalize_row_facets(row.get("facets") or row)
        # If row used top-level axis keys instead of nested facets:
        if not facets:
            facets = _normalize_row_facets(
                {a: row[a] for a in HOLD_AXES if a in row}
            )
        tags = row.get("tags") if isinstance(row.get("tags"), list) else []
        by_source_key[key] = {
            "facets": facets,
            "tags": [normalize_facet_value(str(t)) for t in tags if str(t).strip()],
            "provider": str(row.get("provider") or provider),
            "provisional": bool(row.get("provisional", True)),
            "updated_at": _utc_now(),
            "notes": str(row.get("notes") or "") or None,
        }
        if by_source_key[key]["notes"] is None:
            del by_source_key[key]["notes"]

    return {
        "version": FACETS_SCHEMA_VERSION,
        "provider": provider,
        "updated_at": _utc_now(),
        "stats": {"sources": len(by_source_key)},
        "by_source_key": by_source_key,
    }


def save_source_facets(path: Path, doc: Dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_yaml(path: Path) -> Dict[str, Any]:
    import yaml

    obj = yaml.safe_load(path.read_text(encoding="utf-8"))
    return obj if isinstance(obj, dict) else {}


def cmd_build(args: argparse.Namespace) -> int:
    catalog_path = Path(args.catalog).expanduser().resolve()
    if not catalog_path.is_file():
        print(f"error: catalog not found: {catalog_path}", file=__import__("sys").stderr)
        return 1
    og_root = Path(args.root).expanduser().resolve()
    out = Path(args.out or default_source_facets_path(og_root)).expanduser().resolve()
    catalog = _load_yaml(catalog_path)
    doc = build_source_facets_from_catalog(catalog, provider=str(args.provider))
    save_source_facets(out, doc)
    print(f"Wrote {out}")
    print(f"provider={doc.get('provider')} sources={doc.get('stats', {}).get('sources')}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    og_root = Path(args.root).expanduser().resolve()
    path = Path(args.index or default_source_facets_path(og_root)).expanduser().resolve()
    if not path.is_file():
        print(f"error: source facets not found: {path}", file=__import__("sys").stderr)
        return 1
    doc = load_source_facets(path)
    facets = lookup_source_facets(args.key, doc)
    print(json.dumps({"ok": bool(facets), "key": source_key(args.key), "facets": facets}, indent=2))
    return 0 if facets else 1


def add_source_facets_subparser(sub: argparse._SubParsersAction) -> None:
    sf = sub.add_parser("source-facets", help="Build/query structured facets on seed input videos")
    sf_sub = sf.add_subparsers(dest="source_facets_cmd", required=True)

    build = sf_sub.add_parser("build", help="Build source_facets.json from editorial YAML catalog")
    build.add_argument("--root", default="/home/yuji/comfyui-runpod-data/output/og")
    build.add_argument(
        "--catalog",
        default=str(default_catalog_path()),
        help="YAML catalog path",
    )
    build.add_argument("--provider", default="editorial")
    build.add_argument("--out", default=None)
    build.set_defaults(func=cmd_build)

    show = sf_sub.add_parser("show", help="Show facets for a source basename or path")
    show.add_argument("--root", default="/home/yuji/comfyui-runpod-data/output/og")
    show.add_argument("--index", default=None)
    show.add_argument("--key", required=True)
    show.set_defaults(func=cmd_show)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Source video similarity facets")
    sub = ap.add_subparsers(dest="cmd", required=True)
    add_source_facets_subparser(sub)
    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
