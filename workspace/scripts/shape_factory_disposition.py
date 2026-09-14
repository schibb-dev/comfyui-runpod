#!/usr/bin/env python3
"""Disposition markers: catalog, index, promotion rules, and hook dispatch."""

from __future__ import annotations

import copy
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

from shape_factory_ratings import (
    _atomic_write_json_doc,
    lookup_output_appetite,
    normalize_appetite,
    normalize_appetite_facet,
    utc_now,
)

DISPOSITION_SCHEMA_VERSION = 1
DISPOSITION_INDEX_SCHEMA = "comfyui-runpod.disposition-index.v0"
CATALOG_SCHEMA = "comfyui-runpod.disposition-catalog.v0"

DEFAULT_CATALOG_YAML = Path(__file__).resolve().parent.parent / "disposition_catalog.yaml"


def _seed_catalog_candidates(repo_root: Optional[Path] = None) -> List[Path]:
    script_dir = Path(__file__).resolve().parent
    ws_root = script_dir.parent
    try:
        guessed_repo = script_dir.parents[2]
    except IndexError:
        guessed_repo = script_dir.parents[-1] if script_dir.parents else script_dir
    root = repo_root or guessed_repo
    seen: set[str] = set()
    out: List[Path] = []
    for p in (
        ws_root / "disposition_catalog.yaml",
        root / "disposition_catalog.yaml",
        Path("/workspace/disposition_catalog.yaml"),
        DEFAULT_CATALOG_YAML,
    ):
        key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out

ENTRY_IDS_RETIRED = frozenset({"retire"})
RETIRE_STEP_IDS = frozenset({"retire.trash", "retire.archive"})
CLEAR_ALL_MARKER_IDS = frozenset({"none", "*", "_clear"})


def default_disposition_index_path(og_root: Path) -> Path:
    return og_root.resolve().parent / "_status" / "disposition_index.json"


def default_disposition_catalog_path(og_root: Path) -> Path:
    return og_root.resolve().parent / "_status" / "disposition_catalog.json"


def _init_disposition_doc() -> Dict[str, Any]:
    return {
        "version": DISPOSITION_SCHEMA_VERSION,
        "schema": DISPOSITION_INDEX_SCHEMA,
        "updated_at": utc_now(),
        "by_output_relpath": {},
    }


def _load_or_init_disposition_doc(path: Path) -> Dict[str, Any]:
    if path.is_file():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                doc.setdefault("by_output_relpath", {})
                return doc
        except (OSError, json.JSONDecodeError):
            pass
    return _init_disposition_doc()


