"""Hourly guide bins — Phase 0/1 seed-still curation.

Composes toward:
  [assets] → [pools] → [curation bins] → [workflows] → [priority/schedule]

Each family with a ``source_still`` pool gets its own steer bin (1:1). Manual
Keep/Pin/Later/Out soft-bias that family's hourly ``pool_product`` lottery.
``decisions.jsonl`` is append-only audit only — live state is ``bins.json``.
"""

from __future__ import annotations

import json
import os
import re
import threading
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

BIN_SCHEMA_VERSION = 1
PIPE_SCHEMA_VERSION = 1

DEFAULT_BIN_ID = "hourly-seed-stills"
DEFAULT_POOL_FAMILY = "X-KNEEL-FB9-bare"
# Historical cluster that once shared one bin; ensure_ splits these 1:1 now.
LEGACY_SHARED_STILL_FAMILIES: Tuple[str, ...] = (
    "X-KNEEL-FB9-bare",
    "X-KNEEL-FB9",
    "BounceDanceA",
    "FB9-FaceBlast",
    "FB8VB2",
    "FB8VA5-ZOOMOUT",
    "Breast-shake-FB8VA5",
)
# New bins bind exactly one family (pool_family == workflow_families[0]).
DEFAULT_WORKFLOW_FAMILIES: Tuple[str, ...] = (DEFAULT_POOL_FAMILY,)

BIN_ITEM_STATUSES = ("keep", "later", "out", "pin")
_CONTENT_ID_RE = re.compile(r"[0-9a-f]{64}", re.I)

