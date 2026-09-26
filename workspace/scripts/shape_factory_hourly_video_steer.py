"""Hourly steer for source_video families — clip-shaped units (span + whole-file).

Whole-file units are virtual (`whole:{parent_content_id}`) and are NOT inserted into
the clips DB (absence of a span clip already means full-file Use).
"""

from __future__ import annotations

import hashlib
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from shape_factory_hourly_bins import (
    BIN_ITEM_STATUSES,
    _atomic_write_json,
    _lock,
    _pools_root,
    _utc_now,
    bin_summary,
    bins_path,
    default_data_root,
    extract_content_id,
    get_bin,
    load_bins_doc,
)

CURATION_UNITS = ("auto", "clips", "videos")
WHOLE_PREFIX = "whole:"
_CLIP_ID_RE = re.compile(r"^clip_[0-9a-f]{32}$", re.I)
# Pool globs (FB9_GEX ~1.4k) dominate reload cost; short TTL keeps decks snappy.
# Value: (monotonic_ts, scored newest-first [(mtime, path), ...])
_MEMBERS_CACHE: Dict[str, Tuple[float, List[Tuple[float, Path]]]] = {}
_MEMBERS_TTL_S = 60.0


def is_whole_unit_id(item_id: str) -> bool:
    return str(item_id or "").strip().lower().startswith(WHOLE_PREFIX)


def is_span_clip_id(item_id: str) -> bool:
    return bool(_CLIP_ID_RE.match(str(item_id or "").strip()))


def is_steer_item_id(item_id: str) -> bool:
    """Accept still 64-hex, span clip_*, or whole:* virtual ids."""
    s = str(item_id or "").strip()
    if not s:
        return False
    if len(s) == 64 and all(c in "0123456789abcdef" for c in s.lower()):
        return True
    if is_span_clip_id(s) or is_whole_unit_id(s):
        return True
    return False


def whole_file_clip_id(parent_content_id: str) -> str:
    pid = str(parent_content_id or "").strip().lower()
    if not pid:
        pid = "unknown"
    return f"{WHOLE_PREFIX}{pid}"


def parent_id_from_whole_unit(item_id: str) -> Optional[str]:
    s = str(item_id or "").strip()
    if not s.lower().startswith(WHOLE_PREFIX):
        return None
    return s[len(WHOLE_PREFIX) :].strip().lower() or None


def stable_path_content_id(path: str) -> str:
    """64-hex id: embedded sha if present, else sha256 of normalized path string."""
    raw = str(path or "").strip().replace("\\", "/")
    hit = extract_content_id(raw)
    if hit:
        return hit
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _bin_id_for_video_family(family: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(family or "").strip()).strip("-_.")
    safe = safe or "unknown"
    return f"steer-video-{safe}"


def discover_source_video_families(*, data_root: Optional[Path] = None) -> List[str]:
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
        if re.search(r"(?m)^\s*source_video\s*:", text) or re.search(
            r"(?m)^\s*slot:\s*source_video\s*$", text
        ):
            out.append(fam_dir.name)
    return out


def ensure_steer_bins_for_source_videos(*, data_root: Optional[Path] = None) -> List[str]:
    """Create empty 1:1 clip-shaped bins for each source_video family."""
    data_root = (data_root or default_data_root()).resolve()
    families = discover_source_video_families(data_root=data_root)
    created: List[str] = []
    with _lock:
        doc = load_bins_doc(data_root)
        bins = doc.setdefault("bins", {})
        if not isinstance(bins, dict):
            bins = {}
            doc["bins"] = bins
        dirty = False
        for fam in families:
            existing_id = None
            for bid, row in bins.items():
                if not isinstance(row, dict):
                    continue
                if str(row.get("pool_family") or "").strip() != fam:
                    continue
                slot = str(row.get("pool_slot") or "").strip() or "source_still"
                kind = str(row.get("asset_kind") or "").strip()
                if slot == "source_video" or kind in {"clip", "video", "video_seed"}:
                    existing_id = str(row.get("id") or bid)
                    if list(row.get("workflow_families") or []) != [fam]:
                        row["workflow_families"] = [fam]
                        row["updated_at"] = _utc_now()
                        dirty = True
                    if str(row.get("curation_unit") or "").strip() not in CURATION_UNITS:
                        row["curation_unit"] = "auto"
                        dirty = True
                    break
            if existing_id:
                continue
            bid = _bin_id_for_video_family(fam)
            if isinstance(bins.get(bid), dict):
                continue
            now = _utc_now()
            bins[bid] = {
                "id": bid,
                "label": f"{fam} seed clips",
                "curation_mode": "manual",
                "curation_unit": "auto",
                "asset_kind": "clip",
                "pool_family": fam,
                "pool_slot": "source_video",
                "workflow_families": [fam],
                "bias_mult": 8.0,
                "pin_mult": 16.0,
                "items": [],
                "updated_at": now,
            }
            created.append(bid)
            dirty = True
        if dirty:
            doc["updated_at"] = _utc_now()
            _atomic_write_json(bins_path(data_root), doc)
    return created