def _load_yaml(path: Path) -> Dict[str, Any]:
    if yaml is None or not path.is_file():
        return {}
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _marker_index(catalog: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in catalog.get("markers") or []:
        if isinstance(row, dict) and row.get("id"):
            out[str(row["id"])] = row
    return out


def load_seed_catalog(repo_root: Optional[Path] = None) -> Dict[str, Any]:
    for path in _seed_catalog_candidates(repo_root):
        doc = _load_yaml(path)
        if doc:
            doc.setdefault("schema", CATALOG_SCHEMA)
            doc.setdefault("promotion_rules", {})
            doc.setdefault("markers", [])
            return doc
    return {"version": 1, "schema": CATALOG_SCHEMA, "promotion_rules": {}, "markers": []}


def merge_catalog(seed: Dict[str, Any], overlay: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge runtime overlay onto seed; overlay markers replace by id."""
    merged = copy.deepcopy(seed)
    if not overlay:
        return merged
    merged["updated_at"] = overlay.get("updated_at") or merged.get("updated_at")
    if overlay.get("promotion_rules"):
        rules = merged.setdefault("promotion_rules", {})
        if isinstance(rules, dict) and isinstance(overlay["promotion_rules"], dict):
            rules.update(overlay["promotion_rules"])
    seed_by_id = _marker_index(merged)
    overlay_by_id = _marker_index(overlay)
    for mid, row in overlay_by_id.items():
        if mid in seed_by_id:
            base = copy.deepcopy(seed_by_id[mid])
            base.update(row)
            seed_by_id[mid] = base
        else:
            seed_by_id[mid] = copy.deepcopy(row)
    merged["markers"] = sorted(
        seed_by_id.values(),
        key=lambda m: (
            {"entry": 0, "reason": 1, "step": 2}.get(str(m.get("kind") or ""), 3),
            int(m.get("order") or 999),
            str(m.get("id")),
        ),
    )
    return merged


def load_merged_catalog(
    *,
    og_root: Optional[Path] = None,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    seed = load_seed_catalog(repo_root)
    if og_root is None:
        return seed
    overlay = _load_json(default_disposition_catalog_path(og_root))
    return merge_catalog(seed, overlay if overlay.get("markers") else None)


def save_catalog_overlay(og_root: Path, catalog: Dict[str, Any]) -> Dict[str, Any]:
    path = default_disposition_catalog_path(og_root)
    out = {
        "version": catalog.get("version", 1),
        "schema": CATALOG_SCHEMA,
        "updated_at": utc_now(),
        "promotion_rules": catalog.get("promotion_rules") or {},
        "markers": catalog.get("markers") or [],
    }
    _atomic_write_json_doc(path, out)
    return {"ok": True, "path": str(path), "saved": out}


def catalog_entries(catalog: Dict[str, Any], *, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in catalog.get("markers") or []:
        if not isinstance(row, dict) or row.get("enabled") is False:
            continue
        if kind and str(row.get("kind")) != kind:
            continue
        rows.append(row)
    return rows


def lookup_output_disposition(output_path: str, disposition_doc: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Resolve disposition row by path variants (mirrors appetite lookup)."""
    table = (disposition_doc or {}).get("by_output_relpath") or {}
    if not isinstance(table, dict):
        return None
    raw = str(output_path or "").strip().replace("\\", "/")
    if not raw:
        return None
    keys = [raw, Path(raw).name]
    if "/output/output/" in raw:
        keys.append(re.sub(r"^.*?/output/output/", "output/", raw))
    if "/og/" in raw:
        tail = raw.split("/og/", 1)[-1]
        keys.append(f"output/og/{tail.rstrip('/')}")
        keys.append(f"og/{tail.rstrip('/')}")
    expanded: List[str] = []
    for key in keys:
        key = key.strip().replace("\\", "/")
        if not key:
            continue
        expanded.append(key)
        for suffix in (".mp4", ".MP4", ".png", ".PNG", ".webm", ".WEBM"):
            if key.endswith(suffix):
                expanded.append(key[: -len(suffix)])
    seen: set[str] = set()
    for key in expanded:
        if not key or key in seen:
            continue
        seen.add(key)
        row = table.get(key)
        if isinstance(row, dict):
            return row
    return None


def entry_ids_from_markers(
    markers: Any,
    catalog: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Catalog-order entry ids present in markers (assets may carry several)."""
    present = {str(m).strip() for m in (markers or []) if str(m).strip()}
    if not present:
        return []
    rows = catalog_entries(catalog or {}, kind="entry")
    ordered = [str(m["id"]) for m in rows if str(m.get("id") or "") in present]
    if ordered:
        return ordered
    fallback = ("refine", "investigate", "advance", "park", "retire")
    return [mid for mid in fallback if mid in present]


def primary_entry_from_markers(
    markers: Any,
    catalog: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Catalog-order first entry id in markers (display default when several)."""
    ordered = entry_ids_from_markers(markers, catalog)
    return ordered[0] if ordered else None


def _entry_ids(catalog: Dict[str, Any]) -> Set[str]:
    return {str(m["id"]) for m in catalog_entries(catalog, kind="entry") if m.get("id")}


def entries_in_conflict(catalog: Dict[str, Any], a: str, b: str) -> bool:
    """True when two entry markers cannot be set together (bidirectional)."""
    left, right = str(a or "").strip(), str(b or "").strip()
    if not left or not right or left == right:
        return False
    by_id = _marker_index(catalog)
    sa, sb = by_id.get(left) or {}, by_id.get(right) or {}
    if str(sa.get("kind") or "") != "entry" or str(sb.get("kind") or "") != "entry":
        return False
    if sa.get("exclusive") or sb.get("exclusive"):
        return True
    listed_a = {str(x).strip() for x in (sa.get("conflicts_with") or []) if str(x).strip()}
    listed_b = {str(x).strip() for x in (sb.get("conflicts_with") or []) if str(x).strip()}
    return right in listed_a or left in listed_b


def _drop_entry_process(
    markers: Set[str],
    notes: Dict[str, str],
    reason_detail: Dict[str, Any],
    catalog: Dict[str, Any],
    entry_id: str,
) -> None:
    spec = _marker_index(catalog).get(entry_id) or {}
    process = str(spec.get("process") or entry_id).strip()
    markers.discard(entry_id)
    notes.pop(entry_id, None)
    for kind in ("reason", "step"):
        for row in catalog_entries(catalog, kind=kind):
            if str(row.get("process") or "").strip() != process or not row.get("id"):
                continue
            rid = str(row["id"])
            markers.discard(rid)
            notes.pop(rid, None)
            reason_detail.pop(rid, None)


def apply_incoming_entry_conflicts(
    markers: Set[str],
    notes: Dict[str, str],
    reason_detail: Dict[str, Any],
    catalog: Dict[str, Any],
    incoming_id: str,
) -> None:
    """Drop entries (and their reasons/steps) that cannot coexist with incoming_id."""
    incoming = str(incoming_id or "").strip()
    if not incoming:
        return
    for other in _entry_ids(catalog):
        if other != incoming and entries_in_conflict(catalog, incoming, other):
            _drop_entry_process(markers, notes, reason_detail, catalog, other)


def _clear_all_disposition_markers(
    markers: Set[str],
    notes: Dict[str, str],
    reason_detail: Dict[str, Any],
    catalog: Dict[str, Any],
) -> None:
    drop: Set[str] = set()
    for kind in ("entry", "reason", "step"):
        for row in catalog_entries(catalog, kind=kind):
            if row.get("id"):
                drop.add(str(row["id"]))
    markers -= drop
    for mid in drop:
        notes.pop(mid, None)
        reason_detail.pop(mid, None)


def list_disposition_bucket_items(
    disposition_doc: Optional[Dict[str, Any]],
    catalog: Optional[Dict[str, Any]] = None,
    *,
    entry: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Dedupe disposition_index rows into one item per asset.

    Index stores both a discovery key (``og/…``) and a basename ``short_key``.
    Prefer the longer discovery path for display / library join.
    """
    table = (disposition_doc or {}).get("by_output_relpath") or {}
    if not isinstance(table, dict):
        return []
    cat = catalog or {}
    want = str(entry or "").strip()
    by_short: Dict[str, Dict[str, Any]] = {}
    for key, row in table.items():
        if not isinstance(row, dict):
            continue
        markers = [str(m).strip() for m in (row.get("markers") or []) if str(m).strip()]
        marked = entry_ids_from_markers(markers, cat)
        if not marked:
            continue
        if want and want not in marked:
            continue
        primary = marked[0]
        rel = str(key or "").strip().replace("\\", "/")
        discovery = str(row.get("discovery_key") or "").strip().replace("\\", "/")
        short = str(row.get("short_key") or Path(rel).name).strip()
        if not short:
            short = Path(rel).name or rel
        display = rel if "/" in rel else (discovery if "/" in discovery else rel)
        if discovery and "/" in discovery and ("/" not in display or len(discovery) > len(display)):
            display = discovery
        # Prefer the trash copy when Retire+Trash rewrote (or dual-keyed) the row.
        if "/_trash/" in rel:
            display = rel
        elif "/_trash/" in discovery:
            display = discovery
        notes = row.get("notes") if isinstance(row.get("notes"), dict) else {}
        note = ""
        if isinstance(notes, dict):
            note = str(notes.get(primary) or "").strip()
            if not note:
                for v in notes.values():
                    s = str(v or "").strip()
                    if s:
                        note = s
                        break
        item = {
            "relpath": display,
            "short_key": short,
            "entry": primary,
            "entries": marked,
            "markers": markers,
            "note": note or None,
            "updated_at": row.get("updated_at"),
            "trashed": bool(row.get("trashed")) or "/_trash/" in display,
            "original_relpath": row.get("original_relpath") or None,
        }
        prev = by_short.get(short)
        if prev is None:
            by_short[short] = item
            continue
        prev_rel = str(prev.get("relpath") or "")
        if "/_trash/" in display and "/_trash/" not in prev_rel:
            by_short[short] = item
        elif "/" not in prev_rel and "/" in display:
            by_short[short] = item
        elif "/" in display and "/" in prev_rel and len(display) > len(prev_rel):
            by_short[short] = item
    out = list(by_short.values())
    out.sort(key=lambda r: (str(r.get("updated_at") or ""), str(r.get("relpath") or "")), reverse=True)
    return out


def disposition_bucket_counts(
    items: Sequence[Dict[str, Any]],
    catalog: Optional[Dict[str, Any]] = None,
) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in catalog_entries(catalog or {}, kind="entry"):
        counts[str(row["id"])] = 0
    if not counts:
        counts = {k: 0 for k in ("refine", "investigate", "advance", "park", "retire")}
    for item in items:
        marked = item.get("entries")
        if not isinstance(marked, list) or not marked:
            primary = str(item.get("entry") or "").strip()
            marked = [primary] if primary else []
        seen: Set[str] = set()
        for entry in marked:
            eid = str(entry or "").strip()
            if not eid or eid in seen:
                continue
            seen.add(eid)
            counts[eid] = int(counts.get(eid) or 0) + 1
    return counts


def _discovery_keys_for_relpath(media_relpath: str, og_root: Path, media_abs: Path) -> Tuple[str, str]:
    from correlate_output_ratings import output_relpath_keys_from_xmp

    xmp_like = media_abs.with_suffix(".XMP")
    try:
        short_key, discovery_key = output_relpath_keys_from_xmp(xmp_like, og_root)
    except ValueError:
        short_key = ""
        discovery_key = str(media_relpath or "").replace("\\", "/")
    return short_key, discovery_key


def _append_outcome(row: Dict[str, Any], *, action: str, detail: Any = None) -> None:
    outcomes = row.setdefault("outcomes", [])
    if not isinstance(outcomes, list):
        outcomes = []
        row["outcomes"] = outcomes
    outcomes.append({"at": utc_now(), "action": action, "detail": detail})
    if len(outcomes) > 50:
        row["outcomes"] = outcomes[-50:]


def stamp_output_disposition(
    *,
    media_abs: Path,
    marker_id: str,
    note: Optional[str] = None,
    og_root: Optional[Path] = None,
    disposition_index_path: Optional[Path] = None,
    catalog: Optional[Dict[str, Any]] = None,
    media_relpath: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Set a disposition entry on a media file (creates index row as needed).

    Used by factory/hourly when a job requests a disposition stamp on deposit.
    """
    media_abs = Path(media_abs).expanduser().resolve()
    if not media_abs.is_file():
        raise FileNotFoundError(str(media_abs))

    if og_root is None:
        # Prefer .../output/og/... layout.
        parts = media_abs.parts
        if "og" in parts:
            idx = parts.index("og")
            og_root = Path(*parts[: idx + 1]) if idx > 0 else media_abs.parent
        else:
            og_root = media_abs.parent
    og_root = Path(og_root).expanduser().resolve()

    if disposition_index_path is None:
        disposition_index_path = default_disposition_index_path(og_root)
    if catalog is None:
        catalog = load_merged_catalog(og_root=og_root)

    rel = str(media_relpath or "").strip().replace("\\", "/")
    if not rel:
        try:
            rel = str(media_abs.relative_to(og_root.parent if og_root.name == "og" else og_root))
        except ValueError:
            if "og/" in str(media_abs).replace("\\", "/"):
                rel = "og/" + str(media_abs).replace("\\", "/").split("/og/", 1)[-1]
            else:
                rel = media_abs.name

    return toggle_output_disposition(
        media_abs=media_abs,
        media_relpath=rel,
        marker_id=str(marker_id),
        on=True,
        note=note,
        og_root=og_root,
        disposition_index_path=Path(disposition_index_path),
        catalog=catalog,
    )


_OUTPUT_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv"}


def is_output_video_for_retire_funnel(
    media_abs: Path,
    media_relpath: str,
    *,
    og_root: Path,
) -> bool:
    """True for og/wip videos — stills and input/ uploads stay Remove-only."""
    rel = str(media_relpath or "").replace("\\", "/").strip().lstrip("/")
    low = rel.lower()
    if low.startswith("input/") or "/input/" in f"/{low}":
        return False
    abs_p = Path(media_abs)
    ext = Path(rel or abs_p.name).suffix.lower() or abs_p.suffix.lower()
    if ext not in _OUTPUT_VIDEO_EXTS:
        return False
    og_root = Path(og_root).resolve()
    try:
        abs_p.resolve().relative_to(og_root)
        return True
    except ValueError:
        pass
    wip = og_root.parent / "wip" if og_root.name.lower() == "og" else og_root / "wip"
    try:
        abs_p.resolve().relative_to(wip.resolve())
        return True
    except ValueError:
        pass
    return low.startswith(("og/", "wip/", "output/og/", "output/wip/"))


def stamp_retire_for_remove_appetite(
    *,
    media_abs: Path,
    media_relpath: str,
    og_root: Path,
    appetite: Any = "remove",
    disposition_index_path: Optional[Path] = None,
    catalog: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    One-way funnel: appetite ``remove`` also stamps disposition ``retire``.

    Does not run Trash/Archive. Clearing Remove does not clear Retire.
    Stills / input/ files are skipped so they do not enter Follow-up.
    """
    if normalize_appetite(appetite) != "remove":
        return None
    media_abs = Path(media_abs)
    og_root = Path(og_root).resolve()
    if disposition_index_path is None:
        disposition_index_path = default_disposition_index_path(og_root)
    if not is_output_video_for_retire_funnel(media_abs, media_relpath, og_root=og_root):
        return {"ok": True, "skipped": True, "reason": "not_output_video"}
    doc = _load_or_init_disposition_doc(disposition_index_path)
    row = lookup_output_disposition(media_relpath, doc) or {}
    markers = row.get("markers") if isinstance(row, dict) else []
    if is_retired_disposition(markers if isinstance(markers, list) else []):
        return {
            "ok": True,
            "skipped": True,
            "reason": "already_retired",
            "markers": list(markers) if isinstance(markers, list) else [],
        }
    stamped = stamp_output_disposition(
        media_abs=media_abs,
        media_relpath=media_relpath,
        marker_id="retire",
        og_root=og_root,
        disposition_index_path=disposition_index_path,
        catalog=catalog,
    )
    out = dict(stamped) if isinstance(stamped, dict) else {"ok": True}
    out.setdefault("markers", stamped.get("markers") if isinstance(stamped, dict) else ["retire"])
    out["funnel"] = "remove_to_retire"
    return out


def resolve_output_video_abs(og_root: Path, relpath: str) -> Optional[Path]:
    """Best-effort file for a remove/retire relpath (live og/wip, then trash)."""
    og_root = Path(og_root).resolve()
    root = og_root.parent if og_root.name.lower() == "og" else og_root
    raw = str(relpath or "").replace("\\", "/").strip().lstrip("/")
    if not raw:
        return None
    cands = [raw]
    if raw.startswith("output/"):
        cands.append(raw[len("output/") :])
    paths: List[Path] = []
    for c in cands:
        paths.append(root / c)
        paths.append(og_root / c)
        if not Path(c).suffix:
            for ext in _OUTPUT_VIDEO_EXTS:
                paths.append(root / f"{c}{ext}")
                paths.append(og_root / f"{c}{ext}")
    for p in paths:
        if p.is_file():
            return p
    return find_trashed_output(og_root, Path(raw).name)


def stamp_retire_for_remove_rows(
    *,
    og_root: Path,
    appetite_doc: Optional[Dict[str, Any]] = None,
    appetite_index_path: Optional[Path] = None,
    disposition_index_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Backfill: stamp Retire on existing appetite=remove output videos."""
    from shape_factory_remove_review import list_remove_relpaths

    og_root = Path(og_root).resolve()
    if appetite_doc is None:
        from shape_factory_ratings import load_appetite_doc

        appetite_index_path = Path(
            appetite_index_path or (og_root.parent / "_status" / "appetite_index.json")
        )
        appetite_doc = load_appetite_doc(appetite_index_path)
    if disposition_index_path is None:
        disposition_index_path = default_disposition_index_path(og_root)
    catalog = load_merged_catalog(og_root=og_root)
    stamped: List[str] = []
    skipped: List[Dict[str, str]] = []
    missing = 0
    for row in list_remove_relpaths(appetite_doc or {}):
        rel = str(row.get("relpath") or "").strip()
        media_abs = resolve_output_video_abs(og_root, rel)
        if media_abs is None:
            missing += 1
            skipped.append({"relpath": rel, "reason": "missing_file"})
            continue
        result = stamp_retire_for_remove_appetite(
            media_abs=media_abs,
            media_relpath=rel,
            og_root=og_root,
            appetite="remove",
            disposition_index_path=disposition_index_path,
            catalog=catalog,
        )
        if not result:
            continue
        if result.get("skipped"):
            skipped.append({"relpath": rel, "reason": str(result.get("reason") or "skipped")})
            continue
        stamped.append(rel)
    return {
        "ok": True,
        "stamped": len(stamped),
        "skipped": len(skipped),
        "missing_file": missing,
        "items": stamped,
        "skipped_items": skipped[:40],
        "path": str(disposition_index_path),
    }


def _normalize_modifiers(
    spec: Dict[str, Any],
    modifiers: Optional[List[str]],
) -> List[str]:
    """Validate/clamp modifiers against catalog reason spec."""
    allowed = {
        str(m.get("id")).strip()
        for m in (spec.get("modifiers") or [])
        if isinstance(m, dict) and m.get("id")
    }
    raw = [str(x).strip() for x in (modifiers or []) if str(x).strip()]
    if not raw:
        return []
    unknown = [x for x in raw if x not in allowed]
    if unknown:
        raise ValueError(f"unknown modifier(s): {', '.join(unknown)}")
    mode = str(spec.get("modifier_mode") or "none").strip().lower()
    if mode == "exclusive":
        return [raw[-1]]
    if mode == "multi":
        # Preserve order, unique.
        seen: Set[str] = set()
        out: List[str] = []
        for x in raw:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out
    # mode none — ignore modifiers
    return []


def _reason_ids_for_process(catalog: Dict[str, Any], process: str) -> Set[str]:
    proc = str(process or "").strip()
    out: Set[str] = set()
    for m in catalog_entries(catalog, kind="reason"):
        if str(m.get("process") or "").strip() == proc and m.get("id"):
            out.add(str(m["id"]))
    return out


def toggle_output_disposition(
    *,
    media_abs: Path,
    media_relpath: str,
    marker_id: str,
    on: bool,
    note: Optional[str] = None,
    modifiers: Optional[List[str]] = None,
    og_root: Path,
    disposition_index_path: Path,
    catalog: Dict[str, Any],
) -> Dict[str, Any]:
    media_abs = Path(media_abs)
    if not media_abs.is_file():
        raise FileNotFoundError(str(media_abs))
    marker_id = str(marker_id or "").strip()
    if not marker_id:
        raise ValueError("missing marker")
    by_id = _marker_index(catalog)
    clear_all = marker_id.lower() in CLEAR_ALL_MARKER_IDS
    if clear_all:
        if on:
            raise ValueError("cannot turn on none")
        spec = {"id": "none", "kind": "clear"}
        kind = "clear"
    else:
        spec = by_id.get(marker_id)
        if not spec or spec.get("enabled") is False:
            raise ValueError(f"unknown marker: {marker_id}")
        kind = str(spec.get("kind") or "").strip()
    note_text = str(note or "").strip()

    og_root = Path(og_root).resolve()
    short_key, discovery_key = _discovery_keys_for_relpath(media_relpath, og_root, media_abs)
    doc = _load_or_init_disposition_doc(disposition_index_path)
    table = doc.setdefault("by_output_relpath", {})

    row: Dict[str, Any] = {}
    for k in (discovery_key, short_key):
        if k and isinstance(table.get(k), dict):
            row = copy.deepcopy(table[k])
            break

    markers: Set[str] = set(row.get("markers") or [])
    notes: Dict[str, str] = dict(row.get("notes") or {})
    reason_detail: Dict[str, Any] = {}
    raw_detail = row.get("reason_detail")
    if isinstance(raw_detail, dict):
        reason_detail = copy.deepcopy(raw_detail)

    if on and kind == "reason" and bool(spec.get("requires_note")):
        existing_note = ""
        prev = reason_detail.get(marker_id)
        if isinstance(prev, dict):
            existing_note = str(prev.get("note") or "").strip()
        if not note_text and not existing_note:
            raise ValueError(f"{marker_id} requires a note")

    if on:
        if kind == "entry":
            apply_incoming_entry_conflicts(markers, notes, reason_detail, catalog, marker_id)
        elif kind == "reason":
            # Selecting a reason ensures its process entry is active; keep
            # non-conflicting entries (e.g. refine + advance).
            process = str(spec.get("process") or "").strip()
            if process and process in _entry_ids(catalog):
                apply_incoming_entry_conflicts(markers, notes, reason_detail, catalog, process)
                markers.add(process)
            mods = _normalize_modifiers(spec, modifiers) if modifiers is not None else None
            detail: Dict[str, Any] = {}
            if modifiers is not None:
                if mods:
                    detail["modifiers"] = mods
            elif isinstance(reason_detail.get(marker_id), dict):
                prev_mods = reason_detail[marker_id].get("modifiers")
                if isinstance(prev_mods, list) and prev_mods:
                    detail["modifiers"] = [str(x) for x in prev_mods if str(x).strip()]
            effective_note = note_text
            if not effective_note and isinstance(reason_detail.get(marker_id), dict):
                effective_note = str(reason_detail[marker_id].get("note") or "").strip()
            if effective_note:
                detail["note"] = effective_note
                notes[marker_id] = effective_note
            reason_detail[marker_id] = detail
        markers.add(marker_id)
        if note_text and kind != "reason":
            notes[marker_id] = note_text
    else:
        if kind == "clear":
            _clear_all_disposition_markers(markers, notes, reason_detail, catalog)
        else:
            markers.discard(marker_id)
            notes.pop(marker_id, None)
            if kind == "reason":
                reason_detail.pop(marker_id, None)
            elif kind == "entry":
                # Clearing an entry clears reasons for that process.
                process = str(spec.get("process") or marker_id).strip()
                reason_ids = _reason_ids_for_process(catalog, process)
                markers -= reason_ids
                for rid in reason_ids:
                    reason_detail.pop(rid, None)
                    notes.pop(rid, None)

    # Drop reason_detail keys that are no longer marked.
    for rid in list(reason_detail.keys()):
        if rid not in markers:
            reason_detail.pop(rid, None)

    if markers:
        row = {
            "markers": sorted(markers),
            "notes": notes,
            "reason_detail": reason_detail,
            "short_key": short_key,
            "updated_at": utc_now(),
            "outcomes": row.get("outcomes") or [],
        }
        outcome_detail: Dict[str, Any] = {"marker": marker_id, "on": on}
        if kind == "reason":
            det = reason_detail.get(marker_id) if on else None
            if isinstance(det, dict):
                if det.get("modifiers"):
                    outcome_detail["modifiers"] = det["modifiers"]
                if det.get("note"):
                    outcome_detail["note"] = det["note"]
        _append_outcome(row, action="toggle", detail=outcome_detail)
        for k in (discovery_key, short_key):
            if k:
                table[k] = row
        cleared = False
    else:
        for k in (discovery_key, short_key):
            if k:
                table.pop(k, None)
        cleared = True
        reason_detail = {}
        notes = {}

    doc["updated_at"] = utc_now()
    _atomic_write_json_doc(disposition_index_path, doc)

    return {
        "ok": True,
        "relpath": media_relpath,
        "marker": marker_id,
        "on": on,
        "markers": sorted(markers),
        "notes": notes,
        "reason_detail": reason_detail,
        "cleared": cleared,
        "discovery_key": discovery_key,
        "short_key": short_key,
        "updated_at": row.get("updated_at") if markers else None,
    }


def _appetite_is_high(appetite: Optional[str]) -> bool:
    return normalize_appetite(appetite or "") in ("more", "fast_track")


def _appetite_is_low(appetite: Optional[str]) -> bool:
    return normalize_appetite(appetite or "") in ("", "less", "neutral")


def _rule_matches(
    rule: Dict[str, Any],
    *,
    quality: Optional[float],
    appetite: Optional[str],
    facet: Optional[str],
    predicted_score: Optional[float],
    explicit_quality_missing: bool,
) -> bool:
    app = normalize_appetite(appetite or "")
    if "appetite_in" in rule:
        allowed = {normalize_appetite(x) for x in (rule.get("appetite_in") or [])}
        if app not in allowed:
            return False
    if "quality_min" in rule and quality is not None:
        if float(quality) < float(rule["quality_min"]):
            return False
    if "quality_max" in rule and quality is not None:
        if float(quality) > float(rule["quality_max"]):
            return False
    if rule.get("explicit_quality_missing") and not explicit_quality_missing:
        return False
    if "predicted_max" in rule and predicted_score is not None:
        if float(predicted_score) > float(rule["predicted_max"]):
            return False
    if "facet_in" in rule:
        fac = normalize_appetite_facet(facet or "both")
        if fac not in {normalize_appetite_facet(x) for x in rule.get("facet_in") or []}:
            return False
    if rule.get("appetite_high") and not _appetite_is_high(app):
        return False
    if rule.get("appetite_low") and not _appetite_is_low(app):
        return False
    _ = facet  # reserved for facet_in rules
    return True


def compute_disposition_promotions(
    catalog: Dict[str, Any],
    *,
    quality: Optional[float] = None,
    appetite: Optional[str] = None,
    facet: Optional[str] = None,
    predicted_score: Optional[float] = None,
    explicit_quality_missing: bool = False,
) -> Dict[str, Any]:
    """Return promote + secondary entry marker ids from catalog promotion_rules."""
    rules = catalog.get("promotion_rules") or {}
    if not isinstance(rules, dict):
        return {"promote": [], "secondary": [], "matched_rules": []}

    promote: List[str] = []
    secondary: List[str] = []
    matched: List[str] = []

    for rule_id, rule in rules.items():
        if not isinstance(rule, dict):
            continue
        if not _rule_matches(
            rule,
            quality=quality,
            appetite=appetite,
            facet=facet,
            predicted_score=predicted_score,
            explicit_quality_missing=explicit_quality_missing,
        ):
            continue
        matched.append(str(rule_id))
        for mid in rule.get("promote") or []:
            s = str(mid).strip()
            if s and s not in promote:
                promote.append(s)
        for mid in rule.get("secondary") or []:
            s = str(mid).strip()
            if s and s not in secondary and s not in promote:
                secondary.append(s)

    # Facet tilt: processing + high Q + high A → refine over advance (scenario A).
    if (
        quality is not None
        and float(quality) >= 4
        and _appetite_is_high(appetite)
        and normalize_appetite_facet(facet or "both") == "processing"
    ):
        if "refine" not in promote:
            promote.insert(0, "refine")
        if "advance" in promote:
            promote.remove("advance")
            if "advance" not in secondary:
                secondary.insert(0, "advance")

    if normalize_appetite_facet(facet or "both") == "source" and _appetite_is_high(appetite):
        if "extract" not in promote and "extract" not in secondary:
            secondary.insert(0, "extract")

    return {"promote": promote, "secondary": secondary, "matched_rules": matched}


def disposition_for_item(
    item: Dict[str, Any],
    disposition_doc: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not disposition_doc:
        return {}
    rel = str(item.get("relpath") or item.get("video_relpath") or "").strip()
    row = lookup_output_disposition(rel, disposition_doc)
    if not row:
        return {}
    markers = row.get("markers") or []
    outcomes = row.get("outcomes") if isinstance(row.get("outcomes"), list) else []
    last = outcomes[-1] if outcomes else None
    return {
        "disposition_markers": markers if isinstance(markers, list) else [],
        "disposition_notes": row.get("notes") if isinstance(row.get("notes"), dict) else {},
        "disposition_reason_detail": row.get("reason_detail") if isinstance(row.get("reason_detail"), dict) else {},
        "disposition_updated_at": row.get("updated_at"),
        "disposition_outcomes": outcomes[-8:],
        "disposition_last_outcome": last if isinstance(last, dict) else None,
        "disposition_archived": bool(row.get("archived")),
        "disposition_saved": bool(markers),
    }


def is_retired_disposition(markers: List[str]) -> bool:
    return bool(ENTRY_IDS_RETIRED.intersection(markers or []))


def _companion_paths(media_abs: Path) -> List[Path]:
    stem = media_abs.with_suffix("")
    companions: List[Path] = [media_abs]
    for ext in (".png", ".PNG", ".XMP", ".xmp", ".jpg", ".JPEG", ".jpeg"):
        p = stem.with_suffix(ext)
        if p.is_file() and p not in companions:
            companions.append(p)
    sidecar = media_abs.with_suffix(".trims.json")
    if sidecar.is_file():
        companions.append(sidecar)
    return companions


def trash_output_media(media_abs: Path, *, og_root: Path) -> Dict[str, Any]:
    """Move media + companions to og/_trash/<date>/ (best-effort)."""
    og_root = Path(og_root).resolve()
    media_abs = Path(media_abs).resolve()
    if not media_abs.is_file():
        raise FileNotFoundError(str(media_abs))
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        rel = media_abs.relative_to(og_root)
    except ValueError:
        rel = Path(media_abs.name)
    dest_dir = og_root / "_trash" / day
    dest_dir.mkdir(parents=True, exist_ok=True)
    moved: List[str] = []
    for src in _companion_paths(media_abs):
        dest = dest_dir / src.name
        if dest.exists():
            dest = dest_dir / f"{src.stem}__{int(datetime.now(timezone.utc).timestamp())}{src.suffix}"
        shutil.move(str(src), str(dest))
        moved.append(str(dest))
    return {"ok": True, "moved": moved, "trash_dir": str(dest_dir), "original_relpath": str(rel)}


def _og_library_root(og_root: Path) -> Path:
    """Parent of ``og/`` (workspace output root) for ``og/_trash/…`` relpaths."""
    og_root = Path(og_root).resolve()
    return og_root.parent if og_root.name.lower() == "og" else og_root


def find_trashed_output(og_root: Path, name_or_stem: str) -> Optional[Path]:
    """Newest ``og/_trash/**/<stem>.mp4`` (else .png) for a retired basename."""
    stem = Path(str(name_or_stem or "").strip()).stem
    if not stem:
        return None
    trash = Path(og_root).resolve() / "_trash"
    if not trash.is_dir():
        return None
    hits = [p for p in trash.glob(f"**/{stem}.mp4") if p.is_file()]
    if not hits:
        hits = [p for p in trash.glob(f"**/{stem}.png") if p.is_file()]
    if not hits:
        return None
    hits.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0]


def trashed_relpath_for(og_root: Path, trash_abs: Path) -> str:
    root = _og_library_root(og_root)
    try:
        return trash_abs.resolve().relative_to(root).as_posix()
    except ValueError:
        try:
            return trash_abs.resolve().relative_to(Path(og_root).resolve()).as_posix()
        except ValueError:
            return trash_abs.name


def _original_output_exists(og_root: Path, relpath: str) -> bool:
    raw = str(relpath or "").strip().replace("\\", "/")
    if not raw:
        return False
    og_root = Path(og_root).resolve()
    root = _og_library_root(og_root)
    cands = [raw]
    if raw.startswith("output/"):
        cands.append(raw[len("output/") :])
    if raw.startswith("og/"):
        cands.append(raw[len("og/") :])
    paths: List[Path] = []
    for c in cands:
        paths.append(root / c)
        paths.append(og_root / c)
        if not Path(c).suffix:
            paths.append(root / f"{c}.mp4")
            paths.append(og_root / f"{c}.mp4")
            name = Path(c).name
            paths.append(og_root / name)
            paths.append(og_root / f"{name}.mp4")
            # date/name under og/
            if c.startswith("og/"):
                paths.append(og_root / c[len("og/") :])
                paths.append(og_root / f"{c[len('og/'):]}.mp4")
    for p in paths:
        if p.is_file():
            return True
    return False


def rekey_disposition_row(
    table: Dict[str, Any],
    *,
    old_keys: Sequence[str],
    new_relpath: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Move a disposition row onto a new relpath key (and drop stale aliases)."""
    row: Dict[str, Any] = {}
    for k in old_keys:
        cur = table.get(str(k or "").strip())
        if isinstance(cur, dict):
            row = copy.deepcopy(cur)
            break
    if not row:
        return {}
    if extra:
        row.update(extra)
    row["updated_at"] = utc_now()
    new_rel = str(new_relpath or "").strip().replace("\\", "/")
    if not new_rel:
        return row
    seen: set[str] = set()
    for k in list(old_keys) + [new_rel]:
        kk = str(k or "").strip()
        if kk:
            seen.add(kk)
    for k in seen:
        table.pop(k, None)
    table[new_rel] = row
    if Path(new_rel).name != new_rel:
        table[Path(new_rel).name] = row
    return row


def relocate_trashed_disposition_rows(
    *,
    og_root: Path,
    disposition_index_path: Path,
) -> Dict[str, Any]:
    """
    Point retire rows at ``og/_trash/…`` when the original file is gone.

    Retire+Trash used to move bytes without rewriting the index, so Follow-up
    still requested the old ``output/og/…`` stem and showed a broken thumb.
    """
    og_root = Path(og_root).resolve()
    doc = _load_or_init_disposition_doc(disposition_index_path)
    table = doc.setdefault("by_output_relpath", {})
    # Group alias keys that share short_key / stem.
    groups: Dict[str, List[str]] = {}
    for key, row in list(table.items()):
        if not isinstance(row, dict):
            continue
        markers = row.get("markers") or []
        if "retire" not in markers:
            continue
        short = str(row.get("short_key") or key).strip()
        stem = Path(short).stem or Path(str(key)).stem
        groups.setdefault(stem, []).append(str(key))

    relocated: List[Dict[str, str]] = []
    skipped = 0
    missing = 0
    for stem, keys in groups.items():
        live_keys = [k for k in keys if "/_trash/" not in str(k).replace("\\", "/")]
        if not live_keys:
            skipped += 1
            continue
        if any(_original_output_exists(og_root, k) for k in live_keys):
            skipped += 1
            continue
        trash_abs = find_trashed_output(og_root, stem)
        if trash_abs is None:
            missing += 1
            continue
        new_rel = trashed_relpath_for(og_root, trash_abs)
        row = rekey_disposition_row(
            table,
            old_keys=keys,
            new_relpath=new_rel,
            extra={
                "trashed": True,
                "original_relpath": next((k for k in keys if "/" in k), keys[0]),
                "short_key": new_rel,
                "discovery_key": new_rel,
            },
        )
        if row:
            relocated.append({"stem": stem, "relpath": new_rel})
    if relocated:
        doc["updated_at"] = utc_now()
        _atomic_write_json_doc(disposition_index_path, doc)
    return {
        "ok": True,
        "relocated": len(relocated),
        "skipped_present": skipped,
        "missing_trash": missing,
        "items": relocated,
        "path": str(disposition_index_path),
    }


def run_disposition_hook(
    hook: str,
    *,
    media_abs: Path,
    media_relpath: str,
    og_root: Path,
    disposition_index_path: Path,
    catalog: Dict[str, Any],
    step_spec: Dict[str, Any],
    hook_runner: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Dispatch a catalog hook. hook_runner injects replay/extend from server."""
    hook = str(hook or "none").strip().lower()
    extra = extra or {}
    if hook == "none" or not hook:
        return {"ok": True, "hook": hook, "skipped": True}

    if hook == "set_marker":
        args = step_spec.get("hook_args") or {}
        target = str(args.get("marker") or "").strip()
        if not target:
            return {"ok": False, "hook": hook, "error": "missing target marker"}
        toggled = toggle_output_disposition(
            media_abs=media_abs,
            media_relpath=media_relpath,
            marker_id=target,
            on=True,
            og_root=og_root,
            disposition_index_path=disposition_index_path,
            catalog=catalog,
        )
        # Clear investigate entry when routing.
        inv = toggle_output_disposition(
            media_abs=media_abs,
            media_relpath=media_relpath,
            marker_id="investigate",
            on=False,
            og_root=og_root,
            disposition_index_path=disposition_index_path,
            catalog=catalog,
        )
        return {"ok": True, "hook": hook, "toggled": toggled, "cleared_investigate": inv}

    if hook == "trash":
        # Stamp retire while the file still exists, then move, then rewrite index keys
        # so Follow-up does not keep requesting the vacated original path.
        stamped = toggle_output_disposition(
            media_abs=media_abs,
            media_relpath=media_relpath,
            marker_id="retire",
            on=True,
            og_root=og_root,
            disposition_index_path=disposition_index_path,
            catalog=catalog,
        )
        result = trash_output_media(media_abs, og_root=og_root)
        new_rel = None
        moved = result.get("moved") or []
        mp4 = next((p for p in moved if str(p).lower().endswith(".mp4")), None)
        trash_abs = Path(mp4) if mp4 else (Path(moved[0]) if moved else None)
        if trash_abs is not None:
            new_rel = trashed_relpath_for(og_root, trash_abs)
            doc = _load_or_init_disposition_doc(disposition_index_path)
            table = doc.setdefault("by_output_relpath", {})
            old_keys = [media_relpath, stamped.get("short_key"), stamped.get("discovery_key"), Path(media_relpath).stem]
            rekey_disposition_row(
                table,
                old_keys=[str(k) for k in old_keys if k],
                new_relpath=new_rel,
                extra={
                    "trashed": True,
                    "original_relpath": media_relpath,
                    "short_key": new_rel,
                    "discovery_key": new_rel,
                },
            )
            doc["updated_at"] = utc_now()
            _atomic_write_json_doc(disposition_index_path, doc)
        return {"ok": True, "hook": hook, "new_relpath": new_rel, "stamped": stamped, **result}

    if hook == "archive":
        doc = _load_or_init_disposition_doc(disposition_index_path)
        short_key, discovery_key = _discovery_keys_for_relpath(media_relpath, og_root, media_abs)
        table = doc.setdefault("by_output_relpath", {})
        row = copy.deepcopy(table.get(discovery_key) or table.get(short_key) or {})
        markers = set(row.get("markers") or [])
        markers.add("retire")
        row["markers"] = sorted(markers)
        row["archived"] = True
        row["updated_at"] = utc_now()
        _append_outcome(row, action="archive", detail={})
        for k in (discovery_key, short_key):
            if k:
                table[k] = row
        doc["updated_at"] = utc_now()
        _atomic_write_json_doc(disposition_index_path, doc)
        return {"ok": True, "hook": hook, "archived": True}

    if hook == "open_trim":
        return {
            "ok": True,
            "hook": hook,
            "trim_ui": True,
            "discovery_href": f"/discovery?relpath={media_relpath.strip().replace(chr(92), '/')}",
        }

    if hook == "extract_frame":
        return {
            "ok": True,
            "hook": hook,
            "placeholder": True,
            "detail": "extract_frame not fully implemented — open discovery for manual frame grab",
            "discovery_href": f"/discovery?relpath={media_relpath.strip().replace(chr(92), '/')}",
        }

    if hook == "sampler_pin":
        return {"ok": True, "hook": hook, "pinned": True}

    if hook in ("replay", "replay_front", "extend", "derive", "appetite_more"):
        if hook_runner is None:
            return {"ok": False, "hook": hook, "error": "hook_runner_unavailable"}
        body = dict(extra)
        if hook == "appetite_more":
            from shape_factory_ratings import set_output_appetite, default_appetite_index_path

            set_output_appetite(
                media_abs=media_abs,
                media_relpath=media_relpath,
                appetite="more",
                facet=str(extra.get("facet") or "both"),
                og_root=og_root,
                appetite_index_path=default_appetite_index_path(og_root),
            )
            return {"ok": True, "hook": hook, "appetite_set": "more"}
        # Explicit front=False (Later) demotes catalog replay_front → plain replay.
        # Explicit front=True promotes plain replay → front of queue.
        effective = hook
        if hook == "replay_front" and body.get("front") is False:
            effective = "replay"
            body.pop("front", None)
        elif hook == "replay_front":
            body["front"] = True
        elif hook == "replay" and body.get("front") is True:
            pass
        if effective == "extend":
            body["extend"] = True
        out = hook_runner(effective, body)
        if isinstance(out, dict):
            out = dict(out)
            out.setdefault("hook", effective)
            if body.get("front"):
                out["front"] = True
        return out

    return {"ok": False, "hook": hook, "error": f"unknown_hook:{hook}"}


def run_disposition_step(
    *,
    step_id: str,
    media_abs: Path,
    media_relpath: str,
    og_root: Path,
    disposition_index_path: Path,
    catalog: Dict[str, Any],
    hook_runner: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    by_id = _marker_index(catalog)
    spec = by_id.get(step_id)
    if not spec or spec.get("kind") != "step":
        raise ValueError(f"unknown step: {step_id}")
    hook = str(spec.get("hook") or "none")
    result = run_disposition_hook(
        hook,
        media_abs=media_abs,
        media_relpath=media_relpath,
        og_root=og_root,
        disposition_index_path=disposition_index_path,
        catalog=catalog,
        step_spec=spec,
        hook_runner=hook_runner,
        extra=extra,
    )
    doc = _load_or_init_disposition_doc(disposition_index_path)
    lookup_key = str((result or {}).get("new_relpath") or media_relpath)
    row = lookup_output_disposition(lookup_key, doc) or lookup_output_disposition(media_relpath, doc) or {}
    if isinstance(row, dict):
        short_key = row.get("short_key") or ""
        discovery_key = media_relpath
        for k in (discovery_key, short_key):
            if k and k in (doc.get("by_output_relpath") or {}):
                r = doc["by_output_relpath"][k]
                _append_outcome(r, action=f"step:{step_id}", detail=result)
        doc["updated_at"] = utc_now()
        _atomic_write_json_doc(disposition_index_path, doc)
    return {"ok": True, "step_id": step_id, "hook": hook, "result": result}
