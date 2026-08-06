#!/usr/bin/env python3
"""Disposition markers: catalog, index, promotion rules, and hook dispatch."""

from __future__ import annotations

import copy
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

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
    root = repo_root or script_dir.parents[2]
    seen: set[str] = set()
    out: List[Path] = []
    for p in (
        ws_root / "disposition_catalog.yaml",
        root / "disposition_catalog.yaml",
        DEFAULT_CATALOG_YAML,
    ):
        key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out

ENTRY_IDS_RETIRED = frozenset({"retire"})
RETIRE_STEP_IDS = frozenset({"retire.trash", "retire.archive"})


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
            # One primary entry at a time: clear other entry markers.
            entry_ids = {m["id"] for m in catalog_entries(catalog, kind="entry")}
            markers -= entry_ids
            # Switching away from refine clears refine reasons.
            if marker_id != "refine":
                refine_reasons = _reason_ids_for_process(catalog, "refine")
                markers -= refine_reasons
                for rid in refine_reasons:
                    reason_detail.pop(rid, None)
                    notes.pop(rid, None)
        elif kind == "reason":
            # Selecting a reason ensures its process entry is active.
            process = str(spec.get("process") or "").strip()
            if process and process in {m["id"] for m in catalog_entries(catalog, kind="entry")}:
                entry_ids = {m["id"] for m in catalog_entries(catalog, kind="entry")}
                markers -= entry_ids
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
        result = trash_output_media(media_abs, og_root=og_root)
        toggle_output_disposition(
            media_abs=media_abs,
            media_relpath=media_relpath,
            marker_id="retire",
            on=True,
            og_root=og_root,
            disposition_index_path=disposition_index_path,
            catalog=catalog,
        )
        return {"ok": True, "hook": hook, **result}

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
    row = lookup_output_disposition(media_relpath, doc) or {}
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