def resolve_video_bin_id_for_family(family: str, *, data_root: Optional[Path] = None) -> Optional[str]:
    fam = str(family or "").strip()
    if not fam:
        return None
    data_root = (data_root or default_data_root()).resolve()
    try:
        ensure_steer_bins_for_source_videos(data_root=data_root)
    except Exception:
        pass
    doc = load_bins_doc(data_root)
    bins = doc.get("bins") if isinstance(doc.get("bins"), dict) else {}
    for bid, row in bins.items():
        if not isinstance(row, dict):
            continue
        if str(row.get("pool_family") or "").strip() != fam:
            continue
        slot = str(row.get("pool_slot") or "").strip()
        if slot == "source_video":
            return str(row.get("id") or bid)
    return None


def set_bin_curation_unit(
    bin_id: str,
    unit: str,
    *,
    data_root: Optional[Path] = None,
) -> Dict[str, Any]:
    u = str(unit or "").strip().lower()
    if u not in CURATION_UNITS:
        raise ValueError(f"curation_unit must be one of {CURATION_UNITS}")
    bid = str(bin_id or "").strip()
    with _lock:
        doc = load_bins_doc(data_root)
        bins = doc.setdefault("bins", {})
        row = bins.get(bid)
        if not isinstance(row, dict):
            raise KeyError(f"unknown bin: {bid}")
        row["curation_unit"] = u
        row["updated_at"] = _utc_now()
        doc["updated_at"] = row["updated_at"]
        _atomic_write_json(bins_path(data_root), doc)
    return {"ok": True, "bin": bin_summary(bid, data_root=data_root)}


def _rel_url(rel: str) -> str:
    return "/files/" + urllib.parse.quote(str(rel).replace("\\", "/").lstrip("/"), safe="/")


def _fast_media_rel(path: Path) -> str:
    """String-only /files/ rel — no exists()/resolve() (those dominate huge decks)."""
    raw = str(path).replace("\\", "/")
    for prefix, kind in (
        ("/home/yuji/comfyui-runpod-data/output/", "out"),
        ("/workspace/output/", "out"),
        ("/home/yuji/comfyui-runpod-data/input/", "in"),
        ("/workspace/input/", "in"),
    ):
        if raw.startswith(prefix):
            rest = raw[len(prefix) :]
            return rest if kind == "out" else f"input/{rest}"
    if "/output/" in f"/{raw}":
        return raw.split("/output/", 1)[-1]
    if "/input/" in f"/{raw}":
        return "input/" + raw.split("/input/", 1)[-1]
    return Path(raw).name


def _fast_parent_id(path: Path, rel: str) -> str:
    """Prefer embedded sha; else stable path hash. Never touch asset registry."""
    hit = extract_content_id(path.name) or extract_content_id(rel)
    if hit:
        return hit
    return stable_path_content_id(rel or str(path))


def _collect_video_members_scored(family: str, *, data_root: Path) -> List[Tuple[float, Path]]:
    """Newest-first (mtime, path) for source_video — cached, no appetite filter."""
    fam = str(family or "").strip()
    cache_key = f"{data_root}::{fam}"
    now = time.monotonic()
    hit = _MEMBERS_CACHE.get(cache_key)
    if hit and (now - hit[0]) < _MEMBERS_TTL_S:
        return list(hit[1])

    pools_path = data_root / "pools" / fam / "pools.yaml"
    if not pools_path.is_file():
        return []
    try:
        from shape_factory import load_yaml, resolve_pool_members
    except Exception:
        return []
    try:
        pools_doc = load_yaml(pools_path)
    except Exception:
        return []
    pools = pools_doc.get("pools") if isinstance(pools_doc.get("pools"), dict) else {}
    pool_def = pools.get("source_video") if isinstance(pools.get("source_video"), dict) else None
    if not isinstance(pool_def, dict):
        for cand in pools.values():
            if isinstance(cand, dict) and str(cand.get("slot") or "") == "source_video":
                pool_def = cand
                break
    if not isinstance(pool_def, dict):
        return []
    try:
        members = list(resolve_pool_members(pool_def))
    except Exception:
        return []
    scored: List[Tuple[float, Path]] = []
    for p in members:
        try:
            mt = float(p.stat().st_mtime)
        except OSError:
            mt = 0.0
        scored.append((mt, p))
    scored.sort(key=lambda t: t[0], reverse=True)
    _MEMBERS_CACHE[cache_key] = (now, scored)
    return list(scored)


