"""
Incremental Discovery og/wip index tip-in / ensure.

Keeps ``discovery_og_wip_index.json`` fresh without a full ``rglob`` when a single
output path is known (factory deposit, lineage miss heal).
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_MEDIA_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm"}
_VIDEO_EXTS = {".mp4", ".webm"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_INDEX_LOCK = threading.Lock()


def _normalize_rel_posix(p: str) -> str:
    s = str(p or "").strip().replace("\\", "/").lstrip("/")
    while "//" in s:
        s = s.replace("//", "/")
    return s


def _is_ephemeral(name: str) -> bool:
    n = str(name or "").upper()
    if "_FINAL_" in n:
        return False
    return "_RAW_" in n or "_PREVIEW_" in n


def _file_content_hash(path: Path) -> str:
    st = path.stat()
    size = int(st.st_size)
    h = hashlib.sha256()
    if size <= 25_000_000:
        with path.open("rb") as f:
            while True:
                buf = f.read(1024 * 1024)
                if not buf:
                    break
                h.update(buf)
        return h.hexdigest()
    h.update(str(size).encode())
    h.update(str(int(st.st_mtime)).encode())
    with path.open("rb") as f:
        h.update(f.read(min(2_000_000, size)))
    return h.hexdigest()


def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load_index(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {
            "version": 5,
            "updated_at": "",
            "libraries": {},
            "item_count": 0,
            "items": [],
            "skipped_raw_files": 0,
            "scan_ms": 0,
        }
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "version": 5,
            "updated_at": "",
            "libraries": {},
            "item_count": 0,
            "items": [],
            "skipped_raw_files": 0,
            "scan_ms": 0,
        }
    if not isinstance(obj, dict):
        return {
            "version": 5,
            "updated_at": "",
            "libraries": {},
            "item_count": 0,
            "items": [],
            "skipped_raw_files": 0,
            "scan_ms": 0,
        }
    if not isinstance(obj.get("items"), list):
        obj["items"] = []
    return obj


def _lib_for_rel(rel: str) -> Optional[str]:
    low = rel.lower()
    if low.startswith("og/") or "/og/" in f"/{low}":
        # Prefer path that starts with og/
        if low.startswith("og/"):
            return "og"
        # output/og/... flattened weirdness
        parts = low.split("/")
        if "og" in parts:
            return "og"
    if low.startswith("wip/") or (low.split("/") and "wip" in low.split("/")):
        if low.startswith("wip/"):
            return "wip"
        parts = low.split("/")
        if "wip" in parts:
            return "wip"
    return None


def _member_for_file(path: Path, rel_posix: str, lib: str) -> Dict[str, Any]:
    ext_lc = path.suffix.lower()
    try:
        st = path.stat()
        mtime = float(st.st_mtime)
        size = int(st.st_size)
    except OSError:
        mtime = 0.0
        size = 0
    return {
        "relpath": rel_posix,
        "library": lib,
        "name": path.name,
        "ext": ext_lc,
        "mtime": mtime,
        "size": size,
        "sha256": _file_content_hash(path),
        "workflow_fingerprint": None,
        "class_types_preview": [],
        "has_embedded_prompt": False,
    }


def _merge_group(lib: str, dir_posix: str, group_stem: str, members: List[Dict[str, Any]]) -> Dict[str, Any]:
    videos = [m for m in members if m.get("ext") in _VIDEO_EXTS]
    images = [m for m in members if m.get("ext") in _IMAGE_EXTS]
    primary_video = max(videos, key=lambda m: (float(m.get("mtime") or 0), int(m.get("size") or 0))) if videos else None
    thumb_image = max(images, key=lambda m: (float(m.get("mtime") or 0), int(m.get("size") or 0))) if images else None
    primary = primary_video or thumb_image or members[0]
    members_out: List[Dict[str, str]] = []
    for m in sorted(members, key=lambda x: (str(x.get("ext") or ""), str(x.get("name") or ""))):
        ext = str(m.get("ext") or "").lower()
        if ext in _VIDEO_EXTS:
            kk = "video"
        elif ext in _IMAGE_EXTS:
            kk = "image"
        else:
            kk = "other"
        members_out.append(
            {"relpath": str(m.get("relpath") or ""), "name": str(m.get("name") or ""), "kind": kk}
        )
    h = hashlib.sha256()
    for m in sorted(members, key=lambda x: str(x.get("relpath"))):
        h.update(str(m.get("sha256") or "").encode("utf-8", "replace"))
        h.update(b"\n")
    mtime = max(float(m.get("mtime") or 0) for m in members) if members else 0.0
    size_sum = sum(int(m.get("size") or 0) for m in members)
    return {
        "group_id": f"{lib}:stem:{group_stem}",
        "relpath": str(primary.get("relpath") or ""),
        "library": lib,
        "name": str((primary_video or thumb_image or members[0]).get("name") or ""),
        "mtime": mtime,
        "size": size_sum,
        "sha256": h.hexdigest()[:64],
        "workflow_fingerprint": None,
        "class_types_preview": [],
        "has_embedded_prompt": False,
        "video_relpath": str(primary_video.get("relpath")) if primary_video else None,
        "thumb_relpath": str(thumb_image.get("relpath")) if thumb_image else None,
        "members": members_out,
        "dir": dir_posix,
    }


def _collect_stem_members(output_root: Path, abs_path: Path, rel_posix: str, lib: str) -> List[Dict[str, Any]]:
    """Primary file + same-stem siblings in the same directory."""
    members: List[Dict[str, Any]] = [_member_for_file(abs_path, rel_posix, lib)]
    stem = abs_path.stem
    parent = abs_path.parent
    try:
        out_resolved = output_root.resolve()
    except OSError:
        out_resolved = output_root
    if parent.is_dir():
        for sib in parent.iterdir():
            try:
                if not sib.is_file():
                    continue
            except OSError:
                continue
            if sib.name == abs_path.name:
                continue
            if sib.stem != stem:
                continue
            ext = sib.suffix.lower()
            if ext not in _MEDIA_EXTS:
                continue
            if _is_ephemeral(sib.name):
                continue
            try:
                sib_rel = sib.resolve().relative_to(out_resolved)
            except Exception:
                continue
            sib_posix = _normalize_rel_posix(str(sib_rel).replace("\\", "/"))
            if not sib_posix:
                continue
            members.append(_member_for_file(sib, sib_posix, lib))
    return members


def _item_covers_rel(item: Dict[str, Any], rel: str) -> bool:
    norm = _normalize_rel_posix(rel)
    cands: List[str] = []
    for k in ("relpath", "video_relpath", "thumb_relpath"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            cands.append(_normalize_rel_posix(v))
    mems = item.get("members")
    if isinstance(mems, list):
        for mm in mems:
            if isinstance(mm, dict):
                rv = mm.get("relpath")
                if isinstance(rv, str) and rv.strip():
                    cands.append(_normalize_rel_posix(rv))
    return norm in cands


def upsert_discovery_relpath(
    *,
    index_path: Path,
    output_root: Path,
    relpath: str,
) -> Dict[str, Any]:
    """
    Upsert one stem group for ``relpath`` into the Discovery index.

    Returns ``{ok, item?, created?, updated?, error?, detail?}``.
    """
    rel = _normalize_rel_posix(relpath)
    if not rel:
        return {"ok": False, "error": "missing_or_bad_relpath"}
    lib = _lib_for_rel(rel)
    if not lib:
        return {"ok": False, "error": "not_og_or_wip", "detail": rel}
    ext = Path(rel).suffix.lower()
    if ext not in _MEDIA_EXTS:
        return {"ok": False, "error": "unsupported_ext", "detail": ext}
    if _is_ephemeral(Path(rel).name):
        return {"ok": False, "error": "ephemeral_artifact", "detail": Path(rel).name}

    abs_path = (output_root / rel).resolve()
    try:
        out_resolved = output_root.resolve()
        abs_path.relative_to(out_resolved)
    except Exception:
        return {"ok": False, "error": "path_outside_output_root", "detail": rel}
    if not abs_path.is_file():
        return {"ok": False, "error": "file_missing", "detail": rel}

    members = _collect_stem_members(output_root, abs_path, rel, lib)
    group_stem = Path(abs_path.name).stem.lower()
    dir_posix = _normalize_rel_posix(str(Path(rel).parent).replace("\\", "/")) or "."
    new_item = _merge_group(lib, dir_posix, group_stem, members)
    gid = str(new_item.get("group_id") or "")

    with _INDEX_LOCK:
        idx = _load_index(index_path)
        items = list(idx.get("items") or [])
        existing_i = -1
        already = False
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                continue
            if str(it.get("group_id") or "") == gid or _item_covers_rel(it, rel):
                existing_i = i
                if _item_covers_rel(it, rel) and str(it.get("group_id") or "") == gid:
                    # Refresh members anyway so companion png/mp4 pairs stay current.
                    pass
                already = _item_covers_rel(it, rel)
                break

        created = existing_i < 0
        if existing_i >= 0:
            items[existing_i] = new_item
        else:
            items.append(new_item)

        items.sort(key=lambda it: float(it.get("mtime") or 0) if isinstance(it, dict) else 0.0, reverse=True)
        idx["version"] = max(5, int(idx.get("version") or 0))
        idx["updated_at"] = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        idx["item_count"] = len(items)
        idx["items"] = items
        _atomic_write_json(index_path, idx)

    return {
        "ok": True,
        "item": new_item,
        "created": created,
        "updated": not created,
        "already_present": already and not created,
        "group_id": gid,
        "relpath": rel,
        "index_path": str(index_path),
    }


def ensure_discovery_relpath(
    *,
    index_path: Path,
    output_root: Path,
    relpath: str,
) -> Dict[str, Any]:
    """If ``relpath`` is already indexed, return it; otherwise upsert."""
    rel = _normalize_rel_posix(relpath)
    if not rel:
        return {"ok": False, "error": "missing_or_bad_relpath"}
    idx = _load_index(index_path)
    for it in idx.get("items") or []:
        if isinstance(it, dict) and _item_covers_rel(it, rel):
            return {
                "ok": True,
                "item": it,
                "created": False,
                "updated": False,
                "already_present": True,
                "group_id": it.get("group_id"),
                "relpath": rel,
                "index_path": str(index_path),
            }
    return upsert_discovery_relpath(index_path=index_path, output_root=output_root, relpath=rel)


def tip_in_discovery_relpaths(
    *,
    index_path: Path,
    output_root: Path,
    relpaths: Sequence[str],
) -> Dict[str, Any]:
    """Best-effort upsert for many paths; never raises."""
    results: List[Dict[str, Any]] = []
    for raw in relpaths:
        try:
            results.append(
                ensure_discovery_relpath(
                    index_path=index_path,
                    output_root=output_root,
                    relpath=str(raw or ""),
                )
            )
        except Exception as exc:
            results.append({"ok": False, "error": "exception", "detail": str(exc), "relpath": raw})
    ok_n = sum(1 for r in results if r.get("ok"))
    created_n = sum(1 for r in results if r.get("created"))
    return {"ok": True, "count": len(results), "ok_count": ok_n, "created_count": created_n, "results": results}


def default_discovery_index_path(output_root: Path) -> Path:
    """``<output>/_status/discovery_og_wip_index.json`` with legacy nested fallback."""
    flat = output_root / "_status" / "discovery_og_wip_index.json"
    nested = output_root / "output" / "_status" / "discovery_og_wip_index.json"
    if flat.parent.is_dir() or flat.is_file():
        return flat
    if nested.is_file() or nested.parent.is_dir():
        return nested
    return flat


def relpath_under_output(output_root: Path, abs_path: Path) -> Optional[str]:
    try:
        return _normalize_rel_posix(str(abs_path.resolve().relative_to(output_root.resolve())).replace("\\", "/"))
    except Exception:
        return None