_lock = threading.RLock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_data_root() -> Path:
    env = (os.environ.get("SHAPE_FACTORY_DATA_ROOT") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    # Prefer repo .data when present (dev / WSL layout).
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / ".data"
        if cand.is_dir():
            return cand.resolve()
    return Path.home().resolve() / "comfyui-runpod-data" / "output" / ".data"


def guide_dir(data_root: Optional[Path] = None) -> Path:
    root = (data_root or default_data_root()).resolve()
    return root / "shape_factory" / "hourly_guide"


def bins_path(data_root: Optional[Path] = None) -> Path:
    return guide_dir(data_root) / "bins.json"


def pipes_path(data_root: Optional[Path] = None) -> Path:
    return guide_dir(data_root) / "pipes.json"


def decisions_path(data_root: Optional[Path] = None) -> Path:
    return guide_dir(data_root) / "decisions.jsonl"


def extract_content_id(name: Optional[str]) -> Optional[str]:
    m = _CONTENT_ID_RE.search(str(name or ""))
    return m.group(0).lower() if m else None


def _atomic_write_json(path: Path, doc: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _default_bin() -> Dict[str, Any]:
    return {
        "id": DEFAULT_BIN_ID,
        "label": "Hourly seed stills",
        "curation_mode": "manual",
        "asset_kind": "still",
        "pool_family": DEFAULT_POOL_FAMILY,
        "pool_slot": "source_still",
        "workflow_families": list(DEFAULT_WORKFLOW_FAMILIES),
        "bias_mult": 8.0,
        "pin_mult": 16.0,
        "items": [],
        "updated_at": _utc_now(),
    }


def _default_pipe() -> Dict[str, Any]:
    return {
        "id": "hourly-seed-stills-default",
        "label": "Seed stills → hourly i2v",
        "asset_source": {
            "kind": "catalog",
            "appetite": ["more", "fast_track"],
            "sort": "newest",
            "limit": 48,
        },
        "bin_id": DEFAULT_BIN_ID,
        "pool": {"family": DEFAULT_POOL_FAMILY, "slot": "source_still"},
        "workflows": list(DEFAULT_WORKFLOW_FAMILIES),
        "schedule": {"bind": "hourly"},
        "curation_mode": "manual",
    }


def _init_bins_doc() -> Dict[str, Any]:
    bin_row = _default_bin()
    return {
        "version": BIN_SCHEMA_VERSION,
        "bins": {bin_row["id"]: bin_row},
        "updated_at": _utc_now(),
    }


def _init_pipes_doc() -> Dict[str, Any]:
    return {
        "version": PIPE_SCHEMA_VERSION,
        "pipes": [_default_pipe()],
        "updated_at": _utc_now(),
    }


def load_bins_doc(data_root: Optional[Path] = None) -> Dict[str, Any]:
    path = bins_path(data_root)
    with _lock:
        if not path.is_file():
            doc = _init_bins_doc()
            _atomic_write_json(path, doc)
            return doc
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            doc = _init_bins_doc()
            _atomic_write_json(path, doc)
            return doc
        if not isinstance(doc, dict):
            doc = _init_bins_doc()
        bins = doc.get("bins")
        if not isinstance(bins, dict):
            bins = {}
            doc["bins"] = bins
        if DEFAULT_BIN_ID not in bins:
            bins[DEFAULT_BIN_ID] = _default_bin()
            doc["updated_at"] = _utc_now()
            _atomic_write_json(path, doc)
        return doc


def load_pipes_doc(data_root: Optional[Path] = None) -> Dict[str, Any]:
    path = pipes_path(data_root)
    with _lock:
        if not path.is_file():
            doc = _init_pipes_doc()
            _atomic_write_json(path, doc)
            return doc
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            doc = _init_pipes_doc()
            _atomic_write_json(path, doc)
            return doc
        if not isinstance(doc, dict) or not isinstance(doc.get("pipes"), list):
            doc = _init_pipes_doc()
            _atomic_write_json(path, doc)
        return doc


def get_bin(bin_id: str = DEFAULT_BIN_ID, *, data_root: Optional[Path] = None) -> Dict[str, Any]:
    doc = load_bins_doc(data_root)
    bins = doc.get("bins") if isinstance(doc.get("bins"), dict) else {}
    row = bins.get(str(bin_id or "").strip())
    if isinstance(row, dict):
        return dict(row)
    raise KeyError(f"unknown bin: {bin_id}")


def _bin_id_for_family(family: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(family or "").strip()).strip("-_.")
    safe = safe or "unknown"
    return f"steer-still-{safe}"


def _pools_root(data_root: Optional[Path] = None) -> Path:
    return (data_root or default_data_root()).resolve() / "pools"


def discover_source_still_families(*, data_root: Optional[Path] = None) -> List[str]:
    """Family slugs under ``.data/pools/*/`` that declare a ``source_still`` pool."""
    root = _pools_root(data_root)
    if not root.is_dir():
        return []
    out: List[str] = []
    for fam_dir in sorted(root.iterdir()):
        if not fam_dir.is_dir():
            continue
        yml = fam_dir / "pools.yaml"
        if not yml.is_file():
            continue
        try:
            text = yml.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if re.search(r"(?m)^\s*source_still\s*:", text) or re.search(
            r"(?m)^\s*slot:\s*source_still\s*$", text
        ):
            out.append(fam_dir.name)
    return out


def _families_covered_by_bins(doc: Dict[str, Any]) -> Dict[str, str]:
    """Map family slug → bin_id. Prefer exact ``pool_family`` (1:1 steer)."""
    covered: Dict[str, str] = {}
    bins = doc.get("bins") if isinstance(doc.get("bins"), dict) else {}
    # Pass 1: pool_family is authoritative.
    for bid, row in bins.items():
        if not isinstance(row, dict):
            continue
        bin_id = str(row.get("id") or bid or "").strip() or str(bid)
        pool_fam = str(row.get("pool_family") or "").strip()
        if pool_fam:
            covered[pool_fam] = bin_id
    # Pass 2: legacy workflow_families only if that family has no pool_family bin yet.
    for bid, row in bins.items():
        if not isinstance(row, dict):
            continue
        bin_id = str(row.get("id") or bid or "").strip() or str(bid)
        for wf in row.get("workflow_families") or []:
            fam = str(wf or "").strip()
            if fam and fam not in covered:
                covered[fam] = bin_id
    return covered


def _narrow_legacy_shared_bins(bins: Dict[str, Any]) -> List[str]:
    """
    Force 1:1 steer: each bin's workflow_families is only its pool_family.
    Returns family slugs that were removed from shared lists (need their own bins).
    """
    orphaned: List[str] = []
    for bid, row in list(bins.items()):
        if not isinstance(row, dict):
            continue
        pool_fam = str(row.get("pool_family") or "").strip()
        if not pool_fam:
            continue
        raw_wfs = [str(x).strip() for x in (row.get("workflow_families") or []) if str(x).strip()]
        extras = [f for f in raw_wfs if f != pool_fam]
        if not extras and raw_wfs == [pool_fam]:
            continue
        for f in extras:
            if f not in orphaned:
                orphaned.append(f)
        row["workflow_families"] = [pool_fam]
        row["updated_at"] = _utc_now()
        bins[bid] = row
    return orphaned


def ensure_steer_bins_for_source_stills(*, data_root: Optional[Path] = None) -> List[str]:
    """
    Ensure every family with a ``source_still`` pool has its **own** steer bin.

    Policy (current): one workflow-step / family → one bin. No shared steering
    across families. Legacy multi-family ``workflow_families`` lists are narrowed;
    orphaned families get a dedicated **empty** bin (all candidates start as New —
    do not clone Keep/Pin/Out from another family; those decisions are
    source-sensitive).
    """
    data_root = (data_root or default_data_root()).resolve()
    families = discover_source_still_families(data_root=data_root)
    created: List[str] = []
    with _lock:
        doc = load_bins_doc(data_root)
        bins = doc.setdefault("bins", {})
        if not isinstance(bins, dict):
            bins = {}
            doc["bins"] = bins

        orphaned = _narrow_legacy_shared_bins(bins)
        for fam in orphaned:
            if fam not in families:
                families.append(fam)

        dirty = bool(orphaned)
        covered = _families_covered_by_bins({"bins": bins})

        for fam in families:
            # Already has a bin whose pool_family is this family?
            existing_id = None
            for bid, row in bins.items():
                if isinstance(row, dict) and str(row.get("pool_family") or "").strip() == fam:
                    existing_id = str(row.get("id") or bid)
                    # Keep workflow_families 1:1.
                    if list(row.get("workflow_families") or []) != [fam]:
                        row["workflow_families"] = [fam]
                        row["updated_at"] = _utc_now()
                        dirty = True
                    break
            if existing_id:
                covered[fam] = existing_id
                continue

            bid = _bin_id_for_family(fam) if fam != DEFAULT_POOL_FAMILY else DEFAULT_BIN_ID
            if fam == DEFAULT_POOL_FAMILY and not isinstance(bins.get(DEFAULT_BIN_ID), dict):
                bid = DEFAULT_BIN_ID
            elif fam == DEFAULT_POOL_FAMILY and isinstance(bins.get(DEFAULT_BIN_ID), dict):
                # Default bin exists but pool_family might differ — only reuse if it matches.
                row = bins[DEFAULT_BIN_ID]
                if str(row.get("pool_family") or "").strip() == fam:
                    covered[fam] = DEFAULT_BIN_ID
                    continue
                bid = _bin_id_for_family(fam)

            if isinstance(bins.get(bid), dict):
                row = bins[bid]
                if str(row.get("pool_family") or "").strip() != fam:
                    bid = _bin_id_for_family(fam)
                else:
                    covered[fam] = bid
                    continue

            now = _utc_now()
            bins[bid] = {
                "id": bid,
                "label": f"{fam} seed stills",
                "curation_mode": "manual",
                "asset_kind": "still",
                "pool_family": fam,
                "pool_slot": "source_still",
                "workflow_families": [fam],
                "bias_mult": 8.0,
                "pin_mult": 16.0,
                # Empty → every candidate is New until this family is sorted.
                "items": [],
                "updated_at": now,
            }
            covered[fam] = bid
            created.append(bid)
            dirty = True

        if dirty:
            doc["updated_at"] = _utc_now()
            _atomic_write_json(bins_path(data_root), doc)
    return created


def _item_status_map(items: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        cid = str(it.get("content_id") or "").strip().lower()
        st = str(it.get("status") or "").strip()
        if cid and st in BIN_ITEM_STATUSES:
            out[cid] = st
    return out


def clear_inherited_steer_clones(*, data_root: Optional[Path] = None, min_overlap: float = 0.8) -> List[str]:
    """
    One-shot cleanup: wipe Keep/Pin/Later/Out that were cloned from the former
    shared bin into other families. Keeps ``hourly-seed-stills`` and any bin
    whose decisions have clearly diverged from that source.
    """
    data_root = (data_root or default_data_root()).resolve()
    cleared: List[str] = []
    with _lock:
        doc = load_bins_doc(data_root)
        bins = doc.get("bins") if isinstance(doc.get("bins"), dict) else {}
        source = bins.get(DEFAULT_BIN_ID) if isinstance(bins.get(DEFAULT_BIN_ID), dict) else None
        if not source:
            return cleared
        source_map = _item_status_map(source.get("items"))
        if not source_map:
            return cleared
        dirty = False
        for bid, row in list(bins.items()):
            if not isinstance(row, dict):
                continue
            rid = str(row.get("id") or bid)
            if rid == DEFAULT_BIN_ID:
                continue
            pool_fam = str(row.get("pool_family") or "").strip()
            if pool_fam == DEFAULT_POOL_FAMILY:
                continue
            cur = _item_status_map(row.get("items"))
            if not cur:
                continue
            shared = set(cur) & set(source_map)
            if not shared:
                continue
            same = sum(1 for c in shared if cur.get(c) == source_map.get(c))
            overlap = same / max(len(cur), 1)
            # Near-clone of the default bin → wipe so first visit is all New.
            if overlap < float(min_overlap):
                continue
            row["items"] = []
            row["updated_at"] = _utc_now()
            bins[rid] = row
            cleared.append(rid)
            dirty = True
        if dirty:
            doc["updated_at"] = _utc_now()
            _atomic_write_json(bins_path(data_root), doc)
    return cleared


def list_steer_targets(*, data_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Steer-able bins (source_still), after ensuring one per still-pool family."""
    data_root = (data_root or default_data_root()).resolve()
    ensure_steer_bins_for_source_stills(data_root=data_root)
    doc = load_bins_doc(data_root)
    bins = doc.get("bins") if isinstance(doc.get("bins"), dict) else {}
    out: List[Dict[str, Any]] = []
    seen_families: set[str] = set()
    for bid, row in bins.items():
        if not isinstance(row, dict):
            continue
        slot = str(row.get("pool_slot") or "").strip() or "source_still"
        kind = str(row.get("asset_kind") or "").strip() or "still"
        if slot != "source_still" and kind != "still":
            continue
        pool_fam = str(row.get("pool_family") or "").strip()
        # One target row per pool_family (skip duplicates).
        if pool_fam and pool_fam in seen_families:
            continue
        try:
            summary = bin_summary(str(row.get("id") or bid), data_root=data_root)
        except Exception:
            continue
        if pool_fam:
            seen_families.add(pool_fam)
        out.append(summary)
    out.sort(
        key=lambda s: (
            0 if str(s.get("id") or "") == DEFAULT_BIN_ID else 1,
            str(s.get("pool_family") or s.get("label") or s.get("id") or "").lower(),
        )
    )
    return out


def resolve_bin_id_for_family(family: str, *, data_root: Optional[Path] = None) -> Optional[str]:
    """Bin that steers this family (1:1 via pool_family), or None."""
    fam = str(family or "").strip()
    if not fam:
        return None
    data_root = (data_root or default_data_root()).resolve()
    try:
        ensure_steer_bins_for_source_stills(data_root=data_root)
    except Exception:
        pass
    doc = load_bins_doc(data_root)
    bins = doc.get("bins") if isinstance(doc.get("bins"), dict) else {}
    for bid, row in bins.items():
        if isinstance(row, dict) and str(row.get("pool_family") or "").strip() == fam:
            return str(row.get("id") or bid)
    return _families_covered_by_bins(doc).get(fam)


def bin_summary(bin_id: str = DEFAULT_BIN_ID, *, data_root: Optional[Path] = None) -> Dict[str, Any]:
    row = get_bin(bin_id, data_root=data_root)
    items = row.get("items") if isinstance(row.get("items"), list) else []
    counts = {s: 0 for s in BIN_ITEM_STATUSES}
    for it in items:
        if not isinstance(it, dict):
            continue
        st = str(it.get("status") or "").strip()
        if st in counts:
            counts[st] += 1
    return {
        "id": row.get("id"),
        "label": row.get("label"),
        "curation_mode": row.get("curation_mode"),
        "pool_family": row.get("pool_family"),
        "pool_slot": row.get("pool_slot"),
        "workflow_families": list(row.get("workflow_families") or []),
        "counts": counts,
        "item_count": sum(counts.values()),
        "feed_count": counts["keep"] + counts["pin"],
        "updated_at": row.get("updated_at"),
    }


def _append_decision(row: Dict[str, Any], *, data_root: Optional[Path] = None) -> None:
    path = decisions_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def set_bin_item(
    *,
    bin_id: str,
    content_id: str,
    status: str,
    relpath: str = "",
    surface: str = "api",
    data_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Upsert a still into the bin (or clear it back to New) and append a ledger row.

    ``status`` of ``clear`` / ``new`` removes the still from the bin so it is New again.
    """
    cid = str(content_id or "").strip().lower()
    if not cid or len(cid) != 64:
        raise ValueError("content_id must be 64-hex")
    st = str(status or "").strip().lower()
    clear = st in {"clear", "new", "unset", ""}
    if not clear and st not in BIN_ITEM_STATUSES:
        raise ValueError(f"status must be one of {BIN_ITEM_STATUSES} or clear/new")
    rel = str(relpath or "").strip().replace("\\", "/")
    if not clear and not rel:
        raise ValueError("relpath required")
    now = _utc_now()
    path = bins_path(data_root)
    found: Optional[Dict[str, Any]] = None
    with _lock:
        doc = load_bins_doc(data_root)
        bins = doc.setdefault("bins", {})
        if not isinstance(bins, dict):
            bins = {}
            doc["bins"] = bins
        row = bins.get(bin_id)
        if not isinstance(row, dict):
            if bin_id == DEFAULT_BIN_ID:
                row = _default_bin()
                bins[bin_id] = row
            else:
                raise KeyError(f"unknown bin: {bin_id}")
        items = row.get("items")
        if not isinstance(items, list):
            items = []
            row["items"] = items
        if clear:
            kept: List[Dict[str, Any]] = []
            for it in items:
                if isinstance(it, dict) and str(it.get("content_id") or "").lower() == cid:
                    found = dict(it)
                    found["status"] = None
                    found["updated_at"] = now
                    continue
                if isinstance(it, dict):
                    kept.append(it)
            row["items"] = kept
            if found is None:
                found = {"content_id": cid, "relpath": rel, "status": None, "updated_at": now}
        else:
            for it in items:
                if isinstance(it, dict) and str(it.get("content_id") or "").lower() == cid:
                    found = it
                    break
            if found is None:
                found = {"content_id": cid, "relpath": rel, "status": st, "updated_at": now}
                items.append(found)
            else:
                found["status"] = st
                found["relpath"] = rel or found.get("relpath")
                found["updated_at"] = now
        row["updated_at"] = now
        doc["updated_at"] = now
        _atomic_write_json(path, doc)
    action = "clear" if clear else st
    decision = {
        "at": now,
        "bin_id": bin_id,
        "content_id": cid,
        "relpath": rel or (found.get("relpath") if isinstance(found, dict) else "") or "",
        "action": action,
        "surface": str(surface or "api").strip() or "api",
    }
    _append_decision(decision, data_root=data_root)
    return {"ok": True, "item": found, "bin": bin_summary(bin_id, data_root=data_root), "decision": decision}


def clear_bin_steering(
    *,
    bin_id: str,
    surface: str = "api",
    data_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Wipe Keep/Pin/Later/Out for a bin so every still is New again."""
    bid = str(bin_id or "").strip() or DEFAULT_BIN_ID
    now = _utc_now()
    path = bins_path(data_root)
    cleared = 0
    with _lock:
        doc = load_bins_doc(data_root)
        bins = doc.setdefault("bins", {})
        if not isinstance(bins, dict):
            bins = {}
            doc["bins"] = bins
        row = bins.get(bid)
        if not isinstance(row, dict):
            raise KeyError(f"unknown bin: {bid}")
        items = row.get("items")
        if isinstance(items, list):
            cleared = sum(1 for it in items if isinstance(it, dict) and it.get("status") in BIN_ITEM_STATUSES)
        row["items"] = []
        row["updated_at"] = now
        doc["updated_at"] = now
        _atomic_write_json(path, doc)
    decision = {
        "at": now,
        "bin_id": bid,
        "action": "clear",
        "cleared": cleared,
        "surface": str(surface or "api").strip() or "api",
    }
    _append_decision(decision, data_root=data_root)
    return {
        "ok": True,
        "bin_id": bid,
        "cleared": cleared,
        "bin": bin_summary(bid, data_root=data_root),
        "decision": decision,
    }


def bin_status_maps(
    bin_id: str = DEFAULT_BIN_ID, *, data_root: Optional[Path] = None
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Return (by_content_id → status, by_relpath_norm → status) for feed/out lookups."""
    row = get_bin(bin_id, data_root=data_root)
    by_cid: Dict[str, str] = {}
    by_rel: Dict[str, str] = {}
    for it in row.get("items") or []:
        if not isinstance(it, dict):
            continue
        st = str(it.get("status") or "").strip()
        if st not in BIN_ITEM_STATUSES:
            continue
        cid = str(it.get("content_id") or "").strip().lower()
        rel = str(it.get("relpath") or "").strip().replace("\\", "/").lstrip("/")
        if cid:
            by_cid[cid] = st
        if rel:
            by_rel[rel.lower()] = st
            by_rel[Path(rel).name.lower()] = st
    return by_cid, by_rel


def still_bin_bias_mult(
    path: str,
    *,
    family: str = "",
    bin_id: str = "",
    data_root: Optional[Path] = None,
) -> float:
    """Soft weight for hourly still lottery. ``out`` → 0; pin/keep boost; else 1."""
    data_root = data_root or default_data_root()
    fam = str(family or "").strip()
    bid = str(bin_id or "").strip()
    if not bid:
        bid = resolve_bin_id_for_family(fam, data_root=data_root) or ""
    if not bid:
        return 1.0
    try:
        row = get_bin(bid, data_root=data_root)
    except Exception:
        return 1.0
    workflows = {str(x).strip() for x in (row.get("workflow_families") or []) if str(x).strip()}
    pool_fam = str(row.get("pool_family") or "").strip()
    if fam:
        if workflows and fam not in workflows and fam != pool_fam:
            return 1.0
    by_cid, by_rel = bin_status_maps(bid, data_root=data_root)
    if not by_cid and not by_rel:
        return 1.0
    raw = str(path or "").strip().replace("\\", "/")
    keys = [raw, raw.lstrip("/"), Path(raw).name]
    cid = extract_content_id(raw)
    status = ""
    if cid and cid in by_cid:
        status = by_cid[cid]
    if not status:
        for k in keys:
            hit = by_rel.get(k.lower())
            if hit:
                status = hit
                break
    if not status:
        return 1.0
    if status == "out":
        return 0.0
    if status == "later":
        return 0.25
    bias = float(row.get("bias_mult") or 8.0)
    pin = float(row.get("pin_mult") or 16.0)
    if status == "pin":
        return max(1.0, pin)
    if status == "keep":
        return max(1.0, bias)
    return 1.0


def list_bin_candidates(
    *,
    bin_id: str = DEFAULT_BIN_ID,
    data_root: Optional[Path] = None,
    limit: int = 48,
    appetite_doc: Optional[Dict[str, Any]] = None,
    include_decided: bool = True,
) -> Dict[str, Any]:
    """
    Candidate stills for phone curation.

    Order is a **stable catalog newest** deck: Keep/Pin/Later/Out never move a
    still's position — ``prior_status`` is attached in place. (No status / appetite
    re-ranking until explicit sort controls exist.)
    """
    data_root = (data_root or default_data_root()).resolve()
    try:
        from shape_factory_input_curation import list_catalog_stills
    except Exception as e:
        return {"ok": False, "error": f"catalog_unavailable: {e}", "items": []}

    lim = max(1, min(120, int(limit)))
    payload = list_catalog_stills(
        data_root=data_root,
        q="",
        limit=max(lim * 2, 64),
        offset=0,
        scan=False,
        tag="",
        appetite="",
        sort="newest",
        appetite_doc=None,
    )
    by_cid, _by_rel = bin_status_maps(bin_id, data_root=data_root)

    # Attach appetite from the live index (catalog join is filter-gated).
    ap_by_key: Dict[str, Dict[str, Any]] = {}
    if isinstance(appetite_doc, dict):
        try:
            from shape_factory_input_curation import _still_appetite_lookup_maps

            ap_by_key = _still_appetite_lookup_maps(appetite_doc) or {}
        except Exception:
            table = appetite_doc.get("by_output_relpath")
            if isinstance(table, dict):
                for k, row in table.items():
                    if isinstance(row, dict) and row.get("appetite"):
                        ap_by_key[str(k).replace("\\", "/").lower()] = row

    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for it in payload.get("items") or []:
        if not isinstance(it, dict):
            continue
        cid = str(it.get("content_id") or "").strip().lower() or extract_content_id(it.get("relpath"))
        if not cid or cid in seen:
            continue
        rel = str(it.get("relpath") or "").strip().replace("\\", "/")
        if not rel:
            continue
        ap = str(it.get("appetite") or "").strip().lower()
        facet = it.get("appetite_facet")
        if not ap and ap_by_key:
            for key in (rel.lower(), Path(rel).name.lower(), f"input/{Path(rel).name}".lower()):
                hit = ap_by_key.get(key)
                if isinstance(hit, dict) and hit.get("appetite"):
                    ap = str(hit.get("appetite") or "").strip().lower()
                    facet = hit.get("facet") or hit.get("appetite_facet")
                    break
        quoted = "/files/" + urllib.parse.quote(rel, safe="/")
        prior = by_cid.get(cid) if include_decided else None
        items.append(
            {
                "content_id": cid,
                "relpath": rel,
                "basename": it.get("basename") or Path(rel).name,
                "url": quoted,
                "thumb_url": quoted,
                "appetite": ap or None,
                "appetite_facet": facet,
                "mtime": it.get("mtime"),
                "prior_status": prior,
            }
        )
        seen.add(cid)
        if len(items) >= lim:
            break

    # Do not append out-of-window bin items — that reshuffles the deck after
    # steering. Keep/Pin/Out outside this catalog window still bias hourlies via
    # bins.json; they just are not in this fixed phone deck.

    return {
        "ok": True,
        "bin_id": bin_id,
        "bin": bin_summary(bin_id, data_root=data_root),
        "pipe": next(
            (p for p in (load_pipes_doc(data_root).get("pipes") or []) if isinstance(p, dict) and p.get("bin_id") == bin_id),
            None,
        ),
        "items": items,
        "count": len(items),
    }


def home_steer_teaser(*, data_root: Optional[Path] = None) -> Dict[str, Any]:
    """Compact Home / Hourlies strip payload for seed-still steering."""
    data_root = data_root or default_data_root()
    targets = list_steer_targets(data_root=data_root)
    summary = bin_summary(DEFAULT_BIN_ID, data_root=data_root)
    pipes = load_pipes_doc(data_root).get("pipes") or []
    pipe = next((p for p in pipes if isinstance(p, dict) and p.get("bin_id") == DEFAULT_BIN_ID), None)
    n = len(targets)
    return {
        "ok": True,
        "bin": summary,
        "targets": targets,
        "target_count": n,
        "pipe": pipe,
        "curate_href": "/discovery/factory-map/hourlies/curate",
        "hint": (
            f"Steer {n} source_still pools — Keep/Pin/Later/Out soft-bias hourly picks per family"
            if n
            else "Sort stills into per-family hourly seed bins — keep / pin / later / out"
        ),
    }