def _batch_clips_by_parent(
    clips_con: Any, parent_ids: List[str]
) -> Dict[str, List[Dict[str, Any]]]:
    """One clips query + one stars query for the whole parent set."""
    out: Dict[str, List[Dict[str, Any]]] = {pid: [] for pid in parent_ids}
    ids = [p for p in parent_ids if p]
    if not ids or clips_con is None:
        return out
    try:
        from shape_factory_clips import _row_to_clip
    except Exception:
        return out
    placeholders = ",".join("?" for _ in ids)
    try:
        rows = clips_con.execute(
            f"""
            SELECT * FROM clips
            WHERE parent_content_id IN ({placeholders})
              AND (deleted_at IS NULL OR TRIM(deleted_at) = '')
            ORDER BY mark_in_s ASC, created_at ASC
            """,
            ids,
        ).fetchall()
    except Exception:
        return out
    starred: set[Tuple[str, str]] = set()
    try:
        star_rows = clips_con.execute(
            f"""
            SELECT parent_content_id, clip_id FROM asset_clip_stars
            WHERE parent_content_id IN ({placeholders})
            """,
            ids,
        ).fetchall()
        for sr in star_rows or []:
            starred.add((str(sr["parent_content_id"]), str(sr["clip_id"])))
    except Exception:
        starred = set()
    for row in rows or []:
        try:
            d = _row_to_clip(row)
        except Exception:
            d = None
        if not d:
            continue
        pid = str(d.get("parent_content_id") or row["parent_content_id"] or "").strip()
        cid = str(d.get("clip_id") or "").strip()
        if not pid or pid not in out:
            continue
        d["is_starred"] = (pid, cid) in starred
        out[pid].append(d)
    return out


def _open_clips_ro(registry_path: Path) -> Any:
    """Read-only clips connection; skip schema migrations (they contend under load)."""
    import sqlite3

    path = Path(registry_path)
    if not path.is_file():
        return None
    try:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=2.0)
        con.row_factory = sqlite3.Row
        # Cheap existence check — if clips table missing, treat as empty.
        row = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='clips' LIMIT 1"
        ).fetchone()
        if not row:
            con.close()
            return None
        return con
    except Exception:
        return None


def list_video_bin_candidates(
    *,
    bin_id: str,
    data_root: Optional[Path] = None,
    limit: int = 48,
    registry_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Hybrid deck: span clips (★ first) then whole-file virtual units.
    Honors bin ``curation_unit``: auto | clips | videos.

    Hot path avoids asset-registry writes, path.resolve(), and N+1 clip queries.
    """
    data_root = (data_root or default_data_root()).resolve()
    try:
        row = get_bin(bin_id, data_root=data_root)
    except Exception as e:
        return {"ok": False, "error": str(e), "items": []}
    fam = str(row.get("pool_family") or "").strip()
    if not fam:
        return {"ok": False, "error": "bin_missing_pool_family", "items": []}
    unit = str(row.get("curation_unit") or "auto").strip().lower()
    if unit not in CURATION_UNITS:
        unit = "auto"
    lim = max(1, min(120, int(limit)))

    members_scored = _collect_video_members_scored(fam, data_root=data_root)
    # Newest parents first; only take what we might show (+ a small surplus for clips mode).
    scan_n = max(lim * 2, 64) if unit != "videos" else max(lim, 48)
    scored = members_scored[:scan_n]

    parent_meta: List[Tuple[Path, str, str, float]] = []  # path, parent_cid, rel, mtime
    for mt, path in scored:
        rel = _fast_media_rel(path)
        parent_cid = _fast_parent_id(path, rel)
        parent_meta.append((path, parent_cid, rel, mt))

    clips_con = None
    clips_by_parent: Dict[str, List[Dict[str, Any]]] = {}
    try:
        from shape_factory import default_asset_registry_path

        reg = Path(registry_path) if registry_path else default_asset_registry_path(data_root)
        clips_con = _open_clips_ro(reg)
        if clips_con is not None and unit != "videos":
            clips_by_parent = _batch_clips_by_parent(
                clips_con, [pid for _, pid, _, _ in parent_meta]
            )
    except Exception:
        clips_by_parent = {}

    by_cid, _by_rel = _bin_item_maps(bin_id, data_root=data_root)

    span_starred: List[Dict[str, Any]] = []
    span_other: List[Dict[str, Any]] = []
    wholes: List[Dict[str, Any]] = []

    try:
        for _path, parent_cid, rel, mt in parent_meta:
            quoted = _rel_url(rel)
            live = clips_by_parent.get(parent_cid) or []

            if unit != "videos":
                for clip in live:
                    cid = str(clip.get("clip_id") or "").strip()
                    if not cid:
                        continue
                    item = {
                        "content_id": cid,
                        "clip_id": cid,
                        "unit": "span",
                        "relpath": rel,
                        "basename": Path(rel).name,
                        "url": quoted,
                        "thumb_url": quoted,
                        "mark_in_s": clip.get("mark_in_s"),
                        "mark_out_s": clip.get("mark_out_s"),
                        "label": clip.get("label"),
                        "starred": bool(clip.get("is_starred")),
                        "parent_content_id": parent_cid,
                        "media_kind": "video",
                        "prior_status": by_cid.get(cid.lower()) or by_cid.get(cid),
                        "mtime": None,
                        "updated_at": clip.get("updated_at"),
                    }
                    if item["starred"]:
                        span_starred.append(item)
                    else:
                        span_other.append(item)

            if unit != "clips":
                wid = whole_file_clip_id(parent_cid)
                wholes.append(
                    {
                        "content_id": wid,
                        "clip_id": wid,
                        "unit": "whole",
                        "relpath": rel,
                        "basename": Path(rel).name,
                        "url": quoted,
                        "thumb_url": quoted,
                        "mark_in_s": 0.0,
                        "mark_out_s": None,
                        "label": "Whole file",
                        "starred": False,
                        "parent_content_id": parent_cid,
                        "media_kind": "video",
                        "prior_status": by_cid.get(wid.lower()) or by_cid.get(wid),
                        "mtime": mt,
                        "has_span_clips": bool(live),
                    }
                )
    finally:
        if clips_con is not None:
            try:
                clips_con.close()
            except Exception:
                pass

    def _clip_sort_key(it: Dict[str, Any]) -> Tuple[str, str]:
        return (str(it.get("updated_at") or ""), str(it.get("clip_id") or ""))

    span_starred.sort(key=_clip_sort_key, reverse=True)
    span_other.sort(key=_clip_sort_key, reverse=True)
    # wholes already newest-parent order from scored sort

    if unit == "clips":
        deck = span_starred + span_other
    elif unit == "videos":
        deck = wholes
    else:
        deck = span_starred + span_other + wholes

    items = deck[:lim]
    return {
        "ok": True,
        "bin_id": bin_id,
        "bin": bin_summary(bin_id, data_root=data_root),
        "curation_unit": unit,
        "items": items,
        "count": len(items),
    }


def _bin_item_maps(bin_id: str, *, data_root: Optional[Path] = None) -> Tuple[Dict[str, str], Dict[str, str]]:
    row = get_bin(bin_id, data_root=data_root)
    by_id: Dict[str, str] = {}
    by_rel: Dict[str, str] = {}
    for it in row.get("items") or []:
        if not isinstance(it, dict):
            continue
        st = str(it.get("status") or "").strip()
        if st not in BIN_ITEM_STATUSES:
            continue
        for key in (
            str(it.get("content_id") or "").strip(),
            str(it.get("clip_id") or "").strip(),
        ):
            if key:
                by_id[key.lower()] = st
                by_id[key] = st
        rel = str(it.get("relpath") or "").strip().replace("\\", "/").lstrip("/")
        if rel:
            by_rel[rel.lower()] = st
            by_rel[Path(rel).name.lower()] = st
    return by_id, by_rel


def _status_mult(status: str, row: Dict[str, Any]) -> float:
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


def video_steer_bias_mult(
    *,
    family: str = "",
    clip_id: Optional[str] = None,
    path: str = "",
    parent_content_id: Optional[str] = None,
    data_root: Optional[Path] = None,
) -> float:
    """Soft weight for video-family hourly seeds (span clip or whole-file unit)."""
    fam = str(family or "").strip()
    data_root = data_root or default_data_root()
    bid = resolve_video_bin_id_for_family(fam, data_root=data_root) if fam else None
    if not bid:
        return 1.0
    try:
        row = get_bin(bid, data_root=data_root)
    except Exception:
        return 1.0
    by_id, by_rel = _bin_item_maps(bid, data_root=data_root)
    if not by_id and not by_rel:
        return 1.0

    status = ""
    cid = str(clip_id or "").strip()
    if cid:
        status = by_id.get(cid.lower()) or by_id.get(cid) or ""
    if not status and parent_content_id:
        wid = whole_file_clip_id(str(parent_content_id))
        status = by_id.get(wid.lower()) or by_id.get(wid) or ""
    if not status and path:
        raw = str(path).replace("\\", "/")
        for k in (raw, raw.lstrip("/"), Path(raw).name):
            hit = by_rel.get(k.lower())
            if hit:
                status = hit
                break
        # whole-file id from path hash / embedded
        pid = extract_content_id(raw) or stable_path_content_id(raw)
        wid = whole_file_clip_id(pid)
        status = status or by_id.get(wid.lower()) or by_id.get(wid) or ""
    if not status:
        return 1.0
    return _status_mult(status, row)
