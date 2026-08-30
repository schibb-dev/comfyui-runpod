#!/usr/bin/env python3
"""
Experiments UI server (API + static files) for comfyui-runpod.

Goals:
- Read experiment artifacts from the filesystem (manifest/params/submit/history JSON).
- Serve a small REST API for a React dashboard.
- Serve output MP4/PNG files referenced by history.json.
- No third-party dependencies (std-lib only) so it runs inside the existing container.

Default paths (inside container):
- workspace root: /workspace  (WORKSPACE_PATH env in this repo's Dockerfile)
- experiments root: /workspace/output/output/experiments
- output root (for /files): /workspace/output
- WIP browse root (Create from WIP): /workspace/output/output/wip unless EXPERIMENTS_UI_WIP_ROOT
  is set (e.g. output/output/og relative to workspace, or an absolute path)
- static dist: /workspace/experiments_ui/dist
"""

from __future__ import annotations

import argparse
import collections
import datetime as _dt
import hashlib
import heapq
import json
import math
import mimetypes
import os
import posixpath
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import uuid
import zlib
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve()
for _cand in (_HERE.parent, _HERE.parents[1] / "workspace" / "scripts"):
    try:
        if _cand.is_dir() and str(_cand) not in sys.path:
            sys.path.insert(0, str(_cand))
    except Exception:
        continue
from http_retry import http_json_with_retry, http_text_with_retry, urlopen_read_with_retry


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _http_json(
    method: str,
    url: str,
    body: Optional[Dict[str, Any]] = None,
    *,
    timeout_s: int = 10,
    retry_attempts: Optional[int] = None,
    retry_backoff_s: float = 0.25,
) -> Any:
    return http_json_with_retry(
        method=method,
        url=url,
        payload=body,
        timeout_s=timeout_s,
        retry_attempts=retry_attempts,
        retry_backoff_s=retry_backoff_s,
    )


def _http_text(
    url: str,
    *,
    timeout_s: int = 10,
    accept: str = "text/plain, */*",
    retry_attempts: int = 3,
    retry_backoff_s: float = 0.25,
) -> str:
    return http_text_with_retry(
        url=url,
        timeout_s=timeout_s,
        retry_attempts=retry_attempts,
        retry_backoff_s=retry_backoff_s,
        accept=accept,
    )


def _http_void(
    method: str,
    url: str,
    body: Optional[Dict[str, Any]] = None,
    *,
    timeout_s: int = 10,
    retry_attempts: Optional[int] = None,
    retry_backoff_s: float = 0.25,
) -> None:
    """HTTP call where response body shape is irrelevant."""
    data = None
    headers: Dict[str, str] = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    urlopen_read_with_retry(
        method=method,
        url=url,
        data=data,
        headers=headers,
        timeout_s=timeout_s,
        retry_attempts=retry_attempts,
        retry_backoff_s=retry_backoff_s,
    )


def _comfy_submit_prompt(
    comfy_server: str,
    prompt: Dict[str, Any],
    *,
    front: bool = False,
    client_id: str = "experiments-ui",
    timeout_s: int = 30,
    preview_method: str = "auto",
    extra_data: Optional[Dict[str, Any]] = None,
    outputs_to_execute: Optional[List[Any]] = None,
) -> Any:
    """
    POST a workflow graph to ComfyUI /prompt (same payload shape as the UI uses for requeue).
    Returns Comfy's JSON body. Raises on network / HTTP / JSON errors (urllib / json).

    When ``extra_data`` is provided (e.g. queue reorder), it is preserved so factory
    ``workflow_name`` / job_key metadata survives delete+resubmit.
    """
    comfy = str(comfy_server).rstrip("/")
    # Normalize Windows-style model paths (WAN\file.gguf → WAN/file.gguf) before validate.
    try:
        from comfyui_submit import _normalize_prompt_paths_for_linux  # type: ignore

        prompt = json.loads(json.dumps(prompt))
        _normalize_prompt_paths_for_linux(prompt)
    except Exception:
        prompt = json.loads(json.dumps(prompt)) if not isinstance(prompt, dict) else prompt
    payload: Dict[str, Any] = {"prompt": prompt, "client_id": client_id}
    if front:
        payload["front"] = True
    ed: Dict[str, Any] = {}
    if isinstance(extra_data, dict):
        # Shallow copy; drop nested client_id — top-level client_id is authoritative.
        ed = {k: v for k, v in extra_data.items() if k != "client_id"}
    method = str(preview_method or "").strip()
    if method:
        ed["preview_method"] = method
    if ed:
        payload["extra_data"] = ed
    if isinstance(outputs_to_execute, list) and outputs_to_execute:
        payload["outputs_to_execute"] = outputs_to_execute
    return _http_json("POST", f"{comfy}/prompt", payload, timeout_s=timeout_s)


def _comfy_convert_workflow_to_prompt_dict(
    cfg: "ServerConfig",
    workflow_or_api: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[int]]:
    """
    If already API prompt, return as-is. If UI workflow (nodes+links), POST to Comfy
    POST /workflow/convert (e.g. SethRobinson workflow-to-api-converter custom node).
    Returns (prompt_dict, error_message, http_status_if_http_error).
    """
    if _looks_like_comfy_api_prompt(workflow_or_api):
        return workflow_or_api, None, None
    if not _looks_like_comfy_ui_workflow(workflow_or_api):
        return None, "not_ui_workflow_or_api_prompt", None
    comfy = str(cfg.comfy_server).rstrip("/")
    url = f"{comfy}/workflow/convert"
    try:
        out = _http_json("POST", url, workflow_or_api, timeout_s=120)
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode("utf-8", "replace")[:4000]
        except Exception:
            raw = str(e)
        return None, f"http_{e.code}: {raw}", int(e.code)
    except Exception as e:
        return None, str(e), None
    if not isinstance(out, dict):
        return None, "comfy_convert_non_object", None
    if isinstance(out.get("error"), str) and not _looks_like_comfy_api_prompt(out):
        return None, str(out.get("error")), None
    if not _looks_like_comfy_api_prompt(out):
        return None, "comfy_convert_unexpected_shape", None
    return out, None, None


def _safe_int(x: Any) -> Optional[int]:
    try:
        return int(x)
    except Exception:
        return None


def _safe_float(x: Any) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None


def _slug(s: str) -> str:
    out = []
    for ch in str(s):
        if ch.isalnum() or ch in ("-", "_", "."):
            out.append(ch)
        else:
            out.append("_")
    return "".join(out).strip("_")


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_DISCOVERY_MEDIA_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm"}
_DISCOVERY_VIDEO_EXTS = {".mp4", ".webm"}
_DISCOVERY_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_DISCOVERY_SIDECAR_EXTS = {".xmp", ".json"}
_DISCOVERY_HEALTH_SAMPLE_LIMIT = 25


def _read_png_text_chunks(png_path: Path) -> Dict[str, str]:
    """PNG tEXt / zTXt / iTXt reader (stdlib only). Raises if not a PNG."""
    data = png_path.read_bytes()
    if data[:8] != _PNG_MAGIC:
        raise ValueError("not_png")
    off = 8
    out: Dict[str, str] = {}
    while off + 8 <= len(data):
        length = struct.unpack(">I", data[off : off + 4])[0]
        ctype = data[off + 4 : off + 8]
        cdata = data[off + 8 : off + 8 + length]
        off = off + 12 + length

        if ctype == b"tEXt":
            k, v = cdata.split(b"\x00", 1)
            out[k.decode("latin1", "replace")] = v.decode("utf-8", "replace")
        elif ctype == b"zTXt":
            k, rest = cdata.split(b"\x00", 1)
            compressed = rest[1:]
            try:
                v = zlib.decompress(compressed).decode("utf-8", "replace")
            except Exception:
                v = ""
            out[k.decode("latin1", "replace")] = v
        elif ctype == b"iTXt":
            i = cdata.find(b"\x00")
            if i == -1:
                continue
            keyword = cdata[:i].decode("latin1", "replace")
            comp_flag = cdata[i + 1]
            j = i + 3
            k0 = cdata.find(b"\x00", j)
            if k0 == -1:
                continue
            j = k0 + 1
            k1 = cdata.find(b"\x00", j)
            if k1 == -1:
                continue
            text_bytes = cdata[k1 + 1 :]
            if comp_flag == 1:
                try:
                    text_bytes = zlib.decompress(text_bytes)
                except Exception:
                    text_bytes = b""
            out[keyword] = text_bytes.decode("utf-8", "replace")
    return out


def _class_types_preview_from_prompt_json(prompt_raw: str, *, limit: int = 6) -> List[str]:
    try:
        obj = json.loads(prompt_raw)
    except Exception:
        return []
    if not isinstance(obj, dict):
        return []
    out: List[str] = []
    for _nid, node in obj.items():
        if isinstance(node, dict) and isinstance(node.get("class_type"), str):
            ct = str(node["class_type"]).strip()
            if ct:
                out.append(ct)
            if len(out) >= limit:
                break
    return out


def _workflow_fingerprint_for_prompt_raw(prompt_raw: str) -> str:
    raw = prompt_raw.encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:24]


def _file_content_hash(path: Path) -> str:
    st = path.stat()
    size = int(st.st_size)
    if size <= 25_000_000:
        h = hashlib.sha256()
        with path.open("rb") as f:
            while True:
                buf = f.read(1024 * 1024)
                if not buf:
                    break
                h.update(buf)
        return h.hexdigest()
    h = hashlib.sha256()
    h.update(str(size).encode())
    h.update(str(int(st.st_mtime)).encode())
    with path.open("rb") as f:
        h.update(f.read(min(2_000_000, size)))
    return h.hexdigest()


def _png_metadata_fields(path: Path) -> Tuple[Optional[str], List[str], bool]:
    """
    Returns (workflow_fingerprint, class_types_preview, has_embedded_prompt).
    fingerprint is SHA256 prefix of raw Comfy 'prompt' chunk text when present.
    """
    try:
        chunks = _read_png_text_chunks(path)
    except Exception:
        return (None, [], False)
    pr = chunks.get("prompt")
    if isinstance(pr, str) and pr.strip():
        fp = _workflow_fingerprint_for_prompt_raw(pr)
        prev = _class_types_preview_from_prompt_json(pr)
        return (fp, prev, True)
    wf = chunks.get("workflow")
    if isinstance(wf, str) and wf.strip():
        return (_workflow_fingerprint_for_prompt_raw(wf), [], False)
    return (None, [], False)


def _looks_like_comfy_api_prompt(obj: Any) -> bool:
    """True if JSON matches Comfy /prompt graph shape (node id -> {class_type, inputs, ...})."""
    if not isinstance(obj, dict) or not obj:
        return False
    if isinstance(obj.get("nodes"), list) and isinstance(obj.get("links"), list):
        return False
    for _k, v in obj.items():
        if not isinstance(v, dict):
            return False
        if not isinstance(v.get("class_type"), str):
            return False
    return True


def _looks_like_comfy_ui_workflow(obj: Any) -> bool:
    """Litegraph-style workflow saved in PNG workflow chunk."""
    if not isinstance(obj, dict):
        return False
    return isinstance(obj.get("nodes"), list) and isinstance(obj.get("links"), list)


def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


_TRIM_FILE_LOCK = threading.Lock()
_DISCOVERY_LINEAGE_GRAPH_LOCK = threading.Lock()
_TRIM_CONTEXT_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9._-]{0,127}$")
_TRIM_MEDIA_REL_PATH_MAX = 4096
_TRIM_HANDLE_MIN_GAP_SEC = 0.12
_TRIMS_DOC_VERSION = 1
# Single UI surface for now; more contexts (e.g. compare, extend-wizard) can share the same sidecar file.
DEFAULT_TRIM_CONTEXT = "discovery-player"


def _discovery_trim_video_media_path(cfg: "ServerConfig", media_relpath: str) -> Optional[Path]:
    rel = _normalize_rel_posix(media_relpath)
    if not rel or len(rel) > _TRIM_MEDIA_REL_PATH_MAX:
        return None
    low = rel.lower()
    if not (low.endswith(".mp4") or low.endswith(".webm")):
        return None
    return _safe_join(cfg.output_root, rel)


def _discovery_trim_sidecar_path(media_abs: Path) -> Path:
    """Canonical sidecar next to the video: <stem>.trims.json (same pattern as *.metadata.json)."""
    return media_abs.with_suffix(".trims.json")


def _empty_trims_document() -> Dict[str, Any]:
    return {"v": _TRIMS_DOC_VERSION, "contexts": {}}


def _load_trims_document(sidecar: Path) -> Dict[str, Any]:
    if not sidecar.exists():
        return _empty_trims_document()
    try:
        obj = _read_json(sidecar)
    except Exception:
        return _empty_trims_document()
    if not isinstance(obj, dict) or int(obj.get("v") or 0) != _TRIMS_DOC_VERSION:
        return _empty_trims_document()
    raw_ctx = obj.get("contexts")
    if not isinstance(raw_ctx, dict):
        return _empty_trims_document()
    out_ctx: Dict[str, Any] = {}
    for ck, cv in raw_ctx.items():
        if not isinstance(ck, str) or not isinstance(cv, dict):
            continue
        raw_presets = cv.get("presets")
        if not isinstance(raw_presets, list):
            continue
        presets: List[Dict[str, Any]] = []
        for p in raw_presets:
            if not isinstance(p, dict):
                continue
            pid = str(p.get("id") or "").strip()
            if not pid:
                continue
            try:
                tin = float(p.get("in"))
                tout = float(p.get("out"))
            except Exception:
                continue
            if tin < 0 or tout <= tin or tout - tin < 1e-4:
                continue
            label = (str(p.get("label") or "Trim").strip() or "Trim")[:200]
            presets.append({"id": pid, "label": label, "in": tin, "out": tout, "at": int(p.get("at") or 0)})
        aid_raw = cv.get("active_preset_id")
        aid = str(aid_raw).strip() if aid_raw is not None and str(aid_raw).strip() else None
        if aid and not any(x["id"] == aid for x in presets):
            aid = presets[0]["id"] if presets else None
        out_ctx[ck] = {"active_preset_id": aid, "presets": presets}
    return {"v": _TRIMS_DOC_VERSION, "contexts": out_ctx}


def _trim_clamp(mi: Optional[float], mo: Optional[float], duration: float) -> Optional[Tuple[float, float]]:
    if not (duration > 0 and math.isfinite(duration)):
        return None
    raw_in = max(0.0, float(mi if mi is not None else 0.0))
    raw_out = min(float(duration), float(mo if mo is not None else duration))
    gap = _TRIM_HANDLE_MIN_GAP_SEC
    safe_in = min(raw_in, max(0.0, raw_out - gap))
    safe_out = max(raw_out, safe_in + gap)
    if safe_out - safe_in < gap - 1e-6:
        return None
    return (safe_in, safe_out)


def _trim_is_nontrivial(safe_in: float, safe_out: float, duration: float) -> bool:
    return safe_in > 0.008 or safe_out < duration - 0.008


def _prune_empty_trims_document(doc: Dict[str, Any]) -> bool:
    """Return True if document has no presets left in any context."""
    ctxs = doc.get("contexts")
    if not isinstance(ctxs, dict) or not ctxs:
        return True
    for cv in ctxs.values():
        if isinstance(cv, dict) and isinstance(cv.get("presets"), list) and len(cv.get("presets") or []) > 0:
            return False
    return True


def _scrub_empty_trim_contexts(doc: Dict[str, Any]) -> None:
    ctxs = doc.get("contexts")
    if not isinstance(ctxs, dict):
        doc["contexts"] = {}
        return
    dead = [
        k
        for k, v in list(ctxs.items())
        if not (isinstance(v, dict) and isinstance(v.get("presets"), list) and len(v.get("presets") or []) > 0)
    ]
    for k in dead:
        ctxs.pop(k, None)


def _discovery_trim_mutate_document(cfg: "ServerConfig", media_relpath: str, mutator: Callable[[Dict[str, Any]], None]) -> bool:
    """
    Load `<stem>.trims.json` beside the media file, apply mutator(doc), then save or delete the sidecar.
    Returns False if media_relpath does not resolve to an existing file under output_root.
    """
    media_abs = _discovery_trim_video_media_path(cfg, media_relpath)
    if media_abs is None or not media_abs.is_file():
        return False
    sidecar = _discovery_trim_sidecar_path(media_abs)
    with _TRIM_FILE_LOCK:
        doc = _load_trims_document(sidecar)
        mutator(doc)
        _scrub_empty_trim_contexts(doc)
        if _prune_empty_trims_document(doc):
            if sidecar.exists():
                try:
                    sidecar.unlink()
                except Exception:
                    pass
        else:
            _atomic_write_json(sidecar, doc)
    return True


def _prefer_flat_library_dir(output_root: Path, name: str) -> Path:
    """
    Resolve og|wip|experiments|_status under the output bind.

    Canonical (post-flatten): <output_root>/<name>
    Legacy double-nest:      <output_root>/output/<name>
    Prefer whichever exists; default to flat.
    """
    flat = (output_root / name).resolve()
    nested = (output_root / "output" / name).resolve()
    if flat.is_dir():
        return flat
    if nested.is_dir():
        return nested
    return flat


def _og_wip_library_roots(cfg: "ServerConfig") -> Tuple[Path, Path]:
    return (
        _prefer_flat_library_dir(cfg.output_root, "og"),
        _prefer_flat_library_dir(cfg.output_root, "wip"),
    )


def _output_status_dir(output_root: Path) -> Path:
    return _prefer_flat_library_dir(output_root, "_status")


def _merge_discovery_group(lib: str, dir_posix: str, group_stem: str, members: List[Dict[str, Any]]) -> Dict[str, Any]:
    """One indexed row: video + companion png/jpg/webp merged; metadata prefer PNG with prompt."""
    videos = [m for m in members if m.get("ext") in _DISCOVERY_VIDEO_EXTS]
    images = [m for m in members if m.get("ext") in _DISCOVERY_IMAGE_EXTS]

    def sort_video(m: Dict[str, Any]) -> Tuple[float, int]:
        return (float(m.get("mtime") or 0), int(m.get("size") or 0))

    primary_video = max(videos, key=sort_video) if videos else None

    def img_score(m: Dict[str, Any]) -> Tuple[int, int, float]:
        has_fp = 1 if m.get("workflow_fingerprint") else 0
        is_png = 1 if m.get("ext") == ".png" else 0
        return (has_fp, is_png, float(m.get("mtime") or 0))

    thumb_image = None
    if images:
        thumb_image = max(images, key=img_score)

    wf_fp: Optional[str] = None
    cls_prev: List[str] = []
    has_prompt = False
    meta_src = thumb_image
    if meta_src:
        wf_fp = meta_src.get("workflow_fingerprint")  # type: ignore[assignment]
        cls_prev = list(meta_src.get("class_types_preview") or [])
        has_prompt = bool(meta_src.get("has_embedded_prompt"))
    if wf_fp is None and images:
        for im in sorted(images, key=img_score, reverse=True):
            if im.get("workflow_fingerprint"):
                wf_fp = im.get("workflow_fingerprint")  # type: ignore[assignment]
                cls_prev = list(im.get("class_types_preview") or [])
                has_prompt = bool(im.get("has_embedded_prompt"))
                break

    mtime = max(float(m.get("mtime") or 0) for m in members) if members else 0.0
    size_sum = sum(int(m.get("size") or 0) for m in members)

    members_out: List[Dict[str, str]] = []
    for m in sorted(members, key=lambda x: (str(x.get("ext") or ""), str(x.get("name") or ""))):
        ext = str(m.get("ext") or "").lower()
        if ext in _DISCOVERY_VIDEO_EXTS:
            kk = "video"
        elif ext in _DISCOVERY_IMAGE_EXTS:
            kk = "image"
        else:
            kk = "other"
        members_out.append(
            {
                "relpath": str(m.get("relpath") or ""),
                "name": str(m.get("name") or ""),
                "kind": kk,
            }
        )

    primary = primary_video or thumb_image or members[0]
    display_name = str((primary_video or thumb_image or members[0]).get("name") or "")
    video_relpath = str(primary_video.get("relpath")) if primary_video else None
    thumb_relpath = str(thumb_image.get("relpath")) if thumb_image else None

    # group_stem is exact Path(name).stem (already lowercased in the index key).
    group_id = f"{lib}:stem:{group_stem}"

    h = hashlib.sha256()
    for m in sorted(members, key=lambda x: str(x.get("relpath"))):
        h.update(str(m.get("sha256") or "").encode("utf-8", "replace"))
        h.update(b"\n")

    return {
        "group_id": group_id,
        "relpath": str(primary.get("relpath") or ""),
        "library": lib,
        "name": display_name,
        "mtime": mtime,
        "size": size_sum,
        "sha256": h.hexdigest()[:64],
        "workflow_fingerprint": wf_fp,
        "class_types_preview": cls_prev,
        "has_embedded_prompt": has_prompt,
        "video_relpath": video_relpath,
        "thumb_relpath": thumb_relpath,
        "members": members_out,
    }


def _discovery_is_ephemeral_work_artifact(name: str) -> bool:
    """
    Intermediate work products that should not enter Discovery.

    Role tokens in the basename (``…_RAW_00001.mp4``, ``…_PREVIEW_00001.mp4``)
    are throwaway save targets. Keep ``_FINAL_`` — that is the desired output.
    """
    n = str(name or "").upper()
    return any(tok in n for tok in ("_RAW_", "_PREVIEW_", "_DEBUG_"))


def _build_discovery_og_wip_index(cfg: "ServerConfig") -> Dict[str, Any]:
    og_root, wip_root = _og_wip_library_roots(cfg)
    t0 = time.time()
    try:
        out_resolved = cfg.output_root.resolve()
    except Exception:
        out_resolved = cfg.output_root

    # (library, exact filename stem lowercased) -> all extensions for that output.
    # Matches FB9_GEX2_OVERHEAD_2026-04-13_00006.mp4 + .png even if they land in different subfolders.
    by_stem: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    skipped_raw = 0

    for lib, root in (("og", og_root), ("wip", wip_root)):
        if not root.is_dir():
            continue
        try:
            for p in root.rglob("*"):
                try:
                    if not p.is_file():
                        continue
                except Exception:
                    continue
                ext_lc = p.suffix.lower()
                if ext_lc not in _DISCOVERY_MEDIA_EXTS:
                    continue
                if _discovery_is_ephemeral_work_artifact(p.name):
                    skipped_raw += 1
                    continue
                try:
                    rel = p.resolve().relative_to(out_resolved)
                except Exception:
                    continue
                rel_posix = _normalize_rel_posix(str(rel).replace("\\", "/"))
                if not rel_posix:
                    continue
                try:
                    st = p.stat()
                    mtime = float(st.st_mtime)
                    size = int(st.st_size)
                except Exception:
                    mtime = 0.0
                    size = 0
                wf_fp: Optional[str] = None
                cls_prev: List[str] = []
                has_prompt = False
                if ext_lc == ".png":
                    wf_fp, cls_prev, has_prompt = _png_metadata_fields(p)
                content_hash = _file_content_hash(p)
                stem_key = Path(p.name).stem.lower()
                skey = (lib, stem_key)
                rec = {
                    "relpath": rel_posix,
                    "library": lib,
                    "name": p.name,
                    "ext": ext_lc,
                    "mtime": mtime,
                    "size": size,
                    "sha256": content_hash,
                    "workflow_fingerprint": wf_fp,
                    "class_types_preview": cls_prev,
                    "has_embedded_prompt": has_prompt,
                }
                by_stem.setdefault(skey, []).append(rec)
        except Exception:
            continue

    items: List[Dict[str, Any]] = []
    for (lib, stem_key), members in by_stem.items():
        if not members:
            continue
        vids = [m for m in members if m.get("ext") in _DISCOVERY_VIDEO_EXTS]
        if vids:
            anchor = max(vids, key=lambda m: (float(m.get("mtime") or 0), str(m.get("relpath") or "")))
        else:
            anchor = max(members, key=lambda m: (float(m.get("mtime") or 0), str(m.get("relpath") or "")))
        dir_posix = _normalize_rel_posix(str(Path(str(anchor.get("relpath") or "")).parent).replace("\\", "/")) or "."
        items.append(_merge_discovery_group(lib, dir_posix, stem_key, members))

    items.sort(key=lambda it: float(it.get("mtime") or 0), reverse=True)
    built = {
        "version": 5,
        "updated_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "libraries": {"og": str(og_root), "wip": str(wip_root)},
        "item_count": len(items),
        "items": items,
        "skipped_raw_files": skipped_raw,
        "scan_ms": int((time.time() - t0) * 1000),
    }
    return built


_DISCOVERY_INDEX_CACHE: Dict[str, Tuple[float, int, Dict[str, Any]]] = {}


def _discovery_invalidate_index_cache(path: Optional[Path] = None) -> None:
    if path is None:
        _DISCOVERY_INDEX_CACHE.clear()
        return
    try:
        key = str(path.resolve())
    except Exception:
        key = str(path)
    _DISCOVERY_INDEX_CACHE.pop(key, None)


def _discovery_upsert_relpath(cfg: "ServerConfig", relpath: str) -> Dict[str, Any]:
    """Tip-in / ensure one og|wip media path into discovery_og_wip_index.json."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from discovery_index_upsert import ensure_discovery_relpath  # type: ignore

    idx_path = cfg.discovery_index_path
    out = ensure_discovery_relpath(
        index_path=idx_path,
        output_root=cfg.output_root,
        relpath=relpath,
    )
    if out.get("ok"):
        _discovery_invalidate_index_cache(idx_path)
    return out


def _discovery_library_ensure_payload(cfg: "ServerConfig", body: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/discovery/library/ensure { relpath } | { relpaths: [...] }."""
    rels: List[str] = []
    one = str(body.get("relpath") or "").strip()
    if one:
        rels.append(one)
    raw_list = body.get("relpaths")
    if isinstance(raw_list, list):
        for x in raw_list:
            s = str(x or "").strip()
            if s:
                rels.append(s)
    if not rels:
        return {"ok": False, "error": "missing_relpath"}
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from discovery_index_upsert import tip_in_discovery_relpaths  # type: ignore

    payload = tip_in_discovery_relpaths(
        index_path=cfg.discovery_index_path,
        output_root=cfg.output_root,
        relpaths=rels,
    )
    if payload.get("ok_count"):
        _discovery_invalidate_index_cache(cfg.discovery_index_path)
    payload["discovery_index_path"] = str(cfg.discovery_index_path)
    return payload


def _load_discovery_index_disk(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        st = path.stat()
        key = str(path)
        cached = _DISCOVERY_INDEX_CACHE.get(key)
        if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
            return cached[2]
        obj = _read_json(path)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    try:
        st = path.stat()
        _DISCOVERY_INDEX_CACHE[key] = (st.st_mtime, st.st_size, obj)
    except OSError:
        pass
    return obj


def _discovery_index_health_path(path: Path) -> Path:
    return path.with_name("discovery_index_health.json")


def _discovery_resolve_media_file(cfg: "ServerConfig", relpath: Any) -> Optional[Path]:
    """Resolve a relpath to an on-disk file under output_root or workspace_root (e.g. ``input/`` uploads)."""
    if not isinstance(relpath, str) or not relpath.strip():
        return None
    norm = _normalize_rel_posix(relpath.strip().lstrip("/"))
    if not norm:
        return None
    for root in (cfg.output_root, cfg.workspace_root):
        full = _safe_join(root, norm)
        if full is not None and full.is_file():
            return full
    return None


def _discovery_rel_file_exists(cfg: "ServerConfig", relpath: Any) -> bool:
    return _discovery_resolve_media_file(cfg, relpath) is not None


def _discovery_workspace_input_relpath_for_source(cfg: "ServerConfig", raw: Any) -> Optional[str]:
    """
    Normalize Comfy input uploads to workspace-relative ``input/<filename>``.
    Prompts often cite only the hash filename; the file lives under ``<workspace>/input/``.
    """
    s0 = str(raw or "").strip().replace("\\", "/")
    if not s0:
        return None
    if "?" in s0:
        s0 = s0.split("?", 1)[0]
    if "#" in s0:
        s0 = s0.split("#", 1)[0]
    s = s0.strip().lstrip("/")
    candidates: List[str] = []

    def push(p: str) -> None:
        n = _normalize_rel_posix(p)
        if n and n not in candidates:
            candidates.append(n)

    push(s)
    bn = Path(s).name
    if bn and not s.lower().startswith("input/"):
        push(f"input/{bn}")
    if "/" not in s0.replace("\\", "/") and ".." not in s0 and bn:
        push(f"input/{bn}")

    for cand in candidates:
        full = _safe_join(cfg.workspace_root, cand)
        if full is not None and full.is_file():
            return cand
    return None


def _discovery_workspace_input_group_id(norm_in: str) -> str:
    """Stable parent id for Comfy ``input/`` uploads (basename under ``input/``)."""
    n = _normalize_rel_posix(str(norm_in or "").strip().lstrip("/"))
    bn = Path(n).name
    return f"input:{bn}" if bn else f"input:{n}"


def _discovery_input_path_match_keys(norm_in: str) -> set:
    n = _normalize_rel_posix(str(norm_in or "").strip().lstrip("/"))
    bn = Path(n).name.lower()
    keys = {n.lower(), bn}
    if bn:
        keys.add(f"input/{bn}")
    return keys


def _discovery_source_string_references_input(raw: str, match_keys: set) -> bool:
    s0 = str(raw or "").strip().replace("\\", "/")
    if not s0:
        return False
    s = _normalize_rel_posix(s0.lstrip("/")).lower()
    if s in match_keys:
        return True
    bn = Path(s0).name.lower()
    return bn in match_keys


def _discovery_grep_output_pngs_for_basename(cfg: "ServerConfig", basename: str) -> List[str]:
    """
    Fast search: PNGs under og/wip whose embedded text cites ``basename``.

    Prefers ``rg`` when available. Uses flat/nested og|wip roots (see ``_prefer_flat_library_dir``).
    Returns output-root relpaths.
    """
    bn = str(basename or "").strip()
    if len(bn) < 8:
        return []
    rels: List[str] = []
    seen: set = set()
    rg_bin = shutil.which("rg")
    for root in _og_wip_library_roots(cfg):
        if root is None or not root.is_dir():
            continue
        try:
            if rg_bin:
                proc = subprocess.run(
                    [rg_bin, "-l", "-F", "--glob", "*.png", "--no-messages", bn, str(root)],
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
            else:
                proc = subprocess.run(
                    ["grep", "-r", "-l", "-F", "--include=*.png", bn, str(root)],
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
        except subprocess.TimeoutExpired:
            continue
        except Exception:
            continue
        if proc.returncode not in (0, 1):
            continue
        for line in (proc.stdout or "").splitlines():
            p = Path(line.strip())
            if not p.is_file():
                continue
            try:
                rel = p.resolve().relative_to(cfg.output_root.resolve()).as_posix()
            except Exception:
                continue
            if rel not in seen:
                seen.add(rel)
                rels.append(rel)
    return rels


def _discovery_index_items_matching_stem(
    idx: Dict[str, Any],
    stem: str,
    *,
    exclude_group_id: str = "",
    limit: int = 300,
) -> List[Dict[str, Any]]:
    """Discovery rows whose name/relpath embeds ``stem`` (factory ``src-…`` / companion names)."""
    needle = str(stem or "").strip().lower()
    if len(needle) < 8:
        return []
    items = idx.get("items")
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        gid = str(it.get("group_id") or "")
        if exclude_group_id and gid == exclude_group_id:
            continue
        blob = " ".join(
            str(it.get(k) or "")
            for k in ("name", "relpath", "video_relpath", "thumb_relpath", "group_id")
        ).lower()
        if needle not in blob:
            continue
        out.append(it)
        if limit > 0 and len(out) >= limit:
            break
    return out


def _discovery_index_item_for_png_relpath(idx: Dict[str, Any], png_rel: str) -> Optional[Dict[str, Any]]:
    """Map a PNG under output/ to its merged Discovery row (primary or member)."""
    norm = _normalize_rel_posix(png_rel)
    if not norm:
        return None
    hit = _discovery_item_for_relpath(idx, norm)
    if isinstance(hit, dict):
        return hit
    items = idx.get("items")
    if not isinstance(items, list):
        return None
    for it in items:
        if not isinstance(it, dict):
            continue
        mems = it.get("members")
        if not isinstance(mems, list):
            continue
        for mm in mems:
            if isinstance(mm, dict) and _normalize_rel_posix(str(mm.get("relpath") or "")) == norm:
                return it
    return None


def _discovery_scan_index_for_input_children(
    cfg: "ServerConfig",
    idx: Dict[str, Any],
    input_norm: str,
) -> List[Dict[str, Any]]:
    """
    Find indexed og/wip rows whose *wired* Load* nodes cite this workspace input file.

    Prefers the inverted citation index; falls back to PNG basename grep + verify.
    """
    parent_gid = _discovery_workspace_input_group_id(input_norm)
    bn = Path(_normalize_rel_posix(input_norm)).name
    parent_keys = _discovery_media_citation_match_keys(input_norm)
    # Synthetic seed-shaped dict so citation lookup can reuse the same path.
    seed_like = {
        "group_id": parent_gid,
        "relpath": _normalize_rel_posix(input_norm),
        "name": bn,
    }
    indexed = _discovery_citations_lookup_child_edges(cfg, seed_like, limit=200)
    if indexed:
        return indexed

    out: List[Dict[str, Any]] = []
    seen_children: set = set()
    for png_rel in _discovery_grep_output_pngs_for_basename(cfg, bn):
        it = _discovery_index_item_for_png_relpath(idx, png_rel)
        if not isinstance(it, dict):
            continue
        gid = str(it.get("group_id") or "")
        if not gid or gid in seen_children or gid == parent_gid:
            continue
        via = _discovery_child_item_cites_parent_via_wired_loader(cfg, it, parent_keys)
        if not via:
            continue
        seen_children.add(gid)
        try:
            _discovery_citations_index_child_item(cfg, it, force=False)
        except Exception:
            pass
        out.append(
            {
                "child_group_id": gid,
                "parent_group_id": parent_gid,
                "via_source_raw": via,
                "resolved_parent_relpath": _normalize_rel_posix(input_norm),
                "evidence": "png_prompt_grep_wired",
            }
        )
    return out


def _discovery_media_citation_match_keys(rel_or_name: str) -> set:
    """Path / basename / stem keys used to decide whether a loader string cites this media."""
    n = _normalize_rel_posix(str(rel_or_name or "").strip().lstrip("/").replace("\\", "/"))
    if not n:
        return set()
    bn = Path(n).name
    stem = Path(bn).stem
    keys = {n.lower(), bn.lower()}
    if stem:
        keys.add(stem.lower())
    p = n
    while p.lower().startswith("output/"):
        p = p[7:]
        if p:
            keys.add(p.lower())
    return {k for k in keys if k}


def _discovery_source_string_references_media(raw: str, match_keys: set) -> bool:
    if not match_keys:
        return False
    s0 = str(raw or "").strip().replace("\\", "/")
    if not s0:
        return False
    s = _normalize_rel_posix(s0.lstrip("/")).lower()
    if s in match_keys:
        return True
    bn = Path(s0).name.lower()
    if bn in match_keys:
        return True
    stem = Path(bn).stem.lower()
    if stem and stem in match_keys:
        # Avoid matching a SaveVideo prefix stem against an unrelated file unless extless equality is exact.
        if not _discovery_path_has_media_ext(s0):
            return False
        return True
    for k in match_keys:
        if "/" in k and (s == k or s.endswith("/" + k)):
            return True
    return False


def _discovery_companion_png_relpath_for_item(
    cfg: "ServerConfig", child_item: Dict[str, Any]
) -> Optional[str]:
    """Best companion PNG relpath for embedded-prompt extraction (avoid probing MP4)."""
    probe_rel = _discovery_lineage_facets_probe_relpath(child_item)
    if not probe_rel:
        return None
    if str(probe_rel).lower().endswith(".png"):
        return _normalize_rel_posix(probe_rel)
    thumb = child_item.get("thumb_relpath")
    if isinstance(thumb, str) and thumb.lower().endswith(".png"):
        return _normalize_rel_posix(thumb)
    abs_media = _safe_join(cfg.output_root, _normalize_rel_posix(probe_rel))
    if abs_media is not None:
        cand = abs_media.with_suffix(".png")
        if cand.is_file():
            try:
                return cand.resolve().relative_to(cfg.output_root.resolve()).as_posix()
            except Exception:
                pass
    return _normalize_rel_posix(probe_rel)


def _discovery_wired_loader_paths_for_item(cfg: "ServerConfig", child_item: Dict[str, Any]) -> List[str]:
    """Output-feeding Load* media paths from the child's companion PNG (PNG-only, no ffprobe)."""
    png_rel = _discovery_companion_png_relpath_for_item(cfg, child_item)
    if not png_rel or not str(png_rel).lower().endswith(".png"):
        return []
    abs_png = _safe_join(cfg.output_root, png_rel)
    if abs_png is None or not abs_png.is_file():
        return []
    try:
        chunks = _read_png_text_chunks(abs_png)
        meta = _import_comfy_meta_lib()
        pr_obj, _wf = meta.extract_prompt_workflow_from_png_chunks(chunks)
    except Exception:
        return []
    if not isinstance(pr_obj, dict) or not _looks_like_comfy_api_prompt(pr_obj):
        return []
    wired = _api_prompt_output_feeding_loader_paths(pr_obj)
    out: List[str] = []
    seen: set = set()
    for s in wired.get("output_feeding_loader_paths") or []:
        if not isinstance(s, str):
            continue
        ss = s.strip()
        if not ss or ss in seen:
            continue
        seen.add(ss)
        out.append(ss)
    return out


def _discovery_child_item_cites_parent_via_wired_loader(
    cfg: "ServerConfig",
    child_item: Dict[str, Any],
    parent_match_keys: set,
) -> Optional[str]:
    """
    Return the wired loader path string on ``child_item`` that cites the parent, or None.

    PNG-only (no MP4 ffprobe): orphan / preview-only loaders are ignored via the same
    output-feeding reachability filter as parent inference.
    """
    for s in _discovery_wired_loader_paths_for_item(cfg, child_item):
        if _discovery_source_string_references_media(s, parent_match_keys):
            return s
    return None


# --- Inverted citation index (forward-fill) ---------------------------------

_DISCOVERY_CITATIONS_LOCK = threading.Lock()


def _discovery_citations_db_path(cfg: "ServerConfig") -> Path:
    return _output_status_dir(cfg.output_root) / "discovery_lineage_citations.sqlite"


def _discovery_citations_ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS citations (
          parent_key TEXT NOT NULL,
          child_group_id TEXT NOT NULL,
          via_source_raw TEXT NOT NULL DEFAULT '',
          evidence TEXT,
          child_relpath TEXT,
          updated_at TEXT,
          PRIMARY KEY (parent_key, child_group_id, via_source_raw)
        );
        CREATE INDEX IF NOT EXISTS idx_citations_parent ON citations(parent_key);
        CREATE INDEX IF NOT EXISTS idx_citations_child ON citations(child_group_id);
        CREATE TABLE IF NOT EXISTS citation_scan_state (
          child_group_id TEXT PRIMARY KEY,
          probe_relpath TEXT,
          scanned_at TEXT,
          loader_count INTEGER,
          ok INTEGER
        );
        """
    )


def _discovery_citations_connect(cfg: "ServerConfig") -> sqlite3.Connection:
    path = _discovery_citations_db_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=60.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    _discovery_citations_ensure_schema(con)
    return con


def _discovery_citations_upsert_postings(
    cfg: "ServerConfig",
    *,
    child_group_id: str,
    child_relpath: Optional[str],
    via_paths: List[str],
    evidence: str,
    scanned: bool = True,
    ok: bool = True,
) -> int:
    """
    Replace citation postings for one child with wired loader paths.
    Each via path is expanded to all match keys (basename, stem, stripped output/…).
    Returns number of posting rows written.
    """
    gid = str(child_group_id or "").strip()
    if not gid:
        return 0
    ts = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    probe = _normalize_rel_posix(str(child_relpath or "")) or None
    rows: List[Tuple[str, str, str, str, Optional[str], str]] = []
    for via in via_paths:
        if not isinstance(via, str) or not via.strip():
            continue
        v = via.strip()
        for pk in sorted(_discovery_media_citation_match_keys(v)):
            if len(pk) < 4:
                continue
            rows.append((pk, gid, v, evidence, probe, ts))
    with _DISCOVERY_CITATIONS_LOCK:
        con = _discovery_citations_connect(cfg)
        try:
            con.execute("DELETE FROM citations WHERE child_group_id = ?", (gid,))
            if rows:
                con.executemany(
                    """
                    INSERT OR REPLACE INTO citations
                      (parent_key, child_group_id, via_source_raw, evidence, child_relpath, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
            if scanned:
                con.execute(
                    """
                    INSERT OR REPLACE INTO citation_scan_state
                      (child_group_id, probe_relpath, scanned_at, loader_count, ok)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (gid, probe, ts, len(via_paths), 1 if ok else 0),
                )
            con.commit()
        finally:
            con.close()
    return len(rows)


def _discovery_citations_index_child_item(
    cfg: "ServerConfig",
    child_item: Dict[str, Any],
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """Extract wired loaders from child PNG and upsert inverted citation postings."""
    if not isinstance(child_item, dict):
        return {"ok": False, "error": "bad_item"}
    gid = str(child_item.get("group_id") or "").strip()
    if not gid:
        return {"ok": False, "error": "missing_group_id"}
    if not force:
        with _DISCOVERY_CITATIONS_LOCK:
            con = _discovery_citations_connect(cfg)
            try:
                row = con.execute(
                    "SELECT scanned_at FROM citation_scan_state WHERE child_group_id = ?",
                    (gid,),
                ).fetchone()
            finally:
                con.close()
        if row:
            return {"ok": True, "skipped": True, "child_group_id": gid, "scanned_at": row[0]}

    via_paths = _discovery_wired_loader_paths_for_item(cfg, child_item)
    rel = (
        str(child_item.get("relpath") or child_item.get("video_relpath") or "").strip()
        or None
    )
    n = _discovery_citations_upsert_postings(
        cfg,
        child_group_id=gid,
        child_relpath=rel,
        via_paths=via_paths,
        evidence="wired_loader_index",
        scanned=True,
        ok=True,
    )
    return {
        "ok": True,
        "child_group_id": gid,
        "loader_count": len(via_paths),
        "postings": n,
        "via_paths": via_paths,
    }


def _discovery_citations_ingest_lineage_edge_rows(cfg: "ServerConfig", rows: List[Dict[str, Any]]) -> int:
    """Incremental update from persisted parent→child lineage edges (does not clear other vias)."""
    if not rows:
        return 0
    ts = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    postings: List[Tuple[str, str, str, str, Optional[str], str]] = []
    for row in rows:
        if not isinstance(row, dict) or _discovery_lineage_edge_looks_spurious(row):
            continue
        child = str(row.get("child_group_id") or "").strip()
        via = str(row.get("via_source_raw") or "").strip()
        parent_rel = str(row.get("resolved_parent_relpath") or "").strip()
        if not child:
            continue
        keys: set = set()
        if via:
            keys |= _discovery_media_citation_match_keys(via)
        if parent_rel:
            keys |= _discovery_media_citation_match_keys(parent_rel)
        if not keys:
            continue
        evidence = str(row.get("evidence") or "lineage_edge")
        via_store = via or parent_rel
        for pk in sorted(keys):
            if len(pk) < 4:
                continue
            postings.append((pk, child, via_store, evidence, parent_rel or None, ts))
    if not postings:
        return 0
    with _DISCOVERY_CITATIONS_LOCK:
        con = _discovery_citations_connect(cfg)
        try:
            con.executemany(
                """
                INSERT OR REPLACE INTO citations
                  (parent_key, child_group_id, via_source_raw, evidence, child_relpath, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                postings,
            )
            con.commit()
        finally:
            con.close()
    return len(postings)


def _discovery_citations_lookup_child_edges(
    cfg: "ServerConfig",
    seed_item: Dict[str, Any],
    *,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """O(children) forward-fill from the inverted citation index."""
    if not isinstance(seed_item, dict):
        return []
    parent_gid = str(seed_item.get("group_id") or "").strip()
    if not parent_gid:
        return []
    keys: set = set()
    for k in ("relpath", "video_relpath", "name"):
        v = seed_item.get(k)
        if isinstance(v, str) and v.strip():
            keys |= _discovery_media_citation_match_keys(v)
    if not keys:
        return []
    key_list = sorted(keys)
    placeholders = ",".join("?" for _ in key_list)
    with _DISCOVERY_CITATIONS_LOCK:
        con = _discovery_citations_connect(cfg)
        try:
            cur = con.execute(
                f"""
                SELECT child_group_id, via_source_raw, evidence, child_relpath
                FROM citations
                WHERE parent_key IN ({placeholders})
                ORDER BY child_group_id, via_source_raw
                """,
                key_list,
            )
            raw_rows = cur.fetchall()
        finally:
            con.close()

    out: List[Dict[str, Any]] = []
    seen: set = set()
    parent_rel = _normalize_rel_posix(
        str(seed_item.get("relpath") or seed_item.get("video_relpath") or "")
    ) or None
    for child_gid, via, evidence, _child_rel in raw_rows:
        cg = str(child_gid or "")
        if not cg or cg == parent_gid or cg in seen:
            continue
        seen.add(cg)
        out.append(
            {
                "child_group_id": cg,
                "parent_group_id": parent_gid,
                "via_source_raw": via,
                "resolved_parent_relpath": parent_rel,
                "evidence": evidence or "wired_loader_index",
            }
        )
        if limit > 0 and len(out) >= limit:
            break
    return out


def _discovery_seed_media_basenames_for_child_grep(seed_item: Dict[str, Any]) -> List[str]:
    """Basenames worth grepping for when forward-filling children of an indexed row."""
    names: List[str] = []
    seen: set = set()
    for k in ("relpath", "video_relpath", "name", "thumb_relpath"):
        v = seed_item.get(k)
        if not isinstance(v, str) or not v.strip():
            continue
        bn = Path(v.strip().replace("\\", "/")).name
        if len(bn) < 8 or bn.lower() in seen:
            continue
        # Prefer media files; skip pure .json sidecars.
        low = bn.lower()
        if low.endswith((".mp4", ".webm", ".mov", ".mkv", ".png", ".jpg", ".jpeg", ".webp")):
            seen.add(low)
            names.append(bn)
    mems = seed_item.get("members")
    if isinstance(mems, list):
        for mm in mems:
            if not isinstance(mm, dict):
                continue
            for k in ("relpath", "name"):
                v = mm.get(k)
                if not isinstance(v, str) or not v.strip():
                    continue
                bn = Path(v.strip().replace("\\", "/")).name
                low = bn.lower()
                if len(bn) < 8 or low in seen:
                    continue
                if low.endswith((".mp4", ".webm", ".mov", ".mkv", ".png", ".jpg", ".jpeg", ".webp")):
                    seen.add(low)
                    names.append(bn)
    return names


def _discovery_scan_index_for_media_children(
    cfg: "ServerConfig",
    idx: Dict[str, Any],
    seed_item: Dict[str, Any],
    *,
    limit: int = 200,
    warm_index: bool = True,
) -> List[Dict[str, Any]]:
    """
    Forward-fill children of an indexed media row.

    Prefers the inverted citation SQLite index. On a cold miss, optionally warms the index
    from Discovery rows whose names embed the seed stem (factory ``src-…`` keys), then
    re-queries — no full-tree PNG grep.
    """
    if not isinstance(seed_item, dict):
        return []
    parent_gid = str(seed_item.get("group_id") or "")
    if not parent_gid:
        return []

    edges = _discovery_citations_lookup_child_edges(cfg, seed_item, limit=limit)
    if edges or not warm_index:
        return edges

    # Cold miss: index stem-named candidates (writes *all* of each child's citations).
    for bn in _discovery_seed_media_basenames_for_child_grep(seed_item):
        stem = Path(bn).stem
        for it in _discovery_index_items_matching_stem(
            idx, stem, exclude_group_id=parent_gid, limit=max(50, limit * 2)
        ):
            try:
                _discovery_citations_index_child_item(cfg, it, force=False)
            except Exception:
                continue
    return _discovery_citations_lookup_child_edges(cfg, seed_item, limit=limit)


def _discovery_synthetic_library_item_for_workspace_media(
    cfg: "ServerConfig",
    raw_relpath: str,
) -> Optional[Dict[str, Any]]:
    """
    Build a Discovery-shaped row for workspace media outside the og/wip index (e.g. Comfy ``input/`` uploads).
    """
    norm = _discovery_workspace_input_relpath_for_source(cfg, raw_relpath)
    if norm is None:
        norm = _normalize_rel_posix(str(raw_relpath or "").strip().lstrip("/"))
    if not norm:
        return None
    full = _discovery_resolve_media_file(cfg, norm)
    if full is None:
        return None
    library = "input" if norm.lower().startswith("input/") else "workspace"
    try:
        st = full.stat()
        mtime = float(st.st_mtime)
        size = int(st.st_size)
    except Exception:
        mtime = 0.0
        size = 0
    url = _discovery_lineage_file_url(cfg, norm) or ""
    gid = _discovery_workspace_input_group_id(norm) if library == "input" else f"ws:{norm}"
    return {
        "group_id": gid,
        "relpath": norm,
        "library": library,
        "name": full.name,
        "mtime": mtime,
        "size": size,
        "sha256": "",
        "has_embedded_prompt": False,
        "url": url,
        "thumb_url": url or None,
        "video_relpath": None,
        "thumb_relpath": None,
        "video_url": None,
        "members": [],
        "external": True,
    }


def _discovery_index_key(item: Dict[str, Any]) -> str:
    gid = item.get("group_id")
    if isinstance(gid, str) and gid.strip():
        return gid.strip()
    rel = item.get("relpath")
    return str(rel or "")


def _discovery_index_item_map(index_obj: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(index_obj, dict):
        return out
    items = index_obj.get("items")
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        key = _discovery_index_key(item)
        if key:
            out[key] = item
    return out


def _discovery_item_for_relpath(index_obj: Any, rel_posix: str) -> Optional[Dict[str, Any]]:
    """Find merged Discovery row where any member path matches rel_posix (full path or stem)."""
    if not isinstance(index_obj, dict):
        return None
    items = index_obj.get("items")
    if not isinstance(items, list):
        return None
    norm_rel = _normalize_rel_posix(rel_posix.strip())
    if not norm_rel:
        return None
    stem_rel = norm_rel
    for ext in (".mp4", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".xmp"):
        if stem_rel.lower().endswith(ext):
            stem_rel = stem_rel[: -len(ext)]
            break

    def _path_matches(candidate: str) -> bool:
        nc = _normalize_rel_posix(candidate.strip())
        if not nc:
            return False
        if nc == norm_rel:
            return True
        cand_stem = nc
        for ext in (".mp4", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".xmp"):
            if cand_stem.lower().endswith(ext):
                cand_stem = cand_stem[: -len(ext)]
                break
        return cand_stem == stem_rel

    for it in items:
        if not isinstance(it, dict):
            continue
        cands: List[str] = []
        for k in ("relpath", "video_relpath", "thumb_relpath"):
            v = it.get(k)
            if isinstance(v, str) and v.strip():
                cands.append(v.strip())
        mems = it.get("members")
        if isinstance(mems, list):
            for mm in mems:
                if isinstance(mm, dict):
                    rv = mm.get("relpath")
                    if isinstance(rv, str) and rv.strip():
                        cands.append(rv.strip())
        for c in cands:
            if _path_matches(c):
                return it
    return None


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _discovery_rating_sampler_payload(cfg: "ServerConfig", q: Dict[str, List[str]]) -> Dict[str, Any]:
    """GET /api/discovery/rating-sampler — latest or refreshed heuristic rating queue."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_rating_sampler import (  # type: ignore
        SAMPLER_SCHEMA_VERSION,
        analyze_vision_gaps,
        default_sampler_sessions_dir,
        normalize_selection_mode,
        persist_rating_session,
        sample_rating_queue,
    )

    def _session_is_stale(doc: Dict[str, Any]) -> bool:
        """Persisted before stratified buckets landed — regenerate so the UI gets bucket data."""
        try:
            if int(doc.get("version") or 0) < int(SAMPLER_SCHEMA_VERSION):
                return True
        except (TypeError, ValueError):
            return True
        cands = doc.get("candidates") or []
        first = next((c for c in cands if isinstance(c, dict)), None)
        if first is not None and "session_bucket" not in first:
            return True
        return False

    def _truthy(vals: List[str]) -> bool:
        for v in vals:
            if str(v).strip().lower() in ("1", "true", "yes", "on"):
                return True
        return False

    og_root, _wip = _og_wip_library_roots(cfg)
    refresh = _truthy(q.get("refresh", []))

    limit = 100
    for v in q.get("limit", []):
        n = _safe_int(v)
        if n is not None and n > 0:
            limit = min(int(n), 150)
            break

    min_predicted = 2.0
    for v in q.get("min_predicted", []):
        try:
            min_predicted = float(str(v).strip())
        except Exception:
            pass
        break

    mode = "mixed"
    for key in ("mode", "selection_mode", "selection"):
        found = False
        for v in q.get(key, []):
            if isinstance(v, str) and v.strip():
                mode = normalize_selection_mode(v)
                found = True
                break
        if found:
            break

    query_s = ""
    for key in ("q", "query", "search"):
        for v in q.get(key, []):
            if isinstance(v, str) and v.strip():
                query_s = v.strip()
                break
        if query_s:
            break

    include_done = _truthy(q.get("include_done", [])) or _truthy(q.get("include_rated", []))

    def _request_matches(doc: Dict[str, Any]) -> bool:
        req = doc.get("request") if isinstance(doc.get("request"), dict) else {}
        doc_mode = normalize_selection_mode(req.get("mode") or doc.get("selection_mode") or "mixed")
        doc_q = str(req.get("query") if "query" in req else doc.get("query") or "").strip()
        doc_done = bool(req.get("include_done") if "include_done" in req else doc.get("include_done"))
        try:
            doc_limit = int(req.get("limit") if req.get("limit") is not None else limit)
        except (TypeError, ValueError):
            doc_limit = limit
        return (
            doc_mode == mode
            and doc_q == query_s
            and doc_done == include_done
            and doc_limit == limit
        )

    def _sample(*, seed: int = 0) -> Dict[str, Any]:
        sample_seed = seed
        if mode == "random" and seed == 0:
            sample_seed = int(_dt.datetime.now(_dt.timezone.utc).timestamp())
        session = sample_rating_queue(
            og_root=og_root,
            limit=limit,
            discovery_index=cfg.discovery_index_path,
            min_predicted=min_predicted,
            mode=mode,
            query=query_s,
            include_done=include_done,
            seed=sample_seed,
        )
        if session.get("ok"):
            out_path = persist_rating_session(session, og_root=og_root, update_state=True)
            session["session_path"] = str(out_path)
        session["vision_gaps"] = analyze_vision_gaps(session)
        return session

    def _enrich(session: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from shape_factory_map import resolve_shape_factory_data_root  # type: ignore
            from shape_factory_rating_sampler import enrich_session_extension_ranges  # type: ignore

            data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
            enrich_session_extension_ranges(
                session,
                data_root=data_root,
                og_root=og_root,
                output_root=cfg.output_root,
            )
        except Exception:
            pass
        return session

    if refresh:
        return _enrich(_sample())

    sessions_dir = default_sampler_sessions_dir(og_root)
    paths = sorted(sessions_dir.glob("session_*.json")) if sessions_dir.is_dir() else []
    if not paths:
        session = _sample()
        session["bootstrapped"] = True
        return _enrich(session)

    try:
        session = json.loads(paths[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"ok": False, "error": "session_read_failed", "detail": str(e)}
    if not isinstance(session, dict):
        return {"ok": False, "error": "session_invalid"}

    if _session_is_stale(session) or not _request_matches(session):
        session = _sample()
        session["regenerated_stale"] = True
        return _enrich(session)

    session["session_path"] = str(paths[-1])
    session["vision_gaps"] = analyze_vision_gaps(session)
    return _enrich(session)


def _shape_factory_map_payload(cfg: ServerConfig, q: Dict[str, List[str]]) -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_map import build_shape_factory_map, resolve_shape_factory_data_root  # type: ignore

    members_limit = 24
    for v in q.get("members_limit", []):
        n = _safe_int(v)
        # 0 = skip member previews (progressive index shell).
        if n is not None and n >= 0:
            members_limit = min(int(n), 200)
            break

    jobs_limit = 500
    for v in q.get("jobs_limit", []):
        n = _safe_int(v)
        if n is not None and n > 0:
            jobs_limit = min(int(n), 2000)
            break

    jobs_per_family = 40
    for v in q.get("jobs_per_family", []):
        n = _safe_int(v)
        if n is not None and n > 0:
            jobs_per_family = min(int(n), 200)
            break

    projected_pairs_limit = 48
    for v in q.get("projected_pairs_limit", []):
        n = _safe_int(v)
        if n is not None and n >= 0:
            projected_pairs_limit = min(int(n), 200)
            break

    family_filter: Optional[str] = None
    for v in q.get("family", []):
        if isinstance(v, str) and v.strip():
            family_filter = v.strip()
            break

    skip_queue = any(str(v).strip().lower() in {"1", "true", "yes"} for v in q.get("skip_queue", []))

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    if not data_root.is_dir():
        return {
            "ok": False,
            "error": "shape_factory_data_missing",
            "data_root": str(data_root),
            "hint": "Set SHAPE_FACTORY_DATA_ROOT or ensure repo .data/ exists",
        }

    def url_for(rel: str) -> str:
        return "/files/" + urllib.parse.quote(_normalize_rel_posix(rel))

    return build_shape_factory_map(
        data_root=data_root,
        output_root=cfg.output_root,
        comfy_server=str(cfg.comfy_server),
        members_limit=members_limit,
        jobs_limit=jobs_limit,
        jobs_per_family=jobs_per_family,
        family_filter=family_filter,
        skip_queue=skip_queue,
        projected_pairs_limit=projected_pairs_limit,
        url_for=url_for,
        wip_root=cfg.wip_root,
        workspace_root=cfg.workspace_root,
        file_exists=lambda rel: _discovery_rel_file_exists(cfg, rel),
    )


def _asset_recovery_context(cfg: ServerConfig) -> Tuple[Any, Path, Optional[Path]]:
    """Import asset_recovery + resolve shape-factory data_root and registry path."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    import asset_recovery  # type: ignore
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    registry_path: Optional[Path] = None
    try:
        import asset_registry  # type: ignore

        og_root = _prefer_flat_library_dir(cfg.output_root, "og")
        registry_path = asset_registry.default_registry_path(og_root)
    except Exception:
        registry_path = None
    return asset_recovery, data_root, registry_path


def _asset_audit_payload(cfg: ServerConfig, q: Dict[str, List[str]]) -> Dict[str, Any]:
    family = ""
    for v in q.get("family", []):
        if isinstance(v, str) and v.strip():
            family = v.strip()
            break
    if not family:
        return {"ok": False, "error": "missing_family"}
    asset_recovery, data_root, _reg = _asset_recovery_context(cfg)
    if not data_root.is_dir():
        return {"ok": False, "error": "shape_factory_data_missing", "data_root": str(data_root)}
    return asset_recovery.audit_family_missing_sources(
        data_root=data_root,
        workspace_root=cfg.workspace_root,
        family=family,
    )


def _home_fresh_outputs(cfg: ServerConfig, limit: int = 12) -> List[Dict[str, Any]]:
    """Newest indexed outputs (og+wip), enriched with live URLs + rating rollup."""
    idx_path = cfg.discovery_index_path
    idx = _load_discovery_index_disk(idx_path) if idx_path.exists() else None
    items = idx.get("items") if isinstance(idx, dict) else None
    if not isinstance(items, list):
        return []
    rows = [it for it in items if isinstance(it, dict)]
    rows.sort(key=lambda it: float(it.get("mtime") or 0), reverse=True)
    rows = rows[: max(1, int(limit))]

    def _live(relpath: Any) -> Optional[str]:
        if not isinstance(relpath, str) or not relpath.strip():
            return None
        norm = _normalize_rel_posix(relpath.strip())
        if not norm:
            return None
        full = _safe_join(cfg.output_root, norm)
        if full is None or not full.exists() or not full.is_file():
            return None
        return "/files/" + urllib.parse.quote(norm, safe="")

    ratings_doc = _discovery_load_ratings_index(cfg)
    appetite_doc = _discovery_load_appetite_index(cfg)
    out: List[Dict[str, Any]] = []
    for it in rows:
        row: Dict[str, Any] = {
            "group_id": it.get("group_id"),
            "relpath": it.get("relpath"),
            "name": it.get("name"),
            "library": it.get("library"),
            "mtime": it.get("mtime"),
            "url": _live(it.get("relpath")) or "",
            "video_url": _live(it.get("video_relpath")),
            "thumb_url": _live(it.get("thumb_relpath")),
        }
        if ratings_doc or appetite_doc:
            try:
                r = _discovery_ratings_for_item(ratings_doc, it, appetite_doc)
                if r:
                    row["ratings"] = r
            except Exception:
                pass
        out.append(row)
    return out


def _home_summary_payload(cfg: ServerConfig) -> Dict[str, Any]:
    """
    GET /api/home/summary — resume-the-loop aggregation for the Home dashboard.

    Best-effort: each section is independently guarded so one slow/failing source
    never blanks the whole page. Reuses the same helpers the dedicated screens use
    (rating sampler, discovery index, shape-factory map) so numbers stay consistent.
    """
    payload: Dict[str, Any] = {"ok": True}

    # Rating loop — latest cached sampler session (no refresh: fast, reads last session).
    try:
        sampler = _discovery_rating_sampler_payload(cfg, {})
        stats = sampler.get("stats") if isinstance(sampler, dict) else None
        stats = stats if isinstance(stats, dict) else {}
        payload["rating"] = {
            "ok": bool(sampler.get("ok")) if isinstance(sampler, dict) else False,
            "session_path": sampler.get("session_path") if isinstance(sampler, dict) else None,
            "unrated_videos": stats.get("unrated_videos"),
            "scored_pool": stats.get("scored_pool"),
            "selected": stats.get("selected"),
            "buckets": {
                "easy_down": stats.get("bucket_easy_down"),
                "easy_up": stats.get("bucket_easy_up"),
                "middle": stats.get("bucket_middle"),
            },
            "vision_recommended": stats.get("vision_recommended"),
        }
    except Exception as e:
        payload["rating"] = {"ok": False, "error": str(e)}

    # Fresh outputs to triage.
    try:
        payload["fresh_outputs"] = _home_fresh_outputs(cfg, limit=12)
    except Exception as e:
        payload["fresh_outputs"] = []
        payload.setdefault("errors", {})["fresh_outputs"] = str(e)

    # Needs attention + next hourly peek — derived from the shape-factory map (queue skipped
    # for speed; map building is the same call the Factory screen makes).
    attention: Dict[str, Any] = {"missing_sources_total": 0, "families": []}
    hourly: Optional[Dict[str, Any]] = None
    try:
        m = _shape_factory_map_payload(
            cfg, {"skip_queue": ["1"], "members_limit": ["6"], "jobs_limit": ["200"]}
        )
        if isinstance(m, dict) and m.get("ok"):
            fams = m.get("families") if isinstance(m.get("families"), list) else []
            fam_rows: List[Dict[str, Any]] = []
            total_missing = 0
            for fam in fams:
                if not isinstance(fam, dict):
                    continue
                pairs = fam.get("projected_pairs") if isinstance(fam.get("projected_pairs"), list) else []
                miss = sum(
                    1
                    for p in pairs
                    if isinstance(p, dict) and p.get("gap") == "source" and p.get("phase") != "future"
                )
                if miss > 0:
                    fam_rows.append({"family_slug": fam.get("family_slug"), "missing": miss})
                total_missing += miss
            fam_rows.sort(key=lambda r: int(r.get("missing") or 0), reverse=True)
            attention["missing_sources_total"] = total_missing
            attention["families"] = fam_rows[:6]
            jobs = m.get("jobs") if isinstance(m.get("jobs"), dict) else {}
            payload["jobs"] = {
                "total": jobs.get("total"),
                "summary": jobs.get("summary") if isinstance(jobs.get("summary"), dict) else {},
            }
            if isinstance(m.get("hourly"), dict):
                hourly = m.get("hourly")
    except Exception as e:
        payload.setdefault("errors", {})["shape_factory_map"] = str(e)

    # Library health issues (cheap: already computed alongside the index).
    try:
        health = _load_discovery_health_disk(_discovery_index_health_path(cfg.discovery_index_path))
        if isinstance(health, dict):
            attention["library_health"] = health.get("summary")
    except Exception:
        pass

    payload["attention"] = attention
    hourly_out: Dict[str, Any] = {}
    if hourly is not None:
        hourly_out = {
            "next_sample": hourly.get("next_sample"),
            "state_path": hourly.get("state_path"),
        }
    try:
        sch = _hourly_schedule_payload(cfg)
        if sch.get("ok"):
            hourly_out["schedule"] = sch
    except Exception as e:
        payload.setdefault("errors", {})["hourly_schedule"] = str(e)
    if hourly_out:
        payload["hourly"] = hourly_out
    return payload


def _hourly_schedule_payload(cfg: ServerConfig) -> Dict[str, Any]:
    """GET /api/shape-factory/hourly-schedule — live cadence + queue routing controls."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_hourly import (  # type: ignore
        count_factory_pending_submit,
        default_hourly_schedule_path,
        hourly_schedule_status,
    )
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    path = default_hourly_schedule_path(data_root=data_root)
    out = hourly_schedule_status(path=path, data_root=data_root)
    # Live queue depths (best-effort).
    waiting = None
    running = None
    try:
        q = _http_json("GET", f"{str(cfg.comfy_server).rstrip('/')}/queue", timeout_s=4)
        waiting = len(q.get("queue_pending") or [])
        running = len(q.get("queue_running") or [])
    except Exception:
        pass
    factory_pending = None
    try:
        jobs_dir = data_root / "shape_factory" / "jobs"
        if jobs_dir.is_dir():
            factory_pending = count_factory_pending_submit(jobs_dir=jobs_dir)
    except Exception:
        pass
    out["comfy_waiting"] = waiting
    out["comfy_running"] = running
    out["factory_pending"] = factory_pending
    return out


def _hourly_schedule_set_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/shape-factory/hourly-schedule — update cadence / routing controls."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_hourly import (  # type: ignore
        default_hourly_schedule_path,
        hourly_schedule_status,
        load_hourly_schedule,
        mark_hourly_tick,
        save_hourly_schedule,
    )
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    path = default_hourly_schedule_path(data_root=data_root)
    sch = load_hourly_schedule(path=path, data_root=data_root)
    if "interval_minutes" in body:
        sch["interval_minutes"] = body.get("interval_minutes")
    if "enabled" in body:
        sch["enabled"] = bool(body.get("enabled"))
    if "submit_mode" in body and body.get("submit_mode") is not None:
        sch["submit_mode"] = str(body.get("submit_mode"))
    if "comfy_queue_min" in body:
        sch["comfy_queue_min"] = body.get("comfy_queue_min")
    if "comfy_queue_max" in body:
        sch["comfy_queue_max"] = body.get("comfy_queue_max")
    if "pending_queue_max" in body:
        sch["pending_queue_max"] = body.get("pending_queue_max")
    if body.get("mark_tick"):
        save = mark_hourly_tick(sch, path=path, data_root=data_root)
    else:
        save = save_hourly_schedule(sch, path=path, data_root=data_root)
    status = hourly_schedule_status(path=path, data_root=data_root)
    status["saved"] = save
    # Attach live counts from GET helper.
    live = _hourly_schedule_payload(cfg)
    for k in ("comfy_waiting", "comfy_running", "factory_pending"):
        if k in live:
            status[k] = live[k]
    return status


def _asset_recover_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    asset_recovery, data_root, registry_path = _asset_recovery_context(cfg)
    names_raw = body.get("names")
    names: List[str] = []
    if isinstance(names_raw, list):
        names = [str(n).strip() for n in names_raw if str(n).strip()]
    family = str(body.get("family") or "").strip()
    if not names and family:
        if not data_root.is_dir():
            return {"ok": False, "error": "shape_factory_data_missing", "data_root": str(data_root)}
        audit = asset_recovery.audit_family_missing_sources(
            data_root=data_root, workspace_root=cfg.workspace_root, family=family
        )
        names = [m["basename"] for m in (audit.get("missing") or [])]
    if not names:
        return {"ok": True, "recovered": 0, "total": 0, "results": [], "note": "nothing_to_recover"}
    allow_remote = body.get("allow_remote", True) is not False
    return asset_recovery.recover_names(
        names,
        workspace_root=cfg.workspace_root,
        allow_remote=allow_remote,
        registry_path=registry_path,
    )


def _set_asset_rating_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    rel = str(body.get("relpath") or "").strip()
    if not rel:
        raise ValueError("missing relpath")
    if "stars" not in body:
        raise ValueError("missing stars")
    try:
        stars = int(body.get("stars"))
    except (TypeError, ValueError):
        raise ValueError("bad stars")
    if stars < 0 or stars > 5:
        raise ValueError("stars must be 0-5")
    axis = body.get("axis")
    axis_s = str(axis).strip() if axis is not None and str(axis).strip() else None
    media_abs = _safe_join(cfg.output_root, rel)
    if media_abs is None:
        raise ValueError("bad relpath")
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    import shape_factory_ratings  # type: ignore

    og_root = _prefer_flat_library_dir(cfg.output_root, "og")
    return shape_factory_ratings.set_output_rating(
        media_abs=media_abs,
        media_relpath=rel,
        stars=stars,
        og_root=og_root,
        ratings_index_path=_discovery_ratings_index_path(cfg),
        ffprobe=None,
        axis=axis_s,
    )


def _resolve_replay_job_from_relpath(cfg: "ServerConfig", rel: str, body: Dict[str, Any]) -> Tuple[str, str]:
    """Best-effort job_key + family_slug for replay from an output relpath."""
    job_key = str(body.get("job_key") or "").strip()
    family = str(body.get("family_slug") or body.get("family") or "").strip()
    if job_key:
        return job_key, family
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_job_output_index import (  # type: ignore
        default_job_output_index_path,
        lookup_by_relpath,
        open_job_output_index,
    )
    from shape_factory import find_job_by_key  # type: ignore
    from shape_factory_queue import resolve_shape_factory_data_root  # type: ignore
    from shape_factory_rating_sampler import job_key_guess_from_output_relpath  # type: ignore

    og_root = _prefer_flat_library_dir(cfg.output_root, "og")
    index_path = default_job_output_index_path(og_root)
    meta: Dict[str, Any] = {}
    if index_path.is_file():
        try:
            con = open_job_output_index(index_path)
            try:
                row = lookup_by_relpath(con, rel, output_root=cfg.output_root)
            finally:
                con.close()
            if isinstance(row, dict):
                meta = row
        except Exception:
            meta = {}
    job_key = str(meta.get("job_key") or "").strip()
    family = family or str(meta.get("family_slug") or "").strip()
    if job_key:
        return job_key, family

    # Single-job fallback — never full-tree scan on the request path.
    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    guess = job_key_guess_from_output_relpath(rel)
    if guess:
        _path, job = find_job_by_key(data_root, guess)
        if isinstance(job, dict):
            job_key = str(job.get("job_key") or guess).strip()
            family = family or str(job.get("family_slug") or "").strip()
    return job_key, family



def _queue_fresh_from_source_media(cfg: "ServerConfig", rel: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Fresh combo when no parent job exists — Discovery / Rate clip queue fallback."""
    family = str(body.get("family_slug") or body.get("family") or "").strip()
    if not family:
        return {"ok": False, "reason": "family_slug_required_for_fresh_combo"}
    media_abs = _safe_join(cfg.output_root, rel)
    if media_abs is None or not media_abs.is_file():
        return {"ok": False, "reason": "source_media_not_found"}
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_queue import queue_from_source_media  # type: ignore

    try:
        out = queue_from_source_media(
            media_abs=media_abs,
            family_slug=family,
            body=body,
            repo_root=_repo_root(),
            workspace_root=cfg.workspace_root,
            output_root=cfg.output_root,
            comfy_server=str(cfg.comfy_server),
        )
        if isinstance(out, dict):
            out.setdefault("fresh_combo", True)
            out.setdefault("extend_fallback", "fresh_combo")
            out.setdefault("source_media_relpath", rel)
        return out
    except Exception as e:
        return {"ok": False, "reason": str(e), "fresh_combo": False}


def _fast_track_extend(cfg: "ServerConfig", rel: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Best-effort immediate 'do more WITH this' on fast_track: Extend (chain output ->
    video slot), falling back to a plain replay for still-source families. Never raises.

    When no parent job is indexable, queue a fresh combo from this media as source_video.
    """
    try:
        explicit_family = str(body.get("family_slug") or body.get("family") or "").strip()
        job_key, family = _resolve_replay_job_from_relpath(cfg, rel, body)
        target = explicit_family or family

        if not job_key:
            if not target:
                return {"ok": False, "reason": "no_replay_context"}
            fresh_body = dict(body)
            fresh_body["family_slug"] = target
            return _queue_fresh_from_source_media(cfg, rel, fresh_body)

        replay_body: Dict[str, Any] = {"job_key": job_key, "extend": True}
        # Prefer an explicit target family from the request over the source job's family.
        if target:
            replay_body["family_slug"] = target
        if body.get("front"):
            replay_body["front"] = True
        overrides = body.get("overrides")
        if isinstance(overrides, dict) and overrides:
            replay_body["overrides"] = overrides
        for alias in ("identity_anchor", "source_still", "identity_still"):
            if alias in body and body.get(alias) not in (None, ""):
                replay_body[alias] = body.get(alias)
        # Submit / Library always know the clip being extended (``rel``). Parent
        # I2V jobs often complete without stamping submit.outputs / deposit.videos,
        # so forward the media path explicitly or Extend fails with
        # ``extend requires a resolvable output path``.
        explicit_out = str(body.get("output_path") or "").strip()
        if explicit_out:
            replay_body["output_path"] = explicit_out
        else:
            media_abs = _safe_join(cfg.output_root, rel)
            if media_abs is not None and media_abs.is_file():
                replay_body["output_path"] = str(media_abs)
        try:
            return _shape_factory_replay_payload(cfg, replay_body)
        except ValueError as e:
            # Only still-source families (no video slot) fall back to plain replay.
            # Do not swallow missing-output / unsupported-extend errors as silent replays.
            if "extend_not_supported" in str(e).lower():
                replay_body["extend"] = False
                out = _shape_factory_replay_payload(cfg, replay_body)
                out["extend_fallback"] = "replay"
                out["extend_fallback_reason"] = str(e)
                return out
            raise
    except Exception as e:
        return {"ok": False, "reason": str(e)}


def _set_asset_appetite_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    rel = str(body.get("relpath") or "").strip()
    if not rel:
        raise ValueError("missing relpath")
    if "appetite" not in body:
        raise ValueError("missing appetite")
    media_abs = _safe_join(cfg.output_root, rel)
    if media_abs is None:
        raise ValueError("bad relpath")
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    import shape_factory_ratings  # type: ignore

    appetite = shape_factory_ratings.normalize_appetite(body.get("appetite"))
    facet = shape_factory_ratings.normalize_appetite_facet(body.get("facet"))
    og_root = _prefer_flat_library_dir(cfg.output_root, "og")
    saved = shape_factory_ratings.set_output_appetite(
        media_abs=media_abs,
        media_relpath=rel,
        appetite=appetite,
        facet=facet,
        og_root=og_root,
        appetite_index_path=_discovery_appetite_index_path(cfg),
    )
    if appetite == "fast_track":
        saved["queued"] = _fast_track_extend(cfg, rel, body)
    return saved


def _shape_factory_queue_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_queue import queue_from_request_body  # type: ignore

    return queue_from_request_body(
        body,
        repo_root=_repo_root(),
        workspace_root=cfg.workspace_root,
        output_root=cfg.output_root,
        comfy_server=str(cfg.comfy_server),
    )


def _shape_factory_replay_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_queue import replay_from_request_body  # type: ignore

    return replay_from_request_body(
        body,
        repo_root=_repo_root(),
        workspace_root=cfg.workspace_root,
        output_root=cfg.output_root,
        comfy_server=str(cfg.comfy_server),
    )


def _shape_factory_derive_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_queue import derive_from_request_body  # type: ignore

    return derive_from_request_body(
        body,
        repo_root=_repo_root(),
        workspace_root=cfg.workspace_root,
        output_root=cfg.output_root,
        comfy_server=str(cfg.comfy_server),
    )


def _shape_factory_unqueue_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/shape-factory/unqueue — remove waiting Comfy prompt; demote factory job to pending."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_creation_control import mutate_job  # type: ignore
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    prompt_id = str(body.get("prompt_id") or "").strip()
    if not prompt_id and not str(body.get("job_key") or "").strip():
        raise ValueError("missing_prompt_id")
    job_key = str(body.get("job_key") or "").strip() or None
    job_path_raw = str(body.get("job_path") or "").strip() or None
    reason = str(body.get("reason") or "user_unqueue").strip() or "user_unqueue"
    actor = str(body.get("actor") or "operator").strip() or "operator"
    source_surface = str(body.get("source_surface") or "workbench").strip() or "workbench"
    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    return mutate_job(
        action="unqueue_to_pending",
        prompt_id=prompt_id or None,
        server=str(cfg.comfy_server),
        data_root=data_root,
        job_key=job_key,
        job_path=Path(job_path_raw) if job_path_raw else None,
        reason=reason,
        actor=actor,
        source_surface=source_surface,
    )


def _shape_factory_discard_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/shape-factory/discard — archive/expunge a pending or terminal factory job."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_creation_control import mutate_job  # type: ignore
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore
    from shape_factory_work_products import dismiss_history_work_product  # type: ignore

    job_key = str(body.get("job_key") or "").strip() or None
    job_path_raw = str(body.get("job_path") or "").strip() or None
    prompt_id = str(body.get("prompt_id") or "").strip() or None
    history_stub = bool(body.get("history_from_comfy") or body.get("history_stub"))
    if not job_key and not job_path_raw and not prompt_id:
        raise ValueError("missing_job_key")
    reason = str(body.get("reason") or "user_removed").strip() or "user_removed"
    actor = str(body.get("actor") or "operator").strip() or "operator"
    source_surface = str(body.get("source_surface") or "workbench").strip() or "workbench"
    expunge_raw = body.get("expunge")
    expunge = True if expunge_raw is None else bool(expunge_raw) and str(expunge_raw).lower() not in {"0", "false", "no"}
    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    output_root = Path(cfg.output_root).expanduser().resolve()

    def _dismiss() -> Dict[str, Any]:
        return dismiss_history_work_product(
            data_root=data_root,
            prompt_id=prompt_id,
            job_key=job_key,
            reason=reason if reason != "user_removed" else "user_dismissed_history",
            output_root=output_root,
        )

    # History-only stubs never had a .job.json — dismiss so they stop reappearing.
    if history_stub and not job_path_raw:
        return _dismiss()

    result = mutate_job(
        action="discard",
        data_root=data_root,
        job_key=job_key,
        job_path=Path(job_path_raw) if job_path_raw else None,
        server=str(cfg.comfy_server),
        reason=reason,
        actor=actor,
        source_surface=source_surface,
        expunge=expunge,
    )
    if (
        isinstance(result, dict)
        and not result.get("ok")
        and result.get("error") == "job_not_found"
        and (prompt_id or job_key)
    ):
        return _dismiss()
    # Hard-delete should also suppress recent Comfy history stubs for the same
    # prompt/job key, otherwise the row may reappear from /history synthesis.
    if isinstance(result, dict) and result.get("ok") and expunge and (prompt_id or job_key):
        dismissed = _dismiss()
        if isinstance(dismissed, dict) and dismissed.get("ok"):
            result = dict(result)
            result["history_dismissed"] = bool(dismissed.get("dismissed"))
            result["dismissals_path"] = dismissed.get("dismissals_path")
    return result


def _shape_factory_update_pending_trim_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/shape-factory/update-pending-trim — patch VHS window on a pending job."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_creation_control import mutate_job  # type: ignore
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    job_key = str(body.get("job_key") or "").strip() or None
    job_path_raw = str(body.get("job_path") or "").strip() or None
    if not job_key and not job_path_raw:
        raise ValueError("missing_job_key")
    if "skip_first_frames" not in body and "frame_load_cap" not in body:
        raise ValueError("missing_vhs_window")
    try:
        skip = int(body.get("skip_first_frames") or 0)
    except (TypeError, ValueError) as e:
        raise ValueError("bad_skip_first_frames") from e
    try:
        cap = int(body.get("frame_load_cap") or 0)
    except (TypeError, ValueError) as e:
        raise ValueError("bad_frame_load_cap") from e
    mark_in = body.get("mark_in")
    mark_out = body.get("mark_out")
    actor = str(body.get("actor") or "operator").strip() or "operator"
    reason = str(body.get("reason") or "trim_adjustment").strip() or "trim_adjustment"
    source_surface = str(body.get("source_surface") or "submit_edit").strip() or "submit_edit"
    try:
        mark_in_f = float(mark_in) if mark_in is not None and mark_in != "" else None
    except (TypeError, ValueError) as e:
        raise ValueError("bad_mark_in") from e
    try:
        mark_out_f = float(mark_out) if mark_out is not None and mark_out != "" else None
    except (TypeError, ValueError) as e:
        raise ValueError("bad_mark_out") from e
    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    return mutate_job(
        action="update_trim",
        data_root=data_root,
        job_key=job_key,
        job_path=Path(job_path_raw) if job_path_raw else None,
        skip_first_frames=skip,
        frame_load_cap=cap,
        mark_in=mark_in_f,
        mark_out=mark_out_f,
        server=str(cfg.comfy_server),
        actor=actor,
        reason=reason,
        source_surface=source_surface,
    )


def _shape_factory_update_pending_binding_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/shape-factory/update-pending-binding — patch one binding on a pending/editing job."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_creation_control import mutate_job  # type: ignore
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    job_key = str(body.get("job_key") or "").strip() or None
    job_path_raw = str(body.get("job_path") or "").strip() or None
    slot = str(body.get("slot") or "").strip()
    binding_path = str(body.get("path") or body.get("binding_path") or "").strip()
    if not job_key and not job_path_raw:
        raise ValueError("missing_job_key")
    if not slot:
        raise ValueError("missing_slot")
    if not binding_path:
        raise ValueError("missing_binding_path")
    actor = str(body.get("actor") or "operator").strip() or "operator"
    reason = str(body.get("reason") or "binding_adjustment").strip() or "binding_adjustment"
    source_surface = str(body.get("source_surface") or "submit_edit").strip() or "submit_edit"
    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    return mutate_job(
        action="update_binding",
        data_root=data_root,
        job_key=job_key,
        job_path=Path(job_path_raw) if job_path_raw else None,
        server=str(cfg.comfy_server),
        slot=slot,
        binding_path=binding_path,
        actor=actor,
        reason=reason,
        source_surface=source_surface,
    )


def _shape_factory_update_owned_params_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/shape-factory/update-owned-params — patch frames/steps/overlap/seed."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory import update_pending_job_params  # type: ignore
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    job_key = str(body.get("job_key") or "").strip() or None
    job_path_raw = str(body.get("job_path") or "").strip() or None
    if not job_key and not job_path_raw:
        raise ValueError("missing_job_key")
    parameters = body.get("parameters")
    if not isinstance(parameters, dict) or not parameters:
        raise ValueError("missing_parameters")
    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    return update_pending_job_params(
        data_root=data_root,
        job_key=job_key,
        job_path=Path(job_path_raw) if job_path_raw else None,
        parameters=parameters,
        server=str(cfg.comfy_server),
    )


def _shape_factory_update_owned_prompt_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/shape-factory/update-owned-prompt — patch job-owned positive/negative."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory import update_pending_job_owned_prompt  # type: ignore
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    job_key = str(body.get("job_key") or "").strip() or None
    job_path_raw = str(body.get("job_path") or "").strip() or None
    if not job_key and not job_path_raw:
        raise ValueError("missing_job_key")
    has_rows = "positive_rows" in body or "negative_rows" in body
    has_text = "positive" in body or "negative" in body or "label" in body
    if not has_rows and not has_text:
        raise ValueError("missing_prompt_fields")
    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    return update_pending_job_owned_prompt(
        data_root=data_root,
        job_key=job_key,
        job_path=Path(job_path_raw) if job_path_raw else None,
        positive=body.get("positive") if "positive" in body else None,
        negative=body.get("negative") if "negative" in body else None,
        positive_rows=body.get("positive_rows") if "positive_rows" in body else None,
        negative_rows=body.get("negative_rows") if "negative_rows" in body else None,
        label=body.get("label") if "label" in body else None,
        server=str(cfg.comfy_server),
    )


def _shape_factory_promote_template_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/shape-factory/promote-template — write job prompt/params into family library."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory import promote_job_params_to_catalog, promote_job_prompt_to_library  # type: ignore
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    job_key = str(body.get("job_key") or "").strip() or None
    job_path_raw = str(body.get("job_path") or "").strip() or None
    if not job_key and not job_path_raw:
        raise ValueError("missing_job_key")
    fields_raw = body.get("fields")
    fields = [str(f).strip() for f in fields_raw] if isinstance(fields_raw, list) else ["prompt"]
    fields = [f for f in fields if f]
    if not fields:
        fields = ["prompt"]
    unsupported = [f for f in fields if f not in {"prompt", "params"}]
    if unsupported:
        raise ValueError("unsupported_fields")
    mode = str(body.get("mode") or "fork").strip().lower() or "fork"
    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    out: Dict[str, Any] = {"ok": True, "job_key": job_key, "fields": fields, "results": {}}
    if "prompt" in fields:
        prompt_res = promote_job_prompt_to_library(
            data_root=data_root,
            job_key=job_key,
            job_path=Path(job_path_raw) if job_path_raw else None,
            mode=mode,
            label=str(body.get("label") or "").strip() or None,
            note=str(body.get("note") or "").strip() or None,
            positive=body.get("positive") if isinstance(body.get("positive"), str) else None,
            negative=body.get("negative") if isinstance(body.get("negative"), str) else None,
        )
        out["results"]["prompt"] = prompt_res
        if not prompt_res.get("ok"):
            out["ok"] = False
            out["error"] = prompt_res.get("error") or "prompt_promote_failed"
            out["detail"] = prompt_res.get("detail")
            return out
        # Keep top-level keys for existing prompt-only clients.
        for k in ("mode", "path", "bak_path", "doc", "family_slug"):
            if k in prompt_res:
                out[k] = prompt_res[k]
    if "params" in fields:
        # Params promote defaults to overwrite (catalog readable); fork still available.
        params_mode = mode if mode in {"fork", "overwrite"} else "overwrite"
        if "prompt" not in fields and mode == "fork":
            params_mode = "fork"
        elif "prompt" not in fields:
            params_mode = mode or "overwrite"
        params_res = promote_job_params_to_catalog(
            data_root=data_root,
            job_key=job_key,
            job_path=Path(job_path_raw) if job_path_raw else None,
            mode=params_mode,
            parameters=body.get("parameters") if isinstance(body.get("parameters"), dict) else None,
        )
        out["results"]["params"] = params_res
        if not params_res.get("ok"):
            out["ok"] = False
            out["error"] = params_res.get("error") or "params_promote_failed"
            out["detail"] = params_res.get("detail")
            return out
        if "prompt" not in fields:
            for k in ("mode", "path", "bak_path"):
                if k in params_res:
                    out[k] = params_res[k]
    return out


def _shape_factory_begin_edit_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/shape-factory/begin-edit — unqueue if needed; lock job as editing."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_creation_control import mutate_job  # type: ignore
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    job_key = str(body.get("job_key") or "").strip() or None
    job_path_raw = str(body.get("job_path") or "").strip() or None
    actor = str(body.get("actor") or "operator").strip() or "operator"
    reason = str(body.get("reason") or "begin_edit").strip() or "begin_edit"
    source_surface = str(body.get("source_surface") or "submit_edit").strip() or "submit_edit"
    if not job_key and not job_path_raw:
        raise ValueError("missing_job_key")
    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    return mutate_job(
        action="begin_edit",
        data_root=data_root,
        server=str(cfg.comfy_server),
        job_key=job_key,
        job_path=Path(job_path_raw) if job_path_raw else None,
        actor=actor,
        reason=reason,
        source_surface=source_surface,
    )


def _shape_factory_finish_edit_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/shape-factory/finish-edit — release editing lock (later|cancel|now)."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_creation_control import mutate_job  # type: ignore
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    job_key = str(body.get("job_key") or "").strip() or None
    job_path_raw = str(body.get("job_path") or "").strip() or None
    if not job_key and not job_path_raw:
        raise ValueError("missing_job_key")
    action = str(body.get("action") or "").strip().lower()
    if action not in {"later", "cancel", "now"}:
        raise ValueError("bad_action")
    actor = str(body.get("actor") or "operator").strip() or "operator"
    reason = str(body.get("reason") or f"finish_edit:{action}").strip() or f"finish_edit:{action}"
    source_surface = str(body.get("source_surface") or "submit_edit").strip() or "submit_edit"
    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    return mutate_job(
        action="finish_edit",
        data_root=data_root,
        finish_action=action,
        server=str(cfg.comfy_server),
        job_key=job_key,
        job_path=Path(job_path_raw) if job_path_raw else None,
        front=bool(body.get("front") or False),
        dry_run=bool(body.get("dry_run") or False),
        actor=actor,
        reason=reason,
        source_surface=source_surface,
    )


def _shape_factory_job_edit_payload(cfg: ServerConfig, q: Dict[str, List[str]]) -> Dict[str, Any]:
    """GET /api/shape-factory/job-edit?job_key=… — snapshot for Submit edit mode."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory import job_edit_snapshot  # type: ignore
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    job_key = str((q.get("job_key") or [""])[0] or "").strip() or None
    job_path_raw = str((q.get("job_path") or [""])[0] or "").strip() or None
    if not job_key and not job_path_raw:
        raise ValueError("missing_job_key")
    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    return job_edit_snapshot(
        data_root=data_root,
        output_root=Path(cfg.output_root).expanduser().resolve(),
        job_key=job_key,
        job_path=Path(job_path_raw) if job_path_raw else None,
    )


def _clips_registry_path(cfg: "ServerConfig") -> Path:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    import asset_registry as areg  # type: ignore

    # Always under the live Comfy output tree (writable), not shape-factory .data.
    og = Path(cfg.output_root).expanduser().resolve() / "og"
    return areg.default_registry_path(og)


def _resolve_parent_content_id_for_media(
    cfg: "ServerConfig",
    media_relpath: str,
    *,
    con: Any = None,
) -> tuple[Optional[str], Optional[Path], float]:
    """Return (content_id, abs_path, duration_s) for an output-relative media path."""
    rel = _normalize_rel_posix(media_relpath)
    if not rel:
        return None, None, 0.0
    abs_path = _safe_join(cfg.output_root, rel)
    if abs_path is None or not abs_path.is_file():
        return None, None, 0.0
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    import asset_registry as areg  # type: ignore
    from shape_factory_queue import _probe_media_frame_meta  # type: ignore

    duration = float((_probe_media_frame_meta(abs_path) or {}).get("duration") or 0.0)
    own = con is None
    if own:
        reg = _clips_registry_path(cfg)
        con = areg.connect(reg)
    try:
        existing = areg.by_relpath(con, rel)
        if existing and existing.get("content_id"):
            cid = str(existing["content_id"])
        else:
            cid = areg.register(con, abs_path, relpath=rel, kind="video", with_dims=False)
    finally:
        if own:
            con.close()
    return cid, abs_path, duration


def _shape_factory_clips_library_payload(cfg: "ServerConfig", q: Dict[str, List[str]]) -> Dict[str, Any]:
    """GET /api/shape-factory/clips/library — browse clips across parents."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_clips import connect_clips, list_clips_library  # type: ignore
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    def _int(name: str, default: int) -> int:
        raw = (q.get(name) or [""])[0].strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    origin = (q.get("origin") or [""])[0].strip() or None
    query = (q.get("q") or [""])[0].strip() or None
    media_relpath = (q.get("media_relpath") or q.get("media") or [""])[0].strip() or None
    defaults_only = (q.get("defaults_only") or [""])[0].strip().lower() in ("1", "true", "yes")
    include_deleted = (q.get("include_deleted") or [""])[0].strip().lower() in ("1", "true", "yes")
    deleted_only = (q.get("deleted_only") or [""])[0].strip().lower() in ("1", "true", "yes")
    starred_only = (q.get("starred_only") or [""])[0].strip().lower() in ("1", "true", "yes")
    unused_only = (q.get("unused_only") or [""])[0].strip().lower() in ("1", "true", "yes")
    used_only = (q.get("used_only") or [""])[0].strip().lower() in ("1", "true", "yes")
    sort = (q.get("sort") or [""])[0].strip() or None
    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    jobs_root = Path(data_root) / "shape_factory" / "jobs"
    try:
        ratings_doc = _discovery_load_ratings_index(cfg)
    except Exception:
        ratings_doc = None
    reg = _clips_registry_path(cfg)
    con = connect_clips(reg)
    try:
        return list_clips_library(
            con,
            limit=_int("limit", 100),
            offset=_int("offset", 0),
            origin=origin,
            q=query,
            defaults_only=defaults_only,
            media_relpath=media_relpath,
            include_deleted=include_deleted,
            deleted_only=deleted_only,
            jobs_root=jobs_root,
            unused_only=unused_only,
            used_only=used_only,
            starred_only=starred_only,
            sort=sort,
            ratings_doc=ratings_doc,
        )
    finally:
        con.close()


def _shape_factory_clips_derived_payload(cfg: "ServerConfig", q: Dict[str, List[str]]) -> Dict[str, Any]:
    """GET /api/shape-factory/clips/derived — outputs from jobs that used a clip bookmark."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_clips import connect_clips, list_clip_derived_videos  # type: ignore
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    def _int(name: str, default: int) -> int:
        raw = (q.get(name) or [""])[0].strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    clip_id = (q.get("clip_id") or [""])[0].strip() or None
    media_relpath = (q.get("media_relpath") or q.get("media") or [""])[0].strip() or None
    include_pending = (q.get("include_pending") or [""])[0].strip().lower() in ("1", "true", "yes")
    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    jobs_root = Path(data_root) / "shape_factory" / "jobs"
    reg = _clips_registry_path(cfg)
    con = connect_clips(reg)
    try:
        return list_clip_derived_videos(
            jobs_root=jobs_root,
            output_root=cfg.output_root,
            con=con,
            clip_id=clip_id,
            media_relpath=media_relpath,
            limit=_int("limit", 200),
            include_without_output=include_pending,
        )
    finally:
        con.close()


def _shape_factory_clips_list_payload(cfg: "ServerConfig", q: Dict[str, List[str]]) -> Dict[str, Any]:
    """GET /api/shape-factory/clips?media_relpath=og/... or parent_content_id=..."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_clips import (  # type: ignore
        connect_clips,
        get_default_clip_id,
        import_trims_presets_as_clips,
        list_clips_for_parent,
    )

    parent = (q.get("parent_content_id") or [""])[0].strip()
    media_rel = (q.get("media_relpath") or [""])[0].strip()
    duration = 0.0
    abs_path: Optional[Path] = None
    reg = _clips_registry_path(cfg)
    con = connect_clips(reg)
    try:
        if not parent and media_rel:
            parent_id, abs_path, duration = _resolve_parent_content_id_for_media(
                cfg, media_rel, con=con
            )
            parent = parent_id or ""
        if not parent:
            # Stills live under input/, not output/; clip bookmarks are video-only.
            return {
                "ok": True,
                "parent_content_id": None,
                "default_clip_id": None,
                "clips": [],
                "media_relpath": media_rel or None,
            }

        # Bridge sidecar presets once when listing by media.
        if abs_path is not None and abs_path.is_file():
            sidecar = abs_path.with_suffix(".trims.json")
            if sidecar.is_file():
                try:
                    doc = json.loads(sidecar.read_text(encoding="utf-8"))
                    import_trims_presets_as_clips(
                        con,
                        parent_content_id=parent,
                        trims_doc=doc if isinstance(doc, dict) else {},
                        duration_s=duration or None,
                    )
                except Exception:
                    pass
        clips = list_clips_for_parent(con, parent)
        default_id = get_default_clip_id(con, parent)
    finally:
        con.close()
    return {
        "ok": True,
        "parent_content_id": parent,
        "default_clip_id": default_id,
        "clips": clips,
        "media_relpath": media_rel or None,
    }


def _shape_factory_clips_mutate_payload(cfg: "ServerConfig", body: Dict[str, Any]) -> Dict[str, Any]:
    """
    POST /api/shape-factory/clips
      { op: create|update|delete|restore|set_default, ... }
    """
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_clips import (  # type: ignore
        connect_clips,
        create_clip,
        delete_clip,
        get_clip,
        restore_clip,
        set_default_clip,
        star_clip,
        unstar_clip,
        update_clip,
    )
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    op = str(body.get("op") or "create").strip().lower()
    reg = _clips_registry_path(cfg)
    con = connect_clips(reg)
    try:
        if op == "create":
            parent = str(body.get("parent_content_id") or "").strip()
            media_rel = str(body.get("media_relpath") or "").strip()
            duration = 0.0
            if not parent and media_rel:
                parent_id, _abs, duration = _resolve_parent_content_id_for_media(cfg, media_rel)
                parent = parent_id or ""
            if not parent:
                raise ValueError("missing_parent")
            try:
                tin = float(body.get("mark_in") if body.get("mark_in") is not None else body.get("mark_in_s"))
                tout = float(body.get("mark_out") if body.get("mark_out") is not None else body.get("mark_out_s"))
            except (TypeError, ValueError) as e:
                raise ValueError("bad_marks") from e
            create_notes = body.get("notes")
            clip = create_clip(
                con,
                parent_content_id=parent,
                mark_in_s=tin,
                mark_out_s=tout,
                label=str(body.get("label") or "Clip"),
                origin=str(body.get("origin") or "workbench"),
                notes=str(create_notes) if create_notes is not None else None,
                duration_s=duration or None,
            )
            if body.get("set_default"):
                set_default_clip(con, parent, clip["clip_id"])
            from shape_factory_clips import get_default_clip_id as _get_def  # type: ignore

            return {
                "ok": True,
                "clip": clip,
                "default_clip_id": _get_def(con, parent),
            }
        if op == "update":
            cid = str(body.get("clip_id") or "").strip()
            if not cid:
                raise ValueError("missing_clip_id")
            kwargs: Dict[str, Any] = {}
            if body.get("mark_in") is not None or body.get("mark_in_s") is not None:
                kwargs["mark_in_s"] = float(body.get("mark_in") if body.get("mark_in") is not None else body.get("mark_in_s"))
            if body.get("mark_out") is not None or body.get("mark_out_s") is not None:
                kwargs["mark_out_s"] = float(body.get("mark_out") if body.get("mark_out") is not None else body.get("mark_out_s"))
            if body.get("label") is not None:
                kwargs["label"] = str(body.get("label"))
            if body.get("notes") is not None:
                kwargs["notes"] = str(body.get("notes"))
            clip = update_clip(con, cid, **kwargs)
            return {"ok": True, "clip": clip}
        if op == "delete":
            cid = str(body.get("clip_id") or "").strip()
            if not cid:
                raise ValueError("missing_clip_id")
            hard = bool(body.get("hard"))
            jobs_root = None
            if hard:
                data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
                jobs_root = Path(data_root) / "shape_factory" / "jobs"
            ok = delete_clip(con, cid, hard=hard, jobs_root=jobs_root)
            clip = get_clip(con, cid) if ok and not hard else None
            return {"ok": ok, "clip_id": cid, "deleted": True, "hard": hard, "clip": clip}
        if op == "restore":
            cid = str(body.get("clip_id") or "").strip()
            if not cid:
                raise ValueError("missing_clip_id")
            clip = restore_clip(con, cid)
            if clip is None:
                raise KeyError(f"clip_not_found:{cid}")
            return {"ok": True, "clip": clip, "restored": True}
        if op == "set_default":
            parent = str(body.get("parent_content_id") or "").strip()
            media_rel = str(body.get("media_relpath") or "").strip()
            if not parent and media_rel:
                parent_id, _a, _d = _resolve_parent_content_id_for_media(cfg, media_rel)
                parent = parent_id or ""
            if not parent:
                raise ValueError("missing_parent")
            raw_cid = body.get("clip_id")
            cid = str(raw_cid).strip() if raw_cid is not None and str(raw_cid).strip() else None
            default_id = set_default_clip(con, parent, cid)
            return {"ok": True, "parent_content_id": parent, "default_clip_id": default_id}
        if op == "star":
            cid = str(body.get("clip_id") or "").strip()
            if not cid:
                raise ValueError("missing_clip_id")
            clip = star_clip(con, cid)
            return {"ok": True, "clip": clip, "starred": True}
        if op == "unstar":
            cid = str(body.get("clip_id") or "").strip()
            if not cid:
                raise ValueError("missing_clip_id")
            clip = unstar_clip(con, cid)
            return {"ok": True, "clip": clip, "starred": False}
        raise ValueError(f"bad_op:{op}")
    finally:
        con.close()


def _shape_factory_prompt_profile_payload(cfg: ServerConfig, q: Dict[str, List[str]]) -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_queue import prompt_profile_from_request  # type: ignore

    flat: Dict[str, Any] = {}
    for key, vals in q.items():
        if vals:
            flat[key] = vals[0]
    return prompt_profile_from_request(
        flat,
        repo_root=_repo_root(),
        workspace_root=cfg.workspace_root,
        output_root=cfg.output_root,
    )


def _shape_factory_families_payload(cfg: ServerConfig) -> Dict[str, Any]:
    """GET /api/shape-factory/families — config-only extend/vary/derive picker sets."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore
    from shape_factory_work_products import list_submit_family_sets  # type: ignore

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    payload = list_submit_family_sets(
        data_root,
        workspace_root=cfg.workspace_root,
        output_root=cfg.output_root,
    )
    promo_path = _shape_factory_template_promotions_path(cfg, data_root)
    legacy_path = _shape_factory_template_promotions_legacy_path(data_root)
    reg = _shape_factory_template_promotions_load(promo_path, fallback_paths=[legacy_path])
    payload = _shape_factory_apply_promotions_to_family_sets(payload, reg.get("entries"))
    payload["template_promotions"] = {
        "effective": _shape_factory_template_promotions_effective(reg.get("entries")),
        "path": str(promo_path),
    }
    return payload


def _shape_factory_template_promotions_legacy_path(data_root: Path) -> Path:
    return data_root / "_status" / "template_promotions.json"


def _shape_factory_template_promotions_path(cfg: ServerConfig, data_root: Path) -> Path:
    # Prefer queue-ledger status dir: writable in runpod containers where /.data may be RO.
    status_dir = Path(cfg.queue_ledger_state_path).expanduser().resolve().parent
    return status_dir / "template_promotions.json"


def _shape_factory_template_promotions_load(path: Path, *, fallback_paths: Optional[List[Path]] = None) -> Dict[str, Any]:
    candidates = [path]
    for p in fallback_paths or []:
        if p not in candidates:
            candidates.append(p)
    raw: Any = {}
    for cand in candidates:
        try:
            raw = _read_json(cand)
            break
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    entries = raw.get("entries")
    if not isinstance(entries, list):
        entries = []
    out: List[Dict[str, Any]] = []
    for ent in entries:
        if not isinstance(ent, dict):
            continue
        slug = str(ent.get("family_slug") or "").strip()
        if not slug:
            continue
        intent = str(ent.get("intent") or "extend").strip().lower() or "extend"
        if intent not in {"extend", "vary", "derive"}:
            continue
        scope = str(ent.get("scope") or ent.get("mode") or "long_term").strip().lower()
        if scope in {"permanent", "longterm"}:
            scope = "long_term"
        if scope not in {"temporary", "long_term"}:
            scope = "long_term"
        rec: Dict[str, Any] = {
            "family_slug": slug,
            "intent": intent,
            "scope": scope,
            "note": str(ent.get("note") or "").strip() or None,
            "actor": str(ent.get("actor") or "").strip() or None,
            "created_at": str(ent.get("created_at") or "").strip() or None,
            "starts_at": str(ent.get("starts_at") or "").strip() or None,
            "expires_at": str(ent.get("expires_at") or "").strip() or None,
        }
        out.append(rec)
    return {"schema_version": "v1", "entries": out}


def _shape_factory_template_promotions_save(path: Path, reg: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "v1",
        "entries": reg.get("entries") if isinstance(reg.get("entries"), list) else [],
        "updated_at": _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _shape_factory_parse_iso_utc(iso: Optional[str]) -> Optional[_dt.datetime]:
    s = str(iso or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = _dt.datetime.fromisoformat(s)
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc)


def _shape_factory_template_promotions_active(entries: Any, now: Optional[_dt.datetime] = None) -> List[Dict[str, Any]]:
    now_utc = now or _dt.datetime.now(_dt.timezone.utc)
    out: List[Dict[str, Any]] = []
    if not isinstance(entries, list):
        return out
    for ent in entries:
        if not isinstance(ent, dict):
            continue
        starts = _shape_factory_parse_iso_utc(ent.get("starts_at"))
        expires = _shape_factory_parse_iso_utc(ent.get("expires_at"))
        if starts and starts > now_utc:
            continue
        if expires and expires <= now_utc:
            continue
        out.append(ent)
    return out


def _shape_factory_template_promotions_effective(entries: Any) -> Dict[str, Dict[str, Any]]:
    active = _shape_factory_template_promotions_active(entries)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for ent in active:
        slug = str(ent.get("family_slug") or "").strip()
        if not slug:
            continue
        grouped.setdefault(slug, []).append(ent)
    out: Dict[str, Dict[str, Any]] = {}
    for slug, rows in grouped.items():
        rows_sorted = sorted(
            rows,
            key=lambda r: (
                1 if str(r.get("scope") or "") == "temporary" else 0,
                str(r.get("created_at") or ""),
            ),
            reverse=True,
        )
        best = rows_sorted[0] if rows_sorted else {}
        intents = sorted({str(r.get("intent") or "").strip() for r in rows if str(r.get("intent") or "").strip()})
        out[slug] = {
            "scope": best.get("scope") or "long_term",
            "intents": intents,
            "expires_at": best.get("expires_at"),
            "note": best.get("note"),
        }
    return out


def _shape_factory_apply_promotions_to_family_sets(payload: Dict[str, Any], entries: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    active = _shape_factory_template_promotions_active(entries)
    effective = _shape_factory_template_promotions_effective(entries)
    promoted_by_intent: Dict[str, List[str]] = {"extend": [], "vary": [], "derive": []}
    for ent in sorted(
        active,
        key=lambda r: (
            1 if str(r.get("scope") or "") == "temporary" else 0,
            str(r.get("created_at") or ""),
        ),
        reverse=True,
    ):
        slug = str(ent.get("family_slug") or "").strip()
        intent = str(ent.get("intent") or "").strip().lower()
        if not slug or intent not in promoted_by_intent:
            continue
        if slug not in promoted_by_intent[intent]:
            promoted_by_intent[intent].append(slug)

    def _decorate(rows_in: Any) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        if not isinstance(rows_in, list):
            return rows
        for raw in rows_in:
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            slug = str(row.get("slug") or "").strip()
            if slug and slug in effective:
                row["promotion"] = dict(effective[slug])
            rows.append(row)
        return rows

    def _sort_rows(rows: List[Dict[str, Any]], intent: str) -> List[Dict[str, Any]]:
        rank: Dict[str, int] = {slug: i for i, slug in enumerate(promoted_by_intent.get(intent, []))}
        return sorted(
            rows,
            key=lambda r: (
                rank.get(str(r.get("slug") or "").strip(), 10_000),
                str(r.get("slug") or ""),
            ),
        )

    families = _decorate(payload.get("families"))
    payload["families"] = families
    sets = payload.get("sets")
    if not isinstance(sets, dict):
        sets = {}
    for intent in ("extend", "vary", "derive"):
        decorated = _decorate(sets.get(intent) if isinstance(sets, dict) else None)
        sets[intent] = _sort_rows(decorated or list(families), intent)
    payload["sets"] = sets
    payload["extend_families"] = sets.get("extend", families)
    payload["vary_families"] = sets.get("vary", families)
    payload["derive_families"] = sets.get("derive", families)
    defaults = payload.get("extend_family_defaults")
    defaults = dict(defaults) if isinstance(defaults, dict) else {}
    if promoted_by_intent.get("extend"):
        defaults["*"] = promoted_by_intent["extend"][0]
    payload["extend_family_defaults"] = defaults
    return payload


def _shape_factory_template_promotions_payload(cfg: ServerConfig, q: Dict[str, List[str]]) -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    include_expired = str((q.get("include_expired") or ["0"])[0]).strip().lower() in {"1", "true", "yes"}
    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    path = _shape_factory_template_promotions_path(cfg, data_root)
    legacy_path = _shape_factory_template_promotions_legacy_path(data_root)
    reg = _shape_factory_template_promotions_load(path, fallback_paths=[legacy_path])
    entries = reg.get("entries")
    active = _shape_factory_template_promotions_active(entries)
    return {
        "ok": True,
        "path": str(path),
        "schema_version": "v1",
        "entries": entries if include_expired else active,
        "active_entries": active,
        "effective": _shape_factory_template_promotions_effective(entries),
    }


def _shape_factory_input_curation_paths(cfg: ServerConfig, data_root: Path) -> Dict[str, Path]:
    primary_root = data_root / "shape_factory"
    fallback_root = _output_status_dir(cfg.output_root) / "shape_factory"
    return {
        "collections_primary": primary_root / "input_collections.json",
        "bindings_primary": primary_root / "input_collection_bindings.json",
        "tags_primary": primary_root / "input_still_tags.json",
        "tags_db_primary": primary_root / "still_tags.sqlite",
        "collections_fallback": fallback_root / "input_collections.json",
        "bindings_fallback": fallback_root / "input_collection_bindings.json",
        "tags_fallback": fallback_root / "input_still_tags.json",
        "tags_db_fallback": fallback_root / "still_tags.sqlite",
    }


def _shape_factory_still_tag_status_dir(cfg: ServerConfig) -> Path:
    return _output_status_dir(cfg.output_root)


def _shape_factory_input_curation_stills_tag_enqueue_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore
    from vision_still_tags import (  # type: ignore
        enqueue_run,
        kick_worker,
        should_auto_drain_on_enqueue,
    )

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    content_ids = body.get("content_ids")
    if isinstance(content_ids, str):
        content_ids = [content_ids]
    if not isinstance(content_ids, list):
        content_ids = None
    else:
        content_ids = [str(x).strip() for x in content_ids if str(x).strip()]
    collection_id = str(body.get("collection_id") or "").strip() or None
    only_missing = body.get("only_missing", True) is not False
    force = body.get("force") is True
    limit = _safe_int(body.get("limit"))
    if limit is None:
        limit = 12
    limit = max(1, min(5000, int(limit)))
    dry_run = body.get("dry_run") is True
    provider = str(body.get("provider") or ("dry-run" if dry_run else "comfy")).strip() or "comfy"
    comfy_server = str(body.get("comfy_server") or "").strip() or None
    drain_now = body.get("drain_now") is True
    status_dir = _shape_factory_still_tag_status_dir(cfg)
    enq = enqueue_run(
        data_root=data_root,
        content_ids=content_ids or None,
        collection_id=collection_id,
        only_missing=only_missing and not force,
        limit=limit,
        force=force,
        provider=provider,
        comfy_server=comfy_server,
        dry_run=dry_run,
        status_dir=status_dir,
    )
    auto = should_auto_drain_on_enqueue(data_root=data_root, drain_now=drain_now)
    if auto:
        kick_worker(data_root=data_root, status_dir=status_dir, front=body.get("front") is True)
    enq["ok"] = True
    enq["drain_kicked"] = bool(auto)
    enq["queued_for_index_hour"] = not bool(auto)
    return enq


def _shape_factory_input_curation_stills_tag_backlog_payload(cfg: ServerConfig) -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore
    from vision_still_tags import backlog_stats, index_window_status, load_schedule  # type: ignore

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    sch = load_schedule(data_root=data_root)
    stats = backlog_stats(data_root=data_root)
    stats["schedule"] = sch
    stats["window"] = index_window_status(sch)
    return stats


def _shape_factory_input_curation_stills_tag_schedule_payload(cfg: ServerConfig) -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore
    from vision_still_tags import (  # type: ignore
        default_schedule_path,
        index_window_status,
        load_schedule,
    )

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    sch = load_schedule(data_root=data_root)
    return {
        "ok": True,
        "path": str(default_schedule_path(data_root=data_root)),
        "schedule": sch,
        "window": index_window_status(sch),
    }


def _shape_factory_input_curation_stills_tag_schedule_set_payload(
    cfg: ServerConfig, body: Dict[str, Any]
) -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore
    from vision_still_tags import (  # type: ignore
        default_schedule_path,
        index_window_status,
        load_schedule,
        save_schedule,
    )

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    cur = load_schedule(data_root=data_root)
    patch = body.get("schedule") if isinstance(body.get("schedule"), dict) else body
    if not isinstance(patch, dict):
        raise ValueError("schedule object required")
    for k, v in patch.items():
        if k in cur or k == "schema_version":
            cur[k] = v
    saved = save_schedule(cur, data_root=data_root)
    return {
        "ok": True,
        "path": str(default_schedule_path(data_root=data_root)),
        "schedule": saved,
        "window": index_window_status(saved),
    }


def _shape_factory_input_curation_stills_tag_drain_payload(
    cfg: ServerConfig, body: Dict[str, Any]
) -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore
    from vision_still_tags import drain_backlog, kick_drain  # type: ignore

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    status_dir = _shape_factory_still_tag_status_dir(cfg)
    force = body.get("force") is True
    respect = body.get("respect_schedule", not force) is not False
    front = body.get("front")
    front_b = None if front is None else bool(front)
    max_items = _safe_int(body.get("max_items"))
    until = body.get("until_minutes")
    until_f = float(until) if until is not None and str(until).strip() != "" else None
    sync = body.get("sync") is True
    provider = str(body.get("provider") or "").strip() or None
    comfy_server = str(body.get("comfy_server") or "").strip() or None
    if sync:
        result = drain_backlog(
            data_root=data_root,
            status_dir=status_dir,
            force=force,
            respect_schedule=respect and not force,
            front=front_b,
            max_items=max_items,
            until_minutes=until_f,
            provider_override=provider,
            comfy_server_override=comfy_server,
        )
        return {"ok": True, "sync": True, "started": True, "result": result, **{k: result.get(k) for k in ("skipped", "front", "done_items", "runs_processed", "reason") if k in result}}
    kicked = kick_drain(
        data_root=data_root,
        status_dir=status_dir,
        force=force,
        respect_schedule=respect and not force,
        front=front_b,
        max_items=max_items,
        until_minutes=until_f,
    )
    return {"ok": True, "sync": False, **kicked}


def _shape_factory_input_curation_stills_tag_run_payload(cfg: ServerConfig, run_id: str) -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore
    from vision_still_tags import connect, default_db_path, ensure_db, get_run  # type: ignore

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    db = default_db_path(data_root=data_root)
    ensure_db(db)
    con = connect(db)
    try:
        run = get_run(con, run_id)
    finally:
        con.close()
    if not run:
        return {"ok": False, "error": "run_not_found", "run_id": run_id}
    return {"ok": True, "run": run}


def _shape_factory_input_curation_stills_tag_events_payload(
    cfg: ServerConfig, run_id: str, q: Dict[str, List[str]]
) -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore
    from vision_still_tags import connect, default_db_path, ensure_db, list_events  # type: ignore

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    after_id = _safe_int((q.get("after_id") or ["0"])[0]) or 0
    limit = _safe_int((q.get("limit") or ["200"])[0]) or 200
    db = default_db_path(data_root=data_root)
    ensure_db(db)
    con = connect(db)
    try:
        events = list_events(con, run_id=run_id, after_id=int(after_id), limit=int(limit))
    finally:
        con.close()
    return {"ok": True, "run_id": run_id, "events": events, "count": len(events)}


def _shape_factory_input_curation_state_payload(cfg: ServerConfig) -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_input_curation import load_bindings, load_collections  # type: ignore
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    paths = _shape_factory_input_curation_paths(cfg, data_root)
    collections = load_collections(data_root, fallback_paths=[paths["collections_fallback"]])
    bindings = load_bindings(data_root, fallback_paths=[paths["bindings_fallback"]])
    return {
        "ok": True,
        "schema_version": "v1",
        "data_root": str(data_root),
        "paths": {k: str(v) for k, v in paths.items()},
        "collections": collections.get("collections") or [],
        "bindings": (bindings.get("families") or {}) if isinstance(bindings.get("families"), dict) else {},
        "updated_at": max(
            str(collections.get("updated_at") or ""),
            str(bindings.get("updated_at") or ""),
        )
        or None,
    }


def _shape_factory_input_curation_stills_payload(cfg: ServerConfig, q: Dict[str, List[str]]) -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_input_curation import list_catalog_stills  # type: ignore
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    limit = 200
    offset = 0
    for raw in q.get("limit", []):
        n = _safe_int(raw)
        if n is not None:
            limit = max(1, min(2000, int(n)))
            break
    for raw in q.get("offset", []):
        n = _safe_int(raw)
        if n is not None:
            offset = max(0, int(n))
            break
    qtext = str((q.get("q") or [""])[0] or "").strip()
    tag = str((q.get("tag") or [""])[0] or "").strip()
    scan = str((q.get("scan") or ["0"])[0]).strip().lower() in {"1", "true", "yes"}
    payload = list_catalog_stills(
        data_root=data_root, q=qtext, limit=limit, offset=offset, scan=scan, tag=tag
    )
    # Quote file URLs properly for the browser.
    for it in payload.get("items") or []:
        if not isinstance(it, dict):
            continue
        rel = str(it.get("relpath") or "").strip().replace("\\", "/")
        if rel:
            quoted = "/files/" + urllib.parse.quote(rel, safe="/")
            it["url"] = quoted
            it["thumb_url"] = quoted
    payload["data_root"] = str(data_root)
    return payload


def _shape_factory_input_curation_collections_mutate_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_input_curation import (  # type: ignore
        choose_writable_path,
        load_collections,
        save_collections,
    )
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    paths = _shape_factory_input_curation_paths(cfg, data_root)
    write_path = choose_writable_path(paths["collections_primary"], [paths["collections_fallback"]])
    doc = load_collections(data_root, fallback_paths=[paths["collections_fallback"]])
    op = str(body.get("op") or "").strip().lower()
    collection_id = str(body.get("collection_id") or body.get("id") or "").strip()
    now = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = doc.get("collections") if isinstance(doc.get("collections"), list) else []
    by_id = {
        str(row.get("id") or "").strip(): row
        for row in rows
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    }

    if op == "create":
        name = str(body.get("name") or "").strip()
        if not name:
            raise ValueError("missing_name")
        if not collection_id:
            collection_id = _slug(name).lower() or f"collection-{len(by_id)+1}"
        if collection_id in by_id:
            raise ValueError("collection_exists")
        rec = {
            "id": collection_id,
            "name": name,
            "description": str(body.get("description") or "").strip() or None,
            "created_at": now,
            "updated_at": now,
            "items": [],
        }
        rows.append(rec)
        doc["collections"] = rows
    elif op == "rename":
        if not collection_id or collection_id not in by_id:
            raise ValueError("collection_not_found")
        name = str(body.get("name") or "").strip()
        if not name:
            raise ValueError("missing_name")
        by_id[collection_id]["name"] = name
        by_id[collection_id]["updated_at"] = now
    elif op == "delete":
        if not collection_id:
            raise ValueError("missing_collection_id")
        rows = [row for row in rows if not (isinstance(row, dict) and str(row.get("id") or "").strip() == collection_id)]
        doc["collections"] = rows
    elif op == "add_item":
        if not collection_id or collection_id not in by_id:
            raise ValueError("collection_not_found")
        path = str(body.get("path") or "").strip()
        if not path:
            raise ValueError("missing_path")
        items = by_id[collection_id].get("items") if isinstance(by_id[collection_id].get("items"), list) else []
        if not any(isinstance(it, dict) and str(it.get("path") or "").strip() == path for it in items):
            items.append(
                {
                    "path": path,
                    "added_at": now,
                    "note": str(body.get("note") or "").strip() or None,
                }
            )
            by_id[collection_id]["updated_at"] = now
        by_id[collection_id]["items"] = items
    elif op == "remove_item":
        if not collection_id or collection_id not in by_id:
            raise ValueError("collection_not_found")
        path = str(body.get("path") or "").strip()
        if not path:
            raise ValueError("missing_path")
        items = by_id[collection_id].get("items") if isinstance(by_id[collection_id].get("items"), list) else []
        by_id[collection_id]["items"] = [
            it for it in items if not (isinstance(it, dict) and str(it.get("path") or "").strip() == path)
        ]
        by_id[collection_id]["updated_at"] = now
    else:
        raise ValueError("bad_op")

    saved = save_collections(write_path, doc)
    return {
        "ok": True,
        "path": str(write_path),
        "collections": saved.get("collections") or [],
        "updated_at": saved.get("updated_at"),
    }


def _shape_factory_input_curation_bindings_mutate_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_input_curation import (  # type: ignore
        choose_writable_path,
        load_bindings,
        save_bindings,
    )
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    paths = _shape_factory_input_curation_paths(cfg, data_root)
    write_path = choose_writable_path(paths["bindings_primary"], [paths["bindings_fallback"]])
    doc = load_bindings(data_root, fallback_paths=[paths["bindings_fallback"]])
    families = doc.get("families") if isinstance(doc.get("families"), dict) else {}
    doc["families"] = families
    op = str(body.get("op") or "").strip().lower()
    family_slug = str(body.get("family_slug") or "").strip()
    collection_id = str(body.get("collection_id") or "").strip()
    if not family_slug:
        raise ValueError("missing_family_slug")
    current = [str(v) for v in (families.get(family_slug) or []) if str(v).strip()]
    if op == "attach":
        if not collection_id:
            raise ValueError("missing_collection_id")
        if collection_id not in current:
            current.append(collection_id)
    elif op == "detach":
        if not collection_id:
            raise ValueError("missing_collection_id")
        current = [v for v in current if v != collection_id]
    elif op == "set":
        raw = body.get("collection_ids") if isinstance(body.get("collection_ids"), list) else []
        current = [str(v).strip() for v in raw if str(v).strip()]
    else:
        raise ValueError("bad_op")
    families[family_slug] = current
    saved = save_bindings(write_path, doc)
    return {
        "ok": True,
        "path": str(write_path),
        "family_slug": family_slug,
        "collection_ids": current,
        "bindings": saved.get("families") if isinstance(saved.get("families"), dict) else {},
        "updated_at": saved.get("updated_at"),
    }


def _shape_factory_input_curation_tags_mutate_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_input_curation import (  # type: ignore
        choose_writable_path,
        upsert_still_tags,
    )
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    paths = _shape_factory_input_curation_paths(cfg, data_root)
    write_path = choose_writable_path(paths["tags_primary"], [paths["tags_fallback"]])
    content_id = str(body.get("content_id") or "").strip().lower()
    if not content_id:
        raise ValueError("missing_content_id")
    tags = body.get("tags")
    note = body.get("note") if "note" in body else None
    if tags is not None and not isinstance(tags, list):
        raise ValueError("bad_tags")
    saved = upsert_still_tags(
        data_root,
        content_id=content_id,
        tags=[str(t) for t in tags] if isinstance(tags, list) else None,
        note=None if note is None else str(note),
        write_path=write_path,
        fallback_paths=[paths["tags_fallback"]],
    )
    # Prefer SQLite editorial store (G1 JSON kept as write-through during migration).
    try:
        from vision_still_tags import connect, default_db_path, ensure_db, upsert_editorial  # type: ignore

        dbp = default_db_path(data_root=data_root)
        ensure_db(dbp)
        con = connect(dbp)
        try:
            item = upsert_editorial(
                con,
                content_id=content_id,
                tags=[str(t) for t in tags] if isinstance(tags, list) else None,
                note=None if note is None else str(note),
            )
        finally:
            con.close()
        return {
            "ok": True,
            "path": str(dbp),
            "content_id": content_id,
            "tags": list(item.get("editorial_tags") or item.get("tags") or []),
            "note": item.get("note"),
            "updated_at": item.get("updated_at"),
            "store": "still_tags.sqlite",
        }
    except Exception:
        pass
    items = saved.get("items") if isinstance(saved.get("items"), dict) else {}
    meta = items.get(content_id) if isinstance(items.get(content_id), dict) else {"tags": [], "note": None}
    return {
        "ok": True,
        "path": str(write_path),
        "content_id": content_id,
        "tags": list(meta.get("tags") or []),
        "note": meta.get("note"),
        "updated_at": saved.get("updated_at"),
        "store": "input_still_tags.json",
    }


def _shape_factory_input_curation_effective_sources_payload(cfg: ServerConfig, q: Dict[str, List[str]]) -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory import load_yaml, resolve_pool_members  # type: ignore
    from shape_factory_input_curation import merged_source_stills  # type: ignore
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    family_slug = str((q.get("family_slug") or [""])[0] or "").strip()
    if not family_slug:
        raise ValueError("missing_family_slug")
    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    shape_path = data_root / "shapes" / f"{family_slug}.shape.yaml"
    pools_path = data_root / "pools" / family_slug / "pools.yaml"
    if not shape_path.is_file():
        raise FileNotFoundError(f"shape missing: {shape_path}")
    if not pools_path.is_file():
        raise FileNotFoundError(f"pools missing: {pools_path}")

    shape = load_yaml(shape_path)
    reqs = shape.get("requires") if isinstance(shape.get("requires"), list) else []
    source_required = any(
        isinstance(req, dict) and str(req.get("slot") or "").strip() == "source_still" for req in reqs
    )
    pools_doc = load_yaml(pools_path)
    pools = pools_doc.get("pools") if isinstance(pools_doc.get("pools"), dict) else {}
    pool_def = pools.get("source_still")
    if not isinstance(pool_def, dict):
        for _name, cand in pools.items():
            if isinstance(cand, dict) and str(cand.get("slot") or "").strip() == "source_still":
                pool_def = cand
                break
    base_members = resolve_pool_members(pool_def) if isinstance(pool_def, dict) else []
    merged = merged_source_stills(
        family_slug=family_slug,
        base_members=base_members,
        data_root=data_root,
        workspace_root=cfg.workspace_root,
        output_root=cfg.output_root,
    )
    members = [str(p) for p in (merged.get("members") or [])]
    lim = 200
    for raw in q.get("limit", []):
        n = _safe_int(raw)
        if n is not None:
            lim = max(1, min(2000, int(n)))
            break
    return {
        "ok": True,
        "family_slug": family_slug,
        "source_still_required": source_required,
        "pool_count": len(base_members),
        "effective_count": len(members),
        "added_count": int(merged.get("added_count") or 0),
        "deduped_count": int(merged.get("deduped_count") or 0),
        "missing_count": int(merged.get("missing_count") or 0),
        "attached_collection_ids": merged.get("attached_collection_ids") or [],
        "items": [{"path": p, "basename": Path(p).name} for p in members[:lim]],
    }


def _shape_factory_input_curation_appetite_seeds_payload(cfg: ServerConfig, q: Dict[str, List[str]]) -> Dict[str, Any]:
    """Source stills credited by high appetite (more/fast_track, facet source|both) on family jobs."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_input_curation import list_appetite_source_seeds  # type: ignore
    from shape_factory_map import resolve_shape_factory_data_root, _load_jobs  # type: ignore

    family_slug = str((q.get("family_slug") or [""])[0] or "").strip()
    if not family_slug:
        raise ValueError("missing_family_slug")
    lim = 40
    for raw in q.get("limit", []):
        n = _safe_int(raw)
        if n is not None:
            lim = max(1, min(200, int(n)))
            break

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    jobs_root = data_root / "shape_factory" / "jobs" / family_slug
    jobs = _load_jobs(jobs_root) if jobs_root.is_dir() else []
    appetite_doc = _discovery_load_appetite_index(cfg) or {}
    return list_appetite_source_seeds(
        family_slug=family_slug,
        appetite_doc=appetite_doc if isinstance(appetite_doc, dict) else {},
        jobs=jobs,
        limit=lim,
    )


def _shape_factory_template_promotions_set_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    family_slug = str(body.get("family_slug") or "").strip()
    if not family_slug:
        raise ValueError("family_slug is required")
    intents_raw = body.get("intents")
    if isinstance(intents_raw, list):
        intents = [str(x).strip().lower() for x in intents_raw if str(x).strip()]
    else:
        single = str(body.get("intent") or "extend").strip().lower()
        intents = [single] if single else ["extend"]
    intents = [x for x in intents if x in {"extend", "vary", "derive"}]
    if not intents:
        raise ValueError("at least one valid intent is required (extend|vary|derive)")
    scope = str(body.get("scope") or body.get("mode") or "long_term").strip().lower()
    if scope in {"permanent", "longterm"}:
        scope = "long_term"
    if scope not in {"temporary", "long_term"}:
        raise ValueError("scope must be temporary or long_term")
    enabled = bool(body.get("enabled", True))
    actor = str(body.get("actor") or "operator").strip() or "operator"
    note = str(body.get("note") or "").strip()
    now = _dt.datetime.now(_dt.timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    path = _shape_factory_template_promotions_path(cfg, data_root)
    legacy_path = _shape_factory_template_promotions_legacy_path(data_root)
    reg = _shape_factory_template_promotions_load(path, fallback_paths=[legacy_path])
    entries = reg.get("entries")
    if not isinstance(entries, list):
        entries = []
    kept: List[Dict[str, Any]] = []
    for ent in entries:
        if not isinstance(ent, dict):
            continue
        same_slug = str(ent.get("family_slug") or "").strip() == family_slug
        same_scope = str(ent.get("scope") or "").strip() == scope
        same_intent = str(ent.get("intent") or "").strip() in intents
        if same_slug and same_scope and same_intent:
            continue
        kept.append(ent)
    entries = kept

    if enabled:
        starts_at = now_iso
        expires_at = None
        if scope == "temporary":
            ttl_hours = body.get("ttl_hours")
            if ttl_hours is None or ttl_hours == "":
                ttl = 6.0
            else:
                ttl = float(ttl_hours)
            ttl = max(0.25, min(ttl, 24.0 * 14.0))
            expires_at = (now + _dt.timedelta(hours=ttl)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for intent in intents:
            entries.append(
                {
                    "family_slug": family_slug,
                    "intent": intent,
                    "scope": scope,
                    "note": note or None,
                    "actor": actor,
                    "created_at": now_iso,
                    "starts_at": starts_at,
                    "expires_at": expires_at,
                }
            )

    reg["entries"] = entries
    _shape_factory_template_promotions_save(path, reg)
    return {
        "ok": True,
        "path": str(path),
        "entries": _shape_factory_template_promotions_active(entries),
        "effective": _shape_factory_template_promotions_effective(entries),
    }


def _shape_factory_work_products_payload(cfg: ServerConfig, q: Dict[str, List[str]]) -> Dict[str, Any]:
    """GET /api/shape-factory/work-products — recent jobs with construction debug details."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore
    from shape_factory_work_products import (  # type: ignore
        attach_comfy_history_failures,
        attach_live_comfy_queue,
        demote_stale_inflight_items,
        list_recent_work_products,
        reconcile_inflight_jobs_with_comfy,
    )

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    limit_raw = (q.get("limit") or ["40"])[0]
    try:
        limit = int(limit_raw)
    except ValueError:
        limit = 40
    hourly_only = str((q.get("hourly_only") or ["1"])[0]).strip().lower() not in {"0", "false", "no"}
    family = str((q.get("family") or [""])[0]).strip() or None

    # Comfy /queue is canonical for in-flight. Reconcile job.json before listing so
    # the UI never shows ghost running/queued rows after clears/restarts.
    comfy = str(cfg.comfy_server).rstrip("/")
    queue_obj: Any = None
    history_obj: Any = None
    reconcile: Dict[str, Any] | None = None
    try:
        queue_obj = _http_json("GET", f"{comfy}/queue", timeout_s=8)
    except Exception as e:
        queue_obj = {"error": "comfy_queue_fetch_failed", "detail": str(e)}
    try:
        # Same window as Queue monitor history so failures align.
        history_obj = _http_json("GET", f"{comfy}/history?max_items=80", timeout_s=30)
    except Exception as e:
        history_obj = {"error": "comfy_history_fetch_failed", "detail": str(e)}
    if isinstance(queue_obj, dict) and "error" not in queue_obj:
        try:
            reconcile = reconcile_inflight_jobs_with_comfy(
                data_root=data_root,
                comfy_server=comfy,
                queue_running=queue_obj.get("queue_running"),
                queue_pending=queue_obj.get("queue_pending"),
                persist=True,
                repo_root=_repo_root(),
                workspace_root=cfg.workspace_root,
                output_root=cfg.output_root,
                auto_retry_oom=True,
            )
        except Exception as e:
            reconcile = {"ok": False, "error": "reconcile_failed", "detail": str(e)}

    payload = list_recent_work_products(
        data_root=data_root,
        output_root=cfg.output_root,
        limit=limit,
        hourly_only=hourly_only,
        family=family,
    )
    if isinstance(queue_obj, dict) and "error" not in queue_obj:
        payload = attach_live_comfy_queue(
            payload,
            queue_running=queue_obj.get("queue_running"),
            queue_pending=queue_obj.get("queue_pending"),
            data_root=data_root,
            output_root=cfg.output_root,
        )
        payload = demote_stale_inflight_items(
            payload,
            queue_running=queue_obj.get("queue_running"),
            queue_pending=queue_obj.get("queue_pending"),
        )
    if isinstance(history_obj, dict) and "error" not in history_obj:
        try:
            payload = attach_comfy_history_failures(
                payload,
                history=history_obj,
                data_root=data_root,
                output_root=cfg.output_root,
                max_failures=max(20, min(80, int(limit))),
            )
        except Exception as e:
            payload["history_attach_error"] = str(e)
    elif isinstance(history_obj, dict) and history_obj.get("error"):
        payload["history_attach_error"] = history_obj.get("detail") or history_obj.get("error")
    if reconcile is not None:
        payload["comfy_reconcile"] = reconcile
    # Re-attach after live/history rows so synthetic items also get markers when resolvable.
    try:
        from shape_factory_markers import attach_markers_to_work_products  # type: ignore

        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        attach_markers_to_work_products(items, output_root=cfg.output_root)
    except Exception:
        pass
    return payload


def _shape_factory_markers_payload(cfg: ServerConfig, q: Dict[str, List[str]]) -> Dict[str, Any]:
    """GET /api/shape-factory/markers?content_id=… | ?key=&value="""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_markers import (  # type: ignore
        connect,
        list_for,
        markers_path_for_output_root,
        query_by_key,
    )

    db = markers_path_for_output_root(cfg.output_root)
    content_id = str((q.get("content_id") or [""])[0]).strip()
    key = str((q.get("key") or [""])[0]).strip()
    value = str((q.get("value") or [""])[0]).strip() or None
    if content_id:
        if not db.is_file():
            return {"ok": True, "content_id": content_id, "markers": {}, "rows": []}
        con = connect(db)
        try:
            rows = list_for(con, content_id)
            return {
                "ok": True,
                "content_id": content_id,
                "markers": {k: v["value"] for k, v in rows.items()},
                "rows": list(rows.values()),
            }
        finally:
            con.close()
    if not key:
        raise ValueError("content_id or key required")
    if not db.is_file():
        return {"ok": True, "key": key, "value": value, "count": 0, "rows": []}
    con = connect(db)
    try:
        rows = query_by_key(con, key, value=value)
        return {"ok": True, "key": key, "value": value, "count": len(rows), "rows": rows}
    finally:
        con.close()


def _shape_factory_markers_set_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/shape-factory/markers — set one marker (default source=human)."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_markers import connect, markers_path_for_output_root, set_marker  # type: ignore

    content_id = str(body.get("content_id") or "").strip()
    key = str(body.get("key") or "").strip()
    value = body.get("value")
    source = str(body.get("source") or "human").strip() or "human"
    force = bool(body.get("force"))
    if not content_id or not key:
        raise ValueError("content_id and key required")
    db = markers_path_for_output_root(cfg.output_root)
    con = connect(db)
    try:
        saved = set_marker(con, content_id, key, value, source=source, force=force)
    finally:
        con.close()
    return {"ok": not saved.get("blocked"), "saved": saved}


def _shape_factory_quarantine_path() -> Path:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory import resolve_quarantine_registry_path  # type: ignore
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    return resolve_quarantine_registry_path(data_root=data_root, for_write=False)


def _shape_factory_quarantine_list_payload(q: Dict[str, List[str]]) -> Dict[str, Any]:
    """GET /api/shape-factory/quarantine — list registry entries."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory import (  # type: ignore
        list_quarantine_entries,
        load_effective_quarantine_registry,
    )
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    status = str((q.get("status") or ["quarantined"])[0] or "quarantined").strip().lower() or "quarantined"
    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    registry, path = load_effective_quarantine_registry(data_root=data_root)
    entries = list_quarantine_entries(registry, status=status)
    return {
        "ok": True,
        "status_filter": status,
        "quarantine_path": str(path),
        "count": len(entries),
        "entries": entries,
    }


def _shape_factory_quarantine_release_payload(body: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/shape-factory/quarantine/release — sticky human release."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory import (  # type: ignore
        ensure_writable_quarantine_registry,
        release_quarantine_entry,
        save_quarantine_registry,
    )
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    workflow_path = str(body.get("workflow_path") or "").strip() or None
    workflow_name = str(body.get("workflow_name") or "").strip() or None
    note = str(body.get("note") or "").strip()
    if not workflow_path and not workflow_name:
        raise ValueError("workflow_path or workflow_name is required")
    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    registry, path = ensure_writable_quarantine_registry(data_root=data_root)
    entry = release_quarantine_entry(
        registry,
        workflow_path=workflow_path,
        workflow_name=workflow_name,
        note=note,
    )
    save_quarantine_registry(path, registry)
    return {"ok": True, "entry": entry, "quarantine_path": str(path)}


def _quarantine_runtime_error_payload(exc: BaseException) -> Optional[Dict[str, Any]]:
    """Detect quarantine gate failures and return a structured API error body."""
    msg = str(exc)
    low = msg.lower()
    if "quarantined" not in low:
        return None
    return {
        "ok": False,
        "error": "workflow_quarantined",
        "detail": msg,
    }


def _shape_factory_submit_attempts_dir(cfg: ServerConfig) -> Path:
    """Writable status dir (same parent as Comfy queue ledger)."""
    return Path(cfg.queue_ledger_events_path).expanduser().resolve().parent


def _shape_factory_submit_attempts_payload(cfg: ServerConfig, q: Dict[str, List[str]]) -> Dict[str, Any]:
    """GET /api/shape-factory/submit-attempts — recent Submit/queue outcomes."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_submit_attempts import list_attempts_payload  # type: ignore

    limit = 80
    for v in q.get("limit", []):
        li = _safe_int(v)
        if li is not None:
            limit = max(1, min(500, int(li)))
            break
    errors_only = str((q.get("errors_only") or ["0"])[0] or "0").strip().lower() in {"1", "true", "yes"}
    family = str((q.get("family") or q.get("family_slug") or [""])[0] or "").strip() or None
    return list_attempts_payload(
        _shape_factory_submit_attempts_dir(cfg),
        limit=limit,
        errors_only=errors_only,
        family_slug=family,
    )


def _record_shape_factory_queue_attempt(
    cfg: ServerConfig,
    *,
    body: Dict[str, Any],
    ok: bool,
    exc: Optional[BaseException] = None,
    payload: Optional[Dict[str, Any]] = None,
    http_status: Optional[int] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_submit_attempts import record_queue_outcome  # type: ignore

    return record_queue_outcome(
        _shape_factory_submit_attempts_dir(cfg),
        body=body,
        ok=ok,
        exc=exc,
        payload=payload,
        http_status=http_status,
    )


def _vision_slice_captions_payload(cfg: ServerConfig) -> Dict[str, Any]:
    """GET /api/vision/slice-captions — V1 time-slice caption review (grouped by asset)."""
    import importlib

    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    import vision_slice_review as vsr  # type: ignore

    # Workspace scripts are bind-mounted; reload so excerpt UI fields pick up edits
    # without restarting the long-lived Experiments API process.
    vsr = importlib.reload(vsr)
    status_dir = _output_status_dir(cfg.output_root)
    return vsr.list_vision_slice_review(status_dir=status_dir)


def _vision_tag_judgment_get_payload(cfg: ServerConfig) -> Dict[str, Any]:
    """GET /api/vision/tag-judgment — blind tag judgment queue + progress + leaderboard."""
    import importlib
    import traceback

    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    try:
        import vision_tag_judgment_api as vtj  # type: ignore

        vtj = importlib.reload(vtj)
    except Exception as e:
        return {
            "ok": False,
            "error": "vision_tag_judgment_import_failed",
            "detail": f"{e}\n{_workspace_scripts_dir()}\n{traceback.format_exc()}",
        }
    status_dir = _output_status_dir(cfg.output_root)
    return vtj.get_tag_judgment_payload(status_dir)


def _vision_tag_judgment_post_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/vision/tag-judgment — upsert one judgment row (idempotent by sample_id)."""
    import importlib

    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    import vision_tag_judgment_api as vtj  # type: ignore

    vtj = importlib.reload(vtj)
    status_dir = _output_status_dir(cfg.output_root)
    return vtj.save_tag_judgment(status_dir, body if isinstance(body, dict) else {})


def _shape_factory_json_peek_payload(cfg: ServerConfig, q: Dict[str, List[str]]) -> Dict[str, Any]:
    """GET /api/shape-factory/json-peek?path=... — tooltip viewer for construction JSON files."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore
    from shape_factory_work_products import peek_json_file  # type: ignore

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    path = str((q.get("path") or [""])[0] or "").strip()
    return peek_json_file(
        path,

        data_root=data_root,
        output_root=cfg.output_root,
        workspace_root=cfg.workspace_root,
    )


def _workspace_scripts_dir() -> Path:
    """
    Directory that contains ``comfy_meta_lib.py`` and ``snowflake_inventory.py``.

    - Host: this file is ``<repo>/scripts/experiments_ui_server.py`` → libs in ``<repo>/workspace/scripts``.
    - Docker: ``./scripts`` is mounted at ``/workspace/scripts`` (this file only), while
      ``./workspace/scripts`` is mounted at ``/workspace/ws_scripts`` — so we must not use
      ``.../workspace/workspace/scripts``.
    """
    here = Path(__file__).resolve().parent
    candidates: List[Path] = [
        here.parent / "workspace" / "scripts",
        here.parent / "ws_scripts",
        Path("/workspace/ws_scripts"),
    ]
    for d in candidates:
        try:
            if (d / "comfy_meta_lib.py").is_file():
                return d
        except Exception:
            continue
    return candidates[0]


_comfy_meta_mod: Any = None
_snowflake_inventory_mod: Any = None


def _import_comfy_meta_lib() -> Any:
    global _comfy_meta_mod
    if _comfy_meta_mod is not None:
        return _comfy_meta_mod
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    import comfy_meta_lib as m  # type: ignore

    _comfy_meta_mod = m
    return m


def _import_snowflake_inventory() -> Any:
    global _snowflake_inventory_mod
    if _snowflake_inventory_mod is not None:
        return _snowflake_inventory_mod
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    import snowflake_inventory as s  # type: ignore

    _snowflake_inventory_mod = s
    return s


def _coerce_comfy_link_pairs(val: Any, *, depth: int = 0) -> List[Tuple[str, int]]:
    """Best-effort: Comfy API ``inputs`` values like [\"4\", 1] or nested lists of those."""
    if depth > 14:
        return []
    if isinstance(val, list) and len(val) == 2:
        a, b = val[0], val[1]
        if isinstance(b, int) and isinstance(a, (int, str)):
            return [(str(a), int(b))]
        return []
    if isinstance(val, list):
        out: List[Tuple[str, int]] = []
        for it in val:
            out.extend(_coerce_comfy_link_pairs(it, depth=depth + 1))
        return out
    return []


def _api_prompt_graph_facets(prompt: Dict[str, Any]) -> Dict[str, Any]:
    """
    Derive a topology-oriented signature from a Comfy API ``prompt`` dict (node_id -> body).
    This intentionally ignores literal seeds/strings on edges; it focuses on class_type wiring.
    """
    id_to_type: Dict[str, str] = {}
    for nid, node in prompt.items():
        if not isinstance(node, dict):
            continue
        ct = str(node.get("class_type") or "").strip() or "?"
        id_to_type[str(nid)] = ct

    edges: List[Tuple[str, str, str]] = []
    for dst_id, node in prompt.items():
        if not isinstance(node, dict):
            continue
        dst = str(dst_id)
        dst_type = id_to_type.get(dst, "?")
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for iname, val in inputs.items():
            in_key = str(iname)
            for src_id, _slot in _coerce_comfy_link_pairs(val):
                src_type = id_to_type.get(str(src_id), "?")
                edges.append((src_type, in_key, dst_type))

    node_mix = sorted(collections.Counter(id_to_type.values()).items())
    edge_shape = sorted((a, c) for (a, _b, c) in edges)  # (src_type, dst_type) multiset
    payload = {"node_mix": node_mix, "edge_shape": sorted(collections.Counter(edge_shape).items())}
    m = _import_comfy_meta_lib()
    h = m.stable_json_sha256(payload)
    return {
        "api_graph_shape_hash": h,
        "node_count": len(id_to_type),
        "edge_link_count": len(edges),
        "node_mix": node_mix,
    }


def _collect_path_like_strings(val: Any, out: set, *, depth: int = 0) -> None:
    if depth > 18:
        return
    if isinstance(val, str):
        s = val.strip().replace("\\", "/")
        if not s or len(s) > 4096:
            return
        low = s.lower()
        if "/" in s or s.endswith((".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".mov", ".mkv")):
            out.add(s)
        return
    if isinstance(val, dict):
        for vv in val.values():
            _collect_path_like_strings(vv, out, depth=depth + 1)
        return
    if isinstance(val, list):
        for vv in val:
            _collect_path_like_strings(vv, out, depth=depth + 1)


_DISCOVERY_MEDIA_LOADER_TYPES = frozenset(
    {
        "LoadImage",
        "LoadImageWithFilename|pysssss",
        "LoadVideo",
        "VHS_LoadVideo",
        "VHS_LoadVideoPath",
        "VHS_LoadVideoFFmpeg",
        "VHS_LoadVideoFFmpegPath",
    }
)
_DISCOVERY_OUTPUT_SINK_TYPES = frozenset(
    {
        "VHS_VideoCombine",
        "SaveVideo",
        "SaveImage",
        "SaveAnimatedWEBP",
        "SaveAnimatedPNG",
    }
)


def _discovery_loader_media_string(inputs: Dict[str, Any]) -> Optional[str]:
    """Scalar media path/filename from a LoadImage / LoadVideo-style inputs dict."""
    for key in ("image", "video", "path", "file", "url"):
        val = inputs.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().replace("\\", "/")
        if isinstance(val, list) and val:
            last = val[-1]
            if isinstance(last, str) and last.strip():
                return last.strip().replace("\\", "/")
    return None


def _discovery_node_is_saved_output_sink(node: Dict[str, Any]) -> bool:
    ct = str(node.get("class_type") or "")
    if ct not in _DISCOVERY_OUTPUT_SINK_TYPES:
        return False
    inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
    # Preview combines usually set save_output=False; require an explicit save when present.
    if "save_output" in inputs:
        return bool(inputs.get("save_output"))
    return True


def _discovery_node_is_any_output_sink(node: Dict[str, Any]) -> bool:
    return str(node.get("class_type") or "") in _DISCOVERY_OUTPUT_SINK_TYPES


def _api_prompt_output_feeding_loader_paths(prompt: Dict[str, Any]) -> Dict[str, Any]:
    """
    Media paths from LoadImage / LoadVideo* nodes that can reach a *saved* output sink.

    Orphan loaders (no outbound links) and loaders that only feed Preview / dead subgraphs
    are omitted. Multiple loaders that all feed the saved output are kept (multi-input graphs).
    """
    if not isinstance(prompt, dict) or not prompt:
        return {
            "output_feeding_loader_paths_ok": False,
            "output_feeding_loader_paths": [],
            "output_feeding_loader_count": 0,
            "saved_output_sink_count": 0,
        }

    nodes: Dict[str, Dict[str, Any]] = {}
    consumers: Dict[str, List[str]] = {}
    for nid, node in prompt.items():
        if not isinstance(node, dict):
            continue
        sid = str(nid)
        nodes[sid] = node
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        for _iname, ival in inputs.items():
            for src_id, _slot in _coerce_comfy_link_pairs(ival):
                consumers.setdefault(str(src_id), []).append(sid)

    saved_sinks = {nid for nid, node in nodes.items() if _discovery_node_is_saved_output_sink(node)}
    sink_set = saved_sinks
    used_preview_fallback = False
    if not sink_set:
        sink_set = {nid for nid, node in nodes.items() if _discovery_node_is_any_output_sink(node)}
        used_preview_fallback = bool(sink_set)

    def _reaches_sink(start: str) -> bool:
        seen: set = set()
        q: collections.deque = collections.deque([start])
        while q:
            cur = q.popleft()
            if cur in seen:
                continue
            seen.add(cur)
            if cur in sink_set and cur != start:
                return True
            for dst in consumers.get(cur) or []:
                if dst not in seen:
                    q.append(dst)
        return False

    paths: List[str] = []
    seen_paths: set = set()
    for nid, node in nodes.items():
        if str(node.get("class_type") or "") not in _DISCOVERY_MEDIA_LOADER_TYPES:
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        media = _discovery_loader_media_string(inputs)
        if not media:
            continue
        if not sink_set or not _reaches_sink(nid):
            continue
        if media in seen_paths:
            continue
        seen_paths.add(media)
        paths.append(media)

    return {
        "output_feeding_loader_paths_ok": True,
        "output_feeding_loader_paths": paths,
        "output_feeding_loader_count": len(paths),
        "saved_output_sink_count": len(saved_sinks),
        "output_feeding_used_preview_fallback": used_preview_fallback,
    }


def _api_prompt_source_asset_fingerprint(prompt: Dict[str, Any]) -> Dict[str, Any]:
    paths: set = set()
    for _nid, node in prompt.items():
        if not isinstance(node, dict):
            continue
        ins = node.get("inputs")
        if isinstance(ins, dict):
            _collect_path_like_strings(ins, paths)
    ps = sorted(paths)
    if len(ps) > 200:
        ps = ps[:200]
    m = _import_comfy_meta_lib()
    h = m.stable_json_sha256({"paths": ps})
    out: Dict[str, Any] = {
        "source_path_like_count": len(paths),
        "source_paths_sample": ps[:40],
        "source_paths_fingerprint": h,
    }
    out.update(_api_prompt_output_feeding_loader_paths(prompt))
    return out


def _api_prompt_lora_fingerprint(prompt: Dict[str, Any]) -> Dict[str, Any]:
    """
    Cheap API-prompt LoRA fingerprint: hash enabled-ish LoRA entries found in known widget shapes.
    (Snowflake's richer extraction is litegraph-first; this is a pragmatic API-prompt fallback.)
    """
    rows: List[Dict[str, Any]] = []
    for _nid, node in prompt.items():
        if not isinstance(node, dict):
            continue
        ct = str(node.get("class_type") or "")
        if "lora" not in ct.lower():
            continue
        ins = node.get("inputs")
        if not isinstance(ins, dict):
            continue
        try:
            blob = json.dumps(ins, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        except Exception:
            blob = str(ins)
        rows.append({"class_type": ct, "inputs_digest": hashlib.sha256(blob.encode("utf-8", errors="replace")).hexdigest()[:16]})
    rows.sort(key=lambda r: (str(r.get("class_type")), str(r.get("inputs_digest"))))
    m = _import_comfy_meta_lib()
    h = m.stable_json_sha256(rows) if rows else None
    return {"lora_related_node_count": len(rows), "lora_stack_fingerprint": h}


def _probe_png_workflow_chunks(cfg: "ServerConfig", rel_png: str) -> Dict[str, Any]:
    rel = _normalize_rel_posix(rel_png)
    out: Dict[str, Any] = {"relpath": rel, "ok": False}
    if not rel:
        out["error"] = "bad_relpath"
        return out
    abs_p = _safe_join(cfg.output_root, rel)
    if abs_p is None or not abs_p.is_file():
        out["error"] = "file_not_found"
        return out
    try:
        chunks = _read_png_text_chunks(abs_p)
    except Exception as e:
        out["error"] = "png_read_failed"
        out["detail"] = str(e)
        return out

    keys = sorted(chunks.keys())
    out["ok"] = True
    out["png_text_chunk_keys"] = keys
    out["png_text_chunk_key_count"] = len(keys)

    meta = _import_comfy_meta_lib()
    pr_obj, wf_obj = meta.extract_prompt_workflow_from_png_chunks(chunks)

    out["prompt_chunk"] = {
        "present": isinstance(chunks.get("prompt"), str) and bool(str(chunks.get("prompt") or "").strip()),
        "raw_chars": len(str(chunks.get("prompt") or "")),
        "parsed_ok": isinstance(pr_obj, dict) and bool(pr_obj),
        "shape": "api_prompt" if isinstance(pr_obj, dict) and _looks_like_comfy_api_prompt(pr_obj) else "unknown",
    }
    out["workflow_chunk"] = {
        "present": isinstance(chunks.get("workflow"), str) and bool(str(chunks.get("workflow") or "").strip()),
        "raw_chars": len(str(chunks.get("workflow") or "")),
        "parsed_ok": isinstance(wf_obj, dict) and bool(wf_obj),
        "shape": "litegraph" if isinstance(wf_obj, dict) and _looks_like_comfy_ui_workflow(wf_obj) else "unknown",
    }

    facets: Dict[str, Any] = {}
    if isinstance(pr_obj, dict) and _looks_like_comfy_api_prompt(pr_obj):
        try:
            facets["api_prompt"] = {
                "graph": _api_prompt_graph_facets(pr_obj),
                "sources": _api_prompt_source_asset_fingerprint(pr_obj),
                "loras": _api_prompt_lora_fingerprint(pr_obj),
            }
        except Exception as e:
            facets["api_prompt"] = {"error": str(e)}

    if isinstance(wf_obj, dict) and _looks_like_comfy_ui_workflow(wf_obj):
        try:
            si = _import_snowflake_inventory()
            summary = si.summarize_workflow(wf_obj)
            facets["litegraph_workflow"] = {
                "graph_hash": si.graph_fingerprint(wf_obj),
                "recipe_hash": si.recipe_fingerprint(summary),
                "node_count": summary.get("node_count"),
                "link_count": summary.get("link_count"),
                "flags": summary.get("flags"),
            }
        except Exception as e:
            facets["litegraph_workflow"] = {"error": str(e)}

    out["facets"] = facets
    return out


def _probe_mp4_container(cfg: "ServerConfig", rel_video: str) -> Dict[str, Any]:
    rel = _normalize_rel_posix(rel_video)
    out: Dict[str, Any] = {"relpath": rel, "ok": False}
    if not rel:
        out["error"] = "bad_relpath"
        return out
    abs_p = _safe_join(cfg.output_root, rel)
    if abs_p is None or not abs_p.is_file():
        out["error"] = "file_not_found"
        return out
    out["ok"] = True
    try:
        st = abs_p.stat()
        out["size_bytes"] = int(st.st_size)
    except Exception:
        out["size_bytes"] = None

    tags_summary: Dict[str, Any] = {"ok": False}
    try:
        meta = _import_comfy_meta_lib()
        tags = meta.ffprobe_format_tags(abs_p)
        tags_summary["ok"] = True
        tags_summary["tag_keys"] = sorted(tags.keys()) if isinstance(tags, dict) else []
        if isinstance(tags, dict):
            pr2, wf2 = meta.extract_prompt_workflow_from_tags(tags)
            tags_summary["extracted_prompt_shape"] = (
                "api_prompt" if isinstance(pr2, dict) and _looks_like_comfy_api_prompt(pr2) else "none_or_unknown"
            )
            tags_summary["extracted_workflow_shape"] = (
                "litegraph" if isinstance(wf2, dict) and _looks_like_comfy_ui_workflow(wf2) else "none_or_unknown"
            )
    except Exception as e:
        tags_summary["error"] = str(e)
    out["ffprobe"] = tags_summary
    return out


def _discovery_build_workflow_facets_payload(cfg: "ServerConfig", relpath: str) -> Dict[str, Any]:
    rel = _normalize_rel_posix(relpath.strip())
    if not rel:
        return {"ok": False, "error": "missing_or_bad_relpath"}

    idx_path = cfg.discovery_index_path
    idx = _load_discovery_index_disk(idx_path) if idx_path.exists() else None
    if not isinstance(idx, dict):
        return {"ok": False, "error": "discovery_index_missing", "detail": str(idx_path)}

    item = _discovery_item_for_relpath(idx, rel)
    if not isinstance(item, dict):
        return {"ok": False, "error": "not_in_discovery_index", "detail": "relpath not found in cached discovery items"}

    members = item.get("members") if isinstance(item.get("members"), list) else []
    video_rel = item.get("video_relpath") if isinstance(item.get("video_relpath"), str) else None
    if not video_rel:
        # fall back: primary row might be video
        pr = item.get("relpath")
        if isinstance(pr, str) and pr.lower().endswith((".mp4", ".webm")):
            video_rel = pr

    png_members: List[str] = []
    if isinstance(members, list):
        for mm in members:
            if not isinstance(mm, dict):
                continue
            rv = mm.get("relpath")
            if isinstance(rv, str) and rv.lower().endswith(".png"):
                png_members.append(_normalize_rel_posix(rv))

    thumb = item.get("thumb_relpath") if isinstance(item.get("thumb_relpath"), str) else None
    if thumb and thumb.lower().endswith(".png"):
        t2 = _normalize_rel_posix(thumb)
        if t2 and t2 not in png_members:
            png_members.insert(0, t2)

    png_probes: List[Dict[str, Any]] = []
    seen_png: set = set()
    for p in png_members:
        pn = _normalize_rel_posix(p)
        if not pn or pn in seen_png:
            continue
        seen_png.add(pn)
        png_probes.append(_probe_png_workflow_chunks(cfg, pn))

    mp4_probe = _probe_mp4_container(cfg, video_rel) if isinstance(video_rel, str) and video_rel else {"ok": False, "error": "no_video_relpath"}

    provenance: Dict[str, Any] = {
        "kind": "discovery_library_merge",
        "index_version": int(idx.get("version") or 0),
        "group_id": item.get("group_id"),
        "library": item.get("library"),
        "primary_workproduct": {"role": "video", "relpath": video_rel},
        "metadata_carriers": [{"role": "png_sidecar_or_thumb", "relpaths": sorted(seen_png)}],
        "indexed": {
            "workflow_fingerprint_exact": item.get("workflow_fingerprint"),
            "has_embedded_prompt": bool(item.get("has_embedded_prompt")),
            "class_types_preview": item.get("class_types_preview"),
        },
        "notes": [
            "Discovery indexing reads PNG text chunks for workflow metadata; MP4 scan rows do not embed workflow JSON in the index builder.",
            "MP4 ffprobe tags are optional: some muxers embed prompt/workflow; many do not.",
        ],
    }

    payload: Dict[str, Any] = {
        "ok": True,
        "query_relpath": rel,
        "discovery_index_path": str(idx_path),
        "item": {
            "group_id": item.get("group_id"),
            "name": item.get("name"),
            "library": item.get("library"),
            "relpath": item.get("relpath"),
            "video_relpath": item.get("video_relpath"),
            "thumb_relpath": item.get("thumb_relpath"),
            "members": members,
        },
        "mp4": mp4_probe,
        "png_workflow_probes": png_probes,
        "provenance": provenance,
    }
    ratings_doc = _discovery_load_ratings_index(cfg)
    ratings_path = _discovery_ratings_index_path(cfg)
    if ratings_doc:
        payload["ratings_index_path"] = str(ratings_path)
        payload["item"] = _discovery_enrich_item_ratings(payload["item"], cfg, ratings_doc=ratings_doc)
        graph_hash: Optional[str] = None
        for probe in png_probes:
            if not isinstance(probe, dict):
                continue
            facets = probe.get("facets")
            if not isinstance(facets, dict):
                continue
            lg = facets.get("litegraph_workflow")
            if isinstance(lg, dict) and lg.get("graph_hash"):
                graph_hash = str(lg["graph_hash"])
                break
        if graph_hash:
            gh_row = (ratings_doc.get("by_graph_hash") or {}).get(graph_hash)
            if isinstance(gh_row, dict):
                payload["workflow_ratings"] = {
                    "graph_hash": graph_hash,
                    "rating_inferred": gh_row.get("inferred"),
                    "rating_evidence": {
                        "n": gh_row.get("n"),
                        "keepers_4plus": gh_row.get("keepers_4plus"),
                    },
                    "catalog_slug": gh_row.get("catalog_slug"),
                }
                if gh_row.get("inferred") is not None:
                    payload["workflow_ratings"]["rating_effective"] = gh_row.get("inferred")
    return payload


def _discovery_lineage_edges_path(cfg: "ServerConfig") -> Path:
    return cfg.discovery_index_path.with_name("discovery_lineage_edges.json")


def _discovery_ratings_index_path(cfg: "ServerConfig") -> Path:
    return cfg.discovery_index_path.with_name("ratings_index.json")


def _discovery_appetite_index_path(cfg: "ServerConfig") -> Path:
    return cfg.discovery_index_path.with_name("appetite_index.json")


def _discovery_load_appetite_index(cfg: "ServerConfig") -> Optional[Dict[str, Any]]:
    path = _discovery_appetite_index_path(cfg)
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    try:
        from shape_factory_ratings import load_appetite_doc, ratings_db_path_for_index  # type: ignore
    except Exception:
        return None
    db_path = ratings_db_path_for_index(path)
    if not path.is_file() and not db_path.is_file():
        return None
    try:
        mtime_src = db_path if db_path.is_file() else path
        mtime = mtime_src.stat().st_mtime
    except OSError:
        return None
    key = str(db_path if db_path.is_file() else path)
    cached = _APPETITE_INDEX_CACHE.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        doc = load_appetite_doc(path)
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None
    _APPETITE_INDEX_CACHE[key] = (mtime, doc)
    return doc


def _discovery_disposition_index_path(cfg: "ServerConfig") -> Path:
    return cfg.discovery_index_path.with_name("disposition_index.json")


def _discovery_work_items_index_path(cfg: "ServerConfig") -> Path:
    return cfg.discovery_index_path.with_name("work_items_index.json")


def _discovery_load_work_items_index(cfg: "ServerConfig") -> Optional[Dict[str, Any]]:
    path = _discovery_work_items_index_path(cfg)
    if not path.is_file():
        return None
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_work_items import load_work_items_doc  # type: ignore

    return load_work_items_doc(path)


def _discovery_triage_index_path(cfg: "ServerConfig") -> Path:
    return cfg.discovery_index_path.with_name("triage_index.json")


def _discovery_load_triage_index(cfg: "ServerConfig") -> Optional[Dict[str, Any]]:
    path = _discovery_triage_index_path(cfg)
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _discovery_disposition_catalog_path(cfg: "ServerConfig") -> Path:
    return cfg.discovery_index_path.with_name("disposition_catalog.json")


def _discovery_load_disposition_index(cfg: "ServerConfig") -> Optional[Dict[str, Any]]:
    path = _discovery_disposition_index_path(cfg)
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _discovery_load_disposition_catalog(cfg: "ServerConfig") -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_disposition import load_merged_catalog  # type: ignore

    og_root = _prefer_flat_library_dir(cfg.output_root, "og")
    return load_merged_catalog(og_root=og_root, repo_root=_repo_root())


def _disposition_hook_runner(cfg: "ServerConfig", rel: str, body: Dict[str, Any]) -> Callable[[str, Dict[str, Any]], Dict[str, Any]]:
    def _run(hook: str, extra: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(body)
        merged.update(extra or {})
        if hook == "derive":
            explicit_family = str(merged.get("family_slug") or merged.get("family") or "").strip()
            job_key, family = _resolve_replay_job_from_relpath(cfg, rel, merged)
            if not job_key:
                return {"ok": False, "reason": "no_derive_context"}
            derive_body: Dict[str, Any] = {"job_key": job_key}
            target = explicit_family or family
            if target:
                derive_body["family_slug"] = target
            if merged.get("front"):
                derive_body["front"] = True
            facet = str(merged.get("facet") or "").strip()
            if facet:
                derive_body["facet"] = facet
            overrides = merged.get("overrides")
            if isinstance(overrides, dict) and overrides:
                derive_body["overrides"] = overrides
            try:
                return _shape_factory_derive_payload(cfg, derive_body)
            except ValueError as e:
                return {"ok": False, "reason": str(e)}
        if hook == "extend":
            return _fast_track_extend(cfg, rel, merged)
        replay_body: Dict[str, Any] = {"extend": bool(merged.get("extend"))}
        explicit_family = str(merged.get("family_slug") or merged.get("family") or "").strip()
        job_key, family = _resolve_replay_job_from_relpath(cfg, rel, merged)
        if job_key:
            replay_body["job_key"] = job_key
        # Prefer an explicit target family from the request over the source job's family.
        target = explicit_family or family
        if target:
            replay_body["family_slug"] = target
        if merged.get("front"):
            replay_body["front"] = True
        overrides = merged.get("overrides")
        if isinstance(overrides, dict) and overrides:
            replay_body["overrides"] = overrides
        for alias in ("identity_anchor", "source_still", "identity_still"):
            if alias in merged and merged.get(alias) not in (None, ""):
                replay_body[alias] = merged.get(alias)
        if not replay_body.get("job_key"):
            fresh_body = dict(merged)
            if target:
                fresh_body["family_slug"] = target
            return _queue_fresh_from_source_media(cfg, rel, fresh_body)
        try:
            return _shape_factory_replay_payload(cfg, replay_body)
        except ValueError as e:
            if hook == "extend" or merged.get("extend"):
                # Still-source only — never demote a failed lengthen into a silent replay.
                if "extend_not_supported" in str(e).lower():
                    replay_body["extend"] = False
                    out = _shape_factory_replay_payload(cfg, replay_body)
                    out["extend_fallback"] = "replay"
                    out["extend_fallback_reason"] = str(e)
                    return out
            return {"ok": False, "reason": str(e)}

    return _run


def _record_asset_triage_complete_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    rel = str(body.get("relpath") or "").strip()
    if not rel:
        raise ValueError("missing relpath")
    media_abs = _safe_join(cfg.output_root, rel)
    if media_abs is None or not media_abs.is_file():
        raise FileNotFoundError(rel)
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_triage import record_triage_pass  # type: ignore

    og_root = _prefer_flat_library_dir(cfg.output_root, "og")
    disposition_doc = _discovery_load_disposition_index(cfg)
    return record_triage_pass(
        media_abs=media_abs,
        media_relpath=rel,
        og_root=og_root,
        triage_index_path=_discovery_triage_index_path(cfg),
        disposition_doc=disposition_doc,
    )


def _record_batch_triage_complete_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    raw = body.get("relpaths") or body.get("relpath") or []
    if isinstance(raw, str):
        relpaths = [raw]
    elif isinstance(raw, list):
        relpaths = [str(x).strip() for x in raw if str(x).strip()]
    else:
        relpaths = []
    if not relpaths:
        raise ValueError("missing relpaths")
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_rating_sampler import is_rating_complete  # type: ignore
    from shape_factory_ratings import (  # type: ignore
        default_appetite_index_path,
        default_ratings_index_path,
    )
    from shape_factory_triage import record_triage_pass  # type: ignore

    og_root = _prefer_flat_library_dir(cfg.output_root, "og")
    ratings_path = default_ratings_index_path(og_root)
    appetite_path = default_appetite_index_path(og_root)
    ratings_doc: Dict[str, Any] = {}
    appetite_doc: Dict[str, Any] = {}
    try:
        if ratings_path.is_file():
            ratings_doc = json.loads(ratings_path.read_text(encoding="utf-8")) or {}
    except Exception:
        ratings_doc = {}
    try:
        if appetite_path.is_file():
            appetite_doc = json.loads(appetite_path.read_text(encoding="utf-8")) or {}
    except Exception:
        appetite_doc = {}

    disposition_doc = _discovery_load_disposition_index(cfg)
    committed: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for rel in relpaths:
        item = {"relpath": rel}
        if not is_rating_complete(
            item,
            ratings_doc=ratings_doc if isinstance(ratings_doc, dict) else {},
            appetite_doc=appetite_doc if isinstance(appetite_doc, dict) else {},
            og_root=og_root,
        ):
            skipped.append(rel)
            continue
        media_abs = _safe_join(cfg.output_root, rel)
        if media_abs is None or not media_abs.is_file():
            skipped.append(rel)
            continue
        committed.append(
            record_triage_pass(
                media_abs=media_abs,
                media_relpath=rel,
                og_root=og_root,
                triage_index_path=_discovery_triage_index_path(cfg),
                disposition_doc=disposition_doc,
            )
        )
    return {
        "ok": True,
        "committed": committed,
        "skipped": skipped,
        "committed_count": len(committed),
        "skipped_count": len(skipped),
    }


def _set_asset_disposition_toggle_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    rel = str(body.get("relpath") or "").strip()
    if not rel:
        raise ValueError("missing relpath")
    marker = str(body.get("marker") or body.get("marker_id") or "").strip()
    if not marker:
        raise ValueError("missing marker")
    on = body.get("on")
    if on is None:
        on = body.get("enabled", True)
    on = bool(on)
    media_abs = _safe_join(cfg.output_root, rel)
    if media_abs is None or not media_abs.is_file():
        raise FileNotFoundError(rel)
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_disposition import toggle_output_disposition  # type: ignore

    og_root = _prefer_flat_library_dir(cfg.output_root, "og")
    catalog = _discovery_load_disposition_catalog(cfg)
    saved = toggle_output_disposition(
        media_abs=media_abs,
        media_relpath=rel,
        marker_id=marker,
        on=on,
        note=str(body.get("note") or "").strip() or None,
        modifiers=body.get("modifiers") if isinstance(body.get("modifiers"), list) else None,
        og_root=og_root,
        disposition_index_path=_discovery_disposition_index_path(cfg),
        catalog=catalog,
    )
    return saved


def _run_asset_disposition_step_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    rel = str(body.get("relpath") or "").strip()
    step_id = str(body.get("step_id") or body.get("step") or "").strip()
    if not rel:
        raise ValueError("missing relpath")
    if not step_id:
        raise ValueError("missing step_id")
    media_abs = _safe_join(cfg.output_root, rel)
    if media_abs is None or not media_abs.is_file():
        raise FileNotFoundError(rel)
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_disposition import run_disposition_step  # type: ignore
    from shape_factory_work_items import record_run_step_work_item  # type: ignore

    og_root = _prefer_flat_library_dir(cfg.output_root, "og")
    catalog = _discovery_load_disposition_catalog(cfg)
    extra = {
        k: body[k]
        for k in (
            "job_key",
            "family_slug",
            "family",
            "facet",
            "front",
            "identity_anchor",
            "source_still",
            "identity_still",
            "overrides",
        )
        if k in body
    }
    if "front" in extra:
        extra["front"] = bool(extra.get("front"))
    payload = run_disposition_step(
        step_id=step_id,
        media_abs=media_abs,
        media_relpath=rel,
        og_root=og_root,
        disposition_index_path=_discovery_disposition_index_path(cfg),
        catalog=catalog,
        hook_runner=_disposition_hook_runner(cfg, rel, extra),
        extra=extra,
    )
    hook = str(payload.get("hook") or "")
    hook_result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    try:
        priority_override = None
        if "front" in extra:
            priority_override = "front" if extra.get("front") else "normal"
        work = record_run_step_work_item(
            source_relpath=rel,
            step_id=step_id,
            hook=hook,
            hook_result=hook_result,
            work_items_index_path=_discovery_work_items_index_path(cfg),
            factory_family=str(extra.get("family_slug") or extra.get("family") or hook_result.get("family_slug") or ""),
            recipe=step_id,
            priority_override=priority_override,
        )
        if work is not None:
            payload = dict(payload)
            payload["work_item"] = work.get("item")
            payload["work_item_meta"] = {
                "created": work.get("created"),
                "reused": work.get("reused"),
            }
    except Exception as e:
        payload = dict(payload)
        payload["work_item_error"] = str(e)
    return payload


def _identity_still_candidates_payload(cfg: ServerConfig, q: Dict[str, List[str]]) -> Dict[str, Any]:
    """GET /api/discovery/identity-still/candidates?relpath=&job_key=&family_slug="""
    rel = (q.get("relpath") or [""])[0].strip()
    if not rel:
        raise ValueError("missing relpath")
    job_key = (q.get("job_key") or [""])[0].strip()
    family_slug = (q.get("family_slug") or q.get("family") or [""])[0].strip()
    media_abs = _safe_join(cfg.output_root, rel)
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_identity_still import list_identity_still_candidates  # type: ignore
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    return list_identity_still_candidates(
        relpath=rel,
        family_slug=family_slug,
        job_key=job_key,
        workspace_root=cfg.workspace_root,
        output_root=cfg.output_root,
        data_root=data_root,
        media_abs=media_abs if media_abs is not None and media_abs.is_file() else None,
    )


def _identity_still_mint_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/discovery/identity-still/mint { video_relpath|video_path, at? }"""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_identity_still import mint_identity_still_from_video  # type: ignore
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    video_relpath = str(body.get("video_relpath") or "").strip()
    video_path = str(body.get("video_path") or "").strip()
    if not video_relpath and not video_path:
        raise ValueError("missing video_relpath")
    at = str(body.get("at") or "start").strip() or "start"
    return mint_identity_still_from_video(
        video_path=video_path,
        video_relpath=video_relpath,
        at=at,
        workspace_root=cfg.workspace_root,
        output_root=cfg.output_root,
        data_root=data_root,
    )


def _discovery_work_items_list_payload(cfg: ServerConfig, q: Dict[str, List[str]]) -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_work_items import list_work_items, load_work_items_doc  # type: ignore

    path = _discovery_work_items_index_path(cfg)
    doc = load_work_items_doc(path)
    source_relpath = (q.get("source_relpath") or q.get("relpath") or [""])[0].strip() or None
    source_group_id = (q.get("source_group_id") or q.get("group_id") or [""])[0].strip() or None
    pool = (q.get("pool") or [""])[0].strip() or None
    status_raw = (q.get("status") or [""])[0].strip()
    statuses = [s.strip() for s in status_raw.split(",") if s.strip()] if status_raw else None
    include_terminal = (q.get("include_terminal") or ["1"])[0].strip().lower() not in ("0", "false", "no")
    items = list_work_items(
        doc,
        source_relpath=source_relpath,
        source_group_id=source_group_id,
        pool=pool,
        status=statuses,
        include_terminal=include_terminal,
    )
    return {
        "ok": True,
        "items": items,
        "count": len(items),
        "path": str(path),
        "filters": {
            "source_relpath": source_relpath,
            "source_group_id": source_group_id,
            "pool": pool,
            "status": statuses,
            "include_terminal": include_terminal,
        },
    }


def _discovery_work_items_pool_payload(cfg: ServerConfig, q: Dict[str, List[str]]) -> Dict[str, Any]:
    pool = (q.get("pool") or [""])[0].strip().lower()
    if not pool:
        raise ValueError("missing pool")
    q2 = dict(q)
    q2["pool"] = [pool]
    if "status" not in q2:
        q2["status"] = ["draft,queued,running"]
        q2["include_terminal"] = ["0"]
    payload = _discovery_work_items_list_payload(cfg, q2)
    payload["pool"] = pool
    return payload


def _discovery_work_items_create_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_work_items import create_routes_batch, create_work_item  # type: ignore

    path = _discovery_work_items_index_path(cfg)
    rel = str(body.get("source_relpath") or body.get("relpath") or "").strip()
    if not rel:
        raise ValueError("missing source_relpath")
    media_abs = _safe_join(cfg.output_root, rel)
    if media_abs is None or not media_abs.is_file():
        raise FileNotFoundError(rel)
    routes = body.get("routes")
    queue_now = bool(body.get("queue_now") or body.get("front"))
    if isinstance(routes, list) and routes:
        return create_routes_batch(
            source_relpath=rel,
            routes=routes,
            work_items_index_path=path,
            queue_now=queue_now,
        )
    # Single-route create
    step_id = str(body.get("step_id") or body.get("disposition_step") or "").strip()
    pool = str(body.get("pool") or "").strip()
    if step_id and not pool:
        from shape_factory_work_items import route_for_step  # type: ignore

        mapped = route_for_step(step_id)
        if not mapped:
            raise ValueError(f"unknown step_id: {step_id}")
        pool, entry, default_pri = mapped
        priority = str(body.get("priority") or ("front" if queue_now else default_pri))
        disposition_entry = str(body.get("disposition_entry") or entry)
    else:
        disposition_entry = str(body.get("disposition_entry") or "").strip()
        priority = str(body.get("priority") or ("front" if queue_now else "normal"))
        if not pool:
            raise ValueError("missing pool or step_id")
        if not disposition_entry:
            raise ValueError("missing disposition_entry")
    out = create_work_item(
        source_relpath=rel,
        pool=pool,
        disposition_entry=disposition_entry,
        disposition_step=step_id,
        priority=priority,
        status=str(body.get("status") or "draft"),
        factory_family=str(body.get("factory_family") or body.get("family_slug") or "").strip(),
        recipe=str(body.get("recipe") or step_id or "").strip(),
        work_items_index_path=path,
        force_new=bool(body.get("force_new")),
    )
    return {"ok": True, "item": out.get("item"), "created": out.get("created"), "reused": out.get("reused")}


def _discovery_work_items_cancel_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_work_items import cancel_work_item  # type: ignore

    work_id = str(body.get("work_id") or "").strip()
    if not work_id:
        raise ValueError("missing work_id")
    return cancel_work_item(
        work_id,
        work_items_index_path=_discovery_work_items_index_path(cfg),
        reason=str(body.get("reason") or "").strip() or None,
    )


def _discovery_work_items_priority_payload(cfg: ServerConfig, body: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/discovery/work-items/priority { work_id, priority } — safe front↔normal reshape."""
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_work_items import set_work_item_priority  # type: ignore

    work_id = str(body.get("work_id") or "").strip()
    if not work_id:
        raise ValueError("missing work_id")
    priority = str(body.get("priority") or "").strip()
    if not priority:
        raise ValueError("missing priority")
    return set_work_item_priority(
        work_id,
        priority=priority,
        work_items_index_path=_discovery_work_items_index_path(cfg),
    )


def _discovery_disposition_catalog_payload(cfg: ServerConfig) -> Dict[str, Any]:
    catalog = _discovery_load_disposition_catalog(cfg)
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_disposition import catalog_entries  # type: ignore

    entries = catalog_entries(catalog, kind="entry")
    steps = catalog_entries(catalog, kind="step")
    reasons = catalog_entries(catalog, kind="reason")
    return {
        "ok": True,
        "catalog": catalog,
        "entries": entries,
        "steps": steps,
        "reasons": reasons,
        "catalog_path": str(_discovery_disposition_catalog_path(cfg)),
        "seed_path": str(_repo_root() / "disposition_catalog.yaml"),
    }


def _discovery_disposition_suggest_payload(cfg: ServerConfig, q: Dict[str, List[str]]) -> Dict[str, Any]:
    def _qfloat(key: str) -> Optional[float]:
        for v in q.get(key, []):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
        return None

    def _qstr(key: str) -> Optional[str]:
        for v in q.get(key, []):
            s = str(v or "").strip()
            if s:
                return s
        return None

    def _qbool(key: str) -> bool:
        for v in q.get(key, []):
            return str(v).lower() in ("1", "true", "yes")
        return False

    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_disposition import compute_disposition_promotions  # type: ignore

    catalog = _discovery_load_disposition_catalog(cfg)
    rel = _qstr("relpath")
    quality = _qfloat("quality")
    appetite = _qstr("appetite")
    facet = _qstr("facet")
    predicted = _qfloat("predicted_score")
    explicit_missing = _qbool("explicit_quality_missing")

    if rel and quality is None:
        ratings_doc = _discovery_load_ratings_index(cfg)
        idx = _load_discovery_index_disk(cfg.discovery_index_path) if cfg.discovery_index_path.exists() else None
        item = _discovery_item_for_relpath(idx, rel) if isinstance(idx, dict) else {"relpath": rel}
        ratings = _discovery_ratings_for_item(ratings_doc, item if isinstance(item, dict) else {"relpath": rel})
        if ratings.get("rating_explicit") is not None:
            try:
                quality = float(ratings["rating_explicit"])
            except (TypeError, ValueError):
                pass
            explicit_missing = False
        elif ratings.get("rating_effective") is not None:
            try:
                quality = float(ratings["rating_effective"])
            except (TypeError, ValueError):
                pass
            explicit_missing = ratings.get("rating_explicit") is None
        if appetite is None:
            appetite = ratings.get("appetite")
        if facet is None:
            facet = ratings.get("appetite_facet")

    promotions = compute_disposition_promotions(
        catalog,
        quality=quality,
        appetite=appetite,
        facet=facet,
        predicted_score=predicted,
        explicit_quality_missing=explicit_missing,
    )
    return {"ok": True, "relpath": rel, "promotions": promotions, "inputs": {
        "quality": quality,
        "appetite": appetite,
        "facet": facet,
        "predicted_score": predicted,
        "explicit_quality_missing": explicit_missing,
    }}


# Stamp is (db_mtime, json_mtime) for ratings; appetite may use a single float.
_RATINGS_INDEX_CACHE: Dict[str, Tuple[Any, Dict[str, Any]]] = {}
_APPETITE_INDEX_CACHE: Dict[str, Tuple[Any, Dict[str, Any]]] = {}

_RATINGS_VERIFICATIONS_LOCK = threading.Lock()
_RATINGS_VALID_LENSES = frozenset({"as_source", "workflow", "recipe"})


def _invalidate_ratings_caches(cfg: "ServerConfig") -> None:
    """Drop cached ratings/appetite docs after interactive writes (WAL mtime can lag)."""
    for path in (_discovery_ratings_index_path(cfg), _discovery_appetite_index_path(cfg)):
        _RATINGS_INDEX_CACHE.pop(str(path), None)
        _APPETITE_INDEX_CACHE.pop(str(path), None)
        db = path.with_name("ratings.sqlite")
        _RATINGS_INDEX_CACHE.pop(str(db), None)
        _APPETITE_INDEX_CACHE.pop(str(db), None)


def _discovery_ratings_verifications_path(cfg: "ServerConfig") -> Path:
    return cfg.discovery_index_path.with_name("ratings_verifications.json")


def _discovery_ratings_canonical_asset_key(relpath: str) -> str:
    norm = _normalize_rel_posix(relpath.strip())
    if not norm:
        return ""
    keys = _discovery_output_relpath_keys({"relpath": norm, "video_relpath": norm})
    for k in keys:
        if not k.lower().endswith((".mp4", ".png", ".xmp")):
            return k
    return keys[0] if keys else norm


def _discovery_load_ratings_verifications(cfg: "ServerConfig") -> Dict[str, Any]:
    path = _discovery_ratings_verifications_path(cfg)
    if not path.is_file():
        return {"version": 1, "updated_at": None, "by_asset_key": {}}
    try:
        doc = _read_json(path)
    except Exception:
        return {"version": 1, "updated_at": None, "by_asset_key": {}}
    if not isinstance(doc, dict):
        return {"version": 1, "updated_at": None, "by_asset_key": {}}
    if not isinstance(doc.get("by_asset_key"), dict):
        doc["by_asset_key"] = {}
    return doc


def _discovery_persist_ratings_lens_verification(
    cfg: "ServerConfig",
    *,
    asset_key: str,
    lens: str,
    verified: bool,
    override_rating: Optional[int],
    note: Optional[str],
) -> Dict[str, Any]:
    path = _discovery_ratings_verifications_path(cfg)
    now = _dt.datetime.now(tz=_dt.timezone.utc).isoformat(timespec="seconds")
    with _RATINGS_VERIFICATIONS_LOCK:
        doc = _discovery_load_ratings_verifications(cfg)
        by_asset = doc.setdefault("by_asset_key", {})
        if not isinstance(by_asset, dict):
            by_asset = {}
            doc["by_asset_key"] = by_asset
        asset_row = by_asset.get(asset_key)
        if not isinstance(asset_row, dict):
            asset_row = {"lenses": {}}
            by_asset[asset_key] = asset_row
        lenses = asset_row.setdefault("lenses", {})
        if not isinstance(lenses, dict):
            lenses = {}
            asset_row["lenses"] = lenses

        if not verified and override_rating is None and not (note or "").strip():
            lenses.pop(lens, None)
            if not lenses:
                by_asset.pop(asset_key, None)
        else:
            entry: Dict[str, Any] = {"verified": bool(verified)}
            if verified:
                entry["verified_at"] = now
            if override_rating is not None:
                entry["override_rating"] = int(override_rating)
            if note and note.strip():
                entry["note"] = note.strip()[:500]
            lenses[lens] = entry

        doc["version"] = 1
        doc["updated_at"] = now
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, doc)
        return lenses.get(lens) if isinstance(lenses.get(lens), dict) else {}


def _discovery_apply_human_verifications(payload: Dict[str, Any], ver_doc: Dict[str, Any]) -> Dict[str, Any]:
    asset_key = str(payload.get("asset_key") or "").strip()
    if not asset_key:
        return payload
    by_asset = ver_doc.get("by_asset_key")
    if not isinstance(by_asset, dict):
        return payload
    asset_row = by_asset.get(asset_key)
    if not isinstance(asset_row, dict):
        return payload
    lenses = asset_row.get("lenses")
    if not isinstance(lenses, dict):
        return payload

    for block_name, lens_name in (("as_source", "as_source"), ("workflow", "workflow"), ("recipe", "recipe")):
        block = payload.get(block_name)
        if not isinstance(block, dict):
            continue
        lv = lenses.get(lens_name)
        if isinstance(lv, dict):
            block["human"] = {
                "verified": bool(lv.get("verified")),
                "verified_at": lv.get("verified_at"),
                "override_rating": lv.get("override_rating"),
                "note": lv.get("note"),
            }

    explicit_rating = None
    explicit = payload.get("explicit")
    if isinstance(explicit, dict) and explicit.get("rating") is not None:
        try:
            explicit_rating = int(explicit["rating"])
        except Exception:
            pass
    if explicit_rating is not None:
        payload["rating_effective"] = explicit_rating
        return payload

    for block_name in ("workflow", "as_source", "recipe"):
        block = payload.get(block_name)
        if not isinstance(block, dict):
            continue
        human = block.get("human")
        if isinstance(human, dict) and human.get("override_rating") is not None:
            try:
                payload["rating_effective"] = int(human["override_rating"])
                return payload
            except Exception:
                pass

    return payload


def _discovery_load_ratings_index(cfg: "ServerConfig") -> Optional[Dict[str, Any]]:
    path = _discovery_ratings_index_path(cfg)
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    try:
        from shape_factory_ratings import load_ratings_doc, ratings_db_path_for_index  # type: ignore
    except Exception:
        return None
    db_path = ratings_db_path_for_index(path)
    if not path.is_file() and not db_path.is_file():
        return None
    try:
        db_mtime = db_path.stat().st_mtime if db_path.is_file() else 0.0
        json_mtime = path.stat().st_mtime if path.is_file() else 0.0
    except OSError:
        return None
    key = str(db_path if db_path.is_file() else path)
    # Cache key includes JSON mtime so aggregate sections stay valid across sqlite writes.
    stamp = (db_mtime, json_mtime)
    cached = _RATINGS_INDEX_CACHE.get(key)
    if cached and cached[0] == stamp:
        return cached[1]
    try:
        doc = load_ratings_doc(path)
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None
    _RATINGS_INDEX_CACHE[key] = (stamp, doc)
    return doc


def _discovery_output_relpath_keys(item: Dict[str, Any]) -> List[str]:
    keys: List[str] = []
    seen: set = set()
    for raw in (item.get("relpath"), item.get("video_relpath"), item.get("thumb_relpath")):
        if not isinstance(raw, str) or not raw.strip():
            continue
        norm = _normalize_rel_posix(raw.strip())
        if not norm or norm in seen:
            continue
        seen.add(norm)
        keys.append(norm)
        if norm.endswith(".mp4"):
            stem = norm[:-4]
            if stem not in seen:
                seen.add(stem)
                keys.append(stem)
        if norm.endswith(".png"):
            stem = norm[:-4]
            if stem not in seen:
                seen.add(stem)
                keys.append(stem)
    return keys


def _discovery_appetite_for_item(appetite_doc: Optional[Dict[str, Any]], item: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(appetite_doc, dict) or not isinstance(item, dict):
        return {}
    table = appetite_doc.get("by_output_relpath")
    if not isinstance(table, dict):
        return {}
    for key in _discovery_output_relpath_keys(item):
        row = table.get(key)
        if isinstance(row, dict) and row.get("appetite"):
            return {"appetite": row.get("appetite"), "appetite_facet": row.get("facet") or "both"}
    return {}


def _discovery_ratings_for_item(
    ratings_doc: Optional[Dict[str, Any]],
    item: Dict[str, Any],
    appetite_doc: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not isinstance(ratings_doc, dict) or not isinstance(item, dict):
        return dict(_discovery_appetite_for_item(appetite_doc, item))
    by_output = ratings_doc.get("by_output_relpath")
    by_source = ratings_doc.get("by_source_basename")
    if not isinstance(by_output, dict):
        by_output = {}
    if not isinstance(by_source, dict):
        by_source = {}

    out: Dict[str, Any] = {}
    explicit: Optional[int] = None
    for key in _discovery_output_relpath_keys(item):
        row = by_output.get(key)
        if isinstance(row, dict) and row.get("explicit") is not None:
            try:
                explicit = int(row["explicit"])
            except Exception:
                pass
            break
    if explicit is not None:
        out["rating_explicit"] = explicit

    basename = ""
    name = item.get("name")
    if isinstance(name, str) and name.strip():
        basename = Path(name.strip()).name
    if not basename:
        for key in _discovery_output_relpath_keys(item):
            basename = Path(key).name
            if basename:
                break

    src_row = by_source.get(basename) if basename else None
    if isinstance(src_row, dict) and src_row.get("inferred") is not None:
        out["rating_inferred"] = src_row.get("inferred")
        out["rating_evidence"] = {
            "n": src_row.get("n"),
            "keepers_4plus": src_row.get("keepers_4plus") or src_row.get("favorite_fanout"),
        }

    if explicit is not None:
        out["rating_effective"] = explicit
    elif out.get("rating_inferred") is not None:
        out["rating_effective"] = out["rating_inferred"]

    out.update(_discovery_appetite_for_item(appetite_doc, item))
    return out


def _discovery_disposition_for_item(disposition_doc: Optional[Dict[str, Any]], item: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(disposition_doc, dict) or not isinstance(item, dict):
        return {}
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_disposition import disposition_for_item  # type: ignore

    return disposition_for_item(item, disposition_doc)


def _discovery_enrich_item_ratings(
    item: Dict[str, Any],
    cfg: Optional["ServerConfig"],
    ratings_doc: Optional[Dict[str, Any]] = None,
    appetite_doc: Optional[Dict[str, Any]] = None,
    disposition_doc: Optional[Dict[str, Any]] = None,
    work_items_doc: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return item
    if ratings_doc is None and cfg is not None:
        ratings_doc = _discovery_load_ratings_index(cfg)
    if appetite_doc is None and cfg is not None:
        appetite_doc = _discovery_load_appetite_index(cfg)
    if disposition_doc is None and cfg is not None:
        disposition_doc = _discovery_load_disposition_index(cfg)
    if work_items_doc is None and cfg is not None:
        work_items_doc = _discovery_load_work_items_index(cfg)
    if not ratings_doc and not appetite_doc and not disposition_doc and not work_items_doc:
        return item
    ratings = _discovery_ratings_for_item(ratings_doc, item, appetite_doc)
    disp = _discovery_disposition_for_item(disposition_doc, item)
    work: Dict[str, Any] = {}
    if work_items_doc:
        d = _workspace_scripts_dir()
        if d.is_dir() and str(d) not in sys.path:
            sys.path.insert(0, str(d))
        from shape_factory_work_items import work_items_for_item  # type: ignore

        work = work_items_for_item(item, work_items_doc)
    if not ratings and not disp and not work:
        return item
    merged = dict(item)
    merged.update(ratings)
    merged.update(disp)
    merged.update(work)
    return merged


def _discovery_compute_asset_ratings(
    cfg: "ServerConfig",
    idx: Dict[str, Any],
    relpath: str,
) -> Dict[str, Any]:
    rel = _normalize_rel_posix(relpath.strip())
    if not rel:
        return {"ok": False, "error": "missing_or_bad_relpath"}

    ratings_doc = _discovery_load_ratings_index(cfg)
    if not ratings_doc:
        return {
            "ok": False,
            "error": "ratings_index_missing",
            "detail": str(_discovery_ratings_index_path(cfg)),
        }

    item = _discovery_item_for_relpath(idx, rel)
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_ratings import build_asset_ratings_explorer  # type: ignore

    payload = build_asset_ratings_explorer(
        relpath=rel,
        ratings_doc=ratings_doc,
        item=item if isinstance(item, dict) else None,
    )
    payload["asset_key"] = _discovery_ratings_canonical_asset_key(rel)
    payload["ratings_index_path"] = str(_discovery_ratings_index_path(cfg))
    ver_doc = _discovery_load_ratings_verifications(cfg)
    payload["human_verifications_path"] = str(_discovery_ratings_verifications_path(cfg))
    payload = _discovery_apply_human_verifications(payload, ver_doc)
    if isinstance(item, dict):
        payload["item"] = {
            "group_id": item.get("group_id"),
            "name": item.get("name"),
            "library": item.get("library"),
            "relpath": item.get("relpath"),
            "video_relpath": item.get("video_relpath"),
        }
    appetite_doc = _discovery_load_appetite_index(cfg)
    appetite = _discovery_appetite_for_item(appetite_doc, item if isinstance(item, dict) else {"relpath": rel})
    payload["appetite"] = appetite.get("appetite")
    payload["appetite_facet"] = appetite.get("appetite_facet")
    disposition_doc = _discovery_load_disposition_index(cfg)
    disp = _discovery_disposition_for_item(disposition_doc, item if isinstance(item, dict) else {"relpath": rel})
    payload["disposition_markers"] = disp.get("disposition_markers") or []
    payload["disposition_notes"] = disp.get("disposition_notes") or {}
    payload["disposition_reason_detail"] = disp.get("disposition_reason_detail") or {}
    payload["disposition_updated_at"] = disp.get("disposition_updated_at")
    payload["disposition_outcomes"] = disp.get("disposition_outcomes") or []
    payload["disposition_last_outcome"] = disp.get("disposition_last_outcome")
    payload["disposition_archived"] = disp.get("disposition_archived")
    payload["disposition_saved"] = disp.get("disposition_saved")
    work_doc = _discovery_load_work_items_index(cfg)
    if work_doc:
        from shape_factory_work_items import work_items_for_item  # type: ignore

        work = work_items_for_item(item if isinstance(item, dict) else {"relpath": rel}, work_doc)
        payload.update(work)
    triage_doc = _discovery_load_triage_index(cfg)
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_triage import triage_for_item, needs_triage_item  # type: ignore

    item_for_triage = item if isinstance(item, dict) else {"relpath": rel}
    disposition_doc = _discovery_load_disposition_index(cfg)
    triage = triage_for_item(item_for_triage, triage_doc, disposition_doc=disposition_doc)
    payload["needs_triage"] = needs_triage_item(
        item_for_triage,
        triage_doc=triage_doc,
        disposition_doc=disposition_doc,
    )
    payload["last_triaged_at"] = triage.get("last_triaged_at")
    payload["triage_pass_count"] = triage.get("triage_pass_count")
    return payload


_LINEAGE_GRAPH_CACHE: Dict[str, Tuple[float, int, Dict[str, Any]]] = {}


def _discovery_load_lineage_graph(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"version": 1, "edges": []}
    try:
        st = path.stat()
        key = str(path)
        cached = _LINEAGE_GRAPH_CACHE.get(key)
        if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
            return cached[2]
        obj = _read_json(path)
    except Exception:
        return {"version": 1, "edges": []}
    if not isinstance(obj, dict):
        return {"version": 1, "edges": []}
    edges = obj.get("edges")
    if not isinstance(edges, list):
        obj["edges"] = []
    try:
        st = path.stat()
        _LINEAGE_GRAPH_CACHE[str(path)] = (st.st_mtime, st.st_size, obj)
    except OSError:
        pass
    return obj


def _discovery_persist_lineage_edge_rows(cfg: "ServerConfig", rows: List[Dict[str, Any]]) -> int:
    if not rows:
        return 0
    path = _discovery_lineage_edges_path(cfg)
    with _DISCOVERY_LINEAGE_GRAPH_LOCK:
        doc = _discovery_load_lineage_graph(path)
        edges = doc.get("edges")
        if not isinstance(edges, list):
            edges = []
        seen: set = set()
        for e in edges:
            if not isinstance(e, dict):
                continue
            seen.add(
                (
                    str(e.get("child_group_id") or ""),
                    str(e.get("parent_group_id") or ""),
                    str(e.get("via_source_raw") or ""),
                )
            )
        added = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            if _discovery_lineage_edge_looks_spurious(row):
                continue
            key = (
                str(row.get("child_group_id") or ""),
                str(row.get("parent_group_id") or ""),
                str(row.get("via_source_raw") or ""),
            )
            if not key[0] or not key[1]:
                continue
            if key in seen:
                continue
            seen.add(key)
            edges.append(row)
            added += 1
        doc["edges"] = edges
        doc["version"] = 1
        doc["updated_at"] = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _atomic_write_json(path, doc)
        try:
            st = path.stat()
            _LINEAGE_GRAPH_CACHE[str(path)] = (st.st_mtime, st.st_size, doc)
        except OSError:
            _LINEAGE_GRAPH_CACHE.pop(str(path), None)
        # Keep inverted citation index warm for forward-fill lookups.
        try:
            _discovery_citations_ingest_lineage_edge_rows(cfg, rows)
        except Exception:
            pass
        return added


def _discovery_extract_source_path_strings_from_facets_payload(payload: Dict[str, Any]) -> List[str]:
    """
    Parent-hint strings for lineage.

    Prefer media paths from Load* nodes that feed a saved output (orphan / dead loaders
    omitted). Fall back to the broad path-like sample only when the wired analysis is absent.
    """
    out: List[str] = []
    seen: set = set()

    def _add(s: Any) -> None:
        if not isinstance(s, str):
            return
        ss = s.strip()
        if not ss or ss in seen:
            return
        seen.add(ss)
        out.append(ss)

    probes = payload.get("png_workflow_probes")
    if isinstance(probes, list):
        wired_any = False
        for pr in probes:
            if not isinstance(pr, dict):
                continue
            facets = pr.get("facets")
            if not isinstance(facets, dict):
                continue
            api = facets.get("api_prompt")
            if not isinstance(api, dict):
                continue
            sources = api.get("sources")
            if not isinstance(sources, dict):
                continue
            if sources.get("output_feeding_loader_paths_ok"):
                wired_any = True
                sample = sources.get("output_feeding_loader_paths")
                if isinstance(sample, list):
                    for s in sample:
                        _add(s)
        if wired_any:
            return out
        # Legacy / failed analysis: broad path-like scrape (includes SaveVideo prefixes, orphans).
        for pr in probes:
            if not isinstance(pr, dict):
                continue
            facets = pr.get("facets")
            if not isinstance(facets, dict):
                continue
            api = facets.get("api_prompt")
            if not isinstance(api, dict):
                continue
            sources = api.get("sources")
            if not isinstance(sources, dict):
                continue
            sample = sources.get("source_paths_sample")
            if not isinstance(sample, list):
                continue
            for s in sample:
                _add(s)
    return out


def _discovery_lineage_source_string_is_assetish(s: str) -> bool:
    low = s.lower()
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".mov", ".mkv"):
        if low.endswith(ext):
            return True
    if "og/" in low or "wip/" in low or "/output/" in low:
        return True
    if low.startswith("input/"):
        return True
    return False


def _discovery_path_has_media_ext(s: str) -> bool:
    low = str(s or "").strip().lower().replace("\\", "/")
    if "?" in low:
        low = low.split("?", 1)[0]
    if "#" in low:
        low = low.split("#", 1)[0]
    return any(low.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".mov", ".mkv"))


def _discovery_lineage_child_output_stems(child_item: Optional[Dict[str, Any]]) -> List[str]:
    """Basenames / stems for the child row (primary + members), lowercased."""
    if not isinstance(child_item, dict):
        return []
    names: List[str] = []
    for k in ("name", "relpath", "video_relpath", "thumb_relpath"):
        v = child_item.get(k)
        if isinstance(v, str) and v.strip():
            names.append(Path(v.strip()).name)
    mems = child_item.get("members")
    if isinstance(mems, list):
        for mm in mems:
            if not isinstance(mm, dict):
                continue
            for k in ("name", "relpath"):
                v = mm.get(k)
                if isinstance(v, str) and v.strip():
                    names.append(Path(v.strip()).name)
    out: List[str] = []
    seen: set = set()
    for nm in names:
        low = nm.lower().strip()
        if not low or low in seen:
            continue
        seen.add(low)
        out.append(low)
        stem = Path(low).stem
        if stem and stem not in seen:
            seen.add(stem)
            out.append(stem)
        # Comfy batch suffix: Foo_00001 → Foo
        if len(stem) > 6 and stem[-6] == "_" and stem[-5:].isdigit():
            prefix = stem[:-6]
            if prefix and prefix not in seen:
                seen.add(prefix)
                out.append(prefix)
    return out


def _discovery_lineage_source_usable_as_parent_hint(
    raw: str,
    *,
    child_item: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Whether an embedded prompt string may be used as a lineage *parent* hint.

    SaveVideo / VHS ``filename_prefix`` widgets often embed the child's own output
    stem without an extension (e.g. ``output/og/.../FB9_GEX2_2026-04-14``). Those
    must not be treated as parents — prefix-matching them to the newest sibling
    pollutes descendant trees.
    """
    s = str(raw or "").strip().replace("\\", "/")
    if not s or len(s) > 4096:
        return False
    low = s.lower()
    if low.startswith("video/") or low.startswith("audio/") or low.startswith("image/"):
        return False
    if "round(" in low or low in {"true", "false", "null"}:
        return False
    if not _discovery_lineage_source_string_is_assetish(s):
        return False
    # Concrete media paths only for parent edges (extensionless = almost always output naming).
    if not _discovery_path_has_media_ext(s):
        return False
    base = Path(s).name.lower()
    stem = Path(base).stem.lower()
    for child_tok in _discovery_lineage_child_output_stems(child_item):
        if base == child_tok or stem == child_tok:
            return False
        if child_tok.startswith(stem + "_") or child_tok.startswith(base + "_"):
            return False
    return True


def _discovery_lineage_edge_looks_spurious(e: Dict[str, Any]) -> bool:
    """Persisted / inferred edges that attach siblings via SaveVideo filename prefixes."""
    if not isinstance(e, dict):
        return True
    evidence = str(e.get("evidence") or "").strip()
    # Basename grep / workspace input edges intentionally may lack a directory.
    if evidence in {"workspace_input", "png_prompt_grep"}:
        return False
    via = str(e.get("via_source_raw") or "").strip()
    if evidence == "png_prompt_source_path":
        if not via:
            return True
        if not _discovery_path_has_media_ext(via):
            return True
        if not _discovery_lineage_source_usable_as_parent_hint(via):
            return True
    return False


def _discovery_abs_path_to_output_relpath(cfg: "ServerConfig", abs_p: Path) -> Optional[str]:
    try:
        ar = abs_p.resolve()
        root = cfg.output_root.resolve()
        rel = ar.relative_to(root)
        return _normalize_rel_posix(str(rel).replace("\\", "/"))
    except Exception:
        return None


def _discovery_ensure_thumb_payload(cfg: "ServerConfig", body: Dict[str, Any]) -> Dict[str, Any]:
    """
    POST /api/discovery/ensure-thumb — write same-stem .png next to a video when missing.
    """
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from video_companion_thumbs import ensure_companion_thumb  # type: ignore

    rel = str(body.get("relpath") or "").strip()
    if not rel:
        raise ValueError("relpath required")
    force = bool(body.get("force"))
    media_abs = _discovery_resolve_media_file(cfg, rel)
    if media_abs is None or not media_abs.is_file():
        raise FileNotFoundError(rel)

    # Prefer the video member when the caller passed a non-video primary relpath.
    video_abs = media_abs
    if video_abs.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm"}:
        # Try swapping extension to .mp4 next to the resolved file.
        cand = video_abs.with_suffix(".mp4")
        if cand.is_file():
            video_abs = cand
        else:
            raise ValueError(f"not_a_video: {rel}")

    row = ensure_companion_thumb(video_abs, force=force)
    if not row.get("ok"):
        return {
            "ok": False,
            "error": row.get("error") or "ensure_thumb_failed",
            "relpath": rel,
            "detail": row,
        }

    thumb_abs = Path(str(row.get("path") or ""))
    thumb_rel = _discovery_abs_path_to_output_relpath(cfg, thumb_abs) if thumb_abs.is_file() else None
    if not thumb_rel:
        # Fall back to same-stem guess under the video's output-relative path.
        video_rel = _discovery_abs_path_to_output_relpath(cfg, video_abs) or _normalize_rel_posix(rel)
        if video_rel.lower().endswith((".mp4", ".mov", ".mkv", ".webm")):
            thumb_rel = str(Path(video_rel).with_suffix(".png")).replace("\\", "/")
        else:
            thumb_rel = video_rel

    thumb_url = "/files/" + urllib.parse.quote(_normalize_rel_posix(thumb_rel), safe="") if thumb_rel else None
    return {
        "ok": True,
        "relpath": rel,
        "thumb_relpath": thumb_rel,
        "thumb_url": thumb_url,
        "created": bool(row.get("created")),
        "skipped": bool(row.get("skipped")),
        "reason": row.get("reason"),
    }


def _discovery_basename_matches_item(it: Dict[str, Any], base_lc: str) -> bool:
    nm = str(it.get("name") or "")
    if nm.lower() == base_lc:
        return True
    mems = it.get("members")
    if isinstance(mems, list):
        for mm in mems:
            if not isinstance(mm, dict):
                continue
            n2 = str(mm.get("name") or "")
            if n2.lower() == base_lc:
                return True
            rv = mm.get("relpath")
            if isinstance(rv, str) and rv.strip():
                if Path(rv.strip()).name.lower() == base_lc:
                    return True
    for k in ("relpath", "video_relpath", "thumb_relpath"):
        v = it.get(k)
        if isinstance(v, str) and v.strip():
            if Path(v.strip()).name.lower() == base_lc:
                return True
    return False


def _discovery_find_item_by_media_basename(idx: Dict[str, Any], filename: str) -> Optional[Dict[str, Any]]:
    base_lc = Path(str(filename or "").strip()).name.lower().strip()
    if not base_lc:
        return None
    items = idx.get("items")
    if not isinstance(items, list):
        return None
    hits: List[Dict[str, Any]] = []
    for it in items:
        if isinstance(it, dict) and _discovery_basename_matches_item(it, base_lc):
            hits.append(it)
    if not hits:
        return None
    hits.sort(key=lambda x: float(x.get("mtime") or 0), reverse=True)
    return hits[0]


def _discovery_find_item_by_output_relpath_prefix(idx: Dict[str, Any], hint_rel: str) -> Optional[Dict[str, Any]]:
    """
    Match Discovery rows when a prompt cites an output path **without** the final ``_00001.mp4`` suffix.
    E.g. ``output/og/.../Foo_OG`` → ``output/og/.../Foo_OG_00001.mp4``.

    Ambiguous prefixes (many ``Foo_0000N`` siblings) return None — never pick "newest".
    """
    prefix = _normalize_rel_posix(str(hint_rel or "").strip())
    if not prefix or "/" not in prefix:
        return None
    prefix = prefix.rstrip("/")
    items = idx.get("items")
    if not isinstance(items, list):
        return None
    hits: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        for k in ("relpath", "video_relpath", "thumb_relpath"):
            v = it.get(k)
            if not isinstance(v, str) or not v.strip():
                continue
            rn = _normalize_rel_posix(v.strip())
            if rn == prefix or rn.startswith(prefix + "_") or rn.startswith(prefix + "."):
                hits.append(it)
                break
    if not hits:
        return None
    # Dedupe by group_id — many members can hit the same row.
    by_gid: Dict[str, Dict[str, Any]] = {}
    for it in hits:
        gid = str(it.get("group_id") or "") or str(it.get("relpath") or id(it))
        by_gid.setdefault(gid, it)
    uniq = list(by_gid.values())
    if len(uniq) != 1:
        return None
    return uniq[0]


def _discovery_resolve_lineage_parent_for_source(
    cfg: "ServerConfig",
    idx: Dict[str, Any],
    raw: str,
    *,
    child_gid: str,
    child_item: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[Dict[str, Any]]]:
    """
    Resolve one embedded path string to an indexed parent row, output relpath, or external file metadata.
    """
    if not _discovery_lineage_source_usable_as_parent_hint(raw, child_item=child_item):
        return None, None, None
    resolved_rel = _discovery_try_resolve_path_like_to_relpath(cfg, raw)
    pit = _discovery_item_for_relpath(idx, resolved_rel) if resolved_rel else None
    if pit is None:
        pit = _discovery_find_item_by_media_basename(idx, raw)
    if pit is None and resolved_rel:
        pit = _discovery_find_item_by_media_basename(idx, Path(resolved_rel).name)
    if pit is None and _discovery_path_has_media_ext(raw):
        for rel in _discovery_candidate_output_relpaths_for_path_hint(cfg, raw):
            # Only exact-ish prefix match when the hint itself named a media file;
            # never for SaveVideo filename_prefix stems.
            pit = _discovery_find_item_by_output_relpath_prefix(idx, rel)
            if pit is not None:
                resolved_rel = _discovery_lineage_facets_probe_relpath(pit)
                break
    if pit is None:
        abs_p = _discovery_resolve_existing_media_abs_path(cfg, raw)
        if abs_p is not None and abs_p.is_file():
            out_rel = _discovery_abs_path_to_output_relpath(cfg, abs_p)
            if out_rel is None:
                try:
                    wsrel = str(abs_p.resolve().relative_to(cfg.workspace_root.resolve())).replace("\\", "/")
                except Exception:
                    wsrel = ""
                in_rel = _discovery_workspace_input_relpath_for_source(cfg, raw) or (
                    _normalize_rel_posix(wsrel) if wsrel else None
                )
                return (
                    None,
                    None,
                    {
                        "via_source_raw": raw,
                        "abs_path": str(abs_p.resolve()),
                        "workspace_relpath": in_rel or wsrel or None,
                        "kind": "outside_output_root",
                    },
                )
            if _discovery_lineage_source_string_is_assetish(raw):
                pit = _discovery_find_item_by_output_relpath_prefix(idx, out_rel)
                if pit is None:
                    pit = _discovery_item_for_relpath(idx, out_rel)
                resolved_rel = out_rel
    if not isinstance(pit, dict):
        return None, None, None
    pgid = str(pit.get("group_id") or "")
    if not pgid or pgid == child_gid:
        return None, None, None
    edge_rel = resolved_rel or _discovery_lineage_facets_probe_relpath(pit)
    return pit, edge_rel, None


def _discovery_infer_lineage_session_edges(
    cfg: "ServerConfig",
    idx: Dict[str, Any],
    seed_item: Dict[str, Any],
    *,
    max_depth: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Infer parent edges for one seed from embedded prompt paths (PNG facets).
    Returns (edges, external_source dicts).
    """
    edges: List[Dict[str, Any]] = []
    external_sources: List[Dict[str, Any]] = []
    if not isinstance(seed_item, dict):
        return edges, external_sources
    max_depth = max(0, min(int(max_depth), 4))
    processed_groups: set = set()
    queue: collections.deque = collections.deque()
    queue.append((seed_item, 0))

    while queue:
        item, depth = queue.popleft()
        if not isinstance(item, dict):
            continue
        gid = str(item.get("group_id") or "")
        if not gid or gid in processed_groups:
            continue
        processed_groups.add(gid)

        probe_rel = _discovery_lineage_facets_probe_relpath(item)
        if not probe_rel:
            continue
        try:
            facets = _discovery_build_workflow_facets_payload(cfg, probe_rel)
        except Exception:
            continue
        if not isinstance(facets, dict) or not facets.get("ok"):
            continue

        strings = _discovery_extract_source_path_strings_from_facets_payload(facets)
        parent_items: Dict[str, Dict[str, Any]] = {}
        for s in strings:
            pit, edge_rel, ext = _discovery_resolve_lineage_parent_for_source(
                cfg, idx, s, child_gid=gid, child_item=item
            )
            if ext is not None:
                external_sources.append(ext)
                in_rel = ext.get("workspace_relpath")
                if not isinstance(in_rel, str) or not in_rel.strip():
                    in_rel = _discovery_workspace_input_relpath_for_source(cfg, s)
                if isinstance(in_rel, str) and in_rel.strip():
                    edges.append(
                        {
                            "child_group_id": gid,
                            "parent_group_id": _discovery_workspace_input_group_id(in_rel),
                            "via_source_raw": s,
                            "resolved_parent_relpath": _normalize_rel_posix(in_rel.strip()),
                            "evidence": "workspace_input",
                        }
                    )
                continue
            if pit is None:
                continue
            pgid = str(pit.get("group_id") or "")
            if not pgid or pgid == gid:
                continue
            parent_items[pgid] = pit
            edges.append(
                {
                    "child_group_id": gid,
                    "parent_group_id": pgid,
                    "via_source_raw": s,
                    "resolved_parent_relpath": edge_rel,
                    "evidence": "png_prompt_source_path",
                }
            )

        if depth < max_depth:
            for pit in parent_items.values():
                if not isinstance(pit, dict):
                    continue
                pgid = str(pit.get("group_id") or "")
                if pgid in processed_groups:
                    continue
                queue.append((pit, depth + 1))

    return edges, external_sources


def _discovery_merge_externals_into_provenance_chain(
    chain: List[Dict[str, Any]],
    external_sources: List[Dict[str, Any]],
    seed_gid: str,
    cfg: Optional["ServerConfig"],
) -> List[Dict[str, Any]]:
    """Prepend workspace ``input/`` sources as the oldest provenance (leftmost in the UI)."""
    if not external_sources:
        return chain
    seen: set = set()
    ext_entries: List[Dict[str, Any]] = []
    for ext in external_sources:
        if not isinstance(ext, dict):
            continue
        key = str(ext.get("via_source_raw") or ext.get("abs_path") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        ws = ext.get("workspace_relpath")
        abs_p = ext.get("abs_path")
        norm_in: Optional[str] = None
        if cfg is not None:
            norm_in = _discovery_workspace_input_relpath_for_source(
                cfg, ext.get("via_source_raw") or ws or abs_p or key
            )
        if norm_in is None and isinstance(ws, str) and ws.strip():
            norm_in = _normalize_rel_posix(ws.strip())
        name = Path(str(norm_in or ws or abs_p or key)).name
        item: Dict[str, Any] = {
            "group_id": None,
            "name": name,
            "relpath": norm_in,
            "workspace_relpath": norm_in,
            "media_kind": "image" if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")) else "other",
        }
        if cfg is not None and norm_in:
            tu = _discovery_lineage_file_url(cfg, norm_in)
            if tu:
                item["thumb_url"] = tu
            if not item.get("thumb_url") and isinstance(abs_p, str):
                try:
                    ar = Path(abs_p)
                    if ar.is_file():
                        try:
                            wsrel = str(ar.resolve().relative_to(cfg.workspace_root.resolve())).replace("\\", "/")
                            in_rel = _discovery_workspace_input_relpath_for_source(cfg, wsrel) or _normalize_rel_posix(wsrel)
                            tu = _discovery_lineage_file_url(cfg, in_rel)
                            if tu:
                                item["thumb_url"] = tu
                                item["relpath"] = in_rel
                                item["workspace_relpath"] = in_rel
                        except Exception:
                            pass
                except Exception:
                    pass
        if cfg is not None:
            item = _discovery_enrich_item_ratings(item, cfg)
        ext_entries.append(
            {
                "depth": 0,
                "role": "source",
                "group_id": "",
                "item": item,
                "via_source_raw": ext.get("via_source_raw"),
                "external": True,
            }
        )
    if not ext_entries:
        return chain
    return _discovery_splice_external_entries_onto_chain(chain, ext_entries, seed_gid)


def _discovery_splice_external_entries_onto_chain(
    chain: List[Dict[str, Any]],
    ext_entries: List[Dict[str, Any]],
    seed_gid: str,
) -> List[Dict[str, Any]]:
    if not ext_entries:
        return chain
    if not chain:
        out = list(ext_entries)
    elif chain and str(chain[-1].get("group_id") or "") == seed_gid:
        head = chain[:-1]
        seed_row = dict(chain[-1])
        out = list(ext_entries) + head + [seed_row]
    else:
        out = list(ext_entries) + list(chain)

    merged: List[Dict[str, Any]] = []
    for i, row in enumerate(out):
        if not isinstance(row, dict):
            continue
        r = dict(row)
        r["depth"] = i
        if str(r.get("group_id") or "") == seed_gid:
            r["role"] = "seed"
        elif i == 0:
            r["role"] = "root"
        elif r.get("external"):
            r["role"] = "source"
        else:
            r["role"] = "ancestor"
        merged.append(r)
    return merged


def _discovery_input_parent_entries_from_edges(
    cfg: "ServerConfig",
    child_gid: str,
    merged_edges: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build external provenance cards from persisted ``input:…`` parent edges (graph-only mode)."""
    if not child_gid:
        return []
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for e in merged_edges:
        if not isinstance(e, dict):
            continue
        if str(e.get("child_group_id") or "") != child_gid:
            continue
        pid = str(e.get("parent_group_id") or "")
        if not pid.startswith("input:"):
            continue
        bn = pid[len("input:") :].strip()
        if not bn or bn in seen:
            continue
        seen.add(bn)
        via = str(e.get("via_source_raw") or bn)
        resolved = str(e.get("resolved_parent_relpath") or "").strip().replace("\\", "/")
        norm_in = resolved if resolved.lower().startswith("input/") else None
        if not norm_in:
            norm_in = _discovery_workspace_input_relpath_for_source(cfg, via) or _discovery_workspace_input_relpath_for_source(
                cfg, bn
            )
        if not norm_in:
            norm_in = f"input/{bn}"
        name = Path(norm_in).name
        item: Dict[str, Any] = {
            "group_id": None,
            "name": name,
            "relpath": norm_in,
            "workspace_relpath": norm_in,
            "media_kind": "image" if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")) else "other",
        }
        tu = _discovery_lineage_file_url(cfg, norm_in)
        if tu:
            item["thumb_url"] = tu
        item = _discovery_enrich_item_ratings(item, cfg)
        out.append(
            {
                "depth": 0,
                "role": "source",
                "group_id": pid,
                "item": item,
                "via_source_raw": via,
                "external": True,
                "evidence": e.get("evidence"),
            }
        )
    return out


def _discovery_prepend_persisted_input_sources_to_chain(
    cfg: "ServerConfig",
    chain: List[Dict[str, Any]],
    merged_edges: List[Dict[str, Any]],
    seed_gid: str,
) -> List[Dict[str, Any]]:
    """
    Graph-only loads skip live PNG infer, so ``input/`` stills only appear if we surface
    persisted ``input:…`` edges onto the oldest indexed ancestor (usually the I2V OG).
    """
    if not chain or (chain and chain[0].get("external")):
        return chain
    root_gid = str(chain[0].get("group_id") or "")
    if not root_gid:
        return chain
    ext_entries = _discovery_input_parent_entries_from_edges(cfg, root_gid, merged_edges)
    if not ext_entries:
        return chain
    return _discovery_splice_external_entries_onto_chain(chain, ext_entries, seed_gid)


def _discovery_candidate_output_relpaths_for_path_hint(cfg: "ServerConfig", raw: str) -> List[str]:
    s0 = str(raw or "").strip().replace("\\", "/")
    if not s0:
        return []
    if "?" in s0:
        s0 = s0.split("?", 1)[0]
    if "#" in s0:
        s0 = s0.split("#", 1)[0]
    s = s0.strip()
    seen: set = set()
    out: List[str] = []

    def push(rel: str) -> None:
        n = _normalize_rel_posix(rel)
        if not n or n in seen:
            return
        seen.add(n)
        out.append(n)

    t = s.lstrip("/")
    push(t)

    # Collapse repeated "output/output/..." segments (Comfy sometimes records extra prefix).
    cur = t
    while "output/output/" in cur:
        cur = cur.replace("output/output/", "output/", 1)
        push(cur)

    # Flat layout: output/og|wip/... may live as og|wip/... under the bind root.
    if t.startswith("output/og/") or t.startswith("output/wip/") or t.startswith("output/experiments/"):
        push(t[len("output/") :])
    if (t.startswith("output/og/") or t.startswith("output/wip/")) and not t.startswith("output/output/"):
        push("output/" + t)

    # Recover missing leading output segments from an og|wip tail.
    low = s.lower()
    for marker in ("og/", "wip/"):
        j = low.rfind(marker)
        if j < 0:
            continue
        tail = s[j:]
        push(tail)
        push("output/" + tail)
        push("output/output/" + tail)

    # Directory / stem hints without extension: try common media suffixes.
    low2 = t.lower()
    has_media_ext = any(low2.endswith(ext) for ext in _DISCOVERY_MEDIA_EXTS)
    if not has_media_ext and ("/" in t) and not t.endswith("/"):
        for ext in sorted(_DISCOVERY_MEDIA_EXTS):
            push(t + ext)

    return out


def _discovery_resolve_existing_media_abs_path(cfg: "ServerConfig", raw: str) -> Optional[Path]:
    s0 = str(raw or "").strip().replace("\\", "/")
    if not s0:
        return None
    if "?" in s0:
        s0 = s0.split("?", 1)[0]
    if "#" in s0:
        s0 = s0.split("#", 1)[0]
    s = s0.strip()

    try:
        p = Path(s)
        if p.is_absolute():
            if p.is_file():
                return p
    except Exception:
        pass

    norm = _normalize_rel_posix(s.lstrip("/"))
    if norm:
        full = _safe_join(cfg.output_root, norm)
        if full is not None and full.is_file():
            return full

    wnorm = _normalize_rel_posix(s.lstrip("/"))
    if wnorm:
        wfull = _safe_join(cfg.workspace_root, wnorm)
        if wfull is not None and wfull.is_file():
            return wfull

    # Comfy input uploads (prompts often cite only the hash filename, or a path missing ``input/``).
    bn = Path(s).name
    if bn and _discovery_lineage_source_string_is_assetish(bn):
        in_full = _safe_join(cfg.workspace_root, _normalize_rel_posix(f"input/{bn}"))
        if in_full is not None and in_full.is_file():
            return in_full
    return None


def _discovery_try_resolve_path_like_to_relpath(cfg: "ServerConfig", raw: str) -> Optional[str]:
    for rel in _discovery_candidate_output_relpaths_for_path_hint(cfg, raw):
        if _discovery_rel_file_exists(cfg, rel):
            return rel
    abs_p = _discovery_resolve_existing_media_abs_path(cfg, raw)
    if abs_p is None or not abs_p.is_file():
        return None
    out_rel = _discovery_abs_path_to_output_relpath(cfg, abs_p)
    if out_rel:
        return out_rel
    in_rel = _discovery_workspace_input_relpath_for_source(cfg, raw)
    if in_rel:
        return in_rel
    try:
        return _normalize_rel_posix(str(abs_p.resolve().relative_to(cfg.workspace_root.resolve())).replace("\\", "/"))
    except Exception:
        return None


def _discovery_lineage_media_kind(summary: Dict[str, Any]) -> str:
    """Coarse label for provenance display: png | video | image | other."""
    for k in ("relpath", "video_relpath", "thumb_relpath"):
        v = summary.get(k)
        if not isinstance(v, str) or not v.strip():
            continue
        ext = Path(v.strip()).suffix.lower()
        if ext in (".mp4", ".webm", ".mov", ".mkv"):
            return "video"
        if ext == ".png":
            return "png"
        if ext in (".jpg", ".jpeg", ".webp", ".gif"):
            return "image"
    return "other"


def _discovery_lineage_file_url(cfg: "ServerConfig", relpath: Any) -> Optional[str]:
    if not isinstance(relpath, str) or not relpath.strip():
        return None
    norm = _normalize_rel_posix(relpath.strip().lstrip("/"))
    if not norm:
        return None
    in_norm = _discovery_workspace_input_relpath_for_source(cfg, norm) or norm
    if _discovery_resolve_media_file(cfg, in_norm) is None:
        return None
    return "/files/" + urllib.parse.quote(in_norm, safe="")


def _discovery_lineage_file_url_unchecked(relpath: Any) -> Optional[str]:
    """Build a ``/files/`` URL without existence probes (lineage lists are latency-sensitive)."""
    if not isinstance(relpath, str) or not relpath.strip():
        return None
    norm = _normalize_rel_posix(relpath.strip().lstrip("/"))
    if not norm:
        return None
    return "/files/" + urllib.parse.quote(norm, safe="")


def _discovery_lineage_summarize_item(
    item: Dict[str, Any],
    cfg: Optional["ServerConfig"] = None,
    *,
    enrich: bool = False,
    ratings_doc: Optional[Dict[str, Any]] = None,
    appetite_doc: Optional[Dict[str, Any]] = None,
    disposition_doc: Optional[Dict[str, Any]] = None,
    work_items_doc: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Compact item summary for lineage UI cards.

    By default skips ratings/appetite/work-item enrichment and disk existence checks:
    enriching every sibling while reloading indexes held the GIL for tens of seconds and
    starved the rest of the Discovery page (ratings, facets, parameters).
    """
    if not isinstance(item, dict):
        out = {
            "group_id": None,
            "name": None,
            "library": None,
            "relpath": None,
            "video_relpath": None,
            "thumb_relpath": None,
        }
        out["media_kind"] = _discovery_lineage_media_kind(out)
        return out
    out = {
        "group_id": item.get("group_id"),
        "name": item.get("name"),
        "library": item.get("library"),
        "relpath": item.get("relpath"),
        "video_relpath": item.get("video_relpath"),
        "thumb_relpath": item.get("thumb_relpath"),
    }
    out["media_kind"] = _discovery_lineage_media_kind(out)
    if cfg is not None:
        # Prefer cheap URLs for graph cards; existence probing is for single-asset detail paths.
        thumb_u = _discovery_lineage_file_url_unchecked(out.get("thumb_relpath"))
        if thumb_u:
            out["thumb_url"] = thumb_u
        primary_u = _discovery_lineage_file_url_unchecked(out.get("relpath"))
        if primary_u:
            out["url"] = primary_u
        video_u = _discovery_lineage_file_url_unchecked(out.get("video_relpath"))
        if video_u:
            out["video_url"] = video_u
        if not thumb_u and primary_u and out.get("media_kind") in ("png", "image"):
            out["thumb_url"] = primary_u
        if enrich:
            out = _discovery_enrich_item_ratings(
                out,
                cfg,
                ratings_doc=ratings_doc,
                appetite_doc=appetite_doc,
                disposition_doc=disposition_doc,
                work_items_doc=work_items_doc,
            )
    return out


def _discovery_lineage_facets_probe_relpath(item: Dict[str, Any]) -> str:
    for k in ("relpath", "video_relpath", "thumb_relpath"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return _normalize_rel_posix(v.strip())
    return ""


def _discovery_index_items_by_group_id(index_obj: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(index_obj, dict):
        return out
    items_list = index_obj.get("items")
    if not isinstance(items_list, list):
        return out
    for it in items_list:
        if not isinstance(it, dict):
            continue
        gid = str(it.get("group_id") or "").strip()
        if gid:
            out[gid] = it
    return out


def _lineage_edge_parent_child(e: Dict[str, Any]) -> Tuple[str, str]:
    return (str(e.get("parent_group_id") or ""), str(e.get("child_group_id") or ""))


def _lineage_merge_edge_dicts(session_edges: List[Dict[str, Any]], graph_edges: List[Any]) -> List[Dict[str, Any]]:
    merged: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for e in session_edges:
        if not isinstance(e, dict) or _discovery_lineage_edge_looks_spurious(e):
            continue
        pid, cid = _lineage_edge_parent_child(e)
        if pid and cid:
            merged[(cid, pid)] = e
    for e in graph_edges:
        if not isinstance(e, dict) or _discovery_lineage_edge_looks_spurious(e):
            continue
        pid, cid = _lineage_edge_parent_child(e)
        if pid and cid:
            merged.setdefault((cid, pid), e)
    return list(merged.values())


def _lineage_parent_group_ids(child_gid: str, merged_edges: List[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    seen: set = set()
    for e in merged_edges:
        if str(e.get("child_group_id") or "") != child_gid:
            continue
        pid = str(e.get("parent_group_id") or "")
        if pid and pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out


def _lineage_longest_ancestor_depth(
    gid: str,
    merged_edges: List[Dict[str, Any]],
    memo: Dict[str, int],
    visiting: Optional[set] = None,
) -> int:
    if not gid:
        return 0
    if gid in memo:
        return memo[gid]
    if visiting is None:
        visiting = set()
    if gid in visiting:
        return 0
    visiting.add(gid)
    parents = _lineage_parent_group_ids(gid, merged_edges)
    if not parents:
        memo[gid] = 0
        visiting.discard(gid)
        return 0
    d = 1 + max(_lineage_longest_ancestor_depth(p, merged_edges, memo, visiting) for p in parents)
    memo[gid] = d
    visiting.discard(gid)
    return d


def _build_lineage_provenance_chain(
    seed_gid: str,
    merged_edges: List[Dict[str, Any]],
    by_gid: Dict[str, Dict[str, Any]],
    cfg: Optional["ServerConfig"] = None,
    *,
    max_hops: int = 32,
) -> List[Dict[str, Any]]:
    """
  Oldest → newest linear chain ending at the seed (e.g. png → video → video).
  When multiple parents exist, follow the parent on the longest upstream path.
    """
    if not seed_gid:
        return []
    memo: Dict[str, int] = {}
    up: List[str] = []
    gid = seed_gid
    seen: set = set()
    while gid and gid not in seen and len(up) < max(1, int(max_hops)):
        seen.add(gid)
        up.append(gid)
        parents = _lineage_parent_group_ids(gid, merged_edges)
        if not parents:
            break
        # Only follow indexed Discovery rows. Synthetic parents (e.g. ``input:<hash>.jpeg`` from
        # ``workspace_input`` edges) are not in ``by_gid``; their files are shown as external
        # sources via ``_discovery_merge_externals_into_provenance_chain``, not as graph hops.
        indexed_parents = [p for p in parents if p and p in by_gid]
        if not indexed_parents:
            break
        if len(indexed_parents) == 1:
            gid = indexed_parents[0]
            continue
        gid = max(indexed_parents, key=lambda p: _lineage_longest_ancestor_depth(p, merged_edges, memo))
    out: List[Dict[str, Any]] = []
    for i, g in enumerate(reversed(up)):
        it = by_gid.get(g)
        summary = _discovery_lineage_summarize_item(it, cfg) if isinstance(it, dict) else {"group_id": g}
        if g == seed_gid:
            role = "seed"
        elif i == 0:
            role = "root"
        else:
            role = "ancestor"
        out.append({"depth": i, "role": role, "group_id": g, "item": summary})
    return out


def _discovery_lineage_graph_views(
    cfg: "ServerConfig",
    idx: Dict[str, Any],
    seed_item: Dict[str, Any],
    seed_gid: str,
    session_edges: List[Dict[str, Any]],
    *,
    peek_group_id: Optional[str],
) -> Dict[str, Any]:
    def _sum(it: Any) -> Dict[str, Any]:
        return _discovery_lineage_summarize_item(it, cfg) if isinstance(it, dict) else {"group_id": None}
    graph_path = _discovery_lineage_edges_path(cfg)
    graph_doc = _discovery_load_lineage_graph(graph_path)
    graph_edges = graph_doc.get("edges")
    if not isinstance(graph_edges, list):
        graph_edges = []
    by_gid = _discovery_index_items_by_group_id(idx)
    merged_edges = _lineage_merge_edge_dicts(session_edges, graph_edges)
    provenance_chain = _build_lineage_provenance_chain(seed_gid, merged_edges, by_gid, cfg)
    provenance_chain = _discovery_prepend_persisted_input_sources_to_chain(
        cfg, provenance_chain, merged_edges, seed_gid
    )
    sibling_rows = _build_lineage_siblings_for_seed(seed_gid, merged_edges, by_gid, cfg)
    descendants_direct_seed = _build_lineage_direct_children(seed_gid, merged_edges, by_gid, cfg)
    descendants_transitive = _build_lineage_descendants_transitive(seed_gid, merged_edges, by_gid, limit=96)
    peek_gid = (peek_group_id or "").strip() or seed_gid
    descendants: List[Dict[str, Any]] = []
    for e in merged_edges:
        if not isinstance(e, dict):
            continue
        if str(e.get("parent_group_id") or "") != peek_gid:
            continue
        cid = str(e.get("child_group_id") or "")
        cit = by_gid.get(cid)
        row = dict(e)
        row["child"] = _sum(cit) if isinstance(cit, dict) else None
        descendants.append(row)
    return {
        "lineage_graph_path": graph_path,
        "merged_edges": merged_edges,
        "merged_edge_count": len(merged_edges),
        "provenance_chain": provenance_chain,
        "ancestry_nav": provenance_chain,
        "siblings": sibling_rows,
        "descendants_direct_seed": descendants_direct_seed,
        "descendants_transitive": descendants_transitive,
        "descendants": descendants,
        "peek_parent_group_id": peek_gid,
        "same_row_members": _discovery_same_row_member_summaries(seed_item),
    }


def _build_lineage_ancestry_nav(
    seed_gid: str,
    merged_edges: List[Dict[str, Any]],
    by_gid: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Best-first walk **up** the merged parent graph (shortest hop count first): seed → parents → grandparents…"""
    out: List[Dict[str, Any]] = []
    if not seed_gid:
        return out
    seen: set = set()
    heap: List[Tuple[int, str]] = [(0, seed_gid)]
    while heap:
        d, gid = heapq.heappop(heap)
        if not gid or gid in seen:
            continue
        seen.add(gid)
        it = by_gid.get(gid)
        summary = _discovery_lineage_summarize_item(it) if isinstance(it, dict) else {"group_id": gid}
        role = "seed" if d == 0 else "ancestor"
        out.append({"depth": d, "role": role, "group_id": gid, "item": summary})
        if d >= 24:
            continue
        for e in merged_edges:
            if str(e.get("child_group_id") or "") != gid:
                continue
            pid = str(e.get("parent_group_id") or "")
            if pid and pid not in seen:
                heapq.heappush(heap, (d + 1, pid))
    return out


def _build_lineage_siblings_for_seed(
    seed_gid: str,
    merged_edges: List[Dict[str, Any]],
    by_gid: Dict[str, Dict[str, Any]],
    cfg: Optional["ServerConfig"] = None,
) -> List[Dict[str, Any]]:
    """Other indexed rows that share a persisted / inferred **parent** with the seed (same hop, different child)."""
    if not seed_gid:
        return []
    parent_ids: set = set()
    for e in merged_edges:
        if str(e.get("child_group_id") or "") != seed_gid:
            continue
        pid = str(e.get("parent_group_id") or "")
        if pid:
            parent_ids.add(pid)
    if not parent_ids:
        return []
    sib_gids: set = set()
    for e in merged_edges:
        pid = str(e.get("parent_group_id") or "")
        cid = str(e.get("child_group_id") or "")
        if pid in parent_ids and cid and cid != seed_gid:
            sib_gids.add(cid)
    rows: List[Dict[str, Any]] = []
    for cid in sorted(sib_gids):
        it = by_gid.get(cid)
        if not isinstance(it, dict):
            continue
        rows.append(
            {
                "group_id": cid,
                "item": _discovery_lineage_summarize_item(it, cfg),
                "shared_parent_group_ids": sorted(
                    pid
                    for pid in parent_ids
                    if any(
                        str(x.get("child_group_id") or "") == cid and str(x.get("parent_group_id") or "") == pid
                        for x in merged_edges
                        if isinstance(x, dict)
                    )
                ),
            }
        )
    return rows


def _build_lineage_direct_children(
    parent_gid: str,
    merged_edges: List[Dict[str, Any]],
    by_gid: Dict[str, Dict[str, Any]],
    cfg: Optional["ServerConfig"] = None,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not parent_gid:
        return out
    seen: set = set()
    for e in merged_edges:
        if str(e.get("parent_group_id") or "") != parent_gid:
            continue
        cid = str(e.get("child_group_id") or "")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        it = by_gid.get(cid)
        row = dict(e) if isinstance(e, dict) else {}
        row["child"] = _discovery_lineage_summarize_item(it, cfg) if isinstance(it, dict) else None
        row["child_group_id"] = cid
        out.append(row)
    return out


def _build_lineage_descendants_transitive(
    seed_gid: str,
    merged_edges: List[Dict[str, Any]],
    by_gid: Dict[str, Dict[str, Any]],
    *,
    limit: int,
) -> List[Dict[str, Any]]:
    """Forward BFS on merged edges (parent → child), capped for UI."""
    if not seed_gid:
        return []
    out: List[Dict[str, Any]] = []
    seen: set = {seed_gid}
    q: collections.deque = collections.deque()
    q.append((seed_gid, 0))
    while q and len(out) < max(1, int(limit)):
        gid, gen = q.popleft()
        for e in merged_edges:
            if str(e.get("parent_group_id") or "") != gid:
                continue
            cid = str(e.get("child_group_id") or "")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            it = by_gid.get(cid)
            row = dict(e) if isinstance(e, dict) else {}
            row["child"] = _discovery_lineage_summarize_item(it) if isinstance(it, dict) else None
            row["child_group_id"] = cid
            row["generation"] = gen + 1
            out.append(row)
            q.append((cid, gen + 1))
    return out


def _discovery_same_row_member_summaries(seed_item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Other files merged into the same Discovery row (e.g. sibling PNG next to MP4)."""
    out: List[Dict[str, Any]] = []
    if not isinstance(seed_item, dict):
        return out
    primary = str(seed_item.get("relpath") or "")
    mems = seed_item.get("members")
    if not isinstance(mems, list):
        return out
    for mm in mems:
        if not isinstance(mm, dict):
            continue
        rel = str(mm.get("relpath") or "").strip()
        if not rel or rel == primary:
            continue
        out.append(
            {
                "relpath": rel,
                "name": mm.get("name"),
                "kind": mm.get("kind"),
            }
        )
    out.sort(key=lambda r: (str(r.get("kind") or ""), str(r.get("relpath") or "")))
    return out


def _discovery_compute_workspace_input_lineage_graph_only(
    cfg: "ServerConfig",
    idx: Dict[str, Any],
    input_rel: str,
    *,
    peek_group_id: Optional[str],
    scan_index: bool = True,
) -> Dict[str, Any]:
    """Lineage for a Comfy ``input/`` upload: descendants are indexed rows that cite this file in embedded prompts."""
    norm = _discovery_workspace_input_relpath_for_source(cfg, input_rel) or _normalize_rel_posix(input_rel.strip())
    if not norm or _discovery_resolve_media_file(cfg, norm) is None:
        return {"ok": False, "error": "input_file_missing", "detail": input_rel}

    seed_item = _discovery_synthetic_library_item_for_workspace_media(cfg, norm)
    if not isinstance(seed_item, dict):
        return {"ok": False, "error": "input_file_missing", "detail": norm}

    seed_gid = _discovery_workspace_input_group_id(norm)
    session_edges: List[Dict[str, Any]] = _discovery_scan_index_for_input_children(cfg, idx, norm) if scan_index else []

    graph_path = _discovery_lineage_edges_path(cfg)
    graph_doc = _discovery_load_lineage_graph(graph_path)
    graph_edges = graph_doc.get("edges")
    if not isinstance(graph_edges, list):
        graph_edges = []
    by_gid = _discovery_index_items_by_group_id(idx)
    merged_edges = _lineage_merge_edge_dicts(session_edges, graph_edges)

    descendants_direct_seed = _build_lineage_direct_children(seed_gid, merged_edges, by_gid, cfg)
    descendants_transitive = _build_lineage_descendants_transitive(seed_gid, merged_edges, by_gid, limit=96)
    peek_gid = (peek_group_id or "").strip() or seed_gid
    descendants: List[Dict[str, Any]] = []
    for e in merged_edges:
        if not isinstance(e, dict):
            continue
        if str(e.get("parent_group_id") or "") != peek_gid:
            continue
        cid = str(e.get("child_group_id") or "")
        cit = by_gid.get(cid)
        row = dict(e)
        row["child"] = _discovery_lineage_summarize_item(cit, cfg) if isinstance(cit, dict) else None
        descendants.append(row)

    seed_summary = dict(seed_item)
    seed_summary["media_kind"] = _discovery_lineage_media_kind(seed_summary)
    if not seed_summary.get("thumb_url"):
        tu = _discovery_lineage_file_url(cfg, norm)
        if tu:
            seed_summary["thumb_url"] = tu
            seed_summary["url"] = tu

    provenance_chain = [
        {
            "depth": 0,
            "role": "seed",
            "group_id": seed_gid,
            "item": seed_summary,
            "external": True,
        }
    ]

    return {
        "ok": True,
        "query_relpath": norm,
        "discovery_index_path": str(cfg.discovery_index_path),
        "lineage_graph_path": str(graph_path),
        "graph_only": True,
        "infer_parents": False,
        "max_depth": 0,
        "persist": False,
        "persisted_new_edges": 0,
        "peek_parent_group_id": peek_gid,
        "seed": seed_summary,
        "provenance_chain": provenance_chain,
        "ancestry_nav": provenance_chain,
        "siblings": [],
        "descendants_direct_seed": descendants_direct_seed,
        "descendants_transitive": descendants_transitive,
        "same_row_members": [],
        "expansions": [],
        "edges": session_edges,
        "merged_edge_count": len(merged_edges),
        "unresolved_source_strings": [],
        "descendants": descendants,
        "external_sources": [],
        "errors": [],
        "notes": [
            "Workspace input seed: descendants are indexed og/wip rows whose embedded prompt cites this file.",
            f"index_scan_children={len(session_edges)} merged_graph_edges={len(merged_edges)}.",
            "Re-run backfill with persist or open lineage with persist=1 on indexed children to store workspace_input edges in discovery_lineage_edges.json.",
        ],
    }


def _discovery_compute_asset_lineage_graph_only(
    cfg: "ServerConfig",
    idx: Dict[str, Any],
    seed_item: Dict[str, Any],
    seed_gid: str,
    rel: str,
    *,
    max_depth: int,
    peek_group_id: Optional[str],
    infer_parents: bool,
    infer_children: bool = False,
) -> Dict[str, Any]:
    session_edges: List[Dict[str, Any]] = []
    external_sources: List[Dict[str, Any]] = []
    infer_errors: List[str] = []
    child_scan_edges: List[Dict[str, Any]] = []
    if infer_parents:
        try:
            session_edges, external_sources = _discovery_infer_lineage_session_edges(
                cfg, idx, seed_item, max_depth=min(2, max(1, max_depth))
            )
        except Exception as e:
            infer_errors.append(f"infer_parents_failed:{e}")
    if infer_children:
        try:
            child_scan_edges = _discovery_scan_index_for_media_children(cfg, idx, seed_item)
            session_edges = list(session_edges) + list(child_scan_edges)
        except Exception as e:
            infer_errors.append(f"infer_children_failed:{e}")

    views = _discovery_lineage_graph_views(cfg, idx, seed_item, seed_gid, session_edges, peek_group_id=peek_group_id)
    provenance_chain = views["provenance_chain"]
    if external_sources:
        provenance_chain = _discovery_merge_externals_into_provenance_chain(
            provenance_chain, external_sources, seed_gid, cfg
        )

    ratings_path = _discovery_ratings_index_path(cfg)
    ratings_doc = _discovery_load_ratings_index(cfg)
    notes = [
        (
            "Merged graph edges with live parent inference from this asset's embedded prompt (infer_parents=1)."
            if infer_parents
            else "Read persisted discovery_lineage_edges.json only (infer_parents=0)."
        ),
        "provenance_chain is oldest → current; external/workspace inputs appear as source before the seed.",
    ]
    if infer_children:
        notes.append(
            f"Forward-fill via citation index "
            f"(infer_children=1); candidates_verified={len(child_scan_edges)} "
            f"db={_discovery_citations_db_path(cfg)}."
        )
    payload: Dict[str, Any] = {
        "ok": True,
        "query_relpath": rel,
        "discovery_index_path": str(cfg.discovery_index_path),
        "lineage_graph_path": str(views["lineage_graph_path"]),
        "graph_only": True,
        "infer_parents": bool(infer_parents),
        "infer_children": bool(infer_children),
        "max_depth": max_depth,
        "persist": False,
        "persisted_new_edges": 0,
        "peek_parent_group_id": views["peek_parent_group_id"],
        "seed": _discovery_lineage_summarize_item(seed_item, cfg),
        "provenance_chain": provenance_chain,
        "ancestry_nav": provenance_chain,
        "siblings": views["siblings"],
        "descendants_direct_seed": views["descendants_direct_seed"],
        "descendants_transitive": views["descendants_transitive"],
        "same_row_members": views["same_row_members"],
        "expansions": [],
        "edges": session_edges,
        "merged_edge_count": views["merged_edge_count"],
        "unresolved_source_strings": [],
        "descendants": views["descendants"],
        "external_sources": external_sources,
        "child_scan_edges": child_scan_edges,
        "errors": infer_errors,
        "notes": notes,
    }
    if ratings_doc:
        payload["ratings_index_path"] = str(ratings_path)
    return payload


def _discovery_compute_asset_lineage(
    cfg: "ServerConfig",
    idx: Dict[str, Any],
    seed_rel: str,
    *,
    max_depth: int,
    persist: bool,
    peek_group_id: Optional[str],
    graph_only: bool = False,
    infer_parents: bool = True,
    infer_children: bool = False,
) -> Dict[str, Any]:
    rel = _normalize_rel_posix(seed_rel.strip())
    if not rel:
        return {"ok": False, "error": "missing_or_bad_relpath"}

    seed_item = _discovery_item_for_relpath(idx, rel)
    if not isinstance(seed_item, dict):
        in_rel = _discovery_workspace_input_relpath_for_source(cfg, rel)
        if in_rel and _discovery_resolve_media_file(cfg, in_rel):
            if graph_only:
                return _discovery_compute_workspace_input_lineage_graph_only(
                    cfg,
                    idx,
                    in_rel,
                    peek_group_id=peek_group_id,
                )
            scan_edges = _discovery_scan_index_for_input_children(cfg, idx, in_rel)
            added = 0
            if persist and scan_edges:
                ts = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                rows = [{**e, "updated_at": ts} for e in scan_edges]
                added = _discovery_persist_lineage_edge_rows(cfg, rows)
            payload = _discovery_compute_workspace_input_lineage_graph_only(
                cfg, idx, in_rel, peek_group_id=peek_group_id
            )
            if isinstance(payload, dict):
                payload["persist"] = bool(persist)
                payload["persisted_new_edges"] = int(added)
            return payload
        # Natural heal: tip the file into Discovery if it exists under og/wip.
        ensured = False
        try:
            ens = _discovery_upsert_relpath(cfg, rel)
            if ens.get("ok"):
                ensured = True
                idx2 = _load_discovery_index_disk(cfg.discovery_index_path)
                if isinstance(idx2, dict):
                    idx = idx2
                    seed_item = _discovery_item_for_relpath(idx, rel)
        except Exception:
            ensured = False
        if not isinstance(seed_item, dict):
            return {"ok": False, "error": "not_in_discovery_index", "detail": rel}
        # Fall through with refreshed seed; annotate below on success path.
        _ensured_flag = ensured
    else:
        _ensured_flag = False

    seed_gid = str(seed_item.get("group_id") or "")
    max_depth = max(0, min(int(max_depth), 12))

    if graph_only:
        payload = _discovery_compute_asset_lineage_graph_only(
            cfg,
            idx,
            seed_item,
            seed_gid,
            rel,
            max_depth=max_depth,
            peek_group_id=peek_group_id,
            infer_parents=infer_parents,
            infer_children=infer_children,
        )
        if _ensured_flag and isinstance(payload, dict) and payload.get("ok"):
            payload["discovery_index_ensured"] = True
        return payload

    processed_groups: set = set()
    queue: collections.deque = collections.deque()
    if infer_parents:
        queue.append((seed_item, 0))

    edges: List[Dict[str, Any]] = []
    expansions: List[Dict[str, Any]] = []
    unresolved: List[str] = []
    unresolved_seen: set = set()
    errors: List[str] = []

    while queue:
        item, depth = queue.popleft()
        if not isinstance(item, dict):
            errors.append("bad_queue_item")
            continue
        gid = str(item.get("group_id") or "")
        if not gid or gid in processed_groups:
            continue
        processed_groups.add(gid)

        probe_rel = _discovery_lineage_facets_probe_relpath(item)
        if not probe_rel:
            errors.append(f"missing_probe_relpath:{gid}")
            continue
        try:
            facets = _discovery_build_workflow_facets_payload(cfg, probe_rel)
        except Exception as e:
            errors.append(f"facets_failed:{gid}:{e}")
            continue
        if not isinstance(facets, dict) or not facets.get("ok"):
            errors.append(f"facets_not_ok:{gid}:{facets.get('error') if isinstance(facets, dict) else type(facets).__name__}")
            continue

        strings = _discovery_extract_source_path_strings_from_facets_payload(facets if isinstance(facets, dict) else {})
        parent_items: Dict[str, Dict[str, Any]] = {}
        external_sources: List[Dict[str, Any]] = []
        unresolved_for_node: List[str] = []

        for s in strings:
            pit, edge_rel, ext = _discovery_resolve_lineage_parent_for_source(
                cfg, idx, s, child_gid=gid, child_item=item
            )
            if ext is not None:
                external_sources.append(ext)
                in_rel = ext.get("workspace_relpath")
                if not isinstance(in_rel, str) or not in_rel.strip():
                    in_rel = _discovery_workspace_input_relpath_for_source(cfg, s)
                if isinstance(in_rel, str) and in_rel.strip():
                    edges.append(
                        {
                            "child_group_id": gid,
                            "parent_group_id": _discovery_workspace_input_group_id(in_rel),
                            "via_source_raw": s,
                            "resolved_parent_relpath": _normalize_rel_posix(in_rel.strip()),
                            "evidence": "workspace_input",
                        }
                    )
                continue
            if pit is None:
                if _discovery_lineage_source_string_is_assetish(s) and s not in unresolved_seen:
                    unresolved_for_node.append(s)
                continue
            pgid = str(pit.get("group_id") or "")
            if not pgid or pgid == gid:
                continue
            parent_items[pgid] = pit
            edges.append(
                {
                    "child_group_id": gid,
                    "parent_group_id": pgid,
                    "via_source_raw": s,
                    "resolved_parent_relpath": edge_rel,
                    "evidence": "png_prompt_source_path",
                }
            )

        for s in unresolved_for_node:
            if s not in unresolved_seen:
                unresolved_seen.add(s)
                unresolved.append(s)

        expansions.append(
            {
                "depth": depth,
                "item": _discovery_lineage_summarize_item(item, cfg),
                "parent_group_ids": sorted(parent_items.keys()),
                "parents": [_discovery_lineage_summarize_item(pit, cfg) for pit in parent_items.values() if isinstance(pit, dict)],
                "external_sources": external_sources,
                "source_strings_seen": len(strings),
            }
        )

        if depth < max_depth:
            for pit in parent_items.values():
                if not isinstance(pit, dict):
                    continue
                pgid = str(pit.get("group_id") or "")
                if pgid in processed_groups:
                    continue
                queue.append((pit, depth + 1))

    child_scan_edges: List[Dict[str, Any]] = []
    if infer_children:
        try:
            child_scan_edges = _discovery_scan_index_for_media_children(cfg, idx, seed_item)
            edges.extend(child_scan_edges)
        except Exception as e:
            errors.append(f"infer_children_failed:{e}")

    added = 0
    if persist and edges:
        rows: List[Dict[str, Any]] = []
        ts = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for e in edges:
            rows.append(
                {
                    "child_group_id": e.get("child_group_id"),
                    "parent_group_id": e.get("parent_group_id"),
                    "via_source_raw": e.get("via_source_raw"),
                    "resolved_parent_relpath": e.get("resolved_parent_relpath"),
                    "evidence": e.get("evidence"),
                    "updated_at": ts,
                }
            )
        added = _discovery_persist_lineage_edge_rows(cfg, rows)

    views = _discovery_lineage_graph_views(cfg, idx, seed_item, seed_gid, edges, peek_group_id=peek_group_id)
    by_gid = _discovery_index_items_by_group_id(idx)
    merged_edges = views["merged_edges"]
    descendants_transitive = _build_lineage_descendants_transitive(seed_gid, merged_edges, by_gid, limit=96)

    notes = [
        "Parent links are inferred from LoadImage/LoadVideo* nodes in API-format prompts "
        "that feed a saved output (VHS_VideoCombine/Save* with save_output=True). "
        "Orphan loaders and preview-only branches are omitted.",
        "provenance_chain is oldest → current; siblings / descendants read the merged session + discovery_lineage_edges.json graph.",
        "Use graph_only=1 after backfill for fast panel loads without re-probing PNG metadata.",
    ]
    if infer_children:
        notes.append(
            f"Forward-fill via citation index "
            f"(infer_children=1); children_found={len(child_scan_edges)} "
            f"db={_discovery_citations_db_path(cfg)}."
        )

    return {
        "ok": True,
        "query_relpath": rel,
        "discovery_index_path": str(cfg.discovery_index_path),
        "discovery_index_ensured": bool(_ensured_flag),
        "lineage_graph_path": str(views["lineage_graph_path"]),
        "graph_only": False,
        "infer_parents": bool(infer_parents),
        "infer_children": bool(infer_children),
        "max_depth": max_depth,
        "persist": bool(persist),
        "persisted_new_edges": int(added),
        "peek_parent_group_id": views["peek_parent_group_id"],
        "seed": _discovery_lineage_summarize_item(seed_item, cfg),
        "provenance_chain": views["provenance_chain"],
        "ancestry_nav": views["ancestry_nav"],
        "siblings": views["siblings"],
        "descendants_direct_seed": views["descendants_direct_seed"],
        "descendants_transitive": descendants_transitive,
        "same_row_members": views["same_row_members"],
        "expansions": expansions,
        "edges": edges,
        "merged_edge_count": views["merged_edge_count"],
        "unresolved_source_strings": unresolved,
        "descendants": views["descendants"],
        "child_scan_edges": child_scan_edges,
        "errors": errors,
        "notes": notes,
    }


def _discovery_sidecars_for_rel(cfg: "ServerConfig", relpath: Any) -> List[str]:
    if not isinstance(relpath, str) or not relpath.strip():
        return []
    norm = _normalize_rel_posix(relpath.strip())
    if not norm:
        return []
    full = _safe_join(cfg.output_root, norm)
    if full is None:
        return []
    parent = full.parent
    if not parent.is_dir():
        return []
    stem = full.stem
    out: List[str] = []
    try:
        for sibling in parent.iterdir():
            if sibling.stem != stem:
                continue
            if sibling.suffix.lower() not in _DISCOVERY_SIDECAR_EXTS:
                continue
            try:
                rel = sibling.resolve().relative_to(cfg.output_root.resolve()).as_posix()
            except Exception:
                continue
            out.append(rel)
    except Exception:
        return []
    return sorted(out)


def _discovery_sample_append(xs: List[Dict[str, Any]], item: Dict[str, Any], **extra: Any) -> None:
    if len(xs) >= _DISCOVERY_HEALTH_SAMPLE_LIMIT:
        return
    row = {
        "group_id": item.get("group_id"),
        "name": item.get("name"),
        "relpath": item.get("relpath"),
        "video_relpath": item.get("video_relpath"),
        "thumb_relpath": item.get("thumb_relpath"),
    }
    row.update(extra)
    xs.append(row)


def _build_discovery_index_health(
    cfg: "ServerConfig",
    *,
    previous_index: Optional[Dict[str, Any]],
    current_index: Dict[str, Any],
    reason: str,
    from_cache: bool,
) -> Dict[str, Any]:
    current_items = _discovery_index_item_map(current_index)
    previous_items = _discovery_index_item_map(previous_index)

    missing_primary_sample: List[Dict[str, Any]] = []
    missing_video_sample: List[Dict[str, Any]] = []
    missing_thumb_sample: List[Dict[str, Any]] = []
    orphan_sidecar_sample: List[Dict[str, Any]] = []
    orphan_thumb_sample: List[Dict[str, Any]] = []
    removed_sample: List[Dict[str, Any]] = []

    missing_primary = 0
    missing_video = 0
    missing_thumb = 0
    orphan_sidecar = 0
    orphan_thumb = 0

    stale_reference_items = previous_items if previous_items else current_items
    for item in stale_reference_items.values():
        primary_exists = _discovery_rel_file_exists(cfg, item.get("relpath"))
        video_exists = _discovery_rel_file_exists(cfg, item.get("video_relpath"))
        thumb_exists = _discovery_rel_file_exists(cfg, item.get("thumb_relpath"))
        if item.get("relpath") and not primary_exists:
            missing_primary += 1
            _discovery_sample_append(missing_primary_sample, item)
        if item.get("video_relpath") and not video_exists:
            missing_video += 1
            _discovery_sample_append(missing_video_sample, item)
        if item.get("thumb_relpath") and not thumb_exists:
            missing_thumb += 1
            _discovery_sample_append(missing_thumb_sample, item)
        if item.get("thumb_relpath") and thumb_exists and item.get("video_relpath") and not video_exists:
            orphan_thumb += 1
            _discovery_sample_append(orphan_thumb_sample, item)
        if (item.get("relpath") and not primary_exists) or (item.get("video_relpath") and not video_exists):
            sidecars = _discovery_sidecars_for_rel(cfg, item.get("relpath") or item.get("video_relpath") or item.get("thumb_relpath"))
            if sidecars:
                orphan_sidecar += 1
                _discovery_sample_append(orphan_sidecar_sample, item, sidecars=sidecars)

    removed_keys = sorted(set(previous_items.keys()) - set(current_items.keys()))
    for key in removed_keys[:_DISCOVERY_HEALTH_SAMPLE_LIMIT]:
        item = previous_items[key]
        _discovery_sample_append(removed_sample, item)

    return {
        "version": 1,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason": reason,
        "from_cache": from_cache,
        "index_path": str(cfg.discovery_index_path),
        "previous_updated_at": previous_index.get("updated_at") if isinstance(previous_index, dict) else None,
        "current_updated_at": current_index.get("updated_at"),
        "previous_item_count": len(previous_items) if previous_items else None,
        "current_item_count": len(current_items),
        "summary": {
            "missing_primary": missing_primary,
            "missing_video": missing_video,
            "missing_thumb": missing_thumb,
            "orphan_sidecar": orphan_sidecar,
            "orphan_thumb": orphan_thumb,
            "removed_since_previous_index": len(removed_keys),
        },
        "samples": {
            "missing_primary": missing_primary_sample,
            "missing_video": missing_video_sample,
            "missing_thumb": missing_thumb_sample,
            "orphan_sidecar": orphan_sidecar_sample,
            "orphan_thumb": orphan_thumb_sample,
            "removed_since_previous_index": removed_sample,
        },
    }


def _load_discovery_health_disk(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        obj = _read_json(path)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _json_loads_maybe(value: Any, default: Any) -> Any:
    if not isinstance(value, str) or not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _factory_row_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}


_FACTORY_ASSET_PREVIEW_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm"}
_FACTORY_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_FACTORY_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}
_FACTORY_WORKFLOW_EXTS = {".json"}


def _factory_asset_preview_url(item: Dict[str, Any]) -> Optional[str]:
    asset_id = _safe_int(item.get("id"))
    raw_path = item.get("path")
    if asset_id is None or not isinstance(raw_path, str) or not raw_path.strip():
        return None
    suffix = Path(raw_path).suffix.lower()
    if suffix not in _FACTORY_ASSET_PREVIEW_EXTS:
        return None
    return f"/factory-assets/{asset_id}/{urllib.parse.quote(Path(raw_path).name, safe='')}"


def _factory_utc_now() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat(timespec="seconds")


def _factory_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _factory_media_type_for_path(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext in _FACTORY_IMAGE_EXTS:
        return "image"
    if ext in _FACTORY_VIDEO_EXTS:
        return "video"
    if ext == ".json":
        return "json"
    return "unknown"


def _factory_role_for_media_type(media_type: str) -> str:
    if media_type == "image":
        return "source_image"
    if media_type == "video":
        return "source_video"
    return "source_asset"


def _factory_browse_roots(ws: Path, output_root: Path) -> List[Dict[str, Any]]:
    return [
        {
            "id": "input",
            "label": "ComfyUI input",
            "kind": "asset",
            "path": str((ws / "input").resolve()),
        },
        {
            "id": "output",
            "label": "ComfyUI output",
            "kind": "asset",
            "path": str(output_root.resolve()),
        },
        {
            "id": "workflows",
            "label": "ComfyUI workflows",
            "kind": "workflow",
            "path": str((ws / "comfyui_user" / "default" / "workflows").resolve()),
        },
    ]


def _factory_browse_root_by_id(cfg: "ServerConfig", root_id: str) -> Optional[Dict[str, Any]]:
    for root in cfg.factory_browse_roots:
        if root.get("id") == root_id:
            return root
    return None


def _factory_browse_entry_url(root_id: str, relpath: str, path: Path) -> Optional[str]:
    suffix = path.suffix.lower()
    if suffix not in _FACTORY_ASSET_PREVIEW_EXTS:
        return None
    sp = urllib.parse.urlencode({"root": root_id, "relpath": relpath})
    return f"/api/workflow-explorer/factory/browse-file?{sp}"


def _factory_browse_file_allowed(path: Path, kind: str, media_type_filter: str = "all") -> bool:
    suffix = path.suffix.lower()
    if kind == "workflow":
        return suffix in _FACTORY_WORKFLOW_EXTS
    if kind == "asset":
        if media_type_filter == "image":
            return suffix in _FACTORY_IMAGE_EXTS
        if media_type_filter == "video":
            return suffix in _FACTORY_VIDEO_EXTS
        return suffix in _FACTORY_IMAGE_EXTS or suffix in _FACTORY_VIDEO_EXTS
    return suffix in _FACTORY_IMAGE_EXTS or suffix in _FACTORY_VIDEO_EXTS or suffix in _FACTORY_WORKFLOW_EXTS


def _factory_get_bucket(con: sqlite3.Connection, bucket_id: int, bucket_type: str) -> Optional[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM buckets WHERE id = ? AND bucket_type = ?",
        (bucket_id, bucket_type),
    ).fetchone()


def _factory_workflow_contract(workflow: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    inputs: set[str] = set()
    outputs: set[str] = set()
    input_nodes: List[Dict[str, Any]] = []
    output_nodes: List[Dict[str, Any]] = []

    if not _looks_like_comfy_ui_workflow(workflow):
        return {"media_types": ["unknown"], "nodes": []}, {"media_types": ["unknown"], "nodes": []}

    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type") or node.get("class_type") or "")
        mode = node.get("mode", 0)
        title = node.get("title")
        if node_type in {"LoadImage", "LoadImageWithFilename|pysssss"}:
            inputs.add("image")
            input_nodes.append({"id": node.get("id"), "type": node_type, "title": title})
        if node_type in {"VHS_LoadVideo", "VHS_LoadVideoPath", "VHS_LoadVideoFFmpeg", "VHS_LoadVideoFFmpegPath"}:
            inputs.add("video")
            input_nodes.append({"id": node.get("id"), "type": node_type, "title": title})
        if mode in (2, 4):
            continue
        if node_type == "VHS_VideoCombine":
            widgets = node.get("widgets_values")
            if not isinstance(widgets, dict) or widgets.get("save_output") is not False:
                outputs.add("video")
                output_nodes.append({"id": node.get("id"), "type": node_type, "title": title})
        if node_type == "SaveImage":
            outputs.add("image")
            output_nodes.append({"id": node.get("id"), "type": node_type, "title": title})

    return {
        "media_types": sorted(inputs) or ["unknown"],
        "nodes": input_nodes,
    }, {
        "media_types": sorted(outputs) or ["unknown"],
        "nodes": output_nodes,
    }


def _factory_graph_fingerprint(workflow: Any) -> str:
    if not _looks_like_comfy_ui_workflow(workflow):
        return hashlib.sha256(json.dumps(workflow, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    nodes = []
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        nodes.append(
            {
                "id": node.get("id"),
                "type": node.get("type") or node.get("class_type"),
                "mode": node.get("mode", 0),
            }
        )
    links = []
    for link in workflow.get("links") or []:
        if isinstance(link, list):
            links.append(link[:6])
        elif isinstance(link, dict):
            links.append({k: link.get(k) for k in ("origin_id", "origin_slot", "target_id", "target_slot", "type")})
    payload = {"nodes": nodes, "links": links}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _resolve_factory_asset_file(cfg: "ServerConfig", raw_path: str) -> Path:
    raw_path = raw_path.strip()
    candidates: List[Path] = []
    original = Path(raw_path)
    if original.is_absolute():
        candidates.append(original)
    else:
        joined = _safe_join(cfg.output_root, raw_path)
        if joined is not None:
            candidates.append(joined)
        joined_ws = _safe_join(cfg.workspace_root, raw_path)
        if joined_ws is not None:
            candidates.append(joined_ws)

    normalized = raw_path.replace("\\", "/")
    marker = "/comfyui-runpod-data/"
    if marker in normalized:
        rel = normalized.split(marker, 1)[1]
        joined_ws = _safe_join(cfg.workspace_root, rel)
        if joined_ws is not None:
            candidates.append(joined_ws)

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return candidates[0] if candidates else Path(raw_path)


def _load_factory_summary(db_path: Path) -> Dict[str, Any]:
    if not db_path.exists():
        return {
            "ok": False,
            "error": "factory_db_missing",
            "db_path": str(db_path),
            "buckets": [],
            "run_plans": [],
        }
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        buckets: List[Dict[str, Any]] = []
        for row in con.execute(
            """
            SELECT
                b.*,
                (SELECT COUNT(*) FROM asset_items ai WHERE ai.bucket_id = b.id) AS asset_count,
                (SELECT COUNT(*) FROM workflow_items wi WHERE wi.bucket_id = b.id) AS workflow_count
            FROM buckets b
            ORDER BY b.bucket_type, b.name
            """
        ):
            item = _factory_row_dict(row)
            item["metadata"] = _json_loads_maybe(item.pop("metadata_json", ""), {})
            buckets.append(item)

        asset_rows = [
            _factory_row_dict(row)
            for row in con.execute(
                """
                SELECT ai.*, b.name AS bucket_name
                FROM asset_items ai
                JOIN buckets b ON b.id = ai.bucket_id
                ORDER BY b.name, ai.path
                """
            )
        ]
        workflow_rows = [
            _factory_row_dict(row)
            for row in con.execute(
                """
                SELECT wi.*, b.name AS bucket_name
                FROM workflow_items wi
                JOIN buckets b ON b.id = wi.bucket_id
                ORDER BY b.name, wi.path
                """
            )
        ]
        for item in asset_rows:
            item["metadata"] = _json_loads_maybe(item.pop("metadata_json", ""), {})
            item["url"] = _factory_asset_preview_url(item)
        for item in workflow_rows:
            item["metadata"] = _json_loads_maybe(item.pop("metadata_json", ""), {})
            item["input_contract"] = _json_loads_maybe(item.pop("input_contract_json", ""), {})
            item["output_contract"] = _json_loads_maybe(item.pop("output_contract_json", ""), {})

        run_plans: List[Dict[str, Any]] = []
        for row in con.execute(
            """
            SELECT
                rp.*,
                ib.name AS input_bucket_name,
                wb.name AS workflow_bucket_name,
                ob.name AS output_bucket_name
            FROM run_plans rp
            JOIN buckets ib ON ib.id = rp.input_bucket_id
            JOIN buckets wb ON wb.id = rp.workflow_bucket_id
            JOIN buckets ob ON ob.id = rp.output_bucket_id
            ORDER BY rp.name
            """
        ):
            plan = _factory_row_dict(row)
            plan["rules"] = _json_loads_maybe(plan.pop("rules_json", ""), {})
            plan["metadata"] = _json_loads_maybe(plan.pop("metadata_json", ""), {})
            plan["input_assets"] = [x for x in asset_rows if x.get("bucket_id") == plan["input_bucket_id"]]
            plan["workflow_items"] = [x for x in workflow_rows if x.get("bucket_id") == plan["workflow_bucket_id"]]
            plan["output_assets"] = [x for x in asset_rows if x.get("bucket_id") == plan["output_bucket_id"]]
            plan["planned_jobs"] = []
            for job in con.execute(
                """
                SELECT * FROM planned_jobs
                WHERE run_plan_id = ?
                ORDER BY job_key
                """,
                (plan["id"],),
            ):
                j = _factory_row_dict(job)
                j["metadata"] = _json_loads_maybe(j.pop("metadata_json", ""), {})
                plan["planned_jobs"].append(j)
            run_plans.append(plan)

        return {
            "ok": True,
            "db_path": str(db_path),
            "buckets": buckets,
            "assets": asset_rows,
            "workflows": workflow_rows,
            "run_plans": run_plans,
        }
    finally:
        con.close()


def _now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _json_response(handler: BaseHTTPRequestHandler, code: int, obj: Any) -> None:
    raw = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def _text_response(handler: BaseHTTPRequestHandler, code: int, text: str, content_type: str = "text/plain; charset=utf-8") -> None:
    raw = text.encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def _file_relpath_for_api(output_root: Path, wip_root: Path, abs_path: Path) -> str:
    """
    POSIX relpath from output_root for API + /files (e.g. output/output/og/2026-04-10/foo.mp4).
    Falls back through wip_root if abs_path is not under output_root (unusual mounts).
    """
    try:
        return str(abs_path.resolve().relative_to(output_root.resolve())).replace("\\", "/")
    except ValueError:
        pass
    try:
        wr = str(wip_root.resolve().relative_to(output_root.resolve())).replace("\\", "/")
        sub = str(abs_path.resolve().relative_to(wip_root.resolve())).replace("\\", "/")
        if sub in ("", "."):
            return wr
        return f"{wr}/{sub}"
    except ValueError:
        return abs_path.name.replace("\\", "/")


def _discovery_abs_allowed_for_library(cfg: "ServerConfig", abs_p: Path, lib: str) -> bool:
    og_root, wip_root = _og_wip_library_roots(cfg)
    try:
        r = abs_p.resolve()
        ogr = og_root.resolve()
        wipr = wip_root.resolve()
    except Exception:
        return False
    under_og = r == ogr or ogr in r.parents
    under_wip = r == wipr or wipr in r.parents
    if lib == "og":
        return under_og
    if lib == "wip":
        return under_wip
    return under_og or under_wip


def _discovery_resolve_embed_png_abs(
    cfg: "ServerConfig", q: Dict[str, List[str]]
) -> Tuple[Optional[Path], Optional[str]]:
    """
    Pick a PNG under output_root (og/wip) that may carry Comfy workflow / prompt tEXt chunks.
    Returns (absolute_path, api_relpath_for_response) or (None, None).
    """
    thumb = (q.get("thumb_relpath") or [""])[0].strip()
    video = (q.get("video_relpath") or [""])[0].strip()
    primary = (q.get("relpath") or [""])[0].strip()
    lib = (q.get("library") or [""])[0].strip().lower()
    if lib not in ("og", "wip", "all"):
        lib = "all"

    seen: set[str] = set()
    cands: List[str] = []

    def push(rel: str) -> None:
        rel2 = _normalize_rel_posix(rel)
        if not rel2 or rel2 in seen:
            return
        seen.add(rel2)
        cands.append(rel2)

    if thumb:
        push(thumb)

    def sibling_png_from_media(rel: str) -> None:
        rel2 = _normalize_rel_posix(rel)
        if not rel2:
            return
        parent = str(Path(rel2).parent).replace("\\", "/")
        stem = Path(rel2).stem
        if parent and parent != ".":
            push(f"{parent}/{stem}.png")
        else:
            push(f"{stem}.png")

    if video:
        sibling_png_from_media(video)
    if primary and primary != video:
        sibling_png_from_media(primary)
    if primary.lower().endswith(".png"):
        push(primary)

    for rel in cands:
        abs_p = _safe_join(cfg.output_root, rel)
        if abs_p is None or not abs_p.is_file() or abs_p.suffix.lower() != ".png":
            continue
        if not _discovery_abs_allowed_for_library(cfg, abs_p, lib):
            continue
        try:
            _read_png_text_chunks(abs_p)
        except Exception:
            continue
        rel_api = _file_relpath_for_api(cfg.output_root, cfg.wip_root, abs_p)
        return abs_p, rel_api
    return None, None


def _resolve_wip_root(ws: Path, output_root: Path, override: str) -> Path:
    """
    Root directory for GET /api/wip (date folders + MP4 listing).
    Default: <output_root>/wip (flat), falling back to legacy <output_root>/output/wip.
    override: absolute path, or path relative to workspace_root (e.g. output/og).
    """
    o = (override or "").strip()
    if not o:
        return _prefer_flat_library_dir(output_root, "wip")
    p = Path(o)
    if p.is_absolute():
        return p.resolve()
    return (ws / p).resolve()


def _normalize_rel_posix(p: str) -> str:
    """
    Normalize a URL path fragment to a safe POSIX relative path (no leading slash, no ..).
    Returns empty string if invalid.
    """
    p = p.replace("\\", "/")
    p = p.lstrip("/")
    p2 = posixpath.normpath(p)
    if p2 in ("", "."):
        return ""
    if p2.startswith("../") or p2 == "..":
        return ""
    return p2


def _safe_join(root: Path, rel_posix: str) -> Optional[Path]:
    rel_posix = _normalize_rel_posix(rel_posix)
    if not rel_posix:
        return None
    candidate = root.joinpath(*rel_posix.split("/"))
    try:
        resolved = candidate.resolve()
        root_resolved = root.resolve()
    except Exception:
        return None
    if root_resolved == resolved or root_resolved in resolved.parents:
        return resolved
    return None


def _parse_range_header(range_header: Optional[str], size: int) -> Optional[Tuple[int, int]]:
    """
    Parse a single HTTP Range header of form: bytes=start-end
    Returns (start, end) inclusive, or None if unsupported/invalid.
    """
    if not range_header:
        return None
    m = re.match(r"^\s*bytes=(\d*)-(\d*)\s*$", range_header)
    if not m:
        return None
    a, b = m.group(1), m.group(2)
    if a == "" and b == "":
        return None
    if a == "":
        try:
            suf = int(b)
        except Exception:
            return None
        if suf <= 0:
            return None
        if suf > size:
            suf = size
        return (size - suf, size - 1)
    try:
        start = int(a)
    except Exception:
        return None
    if b == "":
        end = size - 1
    else:
        try:
            end = int(b)
        except Exception:
            return None
    if start < 0 or end < start:
        return None
    if start >= size:
        return None
    if end >= size:
        end = size - 1
    return (start, end)


def _stream_file(
    handler: BaseHTTPRequestHandler,
    path: Path,
    *,
    content_type: str,
    cache_control: str,
    allow_ranges: bool = True,
) -> None:
    st = path.stat()
    size = int(st.st_size)
    rng = _parse_range_header(handler.headers.get("Range"), size) if allow_ranges else None

    if rng is None:
        handler.send_response(200)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(size))
        handler.send_header("Cache-Control", cache_control)
        if allow_ranges:
            handler.send_header("Accept-Ranges", "bytes")
        handler.end_headers()
        if handler.command == "HEAD":
            return
        with path.open("rb") as f:
            while True:
                buf = f.read(1024 * 1024)
                if not buf:
                    break
                handler.wfile.write(buf)
        return

    start, end = rng
    length = end - start + 1
    handler.send_response(206)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(length))
    handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Cache-Control", cache_control)
    handler.end_headers()
    if handler.command == "HEAD":
        return
    with path.open("rb") as f:
        f.seek(start)
        remaining = length
        while remaining > 0:
            chunk = f.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            handler.wfile.write(chunk)
            remaining -= len(chunk)


def _extract_outputs_from_history(history_obj: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(history_obj, dict) or not history_obj:
        return out

    record = None
    if "outputs" in history_obj and isinstance(history_obj.get("outputs"), dict):
        record = history_obj
    else:
        for _k, v in history_obj.items():
            if isinstance(v, dict) and isinstance(v.get("outputs"), dict):
                record = v
                break
    if not isinstance(record, dict):
        return out

    outputs = record.get("outputs")
    if not isinstance(outputs, dict):
        return out

    for node_id, node_out in outputs.items():
        if not isinstance(node_out, dict):
            continue
        for kind, items in node_out.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                fn = item.get("filename")
                sub = item.get("subfolder", "")
                if not isinstance(fn, str) or not fn.strip():
                    continue
                if not isinstance(sub, str):
                    sub = ""
                rel = _normalize_rel_posix(f"{sub}/{fn}" if sub else fn)
                if not rel:
                    continue
                out.append(
                    {
                        "node_id": str(node_id),
                        "kind": str(kind),
                        "filename": fn,
                        "subfolder": sub,
                        "type": item.get("type"),
                        "format": item.get("format"),
                        "frame_rate": item.get("frame_rate"),
                        "workflow": item.get("workflow"),
                        "fullpath": item.get("fullpath"),
                        "relpath": rel,
                    }
                )
    return out


_OUTPUT_ROLE_SEQ_RE = re.compile(r"(?i)_(?:FINAL|PREVIEW|RAW|DEBUG)_\d+$")
_OUTPUT_PLAIN_SEQ_RE = re.compile(r"_\d{5}$")
_FINAL_SEQ_RE = re.compile(r"(?i)_FINAL_(\d+)$")


def _output_job_stem(name: str) -> str:
    """Strip ``_FINAL_00024`` / ``_PREVIEW_00001`` / ``_00002`` from an output basename."""
    stem = Path(str(name or "")).stem
    stem = _OUTPUT_ROLE_SEQ_RE.sub("", stem)
    return _OUTPUT_PLAIN_SEQ_RE.sub("", stem)


def _latest_final_mp4_near(path: Path) -> Optional[Path]:
    """If ``path`` sits next to ``{stem}_FINAL_*.mp4``, return the highest-numbered one."""
    parent = path.parent
    stem = _output_job_stem(path.name)
    if not stem:
        return None
    try:
        if not parent.is_dir():
            return None
    except OSError:
        return None
    found: List[Tuple[int, Path]] = []
    try:
        candidates = list(parent.glob(f"{stem}_FINAL_*.mp4"))
    except OSError:
        return None
    for cand in candidates:
        try:
            if not cand.is_file():
                continue
        except OSError:
            continue
        m = _FINAL_SEQ_RE.search(cand.stem)
        n = int(m.group(1)) if m else -1
        found.append((n, cand))
    if not found:
        return None
    found.sort(key=lambda t: t[0])
    return found[-1][1]


def _history_media_probe(cfg: "ServerConfig", norm: str) -> Optional[Path]:
    """Path under output_root for sibling lookup, even if the named file was deleted."""
    joined = _safe_join(cfg.output_root, norm)
    if joined is not None:
        return joined
    try:
        cand = cfg.output_root.joinpath(*norm.split("/"))
        root_resolved = cfg.output_root.resolve()
        parent = cand.parent.resolve()
        if root_resolved == parent or root_resolved in parent.parents:
            return cand
    except Exception:
        return None
    return None


def _pick_primary_media(outputs: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    """Prefer durable ``output`` library files over Comfy ``temp`` intermediates."""

    def score(o: Dict[str, Any]) -> tuple:
        rel = str(o.get("relpath") or "")
        name = Path(rel).name.upper()
        typ = str(o.get("type") or "").lower()
        # Higher is better.
        durable = 2 if typ == "output" else (1 if typ in {"input", ""} else 0)
        under_lib = 1 if any(p in rel.replace("\\", "/") for p in ("/og/", "/wip/", "og/", "wip/")) else 0
        ephemeral = 1 if _discovery_is_ephemeral_work_artifact(name) else 0
        final = 1 if "_FINAL_" in name else 0
        return (final, 1 - ephemeral, durable, under_lib)

    vids = [o for o in outputs if isinstance(o.get("relpath"), str) and str(o["relpath"]).lower().endswith(".mp4")]
    imgs = [
        o
        for o in outputs
        if isinstance(o.get("relpath"), str)
        and str(o["relpath"]).lower().endswith((".png", ".webp", ".jpg", ".jpeg"))
        and not _discovery_is_ephemeral_work_artifact(str(o.get("relpath") or ""))
    ]
    vids.sort(key=score, reverse=True)
    imgs.sort(key=score, reverse=True)
    keepers = [o for o in vids if not _discovery_is_ephemeral_work_artifact(str(o.get("relpath") or ""))]
    chosen_vids = keepers or vids
    vid = str(chosen_vids[0]["relpath"]) if chosen_vids else None
    img = str(imgs[0]["relpath"]) if imgs else None
    return vid, img


def _rewrite_history_media_rel(cfg: "ServerConfig", rel: Optional[str]) -> Optional[str]:
    """If Comfy history named a deleted ``_PREVIEW_`` / ``_00001`` file, point at the FINAL sibling."""
    if not isinstance(rel, str) or not rel.strip():
        return None
    norm = _normalize_rel_posix(rel)
    if not norm:
        return None
    full = _discovery_resolve_media_file(cfg, norm)
    ephemeral = _discovery_is_ephemeral_work_artifact(Path(norm).name)
    if full is not None and not ephemeral:
        return norm
    probe = full if full is not None else _history_media_probe(cfg, norm)
    if probe is not None:
        try:
            near = _latest_final_mp4_near(probe)
        except Exception:
            near = None
        if near is not None and near.is_file():
            try:
                return str(near.resolve().relative_to(cfg.output_root.resolve())).replace("\\", "/")
            except Exception:
                pass
    alt = re.sub(r"(?i)_PREVIEW_", "_FINAL_", norm)
    if alt != norm and _discovery_resolve_media_file(cfg, alt):
        return alt
    return None if (full is None or ephemeral) else norm


def _history_queue_index(record: Any) -> int:
    """Comfy prompt tuple starts with a monotonic queue number — higher is newer."""
    if not isinstance(record, dict):
        return -1
    prompt = record.get("prompt")
    if isinstance(prompt, list) and prompt:
        n = _safe_int(prompt[0])
        if n is not None:
            return int(n)
    return -1


def _ms_to_utc_iso(ms: Any) -> Optional[str]:
    try:
        v = float(ms)
    except (TypeError, ValueError):
        return None
    # Comfy execution messages use epoch milliseconds.
    if v > 1e12:
        v = v / 1000.0
    if v <= 0:
        return None
    try:
        return _dt.datetime.fromtimestamp(v, tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return None


def _history_status_and_times(record: Any) -> Dict[str, Any]:
    """Normalize Comfy history status + derive queue/start and last-change times."""
    out: Dict[str, Any] = {
        "status": "complete",
        "queued_at": None,
        "changed_at": None,
        "error_message": None,
        "error_node": None,
    }
    if not isinstance(record, dict):
        return out
    st = record.get("status") if isinstance(record.get("status"), dict) else {}
    status_str = st.get("status_str")
    if isinstance(status_str, str) and status_str.strip():
        out["status"] = status_str.strip().lower()
    elif st.get("completed") is False:
        out["status"] = "error"
    elif st.get("completed") is True:
        out["status"] = "success"

    messages = st.get("messages") if isinstance(st.get("messages"), list) else []
    started_ms: Optional[float] = None
    changed_ms: Optional[float] = None
    for msg in messages:
        if not isinstance(msg, list) or not msg:
            continue
        kind = msg[0] if isinstance(msg[0], str) else ""
        meta = msg[1] if len(msg) > 1 and isinstance(msg[1], dict) else {}
        ts = meta.get("timestamp")
        if isinstance(ts, (int, float)):
            changed_ms = float(ts)
            if started_ms is None and kind in ("execution_start", "execution_cached"):
                started_ms = float(ts)
            if started_ms is None:
                started_ms = float(ts)
        if kind == "execution_success" and out["status"] not in ("error", "interrupted"):
            out["status"] = "success"

    # Prefer shared extractor (full exception text + status fallback).
    try:
        d = _workspace_scripts_dir()
        if d.is_dir() and str(d) not in sys.path:
            sys.path.insert(0, str(d))
        from shape_factory import extract_history_execution_error, format_history_error_text  # type: ignore

        err = extract_history_execution_error(record)
        if err:
            kind = str(err.get("kind") or "")
            if kind == "execution_interrupted" or out["status"] == "interrupted":
                out["status"] = "interrupted"
            elif out["status"] not in ("interrupted",):
                out["status"] = "error"
            out["error_message"] = format_history_error_text(err) or err.get("exception_message")
            node = err.get("node_type") or err.get("node_id")
            if node is not None:
                out["error_node"] = str(node)
    except Exception:
        # Local fallback if workspace scripts are unavailable.
        for msg in messages:
            if not isinstance(msg, list) or not msg:
                continue
            kind = msg[0] if isinstance(msg[0], str) else ""
            meta = msg[1] if len(msg) > 1 and isinstance(msg[1], dict) else {}
            if kind == "execution_error":
                out["status"] = "error"
                exc = meta.get("exception_message") or meta.get("exception_type") or "execution_error"
                out["error_message"] = str(exc).strip() or "execution_error"
                node = meta.get("node_type") or meta.get("node_id")
                if node is not None:
                    out["error_node"] = str(node)
            elif kind == "execution_interrupted":
                out["status"] = "interrupted"
                out["error_message"] = "Interrupted"
                node = meta.get("node_type") or meta.get("node_id")
                if node is not None:
                    out["error_node"] = str(node)

    if out["status"] in ("error", "failed", "interrupted") and not out.get("error_message"):
        out["error_message"] = "execution failed (no Comfy exception text)"

    out["queued_at"] = _ms_to_utc_iso(started_ms)
    out["changed_at"] = _ms_to_utc_iso(changed_ms) or out["queued_at"]
    return out


def _demote_hollow_history_success(
    status_info: Dict[str, Any],
    *,
    primary_video: Optional[str],
    primary_image: Optional[str],
) -> Dict[str, Any]:
    """
    Comfy often marks graphs ``success`` even when no media was produced
    (early abort / bad LoadImage still leaves scalar ``value`` outputs).
    Treat those as errors in Queue history so they don't look like keepers.
    """
    st = str(status_info.get("status") or "").strip().lower()
    if st not in {"success", "complete", "completed"}:
        return status_info
    if primary_video or primary_image:
        return status_info
    status_info = dict(status_info)
    status_info["status"] = "error"
    status_info["hollow_success"] = True
    if not status_info.get("error_message"):
        status_info["error_message"] = "no output media (Comfy reported success)"
    return status_info


def _extract_input_media_from_prompt(prompt_obj: Any) -> Tuple[Optional[str], Optional[str]]:
    if not isinstance(prompt_obj, dict):
        return (None, None)
    for _nid, node in prompt_obj.items():
        if not isinstance(node, dict):
            continue
        ctype = node.get("class_type")
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        if ctype == "LoadImage":
            image = inputs.get("image")
            if isinstance(image, str) and image.strip():
                return (image.strip().replace("\\", "/"), "image")
            if isinstance(image, list) and image:
                last = image[-1]
                if isinstance(last, str) and last.strip():
                    return (last.strip().replace("\\", "/"), "image")
        if ctype in ("VHS_LoadVideo", "LoadVideo", "VHS_LoadVideoPath", "VHS_LoadVideoFFmpegPath", "VHS_LoadVideoFFmpeg"):
            video = inputs.get("video") or inputs.get("path") or inputs.get("file")
            if isinstance(video, str) and video.strip():
                return (video.strip().replace("\\", "/"), "video")
    return (None, None)


def _extract_key_params_from_prompt(prompt_obj: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not isinstance(prompt_obj, dict):
        return out
    vhs_load_types = {
        "VHS_LoadVideoPath",
        "VHS_LoadVideo",
        "VHS_LoadVideoFFmpegPath",
        "VHS_LoadVideoFFmpeg",
        "LoadVideo",
    }
    for _nid, node in prompt_obj.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for key in ("seed", "noise_seed", "steps", "cfg", "sampler_name", "scheduler", "denoise", "model"):
            if key in out:
                continue
            val = inputs.get(key)
            if isinstance(val, (str, int, float, bool)) and str(val).strip():
                out[key] = val
        # VHS trim window — show when present on a video loader (first wins).
        ctype = str(node.get("class_type") or "")
        if ctype in vhs_load_types:
            if "skip_first_frames" not in out and inputs.get("skip_first_frames") is not None:
                try:
                    out["skip_first_frames"] = max(0, int(inputs["skip_first_frames"]))
                except (TypeError, ValueError):
                    pass
            if "frame_load_cap" not in out and inputs.get("frame_load_cap") is not None:
                try:
                    out["frame_load_cap"] = max(0, int(inputs["frame_load_cap"]))
                except (TypeError, ValueError):
                    pass
            if "force_rate" not in out and inputs.get("force_rate") is not None:
                try:
                    fr = float(inputs["force_rate"])
                    if fr > 0:
                        out["force_rate"] = fr
                except (TypeError, ValueError):
                    pass
    return out


def _queue_trim_nontrivial(skip: Any, cap: Any) -> bool:
    try:
        s = int(skip) if skip is not None and skip != "" else 0
    except (TypeError, ValueError):
        s = 0
    try:
        c = int(cap) if cap is not None and cap != "" else 0
    except (TypeError, ValueError):
        c = 0
    return s > 0 or c > 0


def _queue_slim_vhs_window(win: Any) -> Optional[Dict[str, Any]]:
    """Normalize job/prompt VHS + Use marks for Queue UI."""
    if not isinstance(win, dict):
        return None
    out: Dict[str, Any] = {}
    for key in ("skip_first_frames", "frame_load_cap"):
        raw = win.get(key)
        if raw is None or raw == "":
            continue
        try:
            out[key] = max(0, int(raw))
        except (TypeError, ValueError):
            pass
    for key in ("mark_in", "mark_out"):
        raw = win.get(key)
        if raw is None or raw == "":
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(val) and val >= 0:
            out[key] = val
    if not out:
        return None
    return out


def _queue_enrich_from_job(
    *,
    job_key: Optional[str],
    key_params: Dict[str, Any],
    output_root: Optional[Path] = None,
    workspace_root: Optional[Path] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], Dict[str, Any], Optional[Dict[str, Any]]]:
    """
    Merge factory job metadata into a Comfy queue row.

    Returns ``(key_params, vhs_window, glance, prompt_profile)``.
    """
    params = dict(key_params) if isinstance(key_params, dict) else {}
    glance: Dict[str, Any] = {}
    prompt_profile: Optional[Dict[str, Any]] = None
    key = str(job_key or "").strip()
    job: Optional[Dict[str, Any]] = None
    data_root: Optional[Path] = None
    if key:
        try:
            from shape_factory_map import resolve_shape_factory_data_root  # type: ignore
            from shape_factory import find_job_by_key  # type: ignore

            data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
            _path, job = find_job_by_key(data_root, key)
        except Exception:
            job = None

    win = job.get("vhs_window") if isinstance(job, dict) else None
    slim_job = _queue_slim_vhs_window(win)
    graph_nontrivial = _queue_trim_nontrivial(params.get("skip_first_frames"), params.get("frame_load_cap"))
    if slim_job and not graph_nontrivial:
        if "skip_first_frames" in slim_job:
            params["skip_first_frames"] = slim_job["skip_first_frames"]
        if "frame_load_cap" in slim_job:
            params["frame_load_cap"] = slim_job["frame_load_cap"]
    slim = _queue_slim_vhs_window(
        {
            "skip_first_frames": params.get("skip_first_frames"),
            "frame_load_cap": params.get("frame_load_cap"),
            "mark_in": (slim_job or {}).get("mark_in"),
            "mark_out": (slim_job or {}).get("mark_out"),
        }
    )

    if isinstance(job, dict):
        fam = str(job.get("family_slug") or "").strip()
        if fam:
            glance["family_slug"] = fam
        shape_id = str(job.get("shape_id") or "").strip()
        if shape_id:
            glance["shape_id"] = shape_id
        construction = job.get("construction") if isinstance(job.get("construction"), dict) else {}
        pick = str(job.get("pick_mode") or construction.get("pick_mode") or "").strip()
        if pick:
            glance["pick_mode"] = pick
        step = str(construction.get("step") or "").strip()
        if step:
            glance["step"] = step
        seed_mode = str(construction.get("seed_mode") or "").strip()
        if seed_mode:
            glance["seed_mode"] = seed_mode
        noise = construction.get("noise_seed")
        if noise is None:
            noise = params.get("noise_seed", params.get("seed"))
        try:
            if noise is not None and str(noise).strip() != "":
                glance["noise_seed"] = int(noise)
        except (TypeError, ValueError):
            pass
        if key.startswith("hourly") or fam.startswith("hourly"):
            glance["is_hourly"] = True
        try:
            from shape_factory_work_products import job_is_hourly_product  # type: ignore

            if job_is_hourly_product(job, None):
                glance["is_hourly"] = True
        except Exception:
            pass
        binds = job.get("bindings") if isinstance(job.get("bindings"), dict) else {}
        for slot in ("prompt_profile", "source_video", "source_still", "identity_anchor"):
            meta = binds.get(slot) if isinstance(binds, dict) else None
            if not isinstance(meta, dict):
                continue
            raw = str(meta.get("relpath") or meta.get("path") or "").strip()
            if not raw:
                continue
            name = Path(raw.replace("\\", "/")).name
            if slot == "prompt_profile":
                glance["prompt_profile"] = name
                try:
                    from shape_factory_owned_prompt import (  # type: ignore
                        ensure_owned_prompt_from_bindings,
                        get_owned_prompt,
                        owned_prompt_to_excerpt,
                    )

                    owned = get_owned_prompt(job) or ensure_owned_prompt_from_bindings(
                        job, data_root=data_root
                    )
                    if owned is not None:
                        prompt_profile = owned_prompt_to_excerpt(owned, data_root=data_root)
                        label = str(owned.get("label") or "").strip()
                        if label:
                            glance["prompt_profile"] = label
                        elif owned.get("source_profile"):
                            glance["prompt_profile"] = Path(str(owned["source_profile"])).name
                        if prompt_profile.get("snowflake"):
                            glance["prompt_snowflake"] = True
                    else:
                        from shape_factory_work_products import _prompt_excerpt  # type: ignore

                        prompt_profile = _prompt_excerpt(
                            raw,
                            data_root=data_root,
                            output_root=output_root,
                            workspace_root=workspace_root,
                        )
                except Exception:
                    prompt_profile = {"path": raw, "basename": name}
            elif slot in ("source_video", "source_still") and "source_name" not in glance:
                glance["source_name"] = name
            elif slot == "identity_anchor":
                glance["identity_name"] = name
        # Jobs with owned prompt but missing binding path still surface prompt.
        if prompt_profile is None:
            try:
                from shape_factory_owned_prompt import get_owned_prompt, owned_prompt_to_excerpt  # type: ignore

                owned = get_owned_prompt(job)
                if owned is not None:
                    prompt_profile = owned_prompt_to_excerpt(owned, data_root=data_root)
                    glance["prompt_profile"] = str(
                        owned.get("label")
                        or Path(str(owned.get("source_profile") or "")).name
                        or "owned-prompt"
                    )
                    if prompt_profile.get("snowflake"):
                        glance["prompt_snowflake"] = True
            except Exception:
                pass
        source_slot = str(construction.get("source_slot") or "").strip()
        if source_slot in ("source_still", "source_image") or "source_still" in binds or "source_image" in binds:
            glance["workflow_kind"] = "image"
        elif source_slot == "source_video" or "source_video" in binds:
            glance["workflow_kind"] = "extend"

    # Graph seed fallback when no job file.
    if "noise_seed" not in glance:
        for k in ("noise_seed", "seed"):
            raw = params.get(k)
            try:
                if raw is not None and str(raw).strip() != "":
                    glance["noise_seed"] = int(raw)
                    break
            except (TypeError, ValueError):
                continue
    for k in ("sampler_name", "scheduler", "cfg", "steps", "denoise"):
        if k in params and params[k] is not None and str(params[k]).strip() != "":
            glance[k] = params[k]

    return params, slim, glance, prompt_profile


def _queue_enrich_trim_from_job(
    *,
    job_key: Optional[str],
    key_params: Dict[str, Any],
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Back-compat wrapper — prefer ``_queue_enrich_from_job``."""
    params, slim, _glance, _prompt = _queue_enrich_from_job(job_key=job_key, key_params=key_params)
    return params, slim


def _guess_workflow_name(prompt_obj: Any, raw_item: Any) -> Optional[str]:
    if isinstance(raw_item, list) and len(raw_item) >= 4 and isinstance(raw_item[3], dict):
        meta = raw_item[3]
        for k in ("workflow_name", "filename", "name"):
            v = meta.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        extra = meta.get("extra_pnginfo")
        if isinstance(extra, dict):
            wf = extra.get("workflow")
            if isinstance(wf, dict):
                name = wf.get("name")
                if isinstance(name, str) and name.strip():
                    return name.strip()
        client = meta.get("client_id")
        if isinstance(client, str) and client.strip():
            return f"client:{client.strip()[:12]}"
    if isinstance(prompt_obj, dict):
        n = sum(1 for v in prompt_obj.values() if isinstance(v, dict) and v.get("class_type"))
        if n:
            return f"graph ({n} nodes)"
    return None


def _queue_item_job_key(workflow_name: Optional[str]) -> Optional[str]:
    """Heuristic: factory submits set workflow_name to the job_key."""
    name = str(workflow_name or "").strip()
    if not name:
        return None
    if name.startswith("client:") or name.startswith("graph ("):
        return None
    stem = _output_job_stem(name)
    if "__" in stem or stem.startswith("hourly"):
        return stem
    if "__" in name or name.startswith("hourly"):
        return name
    return None


def _files_url_for_rel(rel: Optional[str]) -> Optional[str]:
    if not isinstance(rel, str) or not rel.strip():
        return None
    norm = _normalize_rel_posix(rel.strip().lstrip("/"))
    if not norm:
        return None
    return "/files/" + urllib.parse.quote(norm)


def _queue_resolve_input_media(cfg: "ServerConfig", prompt_obj: Any) -> Dict[str, Any]:
    """Resolve input media path/URL/thumb for a queued Comfy prompt."""
    raw_rel, kind = _extract_input_media_from_prompt(prompt_obj)
    if not raw_rel:
        return {
            "input_media_relpath": None,
            "input_media_url": None,
            "input_media_kind": None,
            "input_thumb_url": None,
        }
    ws_in = _discovery_workspace_input_relpath_for_source(cfg, raw_rel)
    candidates: List[str] = []
    raw_norm = _normalize_rel_posix(str(raw_rel).lstrip("/"))
    stripped = raw_norm
    # Comfy often cites ``output/og/...``; library files live under output_root as ``og/...``.
    while stripped.lower().startswith("output/"):
        stripped = stripped[7:]
    for c in (ws_in, stripped, raw_norm, raw_rel):
        if isinstance(c, str) and c.strip() and c not in candidates:
            candidates.append(c.strip().replace("\\", "/"))
    resolved_rel: Optional[str] = None
    full: Optional[Path] = None
    for cand in candidates:
        hit = _discovery_resolve_media_file(cfg, cand)
        if hit is not None:
            if ws_in and hit == _safe_join(cfg.workspace_root, ws_in):
                resolved_rel = ws_in
            else:
                try:
                    resolved_rel = str(hit.relative_to(cfg.output_root.resolve())).replace("\\", "/")
                except Exception:
                    try:
                        resolved_rel = str(hit.relative_to(cfg.workspace_root.resolve())).replace("\\", "/")
                    except Exception:
                        resolved_rel = cand
            full = hit
            break
    url = _files_url_for_rel(resolved_rel) if full is not None else None
    thumb_url = None
    if full is not None and full.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv"}:
        for companion in (full.with_suffix(".png"), full.with_suffix(".jpg"), full.with_suffix(".webp")):
            if companion.is_file():
                try:
                    thumb_rel = str(companion.relative_to(cfg.output_root.resolve())).replace("\\", "/")
                except Exception:
                    try:
                        thumb_rel = str(companion.relative_to(cfg.workspace_root.resolve())).replace("\\", "/")
                    except Exception:
                        thumb_rel = None
                if thumb_rel:
                    thumb_url = _files_url_for_rel(thumb_rel)
                    break
    if thumb_url is None and kind == "image":
        thumb_url = url
    return {
        "input_media_relpath": resolved_rel or raw_rel,
        "input_media_url": url,
        "input_media_kind": kind,
        "input_thumb_url": thumb_url,
    }


def _history_prompt_obj(record: Any) -> Optional[Dict[str, Any]]:
    """Comfy history value is often {prompt: [num, pid, prompt_dict, extra, ...], outputs: ...}."""
    if not isinstance(record, dict):
        return None
    prompt = record.get("prompt")
    if isinstance(prompt, list) and len(prompt) >= 3 and isinstance(prompt[2], dict):
        return prompt[2]
    if isinstance(prompt, dict):
        return prompt
    return None


def _run_primary_media(
    cfg: "ServerConfig", exp_dir: Path, run_dir: Path
) -> Tuple[Optional[str], Optional[str]]:
    """Lightweight: return (primary_video_relpath, primary_image_relpath) for one run."""
    history_path = run_dir / "history.json"
    outs: List[Dict[str, Any]] = []
    if history_path.exists():
        try:
            history = _read_json(history_path)
            outs = _extract_outputs_from_history(history)
        except Exception:
            pass
    if not outs:
        outs = _find_outputs_for_run_by_fs(cfg=cfg, exp_dir=exp_dir, run_id=run_dir.name)
    return _pick_primary_media(outs)


@dataclass(frozen=True)
class ServerConfig:
    workspace_root: Path
    experiments_root: Path
    output_root: Path
    wip_root: Path
    static_dir: Path
    tune_script: Path
    comfy_server: str
    orchestrator_state_path: Path
    queue_ledger_state_path: Path
    queue_ledger_events_path: Path
    discovery_index_path: Path
    factory_db_path: Path
    factory_browse_roots: List[Dict[str, Any]]


def _resolve_workspace_root(base: Path) -> Path:
    """
    Auto-detect the "workspace root" that contains:
      - output/
      - experiments_ui/
      - scripts/

    This repo is commonly laid out as:
      <repo>/workspace/output/...
      <repo>/workspace/experiments_ui/...
      <repo>/workspace/scripts/...

    But in some container setups it may already be:
      <ws>/output/...
      <ws>/experiments_ui/...
      <ws>/scripts/...
    """
    base = base.resolve()
    if (base / "output").exists() and (base / "experiments_ui").exists() and (base / "scripts").exists():
        return base
    if (base / "workspace" / "output").exists() and (base / "workspace" / "experiments_ui").exists() and (base / "workspace" / "scripts").exists():
        return (base / "workspace").resolve()
    return base


def _iter_experiments(experiments_root: Path) -> List[Path]:
    if not experiments_root.exists():
        return []
    out: List[Path] = []
    for child in sorted([p for p in experiments_root.iterdir() if p.is_dir()], key=lambda p: p.name):
        if (child / "manifest.json").exists():
            out.append(child)
    return out


def _iter_experiments_newest_first(experiments_root: Path, *, limit: int = 0) -> List[Path]:
    if not experiments_root.exists():
        return []
    # Avoid sorting by mtime: on Windows bind-mounts, stat() across many folders can be very slow.
    # Instead, prefer name-desc order (experiment ids include timestamps, so this is usually correct enough).
    ds = [p for p in experiments_root.iterdir() if p.is_dir() and (p / "manifest.json").exists()]
    ds.sort(key=lambda p: p.name, reverse=True)
    if limit and limit > 0:
        ds = ds[: int(limit)]
    return ds


def _load_manifest(exp_dir: Path) -> Optional[Dict[str, Any]]:
    mf = exp_dir / "manifest.json"
    if not mf.exists():
        return None
    try:
        obj = _read_json(mf)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _run_dirs(exp_dir: Path) -> List[Path]:
    runs = exp_dir / "runs"
    if not runs.exists():
        return []
    return sorted([p for p in runs.iterdir() if p.is_dir() and re.match(r"^run_\d+$", p.name)], key=lambda p: p.name)


def _run_status(run_dir: Path) -> str:
    hist = run_dir / "history.json"
    sub = run_dir / "submit.json"
    if hist.exists():
        return "complete"
    if sub.exists():
        return "submitted"
    return "not_submitted"


def _find_outputs_for_run_by_fs(*, cfg: ServerConfig, exp_dir: Path, run_id: str) -> List[Dict[str, Any]]:
    """
    Fallback when history.json is missing/stale: find media files saved under the experiment output folder.

    We look under the experiment dir (which lives under cfg.output_root) for files like:
      <exp_dir>/**/<run_id>_*.mp4|png|webp|jpg|jpeg

    Returns output records compatible with _extract_outputs_from_history() output.
    """
    out: List[Dict[str, Any]] = []
    prefix = f"{run_id}_"
    exts = {".mp4", ".png", ".webp", ".jpg", ".jpeg"}

    try:
        output_root_resolved = cfg.output_root.resolve()
    except Exception:
        output_root_resolved = cfg.output_root

    try:
        for p in exp_dir.rglob("*"):
            try:
                if not p.is_file():
                    continue
            except Exception:
                continue
            if p.suffix.lower() not in exts:
                continue
            if not p.name.startswith(prefix):
                continue
            try:
                rel = p.resolve().relative_to(output_root_resolved)
            except Exception:
                # Not under output root; skip (can't be served by /files safely)
                continue
            rel_posix = _normalize_rel_posix(str(rel).replace("\\", "/"))
            if not rel_posix:
                continue
            out.append(
                {
                    "node_id": "fs",
                    "kind": "fs",
                    "filename": p.name,
                    "subfolder": str(rel.parent).replace("\\", "/"),
                    "type": "output",
                    "format": None,
                    "frame_rate": None,
                    "workflow": None,
                    "fullpath": str(p),
                    "relpath": rel_posix,
                }
            )
    except Exception:
        return out

    return out


def _extract_seed_from_prompt(prompt_obj: Any) -> Optional[int]:
    if not isinstance(prompt_obj, dict):
        return None
    # Prompt is the ComfyUI /prompt graph dict keyed by node id as str.
    for _nid, node in prompt_obj.items():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") != "RandomNoise":
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        s = inputs.get("noise_seed")
        if isinstance(s, int):
            return int(s)
        ss = _safe_int(s)
        if ss is not None:
            return ss
    # fallback: any seed-like int
    for _nid, node in prompt_obj.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for k in ("noise_seed", "seed"):
            s = inputs.get(k)
            if isinstance(s, int):
                return int(s)
            ss = _safe_int(s)
            if ss is not None:
                return ss
    return None


def _summarize_runs(cfg: ServerConfig, *, exp_id: str, exp_dir: Path) -> List[Dict[str, Any]]:
    """
    Build the same run objects used by /api/experiments/{exp_id}/runs,
    but shareable for multi-experiment aggregation.
    """
    runs_out: List[Dict[str, Any]] = []

    mf = _load_manifest(exp_dir) or {}
    exp_summary = {
        "exp_id": exp_id,
        "created_at": mf.get("created_at"),
        "base_mp4": mf.get("base_mp4"),
        "fixed_seed": mf.get("fixed_seed"),
        "fixed_duration_sec": mf.get("fixed_duration_sec"),
        "sweep": mf.get("sweep") if isinstance(mf.get("sweep"), dict) else {},
    }

    def url_for(relpath: Optional[str]) -> Optional[str]:
        if not relpath:
            return None
        return "/files/" + urllib.parse.quote(relpath)

    for run_dir in _run_dirs(exp_dir):
        params_path = run_dir / "params.json"
        submit_path = run_dir / "submit.json"
        history_path = run_dir / "history.json"
        status_path = run_dir / "status.json"

        try:
            params = _read_json(params_path) if params_path.exists() else {}
        except Exception:
            params = {}
        try:
            submit = _read_json(submit_path) if submit_path.exists() else {}
        except Exception:
            submit = {}
        try:
            history = _read_json(history_path) if history_path.exists() else None
        except Exception:
            history = None
        try:
            status_obj = _read_json(status_path) if status_path.exists() else None
        except Exception:
            status_obj = None

        prompt_id = submit.get("prompt_id") if isinstance(submit, dict) else None
        outs = _extract_outputs_from_history(history)
        status_str = "history.json" if history_path.exists() else "no history.json"

        # Fallback: if history is missing or doesn't include outputs but files exist, infer outputs from filesystem.
        if not outs:
            fs_outs = _find_outputs_for_run_by_fs(cfg=cfg, exp_dir=exp_dir, run_id=run_dir.name)
            if fs_outs:
                outs = fs_outs
                status_str = "fs outputs (history missing/stale)"

        primary_vid, primary_img = _pick_primary_media(outs)
        has_media = bool(primary_vid or primary_img)

        # Improve status: if media exists, treat as complete even if history.json wasn't written yet.
        status = _run_status(run_dir)
        if status != "complete" and has_media:
            status = "complete"

        runs_out.append(
            {
                "exp_id": exp_id,
                "run_id": run_dir.name,
                "status": status,
                "status_str": status_str,
                "prompt_id": prompt_id,
                # Incremental status (written by workspace/scripts/refresh_run_status.py)
                "status_live": status_obj if isinstance(status_obj, dict) else None,
                "params": params if isinstance(params, dict) else {},
                "outputs": [{**o, "url": url_for(o.get("relpath"))} for o in outs],
                "primary_video": {"relpath": primary_vid, "url": url_for(primary_vid)},
                "primary_image": {"relpath": primary_img, "url": url_for(primary_img)},
                "node_errors": submit.get("node_errors") if isinstance(submit, dict) else None,
                "experiment": exp_summary,
            }
        )

    return runs_out


def _summarize_runs_for_queue(cfg: ServerConfig, *, exp_id: str, exp_dir: Path) -> List[Dict[str, Any]]:
    """
    Lightweight run summary intended for queue views.

    Important: do NOT scan filesystem outputs (no rglob). Queue UI only needs:
    - run status
    - prompt_id
    - status_live (phase: queued/running/etc)
    """
    runs_out: List[Dict[str, Any]] = []
    for run_dir in _run_dirs(exp_dir):
        submit_path = run_dir / "submit.json"
        history_path = run_dir / "history.json"
        status_path = run_dir / "status.json"

        try:
            submit = _read_json(submit_path) if submit_path.exists() else {}
        except Exception:
            submit = {}
        try:
            status_obj = _read_json(status_path) if status_path.exists() else None
        except Exception:
            status_obj = None

        prompt_id = submit.get("prompt_id") if isinstance(submit, dict) else None
        status = "complete" if history_path.exists() else ("submitted" if submit_path.exists() else "not_submitted")
        status_str = "history.json" if history_path.exists() else ("submit.json" if submit_path.exists() else "not submitted")

        runs_out.append(
            {
                "exp_id": exp_id,
                "run_id": run_dir.name,
                "status": status,
                "status_str": status_str,
                "prompt_id": prompt_id,
                "status_live": status_obj if isinstance(status_obj, dict) else None,
                "params": {},
                "outputs": [],
                "primary_video": {"relpath": None, "url": None},
                "primary_image": {"relpath": None, "url": None},
                "node_errors": submit.get("node_errors") if isinstance(submit, dict) else None,
            }
        )
    return runs_out


def _default_orchestrator_state() -> Dict[str, Any]:
    return {
        "projects": [],
        "collections": [],
        "workflows": [],
        "pipelines": [],
        "queues": [],
        "saved_items": [],
    }


def _read_orchestrator_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return _default_orchestrator_state()
    try:
        obj = _read_json(path)
    except Exception:
        return _default_orchestrator_state()
    if not isinstance(obj, dict):
        return _default_orchestrator_state()
    base = _default_orchestrator_state()
    for k in base.keys():
        v = obj.get(k)
        if isinstance(v, list):
            base[k] = v
    return base


def _write_orchestrator_state(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_queue_ledger_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        obj = _read_json(path)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _queue_ledger_entry_client_id(rec: Any) -> Optional[str]:
    if not isinstance(rec, dict):
        return None
    extra = rec.get("extra_data")
    if isinstance(extra, dict):
        cid = extra.get("client_id")
        if isinstance(cid, str) and cid.strip():
            return cid.strip()
    return None


def _queue_ledger_entries(st: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Slim one-line summaries of mirrored ledger rows (no prompt payloads)."""
    snap = st.get("last_snapshot") if isinstance(st.get("last_snapshot"), dict) else {}
    running_ids = [str(x) for x in (snap.get("running") or []) if isinstance(x, str) and x.strip()]
    pending_ids = [str(x) for x in (snap.get("pending") or []) if isinstance(x, str) and x.strip()]
    known = st.get("known") if isinstance(st.get("known"), dict) else {}
    backlog = st.get("backlog") if isinstance(st.get("backlog"), list) else []

    role_rank = {"running": 0, "pending": 1, "backlog": 2, "remembered": 3}
    by_id: Dict[str, Dict[str, Any]] = {}

    def upsert(pid: str, role: str, rec: Any) -> None:
        pid = pid.strip()
        if not pid:
            return
        rec_d = rec if isinstance(rec, dict) else {}
        prev = by_id.get(pid)
        if prev is not None and role_rank.get(str(prev.get("role")), 9) <= role_rank.get(role, 9):
            return
        by_id[pid] = {
            "prompt_id": pid,
            "role": role,
            "client_id": _queue_ledger_entry_client_id(rec_d),
            "last_seen_at": rec_d.get("last_seen_at") if isinstance(rec_d.get("last_seen_at"), str) else None,
            "first_seen_at": rec_d.get("first_seen_at") if isinstance(rec_d.get("first_seen_at"), str) else None,
            "last_phase": rec_d.get("last_phase") if isinstance(rec_d.get("last_phase"), str) else None,
            "has_prompt": isinstance(rec_d.get("prompt"), dict),
        }

    for pid in running_ids:
        upsert(pid, "running", known.get(pid))
    for pid in pending_ids:
        upsert(pid, "pending", known.get(pid))
    for item in backlog:
        if not isinstance(item, dict):
            continue
        pid = item.get("prompt_id")
        if not isinstance(pid, str):
            continue
        # Backlog items often embed the known-shaped payload.
        upsert(pid, "backlog", item if item.get("prompt") is not None else known.get(pid) or item)
    for pid, rec in known.items():
        if isinstance(pid, str):
            upsert(pid, "remembered", rec)

    entries = list(by_id.values())
    entries.sort(
        key=lambda e: (
            role_rank.get(str(e.get("role")), 9),
            str(e.get("last_seen_at") or ""),
            str(e.get("prompt_id") or ""),
        )
    )
    return entries


# High-churn / legacy ledger lines; omit from UI "recent activity" by default.
_LEDGER_ACTIVITY_NOISE_TYPES = frozenset(
    {
        "queue_fetch_failed",
        # Legacy churn signal; replaced by queue_enqueued / queue_left.
        "unexpected_queue_delta",
        # Historical per-poll spam (now edge-triggered in ledger, still noisy in old logs).
        "actions_paused",
        "actions_suppressed_breaker",
    }
)


def _tail_jsonl_dicts(path: Path, *, max_lines: int) -> List[Dict[str, Any]]:
    """Return up to max_lines trailing JSON objects from a JSONL file (newest last)."""
    if max_lines <= 0 or not path.is_file():
        return []
    # Read a trailing byte window so we don't scan multi-MB logs on every poll.
    try:
        size = path.stat().st_size
    except OSError:
        return []
    if size <= 0:
        return []
    window = min(size, max(64_000, max_lines * 2_000))
    try:
        with path.open("rb") as f:
            if window < size:
                f.seek(size - window)
                chunk = f.read()
                # Drop partial first line when we seek mid-file.
                nl = chunk.find(b"\n")
                if nl >= 0:
                    chunk = chunk[nl + 1 :]
            else:
                chunk = f.read()
    except OSError:
        return []
    out: collections.deque[Dict[str, Any]] = collections.deque(maxlen=max_lines)
    for raw in chunk.splitlines():
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return list(out)


def _read_queue_ledger_events(
    path: Path,
    *,
    limit: int = 30,
    include_noise: bool = False,
) -> List[Dict[str, Any]]:
    """Newest-first ledger activity for the Queue UI."""
    limit = max(1, min(int(limit), 200))
    # Over-read so filtering noise still fills the limit.
    scan = limit if include_noise else min(200, max(limit * 4, limit))
    rows = _tail_jsonl_dicts(path, max_lines=scan)
    if not include_noise:
        rows = [r for r in rows if str(r.get("type") or "") not in _LEDGER_ACTIVITY_NOISE_TYPES]
    rows = rows[-limit:]
    rows.reverse()
    return rows


def _write_queue_ledger_state(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _queue_ops_output_root(cfg: ServerConfig) -> Path:
    """Host output dir that contains experiments/_status (ledger files)."""
    return cfg.queue_ledger_state_path.parent.parent.parent


def _queue_ops_status(cfg: ServerConfig) -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore
    from suspend_comfy_queue import collect_ops_status  # type: ignore

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    try:
        return collect_ops_status(
            server=str(cfg.comfy_server),
            output_root=_queue_ops_output_root(cfg),
            data_root=data_root,
        )
    except Exception as e:
        return {"ok": False, "error": "ops_status_failed", "detail": str(e)}


_QUEUE_OPS_ACTIONS = (
    "suspend",
    "resume-ops",
    "hourlies-on",
    "hourlies-off",
    "drain-on",
    "drain-off",
    "watch-on",
    "watch-off",
)


def _queue_ops_action(cfg: ServerConfig, action: str) -> Dict[str, Any]:
    d = _workspace_scripts_dir()
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore
    from suspend_comfy_queue import (  # type: ignore
        do_resume,
        do_suspend,
        set_drain_timer,
        set_hourlies_enabled,
        set_watch_queue,
    )

    data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
    output_root = _queue_ops_output_root(cfg)
    if action == "suspend":
        return do_suspend(
            server=str(cfg.comfy_server),
            data_root=data_root,
            output_root=output_root,
        )
    if action == "resume-ops":
        return do_resume(output_root=output_root, feeders=True)
    if action == "hourlies-on":
        out = set_hourlies_enabled(enabled=True, data_root=data_root)
        out["action"] = action
        return out
    if action == "hourlies-off":
        out = set_hourlies_enabled(enabled=False, data_root=data_root)
        out["action"] = action
        return out
    if action == "drain-on":
        return {"ok": True, "action": action, "feeders": set_drain_timer(active=True)}
    if action == "drain-off":
        return {"ok": True, "action": action, "feeders": set_drain_timer(active=False)}
    if action == "watch-on":
        return {"ok": True, "action": action, "feeders": set_watch_queue(active=True)}
    if action == "watch-off":
        return {"ok": True, "action": action, "feeders": set_watch_queue(active=False)}
    return {"ok": False, "error": "bad_action", "action": action}


class Handler(BaseHTTPRequestHandler):
    server: "ExperimentsServer"  # type: ignore[assignment]

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path or "/"
        if len(path) > 1:
            path = path.rstrip("/")

        if path.startswith("/api/"):
            return self._handle_api_get(path, parsed.query)
        if path.startswith("/files/"):
            rel = urllib.parse.unquote(path[len("/files/") :])
            return self._handle_files_get(rel)
        if path.startswith("/factory-assets/"):
            rel = urllib.parse.unquote(path[len("/factory-assets/") :])
            return self._handle_factory_asset_file_get(rel)
        return self._handle_static_get(path)

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path or "/"
        if len(path) > 1:
            path = path.rstrip("/")
        if not path.startswith("/api/"):
            return _json_response(self, 404, {"error": "not_found"})
        return self._handle_api_post(path)

    def _handle_api_get(self, path: str, query: str) -> None:
        cfg = self.server.cfg
        q = urllib.parse.parse_qs(query or "", keep_blank_values=True)

        if path == "/api/wip":
            return self._handle_wip_get(q)

        if path == "/api/discovery/library":
            return self._handle_discovery_library_get(q)

        if path == "/api/discovery/library/item":
            return self._handle_discovery_library_item_get(q)

        if path == "/api/discovery/trim":
            return self._handle_discovery_trim_get(q)

        if path == "/api/discovery/embed-api-prompt":
            return self._handle_discovery_embed_api_prompt_get(q)

        if path == "/api/discovery/workflow-facets":
            return self._handle_discovery_workflow_facets_get(q)

        if path == "/api/discovery/asset-lineage":
            return self._handle_discovery_asset_lineage_get(q)
        if path == "/api/discovery/asset-ratings":
            return self._handle_discovery_asset_ratings_get(q)
        if path == "/api/discovery/rating-sampler":
            return self._handle_discovery_rating_sampler_get(q)
        if path == "/api/discovery/disposition-catalog":
            return self._handle_discovery_disposition_catalog_get(q)
        if path == "/api/discovery/disposition-suggest":
            return self._handle_discovery_disposition_suggest_get(q)
        if path == "/api/discovery/work-items":
            return self._handle_discovery_work_items_get(q)
        if path == "/api/discovery/work-items/pool":
            return self._handle_discovery_work_items_pool_get(q)
        if path == "/api/discovery/asset-audit":
            return self._handle_discovery_asset_audit_get(q)
        if path == "/api/discovery/identity-still/candidates":
            return self._handle_discovery_identity_still_candidates_get(q)

        if path == "/api/home/summary":
            return self._handle_home_summary_get(q)

        if path == "/api/comfy/live-preview":
            return self._handle_comfy_live_preview_get(q)
        if path == "/api/comfy/live-status":
            return self._handle_comfy_live_status_get(q)
        if path == "/api/comfy/logs":
            return self._handle_comfy_logs_get(q)

        if path == "/api/queue":
            # Optional: limit how many experiments we scan (newest first).
            limit_exps = None
            for v in q.get("limit_experiments", []):
                limit_exps = _safe_int(v)
                if limit_exps is not None:
                    break
            if limit_exps is None:
                # Default to a small newest-first window to keep this endpoint responsive
                # on slow filesystems (e.g. Windows bind mounts).
                limit_exps = 5
            limit_exps = max(0, int(limit_exps))

            # Optional: exp_id filters
            exp_filters: List[str] = []
            for v in q.get("exp_id", []):
                if isinstance(v, str) and v.strip():
                    exp_filters.append(v.strip())
            # de-dupe
            seen_f: set = set()
            exp_filters = [x for x in exp_filters if not (x in seen_f or seen_f.add(x))]

            exp_dirs = _iter_experiments_newest_first(cfg.experiments_root, limit=limit_exps) if not exp_filters else []
            if exp_filters:
                for exp_id in exp_filters:
                    d = cfg.experiments_root / exp_id
                    if d.is_dir() and (d / "manifest.json").exists():
                        exp_dirs.append(d)

            # Collect run items (focus on non-complete / inflight).
            exp_runs: List[Dict[str, Any]] = []
            prompt_to_run: Dict[str, Dict[str, Any]] = {}
            for exp_dir in exp_dirs:
                mf = _load_manifest(exp_dir) or {}
                exp_id = mf.get("exp_id") if isinstance(mf.get("exp_id"), str) else exp_dir.name
                for r in _summarize_runs_for_queue(cfg, exp_id=exp_id, exp_dir=exp_dir):
                    status = r.get("status")
                    if status != "complete":
                        exp_runs.append(r)
                    pid = r.get("prompt_id")
                    if isinstance(pid, str) and pid.strip():
                        prompt_to_run[pid.strip()] = {"exp_id": exp_id, "run_id": r.get("run_id")}

            # Fetch ComfyUI queue.
            comfy = str(cfg.comfy_server).rstrip("/")
            queue_obj: Any = None
            try:
                queue_obj = _http_json("GET", f"{comfy}/queue", timeout_s=10)
            except Exception as e:
                queue_obj = {"error": "comfy_queue_fetch_failed", "detail": str(e)}

            comfy_running: List[Dict[str, Any]] = []
            comfy_pending: List[Dict[str, Any]] = []
            ledger_known = _read_queue_ledger_state(cfg.queue_ledger_state_path).get("known")
            ledger_known = ledger_known if isinstance(ledger_known, dict) else {}
            if isinstance(queue_obj, dict):
                for key, out in (("queue_running", comfy_running), ("queue_pending", comfy_pending)):
                    items = queue_obj.get(key)
                    if not isinstance(items, list):
                        continue
                    for it in items:
                        pid = None
                        prompt_obj: Optional[Dict[str, Any]] = None
                        queue_index: Optional[int] = None
                        if isinstance(it, list) and len(it) >= 2 and isinstance(it[1], str):
                            pid = it[1]
                            qn = _safe_int(it[0]) if it else None
                            queue_index = int(qn) if qn is not None else None
                            if len(it) >= 3 and isinstance(it[2], dict):
                                prompt_obj = it[2]
                        mapped = prompt_to_run.get(pid) if isinstance(pid, str) and pid else None
                        media = _queue_resolve_input_media(cfg, prompt_obj)
                        workflow_name = _guess_workflow_name(prompt_obj, it)
                        job_key = _queue_item_job_key(workflow_name)
                        key_params = _extract_key_params_from_prompt(prompt_obj)
                        key_params, vhs_window, glance, prompt_profile = _queue_enrich_from_job(
                            job_key=job_key,
                            key_params=key_params,
                            output_root=cfg.output_root,
                            workspace_root=cfg.workspace_root,
                        )
                        known_rec = ledger_known.get(pid) if isinstance(pid, str) else None
                        known_rec = known_rec if isinstance(known_rec, dict) else {}
                        queued_at = known_rec.get("first_seen_at") if isinstance(known_rec.get("first_seen_at"), str) else None
                        changed_at = known_rec.get("last_seen_at") if isinstance(known_rec.get("last_seen_at"), str) else None
                        out.append(
                            {
                                "prompt_id": pid,
                                "raw": it,
                                "external": mapped is None,
                                "exp_id": mapped.get("exp_id") if isinstance(mapped, dict) else None,
                                "run_id": mapped.get("run_id") if isinstance(mapped, dict) else None,
                                "workflow_name": workflow_name,
                                "job_key": job_key,
                                "queue_index": queue_index,
                                "queued_at": queued_at,
                                "changed_at": changed_at,
                                "input_media_relpath": media.get("input_media_relpath"),
                                "input_media_url": media.get("input_media_url"),
                                "input_media_kind": media.get("input_media_kind"),
                                "input_thumb_url": media.get("input_thumb_url"),
                                "key_params": key_params,
                                "vhs_window": vhs_window,
                                "glance": glance,
                                "prompt_profile": prompt_profile,
                            }
                        )

            return _json_response(
                self,
                200,
                {
                    "experiments": exp_runs,
                    "comfyui": {"running": comfy_running, "pending": comfy_pending, "raw": queue_obj if isinstance(queue_obj, dict) else {}},
                },
            )

        if path == "/api/comfy/history":
            limit = 30
            for v in q.get("limit", []):
                p = _safe_int(v)
                if p is not None:
                    limit = max(1, min(200, int(p)))
                    break
            comfy = str(cfg.comfy_server).rstrip("/")
            # Comfy's unbounded /history is huge and oldest-first; requesting
            # max_items and sorting by queue index keeps the UI on recent runs.
            try:
                hist_obj = _http_json("GET", f"{comfy}/history?max_items={int(limit)}", timeout_s=30)
            except Exception as e:
                return _json_response(self, 502, {"error": "comfy_history_fetch_failed", "detail": str(e), "items": []})
            items_out: List[Dict[str, Any]] = []
            if isinstance(hist_obj, dict):
                ordered = sorted(
                    ((pid, record) for pid, record in hist_obj.items() if isinstance(pid, str)),
                    key=lambda kv: _history_queue_index(kv[1]),
                    reverse=True,
                )
                for pid, record in ordered[:limit]:
                    outs = _extract_outputs_from_history(record)
                    pv, pi = _pick_primary_media(outs)
                    pv = _rewrite_history_media_rel(cfg, pv)
                    pi = _rewrite_history_media_rel(cfg, pi)
                    prompt_obj = _history_prompt_obj(record)
                    raw_prompt = record.get("prompt") if isinstance(record, dict) else None
                    media = _queue_resolve_input_media(cfg, prompt_obj)
                    workflow_name = _guess_workflow_name(prompt_obj, raw_prompt)
                    key_params = _extract_key_params_from_prompt(prompt_obj)
                    status_info = _history_status_and_times(record)
                    status_info = _demote_hollow_history_success(
                        status_info,
                        primary_video=pv,
                        primary_image=pi,
                    )

                    def _mk_url(rel: Optional[str]) -> Optional[str]:
                        if not isinstance(rel, str) or not rel:
                            return None
                        norm = _normalize_rel_posix(rel)
                        if not norm:
                            return None
                        full = _discovery_resolve_media_file(cfg, norm)
                        if full is None:
                            return None
                        return _files_url_for_rel(norm)

                    primary_video_url = _mk_url(pv)
                    primary_image_url = _mk_url(pi)
                    # Companion PNG next to the chosen video, else first image output.
                    output_thumb = None
                    if pv:
                        full_v = _discovery_resolve_media_file(cfg, pv)
                        if full_v is not None:
                            for companion in (full_v.with_suffix(".png"), full_v.with_suffix(".jpg"), full_v.with_suffix(".webp")):
                                if companion.is_file():
                                    try:
                                        thumb_rel = str(companion.relative_to(cfg.output_root.resolve())).replace("\\", "/")
                                    except Exception:
                                        thumb_rel = None
                                    if thumb_rel:
                                        output_thumb = _files_url_for_rel(thumb_rel)
                                        break
                    if output_thumb is None:
                        output_thumb = primary_image_url or media.get("input_thumb_url")
                    # Prefer a human title from the durable output basename when workflow is anonymous.
                    # Resolve job_key from the raw workflow name before title rewrite (basename is not a job_key).
                    job_key = _queue_item_job_key(workflow_name) or _queue_item_job_key(
                        Path(str(pv or pi or "")).name
                    )
                    key_params, vhs_window, glance, prompt_profile = _queue_enrich_from_job(
                        job_key=job_key,
                        key_params=key_params,
                        output_root=cfg.output_root,
                        workspace_root=cfg.workspace_root,
                    )
                    title = workflow_name
                    if not title or str(title).startswith("graph (") or str(title).startswith("client:"):
                        for cand in (pv, pi, media.get("input_media_relpath")):
                            bn = Path(str(cand or "")).name
                            if bn:
                                title = bn
                                break
                    items_out.append(
                        {
                            "prompt_id": pid,
                            "status": status_info.get("status") or "complete",
                            "queued_at": status_info.get("queued_at"),
                            "changed_at": status_info.get("changed_at"),
                            "error_message": status_info.get("error_message"),
                            "error_node": status_info.get("error_node"),
                            "hollow_success": bool(status_info.get("hollow_success")),
                            "workflow_name": title,
                            "job_key": job_key,
                            "key_params": key_params,
                            "vhs_window": vhs_window,
                            "glance": glance,
                            "prompt_profile": prompt_profile,
                            "queue_index": _history_queue_index(record),
                            "primary_video_relpath": pv,
                            "primary_image_relpath": pi,
                            "primary_video_url": primary_video_url,
                            "primary_image_url": primary_image_url,
                            "output_thumb_url": output_thumb,
                            "input_media_relpath": media.get("input_media_relpath"),
                            "input_media_url": media.get("input_media_url"),
                            "input_media_kind": media.get("input_media_kind"),
                            "input_thumb_url": media.get("input_thumb_url"),
                            "outputs": [{**o, "url": _mk_url(o.get("relpath"))} for o in outs],
                        }
                    )
            return _json_response(self, 200, {"items": items_out})

        if path == "/api/orchestrator/state":
            st = _read_orchestrator_state(cfg.orchestrator_state_path)
            return _json_response(self, 200, st)

        if path == "/api/shape-factory/map":
            try:
                payload = _shape_factory_map_payload(cfg, q)
                code = 200 if payload.get("ok") else 404
                return _json_response(self, code, payload)
            except Exception as e:
                return _json_response(self, 500, {"ok": False, "error": "shape_factory_map_failed", "detail": str(e)})

        if path == "/api/shape-factory/prompt-profile":
            try:
                payload = _shape_factory_prompt_profile_payload(cfg, q)
                return _json_response(self, 200, payload)
            except ValueError as e:
                return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
            except FileNotFoundError as e:
                return _json_response(self, 404, {"ok": False, "error": "not_found", "detail": str(e)})
            except Exception as e:
                return _json_response(self, 500, {"ok": False, "error": "prompt_profile_failed", "detail": str(e)})

        if path == "/api/shape-factory/families":
            try:
                payload = _shape_factory_families_payload(cfg)
                code = 200 if payload.get("ok") else 500
                return _json_response(self, code, payload)
            except Exception as e:
                return _json_response(self, 500, {"ok": False, "error": "families_failed", "detail": str(e)})

        if path == "/api/shape-factory/work-products":
            try:
                payload = _shape_factory_work_products_payload(cfg, q)
                code = 200 if payload.get("ok") else 500
                return _json_response(self, code, payload)
            except Exception as e:
                return _json_response(self, 500, {"ok": False, "error": "work_products_failed", "detail": str(e)})

        if path == "/api/shape-factory/markers":
            try:
                payload = _shape_factory_markers_payload(cfg, q)
                return _json_response(self, 200, payload)
            except ValueError as e:
                return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
            except Exception as e:
                return _json_response(self, 500, {"ok": False, "error": "markers_failed", "detail": str(e)})

        if path == "/api/shape-factory/job-edit":
            try:
                payload = _shape_factory_job_edit_payload(cfg, q)
                if payload.get("error") == "job_not_found":
                    return _json_response(self, 404, payload)
                code = 200 if payload.get("ok") else 400
                return _json_response(self, code, payload)
            except ValueError as e:
                return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
            except Exception as e:
                return _json_response(self, 500, {"ok": False, "error": "job_edit_failed", "detail": str(e)})

        if path == "/api/shape-factory/clips/library":
            try:
                payload = _shape_factory_clips_library_payload(cfg, q)
                return _json_response(self, 200, payload)
            except Exception as e:
                return _json_response(
                    self, 500, {"ok": False, "error": "clips_library_failed", "detail": str(e)}
                )

        if path == "/api/shape-factory/clips/derived":
            try:
                payload = _shape_factory_clips_derived_payload(cfg, q)
                return _json_response(self, 200, payload)
            except Exception as e:
                return _json_response(
                    self, 500, {"ok": False, "error": "clips_derived_failed", "detail": str(e)}
                )

        if path == "/api/shape-factory/clips":
            try:
                payload = _shape_factory_clips_list_payload(cfg, q)
                return _json_response(self, 200, payload)
            except ValueError as e:
                return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
            except Exception as e:
                detail = str(e)
                # Workbench fans out one GET per card; soft-fail lock/contention
                # so the page stays usable (empty chips) instead of error spam.
                if "database is locked" in detail.lower() or "locked" in detail.lower():
                    return _json_response(
                        self,
                        200,
                        {
                            "ok": True,
                            "parent_content_id": None,
                            "default_clip_id": None,
                            "clips": [],
                            "media_relpath": (q.get("media_relpath") or [None])[0],
                            "degraded": True,
                            "detail": detail,
                        },
                    )
                return _json_response(self, 500, {"ok": False, "error": "clips_list_failed", "detail": detail})

        if path == "/api/shape-factory/hourly-schedule":
            try:
                payload = _hourly_schedule_payload(cfg)
                code = 200 if payload.get("ok") else 500
                return _json_response(self, code, payload)
            except Exception as e:
                return _json_response(self, 500, {"ok": False, "error": "hourly_schedule_failed", "detail": str(e)})

        if path == "/api/shape-factory/quarantine":
            try:
                payload = _shape_factory_quarantine_list_payload(q)
                return _json_response(self, 200, payload)
            except Exception as e:
                return _json_response(self, 500, {"ok": False, "error": "quarantine_list_failed", "detail": str(e)})

        if path == "/api/shape-factory/submit-attempts":
            try:
                payload = _shape_factory_submit_attempts_payload(cfg, q)
                return _json_response(self, 200, payload)
            except Exception as e:
                return _json_response(self, 500, {"ok": False, "error": "submit_attempts_failed", "detail": str(e)})

        if path == "/api/shape-factory/template-promotions":
            try:
                payload = _shape_factory_template_promotions_payload(cfg, q)
                return _json_response(self, 200, payload)
            except Exception as e:
                return _json_response(
                    self, 500, {"ok": False, "error": "template_promotions_list_failed", "detail": str(e)}
                )

        if path == "/api/shape-factory/input-curation/stills":
            try:
                payload = _shape_factory_input_curation_stills_payload(cfg, q)
                return _json_response(self, 200, payload)
            except Exception as e:
                return _json_response(self, 500, {"ok": False, "error": "input_curation_stills_failed", "detail": str(e)})

        if path == "/api/shape-factory/input-curation/stills/tag/backlog":
            try:
                payload = _shape_factory_input_curation_stills_tag_backlog_payload(cfg)
                return _json_response(self, 200, payload)
            except Exception as e:
                return _json_response(
                    self, 500, {"ok": False, "error": "still_tag_backlog_failed", "detail": str(e)}
                )

        if path == "/api/shape-factory/input-curation/stills/tag/schedule":
            try:
                payload = _shape_factory_input_curation_stills_tag_schedule_payload(cfg)
                return _json_response(self, 200, payload)
            except Exception as e:
                return _json_response(
                    self, 500, {"ok": False, "error": "still_tag_schedule_failed", "detail": str(e)}
                )

        if path.startswith("/api/shape-factory/input-curation/stills/tag/runs/"):
            rest = path[len("/api/shape-factory/input-curation/stills/tag/runs/") :].strip("/")
            parts = [p for p in rest.split("/") if p]
            if not parts:
                return _json_response(self, 404, {"ok": False, "error": "missing_run_id"})
            run_id = parts[0]
            try:
                if len(parts) >= 2 and parts[1] == "events":
                    payload = _shape_factory_input_curation_stills_tag_events_payload(cfg, run_id, q)
                else:
                    payload = _shape_factory_input_curation_stills_tag_run_payload(cfg, run_id)
                code = 200 if payload.get("ok") else 404
                return _json_response(self, code, payload)
            except Exception as e:
                return _json_response(
                    self, 500, {"ok": False, "error": "input_curation_stills_tag_status_failed", "detail": str(e)}
                )

        if path == "/api/shape-factory/input-curation/state":
            try:
                payload = _shape_factory_input_curation_state_payload(cfg)
                return _json_response(self, 200, payload)
            except Exception as e:
                return _json_response(self, 500, {"ok": False, "error": "input_curation_state_failed", "detail": str(e)})

        if path == "/api/shape-factory/input-curation/effective-sources":
            try:
                payload = _shape_factory_input_curation_effective_sources_payload(cfg, q)
                return _json_response(self, 200, payload)
            except ValueError as e:
                return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
            except FileNotFoundError as e:
                return _json_response(self, 404, {"ok": False, "error": "not_found", "detail": str(e)})
            except Exception as e:
                return _json_response(
                    self, 500, {"ok": False, "error": "input_curation_effective_sources_failed", "detail": str(e)}
                )

        if path == "/api/shape-factory/input-curation/appetite-seeds":
            try:
                payload = _shape_factory_input_curation_appetite_seeds_payload(cfg, q)
                return _json_response(self, 200, payload)
            except ValueError as e:
                return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
            except Exception as e:
                return _json_response(
                    self, 500, {"ok": False, "error": "input_curation_appetite_seeds_failed", "detail": str(e)}
                )

        if path == "/api/vision/slice-captions":
            try:
                payload = _vision_slice_captions_payload(cfg)
                code = 200 if payload.get("ok") else 500
                return _json_response(self, code, payload)
            except Exception as e:
                return _json_response(self, 500, {"ok": False, "error": "vision_slice_captions_failed", "detail": str(e)})

        if path == "/api/vision/tag-judgment":
            try:
                payload = _vision_tag_judgment_get_payload(cfg)
                code = 200 if payload.get("ok") else 500
                return _json_response(self, code, payload)
            except Exception as e:
                return _json_response(self, 500, {"ok": False, "error": "vision_tag_judgment_failed", "detail": str(e)})

        if path == "/api/shape-factory/json-peek":
            try:
                payload = _shape_factory_json_peek_payload(cfg, q)
                code = 200 if payload.get("ok") else (404 if payload.get("error") == "not_found" else 400)
                return _json_response(self, code, payload)
            except Exception as e:
                return _json_response(self, 500, {"ok": False, "error": "json_peek_failed", "detail": str(e)})

        if path == "/api/workflow-explorer/factory":
            try:
                return _json_response(self, 200, _load_factory_summary(cfg.factory_db_path))
            except Exception as e:
                return _json_response(self, 500, {"ok": False, "error": "factory_summary_failed", "detail": str(e)})

        if path == "/api/workflow-explorer/factory/browse":
            return self._handle_factory_browse_get(q)

        if path == "/api/workflow-explorer/factory/browse-file":
            return self._handle_factory_browse_file_get(q)

        if path == "/api/queue/ledger-status":
            st = _read_queue_ledger_state(cfg.queue_ledger_state_path)
            entries = _queue_ledger_entries(st)
            snap = st.get("last_snapshot") if isinstance(st.get("last_snapshot"), dict) else {}
            known = st.get("known") if isinstance(st.get("known"), dict) else {}
            out = {
                "enabled": True,
                "state_path": str(cfg.queue_ledger_state_path),
                "events_path": str(cfg.queue_ledger_events_path),
                "mode": st.get("mode"),
                "updated_at": st.get("updated_at"),
                "paused": bool(st.get("paused")),
                "pending_target": st.get("pending_target"),
                "backlog_count": len(st.get("backlog", [])) if isinstance(st.get("backlog"), list) else 0,
                "known_count": len(known),
                "breaker": st.get("breaker") if isinstance(st.get("breaker"), dict) else {"open": False},
                "stats": st.get("stats") if isinstance(st.get("stats"), dict) else {},
                "snapshot": {
                    "running": [x for x in (snap.get("running") or []) if isinstance(x, str)],
                    "pending": [x for x in (snap.get("pending") or []) if isinstance(x, str)],
                },
                "entries": entries,
            }
            try:
                out["ops"] = _queue_ops_status(cfg)
            except Exception as e:
                out["ops"] = {"ok": False, "error": "ops_status_failed", "detail": str(e)}
            return _json_response(self, 200, out)

        if path == "/api/queue/ledger-events":
            limit_raw = (q.get("limit") or ["30"])[0]
            try:
                limit = int(limit_raw)
            except Exception:
                limit = 30
            include_noise = str((q.get("include_noise") or ["0"])[0]).strip().lower() in (
                "1",
                "true",
                "yes",
            )
            events = _read_queue_ledger_events(
                cfg.queue_ledger_events_path,
                limit=limit,
                include_noise=include_noise,
            )
            return _json_response(
                self,
                200,
                {
                    "ok": True,
                    "events_path": str(cfg.queue_ledger_events_path),
                    "limit": limit,
                    "include_noise": include_noise,
                    "events": events,
                },
            )

        if path == "/api/experiments":
            # Always scan experiments_root: experiments are often created via CLI
            # (tune_experiment.py generate), not only POST /api/create-experiment, so a
            # long-lived in-memory cache made new runs invisible until server restart.
            exps: List[Dict[str, Any]] = []
            by_base_mp4: Dict[str, List[str]] = {}
            output_to_run: Dict[str, Dict[str, str]] = {}
            for exp_dir in _iter_experiments(cfg.experiments_root):
                mf = _load_manifest(exp_dir) or {}
                exp_id = mf.get("exp_id") if isinstance(mf.get("exp_id"), str) else exp_dir.name
                runs = _run_dirs(exp_dir)
                counts = {"total": len(runs), "complete": 0, "submitted": 0, "not_submitted": 0}
                for rd in runs:
                    counts[_run_status(rd)] += 1  # type: ignore[index]
                base_mp4 = mf.get("base_mp4")
                if isinstance(base_mp4, str) and base_mp4.strip():
                    key = _normalize_rel_posix(base_mp4.strip()) or base_mp4.strip()
                    by_base_mp4.setdefault(key, []).append(exp_id)
                for run_dir in runs:
                    pv, pi = _run_primary_media(cfg, exp_dir, run_dir)
                    for relpath in (pv, pi):
                        if isinstance(relpath, str) and relpath.strip():
                            rn = _normalize_rel_posix(relpath.strip())
                            if rn and rn not in output_to_run:
                                output_to_run[rn] = {"exp_id": exp_id, "run_id": run_dir.name}
                exps.append(
                    {
                        "exp_id": exp_id,
                        "dir": str(exp_dir),
                        "created_at": mf.get("created_at"),
                        "base_mp4": base_mp4,
                        "fixed_seed": mf.get("fixed_seed"),
                        "fixed_duration_sec": mf.get("fixed_duration_sec"),
                        "sweep": mf.get("sweep") if isinstance(mf.get("sweep"), dict) else {},
                        "run_counts": counts,
                    }
                )
            payload: Dict[str, Any] = {
                "experiments": exps,
                "relations": {"by_base_mp4": by_base_mp4, "output_to_run": output_to_run},
            }
            return _json_response(self, 200, payload)

        if path == "/api/runs":
            exp_ids: List[str] = []
            for v in q.get("exp_id", []):
                if isinstance(v, str) and v.strip():
                    exp_ids.append(v.strip())
            for v in q.get("exp_ids", []):
                if not isinstance(v, str):
                    continue
                for part in v.split(","):
                    if part.strip():
                        exp_ids.append(part.strip())

            # de-dupe, preserve order
            seen: set = set()
            exp_ids = [x for x in exp_ids if not (x in seen or seen.add(x))]
            if not exp_ids:
                return _json_response(self, 400, {"error": "missing_exp_id"})

            runs_all: List[Dict[str, Any]] = []
            exp_meta: Dict[str, Any] = {}
            for exp_id in exp_ids:
                exp_dir = cfg.experiments_root / exp_id
                mf = _load_manifest(exp_dir)
                if mf is None:
                    continue
                exp_meta[exp_id] = mf
                runs_all.extend(_summarize_runs(cfg, exp_id=exp_id, exp_dir=exp_dir))
            return _json_response(self, 200, {"exp_ids": exp_ids, "experiments": exp_meta, "runs": runs_all})

        m = re.match(r"^/api/experiments/([^/]+)/runs$", path)
        if m:
            exp_id = m.group(1)
            exp_dir = cfg.experiments_root / exp_id
            mf = _load_manifest(exp_dir)
            if mf is None:
                return _json_response(self, 404, {"error": "experiment_not_found", "exp_id": exp_id})

            runs_out = _summarize_runs(cfg, exp_id=exp_id, exp_dir=exp_dir)
            return _json_response(self, 200, {"exp_id": exp_id, "manifest": mf, "runs": runs_out})

        return _json_response(self, 404, {"error": "unknown_api_route", "path": path})

    def _handle_api_post(self, path: str) -> None:
        if path == "/api/next-experiment":
            return self._handle_next_experiment()
        if path == "/api/create-experiment":
            return self._handle_create_experiment()
        if path == "/api/queue/requeue-run":
            return self._handle_requeue_run()
        if path == "/api/queue/submit-prompt":
            return self._handle_queue_submit_prompt()
        if path == "/api/queue/move-prompt":
            return self._handle_queue_move_prompt()
        if path == "/api/queue/comfy-cancel":
            return self._handle_comfy_cancel()
        if path == "/api/queue/comfy-clear":
            return self._handle_comfy_clear()
        if path == "/api/orchestrator/state":
            return self._handle_orchestrator_state_post()
        if path == "/api/orchestrator/saved-items":
            return self._handle_orchestrator_saved_item_post()
        if path == "/api/queue/ledger-control":
            return self._handle_queue_ledger_control()
        if path == "/api/discovery/trim":
            return self._handle_discovery_trim_post()
        if path == "/api/discovery/asset-ratings/verify":
            return self._handle_discovery_asset_ratings_verify_post()
        if path == "/api/discovery/asset-recover":
            return self._handle_discovery_asset_recover_post()
        if path == "/api/discovery/ensure-thumb":
            return self._handle_discovery_ensure_thumb_post()
        if path == "/api/discovery/library/ensure":
            return self._handle_discovery_library_ensure_post()
        if path == "/api/discovery/asset-ratings/set":
            return self._handle_discovery_asset_ratings_set_post()
        if path == "/api/discovery/asset-appetite/set":
            return self._handle_discovery_asset_appetite_set_post()
        if path == "/api/discovery/disposition-catalog":
            return self._handle_discovery_disposition_catalog_post()
        if path == "/api/discovery/asset-disposition/toggle":
            return self._handle_discovery_asset_disposition_toggle_post()
        if path == "/api/discovery/asset-disposition/run-step":
            return self._handle_discovery_asset_disposition_run_step_post()
        if path == "/api/discovery/identity-still/mint":
            return self._handle_discovery_identity_still_mint_post()
        if path == "/api/discovery/work-items/create":
            return self._handle_discovery_work_items_create_post()
        if path == "/api/discovery/work-items/cancel":
            return self._handle_discovery_work_items_cancel_post()
        if path == "/api/discovery/work-items/priority":
            return self._handle_discovery_work_items_priority_post()
        if path == "/api/discovery/asset-triage/complete":
            return self._handle_discovery_asset_triage_complete_post()
        if path == "/api/discovery/asset-triage/complete-batch":
            return self._handle_discovery_asset_triage_complete_batch_post()
        if path == "/api/workflow-explorer/factory/assets":
            return self._handle_factory_assets_post()
        if path == "/api/workflow-explorer/factory/workflows":
            return self._handle_factory_workflows_post()
        if path == "/api/shape-factory/queue":
            return self._handle_shape_factory_queue_post()
        if path == "/api/shape-factory/replay":
            return self._handle_shape_factory_replay_post()
        if path == "/api/shape-factory/derive":
            return self._handle_shape_factory_derive_post()
        if path == "/api/shape-factory/unqueue":
            return self._handle_shape_factory_unqueue_post()
        if path == "/api/shape-factory/begin-edit":
            return self._handle_shape_factory_begin_edit_post()
        if path == "/api/shape-factory/finish-edit":
            return self._handle_shape_factory_finish_edit_post()
        if path == "/api/shape-factory/discard":
            return self._handle_shape_factory_discard_post()
        if path == "/api/shape-factory/markers":
            return self._handle_shape_factory_markers_post()
        if path == "/api/shape-factory/update-pending-trim":
            return self._handle_shape_factory_update_pending_trim_post()
        if path == "/api/shape-factory/update-pending-binding":
            return self._handle_shape_factory_update_pending_binding_post()
        if path == "/api/shape-factory/update-owned-prompt":
            return self._handle_shape_factory_update_owned_prompt_post()
        if path == "/api/shape-factory/update-owned-params":
            return self._handle_shape_factory_update_owned_params_post()
        if path == "/api/shape-factory/promote-template":
            return self._handle_shape_factory_promote_template_post()
        if path == "/api/shape-factory/clips":
            return self._handle_shape_factory_clips_post()
        if path == "/api/shape-factory/quarantine/release":
            return self._handle_shape_factory_quarantine_release_post()
        if path == "/api/shape-factory/template-promotions/set":
            return self._handle_shape_factory_template_promotions_set_post()
        if path == "/api/shape-factory/input-curation/collections":
            return self._handle_shape_factory_input_curation_collections_post()
        if path == "/api/shape-factory/input-curation/bindings":
            return self._handle_shape_factory_input_curation_bindings_post()
        if path == "/api/shape-factory/input-curation/tags":
            return self._handle_shape_factory_input_curation_tags_post()
        if path == "/api/shape-factory/input-curation/stills/tag":
            return self._handle_shape_factory_input_curation_stills_tag_post()
        if path == "/api/shape-factory/input-curation/stills/tag/schedule":
            return self._handle_shape_factory_input_curation_stills_tag_schedule_post()
        if path == "/api/shape-factory/input-curation/stills/tag/drain":
            return self._handle_shape_factory_input_curation_stills_tag_drain_post()
        if path == "/api/shape-factory/hourly-schedule":
            return self._handle_shape_factory_hourly_schedule_post()
        if path == "/api/vision/tag-judgment":
            return self._handle_vision_tag_judgment_post()
        return _json_response(self, 404, {"error": "unknown_api_route", "path": path})

    def _handle_shape_factory_hourly_schedule_post(self) -> None:
        """
        POST /api/shape-factory/hourly-schedule
          { interval_minutes?, enabled?, submit_mode?, comfy_queue_min?, comfy_queue_max?, pending_queue_max?, mark_tick? }
        """
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _hourly_schedule_set_payload(cfg, body if isinstance(body, dict) else {})
            code = 200 if payload.get("ok") else 500
            return _json_response(self, code, payload)
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "hourly_schedule_set_failed", "detail": str(e)})

    def _handle_shape_factory_quarantine_release_post(self) -> None:
        """POST /api/shape-factory/quarantine/release — human review release."""
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _shape_factory_quarantine_release_payload(body)
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except FileNotFoundError as e:
            return _json_response(self, 404, {"ok": False, "error": "not_found", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "quarantine_release_failed", "detail": str(e)})
        return _json_response(self, 200, payload)

    def _handle_shape_factory_template_promotions_set_post(self) -> None:
        """POST /api/shape-factory/template-promotions/set — temporary or long-term template promotion."""
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        if not isinstance(body, dict):
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": "JSON object required"})
        try:
            payload = _shape_factory_template_promotions_set_payload(cfg, body)
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except Exception as e:
            return _json_response(
                self,
                500,
                {"ok": False, "error": "template_promotions_set_failed", "detail": str(e)},
            )
        return _json_response(self, 200, payload)

    def _handle_shape_factory_input_curation_collections_post(self) -> None:
        """POST /api/shape-factory/input-curation/collections — CRUD collections and items."""
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        if not isinstance(body, dict):
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": "JSON object required"})
        try:
            payload = _shape_factory_input_curation_collections_mutate_payload(cfg, body)
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except Exception as e:
            return _json_response(
                self,
                500,
                {"ok": False, "error": "input_curation_collections_failed", "detail": str(e)},
            )
        return _json_response(self, 200, payload)

    def _handle_shape_factory_input_curation_bindings_post(self) -> None:
        """POST /api/shape-factory/input-curation/bindings — attach/detach collections per family."""
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        if not isinstance(body, dict):
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": "JSON object required"})
        try:
            payload = _shape_factory_input_curation_bindings_mutate_payload(cfg, body)
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "input_curation_bindings_failed", "detail": str(e)})
        return _json_response(self, 200, payload)

    def _handle_shape_factory_input_curation_tags_post(self) -> None:
        """POST /api/shape-factory/input-curation/tags — set tags/note for a still content_id."""
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        if not isinstance(body, dict):
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": "JSON object required"})
        try:
            payload = _shape_factory_input_curation_tags_mutate_payload(cfg, body)
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "input_curation_tags_failed", "detail": str(e)})
        return _json_response(self, 200, payload)

    def _handle_shape_factory_input_curation_stills_tag_post(self) -> None:
        """POST /api/shape-factory/input-curation/stills/tag — enqueue PromptGen batch (background)."""
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        if not isinstance(body, dict):
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": "JSON object required"})
        try:
            payload = _shape_factory_input_curation_stills_tag_enqueue_payload(cfg, body)
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except Exception as e:
            return _json_response(
                self, 500, {"ok": False, "error": "input_curation_stills_tag_failed", "detail": str(e)}
            )
        return _json_response(self, 200, payload)

    def _handle_shape_factory_input_curation_stills_tag_schedule_post(self) -> None:
        """POST /api/shape-factory/input-curation/stills/tag/schedule — update index-hour knobs."""
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _shape_factory_input_curation_stills_tag_schedule_set_payload(
                cfg, body if isinstance(body, dict) else {}
            )
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except Exception as e:
            return _json_response(
                self, 500, {"ok": False, "error": "still_tag_schedule_set_failed", "detail": str(e)}
            )
        return _json_response(self, 200, payload)

    def _handle_shape_factory_input_curation_stills_tag_drain_post(self) -> None:
        """POST /api/shape-factory/input-curation/stills/tag/drain — kick an index-hour drain tick."""
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            body = {}
        if not isinstance(body, dict):
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": "JSON object required"})
        try:
            payload = _shape_factory_input_curation_stills_tag_drain_payload(cfg, body)
        except Exception as e:
            return _json_response(
                self, 500, {"ok": False, "error": "still_tag_drain_failed", "detail": str(e)}
            )
        return _json_response(self, 200, payload)

    def _handle_vision_tag_judgment_post(self) -> None:
        """POST /api/vision/tag-judgment — save blind tag labels for one sample."""
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _vision_tag_judgment_post_payload(cfg, body)
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "vision_tag_judgment_failed", "detail": str(e)})
        code = 200 if payload.get("ok") else 400
        return _json_response(self, code, payload)

    def _handle_shape_factory_replay_post(self) -> None:
        """POST /api/shape-factory/replay — re-run (or extend) a prior job/pair."""
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _shape_factory_replay_payload(cfg, body)
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except FileNotFoundError as e:
            return _json_response(self, 404, {"ok": False, "error": "not_found", "detail": str(e)})
        except RuntimeError as e:
            qerr = _quarantine_runtime_error_payload(e)
            if qerr is not None:
                return _json_response(self, 409, qerr)
            return _json_response(self, 502, {"ok": False, "error": "shape_factory_replay_failed", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "shape_factory_replay_failed", "detail": str(e)})
        status = 200 if payload.get("ok", True) else 400
        return _json_response(self, status, payload)

    def _handle_shape_factory_derive_post(self) -> None:
        """POST /api/shape-factory/derive — rewire a prior job into a new combo."""
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _shape_factory_derive_payload(cfg, body)
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except FileNotFoundError as e:
            return _json_response(self, 404, {"ok": False, "error": "not_found", "detail": str(e)})
        except RuntimeError as e:
            qerr = _quarantine_runtime_error_payload(e)
            if qerr is not None:
                return _json_response(self, 409, qerr)
            return _json_response(self, 502, {"ok": False, "error": "shape_factory_derive_failed", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "shape_factory_derive_failed", "detail": str(e)})
        status = 200 if payload.get("ok", True) else 400
        return _json_response(self, status, payload)

    def _handle_shape_factory_unqueue_post(self) -> None:
        """POST /api/shape-factory/unqueue — waiting-queue delete + demote factory job to pending."""
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _shape_factory_unqueue_payload(cfg, body)
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "shape_factory_unqueue_failed", "detail": str(e)})
        if payload.get("error") == "still_running":
            return _json_response(self, 409, payload)
        code = 200 if payload.get("ok", True) else 502
        return _json_response(self, code, payload)

    def _handle_shape_factory_begin_edit_post(self) -> None:
        """POST /api/shape-factory/begin-edit — lock job as editing (unqueue waiting Comfy prompt)."""
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _shape_factory_begin_edit_payload(cfg, body if isinstance(body, dict) else {})
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "shape_factory_begin_edit_failed", "detail": str(e)})
        if payload.get("error") in {"still_running", "not_editable"}:
            return _json_response(self, 409, payload)
        if payload.get("error") == "comfy_unreachable":
            return _json_response(self, 502, payload)
        if payload.get("error") == "job_not_found":
            return _json_response(self, 404, payload)
        code = 200 if payload.get("ok", True) else 400
        return _json_response(self, code, payload)

    def _handle_shape_factory_finish_edit_post(self) -> None:
        """POST /api/shape-factory/finish-edit — release editing (later|cancel|now)."""
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _shape_factory_finish_edit_payload(cfg, body if isinstance(body, dict) else {})
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "shape_factory_finish_edit_failed", "detail": str(e)})
        if payload.get("error") in {"not_editing", "still_running", "bad_action"}:
            return _json_response(self, 409 if payload.get("error") != "bad_action" else 400, payload)
        if payload.get("error") == "job_not_found":
            return _json_response(self, 404, payload)
        code = 200 if payload.get("ok", True) else 400
        return _json_response(self, code, payload)

    def _handle_shape_factory_discard_post(self) -> None:
        """POST /api/shape-factory/discard — remove a pending factory job from the active set."""
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _shape_factory_discard_payload(cfg, body)
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "shape_factory_discard_failed", "detail": str(e)})
        if payload.get("error") in {"not_pending", "still_on_comfy"}:
            return _json_response(self, 409, payload)
        if payload.get("error") == "job_not_found":
            return _json_response(self, 404, payload)
        code = 200 if payload.get("ok", True) else 400
        return _json_response(self, code, payload)

    def _handle_shape_factory_markers_post(self) -> None:
        """POST /api/shape-factory/markers — { content_id, key, value, source?, force? }."""
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _shape_factory_markers_set_payload(cfg, body if isinstance(body, dict) else {})
            code = 200 if payload.get("ok") else 409
            return _json_response(self, code, payload)
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "markers_set_failed", "detail": str(e)})

    def _handle_shape_factory_update_pending_trim_post(self) -> None:
        """POST /api/shape-factory/update-pending-trim — patch VHS window on a pending job."""
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _shape_factory_update_pending_trim_payload(cfg, body)
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except Exception as e:
            return _json_response(
                self, 500, {"ok": False, "error": "shape_factory_update_pending_trim_failed", "detail": str(e)}
            )
        if payload.get("error") in {"not_pending", "still_on_comfy"}:
            return _json_response(self, 409, payload)
        if payload.get("error") == "job_not_found":
            return _json_response(self, 404, payload)
        code = 200 if payload.get("ok", True) else 400
        return _json_response(self, code, payload)

    def _handle_shape_factory_update_pending_binding_post(self) -> None:
        """POST /api/shape-factory/update-pending-binding — patch one binding on a pending/editing job."""
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _shape_factory_update_pending_binding_payload(cfg, body)
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except Exception as e:
            return _json_response(
                self, 500, {"ok": False, "error": "shape_factory_update_pending_binding_failed", "detail": str(e)}
            )
        if payload.get("error") in {"not_pending", "still_on_comfy"}:
            return _json_response(self, 409, payload)
        if payload.get("error") == "job_not_found":
            return _json_response(self, 404, payload)
        code = 200 if payload.get("ok", True) else 400
        return _json_response(self, code, payload)

    def _handle_shape_factory_update_owned_prompt_post(self) -> None:
        """POST /api/shape-factory/update-owned-prompt — patch job-owned positive/negative."""
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _shape_factory_update_owned_prompt_payload(cfg, body if isinstance(body, dict) else {})
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except Exception as e:
            return _json_response(
                self, 500, {"ok": False, "error": "shape_factory_update_owned_prompt_failed", "detail": str(e)}
            )
        if payload.get("error") in {"not_pending", "still_on_comfy", "prompt_frozen"}:
            return _json_response(self, 409, payload)
        if payload.get("error") == "job_not_found":
            return _json_response(self, 404, payload)
        code = 200 if payload.get("ok", True) else 400
        return _json_response(self, code, payload)

    def _handle_shape_factory_update_owned_params_post(self) -> None:
        """POST /api/shape-factory/update-owned-params — patch frames/steps/overlap/seed."""
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _shape_factory_update_owned_params_payload(cfg, body if isinstance(body, dict) else {})
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except Exception as e:
            return _json_response(
                self, 500, {"ok": False, "error": "shape_factory_update_owned_params_failed", "detail": str(e)}
            )
        if payload.get("error") in {"not_pending", "still_on_comfy"}:
            return _json_response(self, 409, payload)
        if payload.get("error") == "job_not_found":
            return _json_response(self, 404, payload)
        code = 200 if payload.get("ok", True) else 400
        return _json_response(self, code, payload)

    def _handle_shape_factory_promote_template_post(self) -> None:
        """POST /api/shape-factory/promote-template — fork/overwrite family prompt library from job."""
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _shape_factory_promote_template_payload(cfg, body if isinstance(body, dict) else {})
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except Exception as e:
            return _json_response(
                self, 500, {"ok": False, "error": "shape_factory_promote_template_failed", "detail": str(e)}
            )
        if payload.get("error") == "job_not_found":
            return _json_response(self, 404, payload)
        code = 200 if payload.get("ok", True) else 400
        return _json_response(self, code, payload)

    def _handle_shape_factory_clips_post(self) -> None:
        """POST /api/shape-factory/clips — create/update/delete/set_default."""
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _shape_factory_clips_mutate_payload(cfg, body if isinstance(body, dict) else {})
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except KeyError as e:
            return _json_response(self, 404, {"ok": False, "error": "not_found", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "clips_mutate_failed", "detail": str(e)})
        code = 200 if payload.get("ok", True) else 400
        return _json_response(self, code, payload)

    def _handle_shape_factory_queue_post(self) -> None:
        """POST /api/shape-factory/queue — generate + submit one projected combo."""
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _shape_factory_queue_payload(cfg, body)
        except ValueError as e:
            _rec, err_body = _record_shape_factory_queue_attempt(cfg, body=body, ok=False, exc=e, http_status=400)
            print(
                f"[experiments-ui] shape-factory/queue failed attempt={_rec.get('attempt_id')} "
                f"family={_rec.get('family_slug')} error={_rec.get('error')}: {e}",
                flush=True,
            )
            return _json_response(self, 400, err_body or {"ok": False, "error": "bad_request", "detail": str(e)})
        except FileNotFoundError as e:
            _rec, err_body = _record_shape_factory_queue_attempt(cfg, body=body, ok=False, exc=e, http_status=404)
            print(
                f"[experiments-ui] shape-factory/queue failed attempt={_rec.get('attempt_id')} "
                f"family={_rec.get('family_slug')} error={_rec.get('error')}: {e}",
                flush=True,
            )
            return _json_response(self, 404, err_body or {"ok": False, "error": "not_found", "detail": str(e)})
        except RuntimeError as e:
            qerr = _quarantine_runtime_error_payload(e)
            status = 409 if qerr is not None else 502
            _rec, err_body = _record_shape_factory_queue_attempt(cfg, body=body, ok=False, exc=e, http_status=status)
            print(
                f"[experiments-ui] shape-factory/queue failed attempt={_rec.get('attempt_id')} "
                f"family={_rec.get('family_slug')} error={_rec.get('error')}: {e}",
                flush=True,
            )
            body_out = err_body or qerr or {"ok": False, "error": "shape_factory_queue_failed", "detail": str(e)}
            if qerr is not None and err_body is not None:
                body_out = {**err_body, "error": "workflow_quarantined"}
            return _json_response(self, status, body_out)
        except Exception as e:
            _rec, err_body = _record_shape_factory_queue_attempt(cfg, body=body, ok=False, exc=e, http_status=500)
            print(
                f"[experiments-ui] shape-factory/queue failed attempt={_rec.get('attempt_id')} "
                f"family={_rec.get('family_slug')} error={_rec.get('error')}: {e}",
                flush=True,
            )
            return _json_response(
                self, 500, err_body or {"ok": False, "error": "shape_factory_queue_failed", "detail": str(e)}
            )
        _rec, _ = _record_shape_factory_queue_attempt(cfg, body=body, ok=True, payload=payload, http_status=200)
        if isinstance(payload, dict) and _rec.get("attempt_id"):
            payload = {**payload, "attempt_id": _rec.get("attempt_id")}
        return _json_response(self, 200, payload)

    def _read_request_json(self) -> Optional[Dict[str, Any]]:
        n = _safe_int(self.headers.get("Content-Length"))
        if n is None or n <= 0 or n > 10_000_000:
            return None
        raw = self.rfile.read(n)
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        return obj if isinstance(obj, dict) else None

    def _handle_factory_browse_get(self, q: Dict[str, List[str]]) -> None:
        cfg = self.server.cfg
        roots = [
            {
                "id": str(root.get("id") or ""),
                "label": str(root.get("label") or root.get("id") or ""),
                "kind": str(root.get("kind") or "asset"),
                "path": str(root.get("path") or ""),
                "exists": Path(str(root.get("path") or "")).is_dir(),
            }
            for root in cfg.factory_browse_roots
        ]

        kind_hint = (q.get("kind") or [""])[0].strip().lower()
        default_root = next((r["id"] for r in roots if kind_hint and r.get("kind") == kind_hint), roots[0]["id"] if roots else "")
        root_id = (q.get("root") or [default_root])[0].strip()
        root_cfg = _factory_browse_root_by_id(cfg, root_id)
        if root_cfg is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_root", "roots": roots})

        root_path = Path(str(root_cfg.get("path") or ""))
        if not root_path.is_dir():
            return _json_response(
                self,
                200,
                {
                    "ok": True,
                    "roots": roots,
                    "root": {**root_cfg, "exists": False},
                    "dir": "",
                    "parent": None,
                    "entries": [],
                    "error": "root_missing",
                },
            )

        rel_dir = _normalize_rel_posix((q.get("dir") or [""])[0].strip())
        target = root_path.resolve() if not rel_dir else _safe_join(root_path, rel_dir)
        if target is None or not target.is_dir():
            return _json_response(self, 400, {"ok": False, "error": "bad_dir", "root": root_id, "dir": rel_dir, "roots": roots})

        kind = (q.get("kind") or [str(root_cfg.get("kind") or "asset")])[0].strip().lower()
        if kind not in {"asset", "workflow", "all"}:
            kind = str(root_cfg.get("kind") or "asset")
        media_type_filter = (q.get("media_type") or ["all"])[0].strip().lower()
        if media_type_filter not in {"all", "image", "video"}:
            media_type_filter = "all"
        if kind != "asset":
            media_type_filter = "all"
        qtext = (q.get("q") or [""])[0].strip().lower()
        limit = 300
        for v in q.get("limit", []):
            li = _safe_int(v)
            if li is not None:
                limit = max(1, min(2000, int(li)))
                break

        entries: List[Dict[str, Any]] = []
        scanned = 0
        scan_cap = max(limit * 8, 1000)
        try:
            iterator = os.scandir(str(target))
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "browse_failed", "detail": str(e)})

        with iterator:
            for entry in iterator:
                scanned += 1
                if scanned > scan_cap and len(entries) >= limit:
                    break
                child = target / entry.name
                try:
                    is_dir = entry.is_dir()
                    is_file = entry.is_file()
                except Exception:
                    continue
                if not is_dir and not is_file:
                    continue
                if qtext and qtext not in entry.name.lower():
                    continue
                if is_file and not _factory_browse_file_allowed(child, kind, media_type_filter):
                    continue
                try:
                    rel = child.resolve().relative_to(root_path.resolve()).as_posix()
                except Exception:
                    continue
                try:
                    st = entry.stat()
                    size = st.st_size
                    mtime = st.st_mtime
                except Exception:
                    size = 0
                    mtime = 0
                media_type = "directory" if is_dir else _factory_media_type_for_path(entry.name)
                entries.append(
                    {
                        "name": entry.name,
                        "path": str(child.resolve()),
                        "relpath": rel,
                        "is_dir": is_dir,
                        "kind": "directory" if is_dir else kind,
                        "media_type": media_type,
                        "size": size,
                        "mtime": mtime,
                        "url": None if is_dir else _factory_browse_entry_url(root_id, rel, child),
                    }
                )
        entries.sort(key=lambda item: (not bool(item.get("is_dir")), str(item.get("name") or "").lower()))
        truncated = len(entries) > limit or scanned > scan_cap
        entries = entries[:limit]
        parent = posixpath.dirname(rel_dir) if rel_dir else None
        if parent == ".":
            parent = ""
        return _json_response(
            self,
            200,
            {
                "ok": True,
                "roots": roots,
                "root": {**root_cfg, "exists": True},
                "dir": rel_dir,
                "parent": parent,
                "entries": entries,
                "truncated": truncated,
                "limit": limit,
                "media_type": media_type_filter,
            },
        )

    def _handle_factory_browse_file_get(self, q: Dict[str, List[str]]) -> None:
        cfg = self.server.cfg
        root_id = (q.get("root") or [""])[0].strip()
        relpath = (q.get("relpath") or [""])[0].strip()
        root_cfg = _factory_browse_root_by_id(cfg, root_id)
        if root_cfg is None:
            return _json_response(self, 400, {"error": "bad_root"})
        root_path = Path(str(root_cfg.get("path") or ""))
        full = _safe_join(root_path, relpath)
        if full is None or not full.exists() or not full.is_file():
            return _json_response(self, 404, {"error": "file_not_found", "root": root_id, "relpath": relpath})
        if full.suffix.lower() not in _FACTORY_ASSET_PREVIEW_EXTS:
            return _json_response(self, 415, {"error": "unsupported_preview_type", "relpath": relpath})
        ctype, _enc = mimetypes.guess_type(str(full))
        if not ctype:
            ctype = "application/octet-stream"
        try:
            _stream_file(self, full, content_type=ctype, cache_control="public, max-age=60", allow_ranges=True)
        except Exception as e:
            return _json_response(self, 500, {"error": "read_failed", "detail": str(e)})

    def _handle_factory_assets_post(self) -> None:
        cfg = self.server.cfg
        obj = self._read_request_json()
        if obj is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        op = str(obj.get("op") or "add").strip().lower()
        if not cfg.factory_db_path.exists():
            return _json_response(self, 404, {"ok": False, "error": "factory_db_missing", "db_path": str(cfg.factory_db_path)})

        con = sqlite3.connect(cfg.factory_db_path)
        con.row_factory = sqlite3.Row
        try:
            if op == "add":
                bucket_id = _safe_int(obj.get("bucket_id"))
                raw_path = str(obj.get("path") or "").strip()
                if bucket_id is None:
                    return _json_response(self, 400, {"ok": False, "error": "missing_bucket_id"})
                if not raw_path:
                    return _json_response(self, 400, {"ok": False, "error": "missing_path"})
                bucket = _factory_get_bucket(con, int(bucket_id), "asset")
                if bucket is None:
                    return _json_response(self, 404, {"ok": False, "error": "asset_bucket_not_found", "bucket_id": bucket_id})

                resolved = _resolve_factory_asset_file(cfg, raw_path)
                allow_missing = bool(obj.get("allow_missing") or False)
                if not allow_missing and (not resolved.exists() or not resolved.is_file()):
                    return _json_response(self, 404, {"ok": False, "error": "asset_file_not_found", "path": raw_path})

                media_type = str(obj.get("media_type") or "").strip() or _factory_media_type_for_path(raw_path)
                role = str(obj.get("role") or "").strip() or _factory_role_for_media_type(media_type)
                now = _factory_utc_now()
                metadata = {
                    "added_by": "workflow_explorer_ui",
                    "exists_at_add": bool(resolved.exists() and resolved.is_file()),
                    "resolved_path_at_add": str(resolved),
                }
                con.execute(
                    """
                    INSERT INTO asset_items (bucket_id, path, media_type, role, status, metadata_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(bucket_id, path) DO UPDATE SET
                        media_type=excluded.media_type,
                        role=excluded.role,
                        metadata_json=excluded.metadata_json,
                        updated_at=excluded.updated_at
                    """,
                    (int(bucket_id), raw_path, media_type, role, "available", _factory_json_dumps(metadata), now, now),
                )
                con.commit()
            elif op in {"remove", "delete"}:
                item_id = _safe_int(obj.get("item_id"))
                if item_id is None:
                    return _json_response(self, 400, {"ok": False, "error": "missing_item_id"})
                row = con.execute(
                    """
                    SELECT ai.id
                    FROM asset_items ai
                    JOIN buckets b ON b.id = ai.bucket_id
                    WHERE ai.id = ? AND b.bucket_type = 'asset'
                    """,
                    (int(item_id),),
                ).fetchone()
                if row is None:
                    return _json_response(self, 404, {"ok": False, "error": "asset_item_not_found", "item_id": item_id})
                con.execute("DELETE FROM asset_items WHERE id = ?", (int(item_id),))
                con.commit()
            else:
                return _json_response(self, 400, {"ok": False, "error": "bad_op"})

            return _json_response(self, 200, _load_factory_summary(cfg.factory_db_path))
        except sqlite3.IntegrityError as e:
            return _json_response(self, 409, {"ok": False, "error": "factory_integrity_error", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "factory_asset_update_failed", "detail": str(e)})
        finally:
            con.close()

    def _handle_factory_workflows_post(self) -> None:
        cfg = self.server.cfg
        obj = self._read_request_json()
        if obj is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        op = str(obj.get("op") or "add").strip().lower()
        if not cfg.factory_db_path.exists():
            return _json_response(self, 404, {"ok": False, "error": "factory_db_missing", "db_path": str(cfg.factory_db_path)})

        con = sqlite3.connect(cfg.factory_db_path)
        con.row_factory = sqlite3.Row
        try:
            if op == "add":
                bucket_id = _safe_int(obj.get("bucket_id"))
                raw_path = str(obj.get("path") or "").strip()
                if bucket_id is None:
                    return _json_response(self, 400, {"ok": False, "error": "missing_bucket_id"})
                if not raw_path:
                    return _json_response(self, 400, {"ok": False, "error": "missing_path"})
                bucket = _factory_get_bucket(con, int(bucket_id), "workflow")
                if bucket is None:
                    return _json_response(self, 404, {"ok": False, "error": "workflow_bucket_not_found", "bucket_id": bucket_id})

                resolved = _resolve_factory_asset_file(cfg, raw_path)
                if not resolved.exists() or not resolved.is_file():
                    return _json_response(self, 404, {"ok": False, "error": "workflow_file_not_found", "path": raw_path})
                try:
                    workflow = _read_json(resolved)
                except Exception as e:
                    return _json_response(self, 400, {"ok": False, "error": "bad_workflow_json", "detail": str(e)})
                if not _looks_like_comfy_ui_workflow(workflow):
                    return _json_response(self, 400, {"ok": False, "error": "not_litegraph_workflow", "path": raw_path})

                input_contract, output_contract = _factory_workflow_contract(workflow)
                graph_hash = _factory_graph_fingerprint(workflow)
                workflow_type = str(obj.get("workflow_type") or "litegraph").strip() or "litegraph"
                now = _factory_utc_now()
                metadata = {
                    "added_by": "workflow_explorer_ui",
                    "node_count": len(workflow.get("nodes") or []),
                    "link_count": len(workflow.get("links") or []),
                    "resolved_path_at_add": str(resolved),
                }
                con.execute(
                    """
                    INSERT INTO workflow_items (
                        bucket_id, path, workflow_type, graph_hash, input_contract_json, output_contract_json,
                        metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(bucket_id, path) DO UPDATE SET
                        workflow_type=excluded.workflow_type,
                        graph_hash=excluded.graph_hash,
                        input_contract_json=excluded.input_contract_json,
                        output_contract_json=excluded.output_contract_json,
                        metadata_json=excluded.metadata_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        int(bucket_id),
                        raw_path,
                        workflow_type,
                        graph_hash,
                        _factory_json_dumps(input_contract),
                        _factory_json_dumps(output_contract),
                        _factory_json_dumps(metadata),
                        now,
                        now,
                    ),
                )
                con.commit()
            elif op in {"remove", "delete"}:
                item_id = _safe_int(obj.get("item_id"))
                if item_id is None:
                    return _json_response(self, 400, {"ok": False, "error": "missing_item_id"})
                row = con.execute(
                    """
                    SELECT wi.id
                    FROM workflow_items wi
                    JOIN buckets b ON b.id = wi.bucket_id
                    WHERE wi.id = ? AND b.bucket_type = 'workflow'
                    """,
                    (int(item_id),),
                ).fetchone()
                if row is None:
                    return _json_response(self, 404, {"ok": False, "error": "workflow_item_not_found", "item_id": item_id})
                con.execute("DELETE FROM workflow_items WHERE id = ?", (int(item_id),))
                con.commit()
            else:
                return _json_response(self, 400, {"ok": False, "error": "bad_op"})

            return _json_response(self, 200, _load_factory_summary(cfg.factory_db_path))
        except sqlite3.IntegrityError as e:
            return _json_response(self, 409, {"ok": False, "error": "factory_integrity_error", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "factory_workflow_update_failed", "detail": str(e)})
        finally:
            con.close()

    def _handle_discovery_library_get(self, q: Dict[str, List[str]]) -> None:
        """
        GET /api/discovery/library
          ?refresh=1 — rescan output/output/{og,wip}, rewrite JSON index
          ?q= — case-insensitive substring on relpath or filename
          ?since_days=N — keep items with mtime within last N days
          ?library=og|wip|all
          ?limit= — max items after sort (default 800, max 8000)
        """
        cfg = self.server.cfg
        refresh = False
        for v in q.get("refresh", []):
            if str(v).strip().lower() in ("1", "true", "yes", "on"):
                refresh = True
                break

        qtext = (q.get("q") or [""])[0].strip().lower()
        since_days: Optional[float] = None
        for v in q.get("since_days", []):
            since_days = _safe_float(v)
            if since_days is not None:
                break

        lib_filter = "all"
        for v in q.get("library", []):
            s = str(v).strip().lower()
            if s in ("og", "wip", "all"):
                lib_filter = s
                break

        limit = 800
        for v in q.get("limit", []):
            li = _safe_int(v)
            if li is not None:
                limit = max(1, min(8000, int(li)))
                break

        idx_path = cfg.discovery_index_path
        health_path = _discovery_index_health_path(idx_path)
        payload: Dict[str, Any]
        health: Optional[Dict[str, Any]] = None
        from_cache = False
        if refresh or not idx_path.exists():
            previous_payload = _load_discovery_index_disk(idx_path)
            try:
                payload = _build_discovery_og_wip_index(cfg)
                _atomic_write_json(idx_path, payload)
                health = _build_discovery_index_health(
                    cfg,
                    previous_index=previous_payload,
                    current_index=payload,
                    reason="refresh" if previous_payload is not None else "initial_build",
                    from_cache=False,
                )
                _atomic_write_json(health_path, health)
            except Exception as e:
                return _json_response(self, 500, {"error": "discovery_scan_failed", "detail": str(e)})
        else:
            loaded = _load_discovery_index_disk(idx_path)
            if loaded is None:
                try:
                    payload = _build_discovery_og_wip_index(cfg)
                    _atomic_write_json(idx_path, payload)
                    health = _build_discovery_index_health(
                        cfg,
                        previous_index=None,
                        current_index=payload,
                        reason="rebuild_bad_cache",
                        from_cache=False,
                    )
                    _atomic_write_json(health_path, health)
                except Exception as e:
                    return _json_response(self, 500, {"error": "discovery_scan_failed", "detail": str(e)})
            else:
                payload = loaded
                from_cache = True

        # Regroup when on-disk index predates (lib, exact-stem) merge for mp4+png pairs.
        try:
            if int(payload.get("version") or 0) < 5:
                previous_payload = payload
                payload = _build_discovery_og_wip_index(cfg)
                _atomic_write_json(idx_path, payload)
                health = _build_discovery_index_health(
                    cfg,
                    previous_index=previous_payload,
                    current_index=payload,
                    reason="schema_upgrade",
                    from_cache=False,
                )
                _atomic_write_json(health_path, health)
                from_cache = False
        except Exception as e:
            return _json_response(self, 500, {"error": "discovery_scan_failed", "detail": str(e)})

        if health is None:
            health = _load_discovery_health_disk(health_path)
            if not health or health.get("current_updated_at") != payload.get("updated_at"):
                health = _build_discovery_index_health(
                    cfg,
                    previous_index=None,
                    current_index=payload,
                    reason="cache_validation",
                    from_cache=from_cache,
                )
                try:
                    _atomic_write_json(health_path, health)
                except Exception:
                    pass

        items_in = payload.get("items")
        if not isinstance(items_in, list):
            items_in = []

        now = time.time()
        since_cut = None
        if since_days is not None and since_days > 0:
            since_cut = now - float(since_days) * 86400.0

        filtered: List[Dict[str, Any]] = []
        for it in items_in:
            if not isinstance(it, dict):
                continue
            lib = it.get("library")
            if lib_filter != "all" and lib != lib_filter:
                continue
            rp = str(it.get("relpath") or "")
            nm = str(it.get("name") or "")
            if qtext:
                blob_parts = [rp.lower(), nm.lower()]
                mems = it.get("members")
                if isinstance(mems, list):
                    for mm in mems:
                        if isinstance(mm, dict):
                            blob_parts.append(str(mm.get("relpath") or "").lower())
                            blob_parts.append(str(mm.get("name") or "").lower())
                blob = " ".join(blob_parts)
                if qtext not in blob:
                    continue
            if since_cut is not None:
                try:
                    mt = float(it.get("mtime") or 0)
                except Exception:
                    mt = 0.0
                if mt < since_cut:
                    continue
            filtered.append(it)

        total_after_filter = len(filtered)
        truncated = total_after_filter > limit
        filtered = filtered[:limit]

        out = {
            "version": payload.get("version", 1),
            "updated_at": payload.get("updated_at"),
            "index_path": str(idx_path),
            "from_cache": from_cache,
            "scan_ms": payload.get("scan_ms"),
            "item_count_total": payload.get("item_count"),
            "item_count_filtered": total_after_filter,
            "truncated": truncated,
            "limit": limit,
            "health": health,
            "items": filtered,
        }
        ratings_doc = _discovery_load_ratings_index(cfg)
        appetite_doc = _discovery_load_appetite_index(cfg)
        for it in out["items"]:
            if isinstance(it, dict):
                def _live_file_url(relpath: Any) -> Optional[str]:
                    if not isinstance(relpath, str) or not relpath.strip():
                        return None
                    norm = _normalize_rel_posix(relpath.strip())
                    if not norm:
                        return None
                    full = _safe_join(cfg.output_root, norm)
                    if full is None or not full.exists() or not full.is_file():
                        return None
                    return "/files/" + urllib.parse.quote(norm, safe="")

                rp = str(it.get("relpath") or "")
                vr = it.get("video_relpath")
                tr = it.get("thumb_relpath")
                it["url"] = _live_file_url(rp) or ""
                it["video_url"] = _live_file_url(vr)
                it["thumb_url"] = _live_file_url(tr)
                if ratings_doc or appetite_doc:
                    r = _discovery_ratings_for_item(ratings_doc, it, appetite_doc)
                    if r:
                        it["ratings"] = r
        return _json_response(self, 200, out)

    def _handle_discovery_library_item_get(self, q: Dict[str, List[str]]) -> None:
        """
        GET /api/discovery/library/item?group_id=... | ?relpath=...
        Lookup one merged Discovery row from the on-disk index (not subject to library list limit).
        """
        cfg = self.server.cfg
        gid = (q.get("group_id") or [""])[0].strip()
        rel = (q.get("relpath") or [""])[0].strip()
        if not gid and not rel:
            return _json_response(self, 400, {"ok": False, "error": "missing_group_id_or_relpath"})
        idx_path = cfg.discovery_index_path
        idx = _load_discovery_index_disk(idx_path) if idx_path.exists() else None
        if not isinstance(idx, dict):
            return _json_response(self, 400, {"ok": False, "error": "discovery_index_missing", "detail": str(idx_path)})
        item: Optional[Dict[str, Any]] = None
        if gid:
            item = _discovery_index_items_by_group_id(idx).get(gid)
        if item is None and rel:
            norm = _normalize_rel_posix(rel)
            if norm:
                item = _discovery_item_for_relpath(idx, norm)
        if not isinstance(item, dict) and rel:
            item = _discovery_synthetic_library_item_for_workspace_media(cfg, rel)
        if not isinstance(item, dict):
            return _json_response(self, 404, {"ok": False, "error": "not_in_discovery_index"})
        it = dict(item)

        def _live_file_url(relpath: Any) -> Optional[str]:
            return _discovery_lineage_file_url(cfg, relpath)

        for k in ("relpath", "video_relpath", "thumb_relpath"):
            u = _live_file_url(it.get(k))
            if u:
                it[f"{k}_url"] = u
        it["url"] = _live_file_url(it.get("relpath")) or it.get("url") or ""
        it["video_url"] = _live_file_url(it.get("video_relpath")) or it.get("video_url")
        it["thumb_url"] = _live_file_url(it.get("thumb_relpath")) or it.get("thumb_url") or (
            it["url"] if str(it.get("relpath") or "").lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")) else None
        )
        return _json_response(self, 200, {"ok": True, "item": it})

    def _handle_discovery_trim_get(self, q: Dict[str, List[str]]) -> None:
        """
        GET /api/discovery/trim?media_relpath=...&context=discovery-player

        Canonical data lives in ``<stem>.trims.json`` next to the video. Multiple presets per context
        are supported; the active preset drives playback defaults in the UI.
        """
        cfg = self.server.cfg
        media = (q.get("media_relpath") or [""])[0].strip()
        if not media or len(media) > _TRIM_MEDIA_REL_PATH_MAX:
            return _json_response(self, 400, {"error": "bad_media_relpath"})
        context = (q.get("context") or [DEFAULT_TRIM_CONTEXT])[0].strip() or DEFAULT_TRIM_CONTEXT
        if not _TRIM_CONTEXT_RE.match(context):
            return _json_response(self, 400, {"error": "bad_context"})
        media_abs = _discovery_trim_video_media_path(cfg, media)
        if media_abs is None or not media_abs.is_file():
            return _json_response(
                self,
                200,
                {
                    "found": False,
                    "media_relpath": media,
                    "context": context,
                    "active_preset_id": None,
                    "active": None,
                    "presets": [],
                },
            )
        sidecar = _discovery_trim_sidecar_path(media_abs)
        doc = _load_trims_document(sidecar)
        ctxs = doc.get("contexts")
        blk = ctxs.get(context) if isinstance(ctxs, dict) else None
        if not isinstance(blk, dict):
            return _json_response(
                self,
                200,
                {
                    "found": False,
                    "media_relpath": media,
                    "context": context,
                    "active_preset_id": None,
                    "active": None,
                    "presets": [],
                },
            )
        presets = blk.get("presets") if isinstance(blk.get("presets"), list) else []
        aid = blk.get("active_preset_id")
        aid_s = str(aid).strip() if aid is not None and str(aid).strip() else None
        active_row = None
        if aid_s:
            for p in presets:
                if isinstance(p, dict) and p.get("id") == aid_s:
                    active_row = p
                    break
        if active_row is None and presets:
            active_row = presets[0] if isinstance(presets[0], dict) else None
        return _json_response(
            self,
            200,
            {
                "found": bool(active_row),
                "media_relpath": media,
                "context": context,
                "active_preset_id": (active_row or {}).get("id") if isinstance(active_row, dict) else None,
                "active": active_row,
                "presets": presets,
            },
        )

    def _handle_discovery_embed_api_prompt_get(self, q: Dict[str, List[str]]) -> None:
        """
        GET /api/discovery/embed-api-prompt
          ?relpath= (required) &thumb_relpath= &video_relpath= &library=og|wip|all

        Reads Comfy ``prompt`` / ``workflow`` PNG text chunks and returns an API-format ``prompt``
        dict. UI workflows (nodes+links) are converted using POST ``{COMFYUI}/workflow/convert``
        (requires an extension such as workflow-to-api-converter on the Comfy server).
        """
        cfg = self.server.cfg
        primary = (q.get("relpath") or [""])[0].strip()
        if not primary:
            return _json_response(self, 200, {"ok": False, "error": "missing_relpath", "detail": "relpath is required"})

        abs_png, rel_png_api = _discovery_resolve_embed_png_abs(cfg, q)
        if abs_png is None or not rel_png_api:
            return _json_response(
                self,
                200,
                {
                    "ok": False,
                    "error": "png_not_found",
                    "detail": "No candidate PNG under og/wip (thumb, sibling of video, or primary .png).",
                },
            )

        try:
            chunks = _read_png_text_chunks(abs_png)
        except Exception as e:
            return _json_response(
                self,
                200,
                {"ok": False, "error": "png_read_failed", "detail": str(e), "png_relpath": rel_png_api},
            )

        praw = chunks.get("prompt")
        wfraw = chunks.get("workflow")
        pr_obj: Optional[Dict[str, Any]] = None
        wf_obj: Optional[Dict[str, Any]] = None
        if isinstance(praw, str) and praw.strip():
            try:
                v = json.loads(praw)
                if isinstance(v, dict):
                    pr_obj = v
            except Exception:
                pass
        if isinstance(wfraw, str) and wfraw.strip():
            try:
                v = json.loads(wfraw)
                if isinstance(v, dict):
                    wf_obj = v
            except Exception:
                pass

        if pr_obj is None and wf_obj is None:
            return _json_response(
                self,
                200,
                {
                    "ok": False,
                    "error": "no_embedded_json",
                    "detail": "PNG has no parsable workflow or prompt text chunk.",
                    "png_relpath": rel_png_api,
                },
            )

        _convert_hint = (
            "Install a Comfy extension that exposes POST /workflow/convert (e.g. workflow-to-api-converter), "
            "or save API-format prompt into the PNG."
        )

        if pr_obj is not None:
            if _looks_like_comfy_api_prompt(pr_obj):
                return _json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "source": "embedded_png_prompt_api",
                        "png_relpath": rel_png_api,
                        "prompt": pr_obj,
                    },
                )
            if _looks_like_comfy_ui_workflow(pr_obj):
                prompt, err, http = _comfy_convert_workflow_to_prompt_dict(cfg, pr_obj)
                if prompt is not None:
                    return _json_response(
                        self,
                        200,
                        {
                            "ok": True,
                            "source": "embedded_png_prompt_chunk_via_comfy",
                            "png_relpath": rel_png_api,
                            "prompt": prompt,
                            "comfy_convert_http": http,
                        },
                    )
                return _json_response(
                    self,
                    200,
                    {
                        "ok": False,
                        "error": "comfy_convert_failed",
                        "detail": err,
                        "hint": _convert_hint,
                        "png_relpath": rel_png_api,
                        "comfy_convert_http": http,
                    },
                )
            if wf_obj is None:
                return _json_response(
                    self,
                    200,
                    {
                        "ok": False,
                        "error": "unrecognized_prompt_chunk",
                        "detail": "prompt text chunk is neither API prompt nor UI workflow (nodes+links).",
                        "png_relpath": rel_png_api,
                    },
                )

        if wf_obj is not None:
            prompt, err, http = _comfy_convert_workflow_to_prompt_dict(cfg, wf_obj)
            if prompt is not None:
                return _json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "source": "embedded_png_workflow_via_comfy",
                        "png_relpath": rel_png_api,
                        "prompt": prompt,
                        "comfy_convert_http": http,
                    },
                )
            return _json_response(
                self,
                200,
                {
                    "ok": False,
                    "error": "comfy_convert_failed",
                    "detail": err,
                    "hint": _convert_hint,
                    "png_relpath": rel_png_api,
                    "comfy_convert_http": http,
                },
            )

        return _json_response(self, 200, {"ok": False, "error": "no_usable_workflow", "detail": "No workflow chunk to convert.", "png_relpath": rel_png_api})

    def _handle_discovery_workflow_facets_get(self, q: Dict[str, List[str]]) -> None:
        """
        GET /api/discovery/workflow-facets?relpath=...

        Exploratory endpoint: given any member relpath of a merged Discovery library row, describe
        what workflow metadata exists for the **MP4 + PNG pair** model:

        - PNG text chunk inventory + parsed ``prompt`` / ``workflow`` shapes
        - Derived facets (API graph-shape hash, path-like source strings, LoRA-ish node digests)
        - Litegraph hashes (``graph_hash``, ``recipe_hash``) when a UI workflow chunk exists
        - MP4 container ffprobe tag keys (optional embedded prompt/workflow in muxer tags)
        - A compact provenance summary aligned with how Discovery merges stems
        """
        cfg = self.server.cfg
        rel = (q.get("relpath") or [""])[0].strip()
        if not rel:
            return _json_response(self, 400, {"ok": False, "error": "missing_relpath"})
        try:
            payload = _discovery_build_workflow_facets_payload(cfg, rel)
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "workflow_facets_failed", "detail": str(e)})
        return _json_response(self, 200, payload)

    def _handle_discovery_asset_lineage_get(self, q: Dict[str, List[str]]) -> None:
        """
        GET /api/discovery/asset-lineage?relpath=...&max_depth=6&persist=0&graph_only=1&peek_group_id=...
          &infer_parents=1&infer_children=0

        Infer a navigable parent/child graph from embedded prompt path strings (PNG metadata),
        optionally persist discovered edges to ``discovery_lineage_edges.json`` for reverse
        (descendant) queries. ``graph_only=1`` reads the persisted graph only (fast after backfill).
        ``infer_children=1`` forward-fills via the inverted citation index
        (``discovery_lineage_citations.sqlite``), warming from stem-named candidates on cold miss.
        """
        cfg = self.server.cfg
        rel = (q.get("relpath") or [""])[0].strip()
        if not rel:
            return _json_response(self, 400, {"ok": False, "error": "missing_relpath"})
        max_depth = 6
        for v in q.get("max_depth", []):
            try:
                max_depth = int(v)
            except Exception:
                pass
            break
        persist = (q.get("persist") or [""])[0].strip().lower() in ("1", "true", "yes")
        graph_only = (q.get("graph_only") or [""])[0].strip().lower() in ("1", "true", "yes")
        infer_parents = True
        for v in q.get("infer_parents", []):
            if str(v).strip().lower() in ("0", "false", "no"):
                infer_parents = False
            else:
                infer_parents = True
            break
        infer_children = False
        for v in q.get("infer_children", []):
            if str(v).strip().lower() in ("1", "true", "yes"):
                infer_children = True
            else:
                infer_children = False
            break
        peek = (q.get("peek_group_id") or [""])[0].strip()

        idx_path = cfg.discovery_index_path
        idx = _load_discovery_index_disk(idx_path) if idx_path.exists() else None
        if not isinstance(idx, dict):
            return _json_response(self, 400, {"ok": False, "error": "discovery_index_missing", "detail": str(idx_path)})
        try:
            # Allow persist with graph_only when doing a child/parent spot fill so the UI
            # can write edges without the heavier multi-hop parent BFS path when desired.
            do_persist = bool(persist) and (not graph_only or infer_children or infer_parents)
            payload = _discovery_compute_asset_lineage(
                cfg,
                idx,
                rel,
                max_depth=max_depth,
                persist=do_persist,
                peek_group_id=peek or None,
                graph_only=graph_only,
                infer_parents=infer_parents,
                infer_children=infer_children,
            )
            if do_persist and graph_only and isinstance(payload, dict) and payload.get("ok"):
                edges = payload.get("edges")
                if isinstance(edges, list) and edges:
                    ts = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    rows = [{**e, "updated_at": ts} for e in edges if isinstance(e, dict)]
                    added = _discovery_persist_lineage_edge_rows(cfg, rows)
                    payload["persist"] = True
                    payload["persisted_new_edges"] = int(added)
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "asset_lineage_failed", "detail": str(e)})
        return _json_response(self, 200, payload)

    def _handle_discovery_asset_ratings_get(self, q: Dict[str, List[str]]) -> None:
        """
        GET /api/discovery/asset-ratings?relpath=...

        Per-asset ratings explorer: explicit XMP (with disk verification), source-inferred rollup,
        workflow graph_hash rollup, cited sources, and contributor evidence lists.
        """
        cfg = self.server.cfg
        rel = (q.get("relpath") or [""])[0].strip()
        if not rel:
            return _json_response(self, 400, {"ok": False, "error": "missing_relpath"})
        idx_path = cfg.discovery_index_path
        idx = _load_discovery_index_disk(idx_path) if idx_path.exists() else None
        if not isinstance(idx, dict):
            return _json_response(self, 400, {"ok": False, "error": "discovery_index_missing", "detail": str(idx_path)})
        try:
            payload = _discovery_compute_asset_ratings(cfg, idx, rel)
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "asset_ratings_failed", "detail": str(e)})
        status = 200 if payload.get("ok") else 404 if payload.get("error") == "ratings_index_missing" else 400
        return _json_response(self, status, payload)

    def _handle_discovery_rating_sampler_get(self, q: Dict[str, List[str]]) -> None:
        """
        GET /api/discovery/rating-sampler?limit=20&refresh=1

        Returns the latest heuristic rating queue (or builds a new session when refresh=1).
        """
        cfg = self.server.cfg
        try:
            payload = _discovery_rating_sampler_payload(cfg, q)
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "rating_sampler_failed", "detail": str(e)})
        status = 200 if payload.get("ok") else 404 if payload.get("error") == "discovery_index_missing" else 500
        return _json_response(self, status, payload)

    def _handle_discovery_asset_audit_get(self, q: Dict[str, List[str]]) -> None:
        """GET /api/discovery/asset-audit?family=<slug> — missing load_image sources."""
        cfg = self.server.cfg
        try:
            payload = _asset_audit_payload(cfg, q)
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "asset_audit_failed", "detail": str(e)})
        status = 200 if payload.get("ok") else 400
        return _json_response(self, status, payload)

    def _handle_home_summary_get(self, q: Dict[str, List[str]]) -> None:
        """GET /api/home/summary — resume-the-loop dashboard aggregation."""
        cfg = self.server.cfg
        try:
            payload = _home_summary_payload(cfg)
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "home_summary_failed", "detail": str(e)})
        return _json_response(self, 200, payload)

    def _handle_comfy_live_preview_get(self, q: Dict[str, List[str]]) -> None:
        """GET /api/comfy/live-preview?prompt_id=…&frame=N — latest or VHS frame JPEG/PNG bytes."""
        prompt_id = str((q.get("prompt_id") or [""])[0] or "").strip()
        if not prompt_id:
            return _json_response(self, 400, {"ok": False, "error": "missing_prompt_id"})
        frame: Optional[int] = None
        raw_frame = str((q.get("frame") or [""])[0] or "").strip()
        if raw_frame:
            try:
                frame = int(raw_frame)
            except ValueError:
                return _json_response(self, 400, {"ok": False, "error": "bad_frame"})
        d = _workspace_scripts_dir()
        if d.is_dir() and str(d) not in sys.path:
            sys.path.insert(0, str(d))
        try:
            from comfy_live_preview import live_preview_image  # type: ignore
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "live_preview_import_failed", "detail": str(e)})
        got = live_preview_image(prompt_id, frame=frame)
        if got is None:
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        raw, mime = got
        self.send_response(200)
        self.send_header("Content-Type", mime or "image/jpeg")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _handle_comfy_live_status_get(self, q: Dict[str, List[str]]) -> None:
        """GET /api/comfy/live-status?prompt_id=a,b — progress + has_preview for prompt ids."""
        d = _workspace_scripts_dir()
        if d.is_dir() and str(d) not in sys.path:
            sys.path.insert(0, str(d))
        try:
            from comfy_live_preview import live_status_payload  # type: ignore
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "live_status_import_failed", "detail": str(e)})
        raw_ids = (q.get("prompt_id") or q.get("prompt_ids") or [])
        ids: List[str] = []
        for chunk in raw_ids:
            for part in str(chunk or "").split(","):
                s = part.strip()
                if s:
                    ids.append(s)
        try:
            payload = live_status_payload(ids or None)
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "live_status_failed", "detail": str(e)})
        return _json_response(self, 200, payload)

    def _handle_comfy_logs_get(self, q: Dict[str, List[str]]) -> None:
        """
        GET /api/comfy/logs — proxy ComfyUI's in-memory log ring buffer.

        Comfy exposes this as ``GET /internal/logs/raw`` (frontend Logs panel):
        ``{ "entries": [{"t": iso, "m": text}, ...], "size": N }``.
        Optional ``?tail=N`` keeps the last N entries (default 300, max 2000).
        """
        cfg = self.server.cfg
        comfy = str(cfg.comfy_server).rstrip("/")
        tail = 300
        for v in q.get("tail", []):
            p = _safe_int(v)
            if p is not None:
                tail = max(1, min(2000, int(p)))
                break
        try:
            raw = _http_json("GET", f"{comfy}/internal/logs/raw", timeout_s=8)
        except Exception as e:
            # Fallback: plain text blob from /internal/logs
            try:
                text = _http_text(f"{comfy}/internal/logs", timeout_s=8)
                lines = text.splitlines()
                if len(lines) > tail:
                    lines = lines[-tail:]
                return _json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "source": "internal/logs",
                        "size": len(lines),
                        "entries": [{"t": None, "m": line} for line in lines],
                    },
                )
            except Exception as e2:
                return _json_response(
                    self,
                    502,
                    {"ok": False, "error": "comfy_logs_fetch_failed", "detail": f"{e}; fallback={e2}"},
                )
        entries: List[Dict[str, Any]] = []
        size = 0
        if isinstance(raw, dict):
            size = int(raw.get("size") or 0) if isinstance(raw.get("size"), (int, float)) else 0
            rows = raw.get("entries") if isinstance(raw.get("entries"), list) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                m = row.get("m")
                if m is None:
                    continue
                entries.append({"t": row.get("t"), "m": str(m)})
            if not size:
                size = len(entries)
        if len(entries) > tail:
            entries = entries[-tail:]
        return _json_response(
            self,
            200,
            {"ok": True, "source": "internal/logs/raw", "size": size, "entries": entries, "tail": tail},
        )

    def _handle_discovery_asset_recover_post(self) -> None:
        """
        POST /api/discovery/asset-recover  { family? , names?: [...], allow_remote? }

        Locate (local -> verified remote) each missing source, place it in input/,
        register it, and report per-name results. Names default to a family's audit.
        """
        cfg = self.server.cfg
        obj = self._read_request_json()
        if obj is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _asset_recover_payload(cfg, obj)
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "asset_recover_failed", "detail": str(e)})
        status = 200 if payload.get("ok") else 400
        return _json_response(self, status, payload)

    def _handle_discovery_ensure_thumb_post(self) -> None:
        """
        POST /api/discovery/ensure-thumb  { relpath, force? }

        Write a same-stem .png companion next to a video when missing (mid-frame via ffmpeg).
        """
        cfg = self.server.cfg
        obj = self._read_request_json()
        if obj is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _discovery_ensure_thumb_payload(cfg, obj if isinstance(obj, dict) else {})
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except FileNotFoundError as e:
            return _json_response(self, 404, {"ok": False, "error": "media_missing", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "ensure_thumb_failed", "detail": str(e)})
        status = 200 if payload.get("ok") else 400
        return _json_response(self, status, payload)

    def _handle_discovery_library_ensure_post(self) -> None:
        """
        POST /api/discovery/library/ensure  { relpath } | { relpaths: [...] }

        Tip one (or many) og/wip media paths into discovery_og_wip_index.json without a full rescan.
        """
        cfg = self.server.cfg
        obj = self._read_request_json()
        if obj is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _discovery_library_ensure_payload(cfg, obj if isinstance(obj, dict) else {})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "library_ensure_failed", "detail": str(e)})
        status = 200 if payload.get("ok") else 400
        return _json_response(self, status, payload)

    def _handle_discovery_asset_ratings_set_post(self) -> None:
        """
        POST /api/discovery/asset-ratings/set  { relpath, stars: 0-5, axis?: quality_axis }

        Set one quality axis (subject_beauty / render_quality / action_quality) or, when
        axis is omitted, set all three to the same star value. Updates derived explicit
        aggregate + XMP in ratings.sqlite. Returns ``saved`` only (UI applies axes from it);
        does not reload the discovery index.
        """
        cfg = self.server.cfg
        obj = self._read_request_json()
        if obj is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            saved = _set_asset_rating_payload(cfg, obj)
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except FileNotFoundError as e:
            return _json_response(self, 404, {"ok": False, "error": "media_missing", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "rating_set_failed", "detail": str(e)})
        _invalidate_ratings_caches(cfg)
        return _json_response(self, 200, {"ok": True, "saved": saved})

    def _handle_discovery_asset_appetite_set_post(self) -> None:
        """
        POST /api/discovery/asset-appetite/set
          { relpath, appetite: ""|less|neutral|more|fast_track, facet?: both|source|processing,
            job_key?, family_slug? }

        Record a 'do more WITH this' appetite + facet in ratings.sqlite (survives
        'ratings build'). fast_track also fires an immediate Extend (best-effort).
        """
        cfg = self.server.cfg
        obj = self._read_request_json()
        if obj is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            saved = _set_asset_appetite_payload(cfg, obj)
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except FileNotFoundError as e:
            return _json_response(self, 404, {"ok": False, "error": "media_missing", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "appetite_set_failed", "detail": str(e)})
        _invalidate_ratings_caches(cfg)
        return _json_response(self, 200, {"ok": True, "saved": saved})

    def _handle_discovery_disposition_catalog_get(self, q: Dict[str, List[str]]) -> None:
        """GET /api/discovery/disposition-catalog — merged marker catalog."""
        _ = q
        cfg = self.server.cfg
        try:
            payload = _discovery_disposition_catalog_payload(cfg)
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "catalog_load_failed", "detail": str(e)})
        return _json_response(self, 200, payload)

    def _handle_discovery_disposition_suggest_get(self, q: Dict[str, List[str]]) -> None:
        """GET /api/discovery/disposition-suggest — promoted entry markers from Q×A×facet."""
        cfg = self.server.cfg
        try:
            payload = _discovery_disposition_suggest_payload(cfg, q)
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "suggest_failed", "detail": str(e)})
        return _json_response(self, 200, payload)

    def _handle_discovery_disposition_catalog_post(self) -> None:
        """
        POST /api/discovery/disposition-catalog
          { markers?: [...], promotion_rules?: {...} } — writes runtime overlay.
        """
        cfg = self.server.cfg
        obj = self._read_request_json()
        if obj is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        d = _workspace_scripts_dir()
        if d.is_dir() and str(d) not in sys.path:
            sys.path.insert(0, str(d))
        from shape_factory_disposition import load_merged_catalog, save_catalog_overlay  # type: ignore

        og_root = _prefer_flat_library_dir(cfg.output_root, "og")
        current = load_merged_catalog(og_root=og_root, repo_root=_repo_root())
        if isinstance(obj.get("markers"), list):
            current["markers"] = obj["markers"]
        if isinstance(obj.get("promotion_rules"), dict):
            current["promotion_rules"] = obj["promotion_rules"]
        try:
            saved = save_catalog_overlay(og_root, current)
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "catalog_save_failed", "detail": str(e)})
        return _json_response(self, 200, {"ok": True, **saved, "catalog": _discovery_disposition_catalog_payload(cfg)})

    def _handle_discovery_asset_disposition_toggle_post(self) -> None:
        """
        POST /api/discovery/asset-disposition/toggle
          { relpath, marker, on?: bool, note?: string, modifiers?: string[] }
        """
        cfg = self.server.cfg
        obj = self._read_request_json()
        if obj is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            saved = _set_asset_disposition_toggle_payload(cfg, obj)
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except FileNotFoundError as e:
            return _json_response(self, 404, {"ok": False, "error": "media_missing", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "disposition_toggle_failed", "detail": str(e)})
        rel = str(obj.get("relpath") or "").strip()
        promotions = _discovery_disposition_suggest_payload(
            cfg,
            {
                "relpath": [rel],
                "quality": [str(obj["quality"])] if "quality" in obj else [],
                "appetite": [str(obj["appetite"])] if "appetite" in obj else [],
                "facet": [str(obj["facet"])] if "facet" in obj else [],
            },
        ).get("promotions")
        return _json_response(self, 200, {"ok": True, "saved": saved, "promotions": promotions})

    def _handle_discovery_asset_disposition_run_step_post(self) -> None:
        """
        POST /api/discovery/asset-disposition/run-step
          { relpath, step_id, job_key?, family_slug?, facet?, identity_anchor?, overrides? }
        """
        cfg = self.server.cfg
        obj = self._read_request_json()
        if obj is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _run_asset_disposition_step_payload(cfg, obj)
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except FileNotFoundError as e:
            return _json_response(self, 404, {"ok": False, "error": "media_missing", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "disposition_step_failed", "detail": str(e)})
        status = 200 if payload.get("ok", True) else 400
        return _json_response(self, status, payload)

    def _handle_discovery_identity_still_candidates_get(self, q: Dict[str, List[str]]) -> None:
        """GET /api/discovery/identity-still/candidates?relpath=&job_key=&family_slug="""
        cfg = self.server.cfg
        try:
            payload = _identity_still_candidates_payload(cfg, q)
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "identity_still_candidates_failed", "detail": str(e)})
        return _json_response(self, 200, payload)

    def _handle_discovery_identity_still_mint_post(self) -> None:
        """POST /api/discovery/identity-still/mint { video_relpath|video_path, at? }"""
        cfg = self.server.cfg
        obj = self._read_request_json()
        if obj is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _identity_still_mint_payload(cfg, obj if isinstance(obj, dict) else {})
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except FileNotFoundError as e:
            return _json_response(self, 404, {"ok": False, "error": "video_missing", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "identity_still_mint_failed", "detail": str(e)})
        return _json_response(self, 200, payload)

    def _handle_discovery_work_items_get(self, q: Dict[str, List[str]]) -> None:
        """GET /api/discovery/work-items?source_relpath=...&pool=...&status=..."""
        cfg = self.server.cfg
        try:
            payload = _discovery_work_items_list_payload(cfg, q)
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "work_items_list_failed", "detail": str(e)})
        return _json_response(self, 200, payload)

    def _handle_discovery_work_items_pool_get(self, q: Dict[str, List[str]]) -> None:
        """GET /api/discovery/work-items/pool?pool=extend"""
        cfg = self.server.cfg
        try:
            payload = _discovery_work_items_pool_payload(cfg, q)
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "work_items_pool_failed", "detail": str(e)})
        return _json_response(self, 200, payload)

    def _handle_discovery_work_items_create_post(self) -> None:
        """
        POST /api/discovery/work-items/create
          { source_relpath|relpath, routes?: [...], pool?, step_id?, queue_now? }
        """
        cfg = self.server.cfg
        obj = self._read_request_json()
        if obj is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _discovery_work_items_create_payload(cfg, obj)
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except FileNotFoundError as e:
            return _json_response(self, 404, {"ok": False, "error": "media_missing", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "work_items_create_failed", "detail": str(e)})
        return _json_response(self, 200, payload)

    def _handle_discovery_work_items_cancel_post(self) -> None:
        """POST /api/discovery/work-items/cancel { work_id, reason? }"""
        cfg = self.server.cfg
        obj = self._read_request_json()
        if obj is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _discovery_work_items_cancel_payload(cfg, obj)
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except FileNotFoundError as e:
            return _json_response(self, 404, {"ok": False, "error": "work_item_missing", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "work_items_cancel_failed", "detail": str(e)})
        return _json_response(self, 200, payload)

    def _handle_discovery_work_items_priority_post(self) -> None:
        """POST /api/discovery/work-items/priority { work_id, priority }"""
        cfg = self.server.cfg
        obj = self._read_request_json()
        if obj is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _discovery_work_items_priority_payload(cfg, obj)
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except FileNotFoundError as e:
            return _json_response(self, 404, {"ok": False, "error": "work_item_missing", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "work_items_priority_failed", "detail": str(e)})
        return _json_response(self, 200, payload)

    def _handle_discovery_asset_triage_complete_post(self) -> None:
        """
        POST /api/discovery/asset-triage/complete
          { relpath }
        """
        cfg = self.server.cfg
        obj = self._read_request_json()
        if obj is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            saved = _record_asset_triage_complete_payload(cfg, obj)
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except FileNotFoundError as e:
            return _json_response(self, 404, {"ok": False, "error": "media_missing", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "triage_complete_failed", "detail": str(e)})
        return _json_response(self, 200, {"ok": True, "saved": saved})

    def _handle_discovery_asset_triage_complete_batch_post(self) -> None:
        """
        POST /api/discovery/asset-triage/complete-batch
          { relpaths: string[] }
        Records triage for clips that have both explicit quality and appetite (rating complete).
        Clips missing either return to the rate pool.
        """
        cfg = self.server.cfg
        obj = self._read_request_json()
        if obj is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        try:
            payload = _record_batch_triage_complete_payload(cfg, obj)
        except ValueError as e:
            return _json_response(self, 400, {"ok": False, "error": "bad_request", "detail": str(e)})
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "triage_batch_failed", "detail": str(e)})
        return _json_response(self, 200, payload)

    def _handle_discovery_asset_ratings_verify_post(self) -> None:
        """
        POST /api/discovery/asset-ratings/verify
          { "relpath", "lens": "as_source"|"workflow"|"recipe",
            "verified": bool, "override_rating": 1-5|null, "note": "..." }

        Human review of inferred ratings for one asset. Stored in ratings_verifications.json
        (separate from the rebuildable ratings_index.json).
        """
        cfg = self.server.cfg
        obj = self._read_request_json()
        if not obj:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        rel = str(obj.get("relpath") or "").strip()
        if not rel:
            return _json_response(self, 400, {"ok": False, "error": "missing_relpath"})
        lens = str(obj.get("lens") or "").strip()
        if lens not in _RATINGS_VALID_LENSES:
            return _json_response(self, 400, {"ok": False, "error": "bad_lens", "detail": sorted(_RATINGS_VALID_LENSES)})
        verified = obj.get("verified") is True
        override_rating: Optional[int] = None
        if "override_rating" in obj and obj.get("override_rating") is not None:
            try:
                override_rating = int(obj.get("override_rating"))
            except Exception:
                return _json_response(self, 400, {"ok": False, "error": "bad_override_rating"})
            if override_rating < 1 or override_rating > 5:
                return _json_response(self, 400, {"ok": False, "error": "bad_override_rating"})
        note = str(obj.get("note") or "").strip() or None
        asset_key = _discovery_ratings_canonical_asset_key(rel)
        if not asset_key:
            return _json_response(self, 400, {"ok": False, "error": "bad_relpath"})
        try:
            saved = _discovery_persist_ratings_lens_verification(
                cfg,
                asset_key=asset_key,
                lens=lens,
                verified=verified,
                override_rating=override_rating,
                note=note,
            )
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "verify_save_failed", "detail": str(e)})
        idx_path = cfg.discovery_index_path
        idx = _load_discovery_index_disk(idx_path) if idx_path.exists() else None
        if not isinstance(idx, dict):
            return _json_response(self, 400, {"ok": False, "error": "discovery_index_missing"})
        try:
            payload = _discovery_compute_asset_ratings(cfg, idx, rel)
        except Exception as e:
            return _json_response(self, 500, {"ok": False, "error": "asset_ratings_refresh_failed", "detail": str(e)})
        return _json_response(self, 200, {"ok": True, "asset_key": asset_key, "lens": lens, "saved": saved, "ratings": payload})

    def _handle_discovery_trim_post(self) -> None:
        """
        POST /api/discovery/trim
          { "media_relpath", "context": "discovery-player", "op": "save_trim",
            "duration_sec", "in", "out", "preset_id"?, "label"?, "clear"? }

        Writes ``<stem>.trims.json`` beside the media file (see GET). ``clear`` or a trivial range
        removes the active preset entry (and clears ``active_preset_id``).
        """
        cfg = self.server.cfg
        obj = self._read_request_json()
        if not obj:
            return _json_response(self, 400, {"error": "bad_json"})
        op = str(obj.get("op") or "save_trim").strip().lower()
        if op != "save_trim":
            return _json_response(self, 400, {"error": "bad_op"})
        media = str(obj.get("media_relpath") or "").strip()
        if not media or len(media) > _TRIM_MEDIA_REL_PATH_MAX:
            return _json_response(self, 400, {"error": "bad_media_relpath"})
        context = str(obj.get("context") or DEFAULT_TRIM_CONTEXT).strip() or DEFAULT_TRIM_CONTEXT
        if not _TRIM_CONTEXT_RE.match(context):
            return _json_response(self, 400, {"error": "bad_context"})

        try:
            duration = float(obj.get("duration_sec"))
        except Exception:
            return _json_response(self, 400, {"error": "bad_duration_sec"})
        if not (duration > 0 and math.isfinite(duration)):
            return _json_response(self, 400, {"error": "bad_duration_sec"})

        clear = obj.get("clear") is True
        mi = obj.get("in")
        mo = obj.get("out")
        if not clear and mi is None and mo is None:
            return _json_response(self, 400, {"error": "missing_in_out"})

        bounds: Optional[Tuple[float, float]] = None
        if not clear:
            try:
                tin_f = float(mi)
                tout_f = float(mo)
            except Exception:
                return _json_response(self, 400, {"error": "bad_in_out"})
            bounds = _trim_clamp(tin_f, tout_f, duration)
            if bounds is None:
                return _json_response(self, 400, {"error": "invalid_range"})

        pid_in = str(obj.get("preset_id") or "").strip() or None
        label_in = (str(obj.get("label") or "Trim").strip() or "Trim")[:200]

        def _mut(doc: Dict[str, Any]) -> None:
            ctxs = doc.setdefault("contexts", {})
            if not isinstance(ctxs, dict):
                doc["contexts"] = {}
                ctxs = doc["contexts"]
            blk = ctxs.setdefault(context, {"active_preset_id": None, "presets": []})
            if not isinstance(blk, dict):
                blk = {"active_preset_id": None, "presets": []}
                ctxs[context] = blk
            presets = blk.setdefault("presets", [])
            if not isinstance(presets, list):
                blk["presets"] = []
                presets = blk["presets"]
            aid = blk.get("active_preset_id")
            aid_s = str(aid).strip() if aid is not None and str(aid).strip() else None

            def _remove_preset(pid: str) -> None:
                blk["presets"] = [p for p in presets if isinstance(p, dict) and p.get("id") != pid]
                cur = blk.get("active_preset_id")
                cur_s = str(cur).strip() if cur is not None and str(cur).strip() else None
                if cur_s == pid:
                    blk["active_preset_id"] = None

            if clear:
                if pid_in:
                    _remove_preset(pid_in)
                elif aid_s:
                    _remove_preset(aid_s)
                else:
                    blk["presets"] = []
                    blk["active_preset_id"] = None
                if not blk["presets"]:
                    ctxs.pop(context, None)
                return

            tin, tout = bounds  # set only when not clear (bounds validated above)
            if not _trim_is_nontrivial(tin, tout, duration):
                if aid_s:
                    _remove_preset(aid_s)
                blk["active_preset_id"] = None
                if not blk["presets"]:
                    ctxs.pop(context, None)
                return

            now = int(time.time())
            target_id = pid_in or aid_s
            for p in presets:
                if isinstance(p, dict) and p.get("id") == target_id:
                    p["in"] = tin
                    p["out"] = tout
                    p["label"] = label_in
                    p["at"] = now
                    blk["active_preset_id"] = p.get("id")
                    return
            nid = str(uuid.uuid4())
            presets.append({"id": nid, "label": label_in, "in": tin, "out": tout, "at": now})
            blk["active_preset_id"] = nid

        ok = _discovery_trim_mutate_document(cfg, media, _mut)
        if not ok:
            return _json_response(self, 404, {"error": "media_not_found", "media_relpath": media})

        media_abs = _discovery_trim_video_media_path(cfg, media)
        if media_abs is None or not media_abs.is_file():
            return _json_response(self, 200, {"ok": True, "media_relpath": media, "context": context, "active_preset_id": None, "active": None, "presets": []})
        doc2 = _load_trims_document(_discovery_trim_sidecar_path(media_abs))
        ctxs2 = doc2.get("contexts") or {}
        blk2 = ctxs2.get(context) or {}
        presets2 = blk2.get("presets") if isinstance(blk2.get("presets"), list) else []
        aid2 = blk2.get("active_preset_id")
        active = None
        for p in presets2:
            if isinstance(p, dict) and p.get("id") == aid2:
                active = p
                break
        return _json_response(
            self,
            200,
            {
                "ok": True,
                "media_relpath": media,
                "context": context,
                "active_preset_id": aid2,
                "active": active,
                "presets": presets2,
            },
        )

    def _handle_wip_get(self, q: Dict[str, List[str]]) -> None:
        """GET /api/wip?dir= — list date subdirs (dir empty) or MP4s in that date dir."""
        cfg = self.server.cfg
        if not cfg.wip_root.exists():
            return _json_response(self, 200, {"dates": [], "media": [], "dir": ""})

        dir_param = (q.get("dir") or [""])[0].strip() if q else ""
        dir_param = _normalize_rel_posix(dir_param)

        if not dir_param:
            # List date subdirs (YYYY-MM-DD)
            date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
            dates: List[Dict[str, Any]] = []
            for child in sorted([p for p in cfg.wip_root.iterdir() if p.is_dir()], key=lambda p: p.name, reverse=True):
                if date_re.match(child.name):
                    path_posix = _file_relpath_for_api(cfg.output_root, cfg.wip_root, child)
                    dates.append({"name": child.name, "path": path_posix, "date": child.name})
            return _json_response(self, 200, {"dates": dates, "media": [], "dir": ""})

        # List MP4s in wip_root/dir
        target_dir = _safe_join(cfg.wip_root, dir_param)
        if target_dir is None or not target_dir.is_dir():
            return _json_response(self, 400, {"error": "invalid_dir", "dir": dir_param})

        media: List[Dict[str, Any]] = []
        for f in sorted(target_dir.glob("*.mp4"), key=lambda p: p.name):
            if not f.is_file():
                continue
            relpath = _file_relpath_for_api(cfg.output_root, cfg.wip_root, f)
            try:
                st = f.stat()
                size = st.st_size
                mtime = st.st_mtime
            except Exception:
                size = 0
                mtime = 0
            media.append({
                "name": f.name,
                "path": relpath,
                "relpath": relpath,
                "size": size,
                "mtime": mtime,
            })
        return _json_response(self, 200, {"dates": [], "media": media, "dir": dir_param})

    def _handle_create_experiment(self) -> None:
        """POST /api/create-experiment — create a tune experiment from a wip base_mp4 relpath."""
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"error": "bad_json"})

        base_mp4_relpath = body.get("base_mp4_relpath")
        if not isinstance(base_mp4_relpath, str) or not base_mp4_relpath.strip():
            return _json_response(self, 400, {"error": "missing_base_mp4_relpath"})
        base_mp4_relpath = _normalize_rel_posix(base_mp4_relpath.strip())
        if not base_mp4_relpath:
            return _json_response(self, 400, {"error": "bad_base_mp4_relpath"})

        base_mp4 = _safe_join(cfg.output_root, base_mp4_relpath)
        if base_mp4 is None or not base_mp4.exists() or not base_mp4.is_file():
            return _json_response(self, 404, {"error": "base_mp4_not_found", "relpath": base_mp4_relpath})

        seed = _safe_int(body.get("seed"))
        if seed is None:
            return _json_response(self, 400, {"error": "missing_seed"})

        duration = _safe_float(body.get("duration_sec"))
        if duration is None:
            duration = 5.0

        new_exp_id = body.get("exp_id")
        if not isinstance(new_exp_id, str) or not new_exp_id.strip():
            stem = base_mp4.stem
            new_exp_id = _slug(f"tune_{stem}_{_now_stamp()}")
        else:
            new_exp_id = _slug(new_exp_id.strip())

        out_root = str(cfg.experiments_root)
        max_runs = _safe_int(body.get("max_runs")) or 200
        baseline_first = body.get("baseline_first")
        baseline_first = True if baseline_first is None else bool(baseline_first)

        sweep = body.get("sweep") if body.get("sweep") is not None else {}
        if not isinstance(sweep, dict):
            return _json_response(self, 400, {"error": "bad_sweep"})

        def add_values(flag: str, xs: Any) -> List[str]:
            if xs is None:
                return []
            if isinstance(xs, (str, int, float)):
                xs = [xs]
            if not isinstance(xs, list):
                return []
            out: List[str] = []
            for x in xs:
                if isinstance(x, (int, float)):
                    out.append(str(x))
                elif isinstance(x, str) and x.strip():
                    out.append(x.strip())
            return [flag, *out] if out else []

        gen_cmd: List[str] = [
            sys.executable,
            str(cfg.tune_script),
            "generate",
            str(base_mp4),
            "--out-root",
            out_root,
            "--exp-id",
            new_exp_id,
            "--seed",
            str(int(seed)),
            "--duration",
            str(float(duration)),
            "--max-runs",
            str(int(max_runs)),
        ]
        if not baseline_first:
            gen_cmd.append("--no-baseline-first")

        gen_cmd += add_values("--speed", sweep.get("speed"))
        gen_cmd += add_values("--cfg", sweep.get("cfg"))
        gen_cmd += add_values("--denoise", sweep.get("denoise"))
        gen_cmd += add_values("--steps", sweep.get("steps"))
        gen_cmd += add_values("--teacache", sweep.get("teacache"))
        gen_cmd += add_values("--crf", sweep.get("crf"))
        gen_cmd += add_values("--pix-fmt", sweep.get("pix_fmt"))
        gen_cmd += add_values("--skip-blocks", sweep.get("skip_blocks"))
        gen_cmd += add_values("--skip-start", sweep.get("skip_start"))
        gen_cmd += add_values("--skip-end", sweep.get("skip_end"))
        gen_cmd += add_values("--ta-self-temporal", sweep.get("ta_self_temporal"))
        gen_cmd += add_values("--ta-cross-temporal", sweep.get("ta_cross_temporal"))

        try:
            gen = subprocess.run(
                gen_cmd,
                cwd=str(cfg.workspace_root),
                capture_output=True,
                text=True,
                timeout=300,
            )
        except Exception as e:
            return _json_response(self, 500, {"error": "generate_failed", "detail": str(e)})
        if gen.returncode != 0:
            return _json_response(
                self,
                500,
                {"error": "generate_failed", "returncode": gen.returncode, "stdout": gen.stdout, "stderr": gen.stderr},
            )

        exp_dir_out = (gen.stdout or "").strip().splitlines()[-1].strip() if gen.stdout else ""
        if not exp_dir_out:
            exp_dir_out = str(Path(out_root) / new_exp_id)

        return _json_response(
            self,
            200,
            {
                "ok": True,
                "exp_id": new_exp_id,
                "exp_dir": exp_dir_out,
                "base_mp4_relpath": base_mp4_relpath,
                "seed": int(seed),
                "duration_sec": float(duration),
                "sweep": sweep,
                "stdout": gen.stdout,
                "stderr": gen.stderr,
            },
        )

    def _handle_requeue_run(self) -> None:
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"error": "bad_json"})

        exp_id = body.get("exp_id")
        run_id = body.get("run_id")
        front = bool(body.get("front") or False)
        if not isinstance(exp_id, str) or not exp_id.strip():
            return _json_response(self, 400, {"error": "missing_exp_id"})
        if not isinstance(run_id, str) or not run_id.strip():
            return _json_response(self, 400, {"error": "missing_run_id"})
        exp_id = exp_id.strip()
        run_id = run_id.strip()

        exp_dir = cfg.experiments_root / exp_id
        run_dir = exp_dir / "runs" / run_id
        prompt_path = run_dir / "prompt.json"
        if not prompt_path.exists():
            return _json_response(self, 404, {"error": "prompt_not_found", "exp_id": exp_id, "run_id": run_id})

        try:
            prompt_obj = _read_json(prompt_path)
        except Exception as e:
            return _json_response(self, 400, {"error": "bad_prompt_json", "detail": str(e)})
        if not isinstance(prompt_obj, dict):
            return _json_response(self, 400, {"error": "prompt_not_object"})

        comfy = str(cfg.comfy_server).rstrip("/")
        try:
            submit = _comfy_submit_prompt(cfg.comfy_server, prompt_obj, front=front)
        except Exception as e:
            return _json_response(self, 502, {"error": "comfy_submit_failed", "detail": str(e), "server": comfy})

        try:
            (run_dir / "submit.json").write_text(json.dumps(submit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as e:
            return _json_response(self, 500, {"error": "write_submit_failed", "detail": str(e)})

        return _json_response(self, 200, {"ok": True, "exp_id": exp_id, "run_id": run_id, "front": front, "submit": submit})

    def _handle_queue_submit_prompt(self) -> None:
        """
        POST /api/queue/submit-prompt
        Body: { "prompt": { ... Comfy graph ... }, "front"?: bool, "client_id"?: str }
        Submits to Comfy /prompt without reading or writing experiment run artifacts.
        """
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"error": "bad_json"})

        prompt_obj = body.get("prompt")
        if not isinstance(prompt_obj, dict):
            return _json_response(self, 400, {"error": "missing_prompt", "detail": "prompt must be a JSON object"})

        front = bool(body.get("front") or False)
        raw_cid = body.get("client_id")
        if raw_cid is not None and not isinstance(raw_cid, str):
            return _json_response(self, 400, {"error": "bad_client_id"})
        client_id = (raw_cid.strip() if isinstance(raw_cid, str) else "") or "experiments-ui"

        comfy = str(cfg.comfy_server).rstrip("/")
        try:
            submit = _comfy_submit_prompt(cfg.comfy_server, prompt_obj, front=front, client_id=client_id)
        except Exception as e:
            return _json_response(self, 502, {"error": "comfy_submit_failed", "detail": str(e), "server": comfy})

        return _json_response(self, 200, {"ok": True, "front": front, "client_id": client_id, "submit": submit})

    def _handle_queue_move_prompt(self) -> None:
        """
        POST /api/queue/move-prompt
        Body: { "prompt_id": str, "to": "front"|"back", "client_id"?: str }

        Best-effort safe move for waiting prompts:
        1) capture prompt graph (+ extra_data / outputs) from current pending queue row,
        2) forget old id in queue ledger so delete does not trigger restore,
        3) delete prompt from pending queue,
        4) verify it did not transition to running during move,
        5) re-submit graph to requested position (preserving factory metadata),
        6) rebind matching shape-factory job to the new prompt_id.
        """
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"ok": False, "error": "bad_json"})
        prompt_id = body.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id.strip():
            return _json_response(self, 400, {"ok": False, "error": "missing_prompt_id"})
        prompt_id = prompt_id.strip()
        to_raw = str(body.get("to") or "").strip().lower()
        if to_raw not in {"front", "back"}:
            return _json_response(self, 400, {"ok": False, "error": "bad_to", "expected": ["front", "back"]})
        front = to_raw == "front"
        raw_cid = body.get("client_id")
        if raw_cid is not None and not isinstance(raw_cid, str):
            return _json_response(self, 400, {"ok": False, "error": "bad_client_id"})
        client_id = (raw_cid.strip() if isinstance(raw_cid, str) else "") or "experiments-ui"

        comfy = str(cfg.comfy_server).rstrip("/")

        def _queue_lists() -> Tuple[List[Any], List[Any]]:
            qobj = _http_json("GET", f"{comfy}/queue", timeout_s=10)
            if not isinstance(qobj, dict):
                return [], []
            pending = qobj.get("queue_pending")
            running = qobj.get("queue_running")
            return (pending if isinstance(pending, list) else [], running if isinstance(running, list) else [])

        def _extract_pending_row(items: List[Any], pid: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[List[Any]]]:
            for it in items:
                if not (isinstance(it, list) and len(it) >= 2 and isinstance(it[1], str) and it[1].strip() == pid):
                    continue
                prompt = it[2] if len(it) >= 3 and isinstance(it[2], dict) else None
                extra = it[3] if len(it) >= 4 and isinstance(it[3], dict) else None
                outputs = it[4] if len(it) >= 5 and isinstance(it[4], list) else None
                return prompt, extra, outputs
            return None, None, None

        def _has_pid(items: List[Any], pid: str) -> bool:
            for it in items:
                if isinstance(it, list) and len(it) >= 2 and isinstance(it[1], str) and it[1].strip() == pid:
                    return True
            return False

        def _ledger_forget_prompt(pid: str) -> bool:
            """Drop mirrored restore state so intentional delete does not re-queue the old id."""
            try:
                from comfy_queue_ledger import _forget_mirrored_prompt  # type: ignore
            except Exception:
                d = _workspace_scripts_dir()
                if d.is_dir() and str(d) not in sys.path:
                    sys.path.insert(0, str(d))
                try:
                    from comfy_queue_ledger import _forget_mirrored_prompt  # type: ignore
                except Exception:
                    return False
            try:
                st = _read_queue_ledger_state(cfg.queue_ledger_state_path)
                if not isinstance(st, dict):
                    return False
                _forget_mirrored_prompt(st, pid)
                _write_queue_ledger_state(cfg.queue_ledger_state_path, st)
                return True
            except Exception:
                return False

        def _ledger_seed_new_prompt(
            *,
            old_pid: str,
            new_pid: str,
            prompt_obj: Dict[str, Any],
            extra: Optional[Dict[str, Any]],
            outputs: Optional[List[Any]],
        ) -> bool:
            try:
                st = _read_queue_ledger_state(cfg.queue_ledger_state_path)
                if not isinstance(st, dict):
                    return False
                known = st.get("known")
                if not isinstance(known, dict):
                    known = {}
                    st["known"] = known
                prev = known.pop(old_pid, None) if isinstance(known.get(old_pid), dict) else {}
                if not isinstance(prev, dict):
                    prev = {}
                rec = dict(prev)
                rec["prompt"] = prompt_obj
                if isinstance(extra, dict):
                    rec["extra_data"] = extra
                if isinstance(outputs, list):
                    rec["outputs_to_execute"] = outputs
                rec["last_phase"] = "pending"
                known[new_pid] = rec
                snap = st.get("last_snapshot")
                if isinstance(snap, dict):
                    pending_ids = snap.get("pending")
                    if isinstance(pending_ids, list):
                        pending_ids[:] = [x for x in pending_ids if x != old_pid]
                        if new_pid not in pending_ids:
                            pending_ids.append(new_pid)
                _write_queue_ledger_state(cfg.queue_ledger_state_path, st)
                return True
            except Exception:
                return False

        try:
            pending_0, running_0 = _queue_lists()
        except Exception as e:
            return _json_response(
                self,
                502,
                {"ok": False, "error": "comfy_queue_fetch_failed", "detail": str(e), "server": comfy},
            )
        if _has_pid(running_0, prompt_id):
            return _json_response(
                self,
                409,
                {"ok": False, "error": "prompt_already_running", "prompt_id": prompt_id, "detail": "Prompt already started running; cannot reorder pending queue item."},
            )
        prompt_obj, extra_data, outputs_to_execute = _extract_pending_row(pending_0, prompt_id)
        if not isinstance(prompt_obj, dict):
            return _json_response(
                self,
                409,
                {
                    "ok": False,
                    "error": "prompt_not_pending_or_missing_graph",
                    "prompt_id": prompt_id,
                    "detail": "Prompt is not in pending queue (or no prompt graph available). Refresh queue and retry.",
                },
            )

        ledger_forgot = _ledger_forget_prompt(prompt_id)

        try:
            _http_void("POST", f"{comfy}/queue", {"delete": [prompt_id]}, timeout_s=10, retry_attempts=2)
        except Exception as e:
            return _json_response(self, 502, {"ok": False, "error": "comfy_cancel_failed", "detail": str(e), "server": comfy})

        try:
            pending_1, running_1 = _queue_lists()
        except Exception as e:
            return _json_response(
                self,
                502,
                {"ok": False, "error": "comfy_queue_refetch_failed", "detail": str(e), "server": comfy},
            )
        if _has_pid(running_1, prompt_id):
            return _json_response(
                self,
                409,
                {
                    "ok": False,
                    "error": "prompt_became_running",
                    "prompt_id": prompt_id,
                    "detail": "Prompt moved from waiting to running while attempting reorder; left untouched.",
                },
            )
        if _has_pid(pending_1, prompt_id):
            # Fallback: one extra delete attempt in case of timing lag.
            try:
                _http_void("POST", f"{comfy}/queue", {"delete": [prompt_id]}, timeout_s=10, retry_attempts=2)
            except Exception:
                pass
            try:
                pending_2, running_2 = _queue_lists()
            except Exception:
                pending_2, running_2 = pending_1, running_1
            if _has_pid(running_2, prompt_id):
                return _json_response(
                    self,
                    409,
                    {
                        "ok": False,
                        "error": "prompt_became_running",
                        "prompt_id": prompt_id,
                        "detail": "Prompt started running during reorder fallback; skipped re-submit to avoid duplicate run.",
                    },
                )
            if _has_pid(pending_2, prompt_id):
                return _json_response(
                    self,
                    409,
                    {
                        "ok": False,
                        "error": "prompt_still_pending_after_delete",
                        "prompt_id": prompt_id,
                        "detail": "Prompt did not leave pending queue; skipping re-submit to avoid duplicates.",
                    },
                )

        try:
            submit = _comfy_submit_prompt(
                cfg.comfy_server,
                prompt_obj,
                front=front,
                client_id=client_id,
                extra_data=extra_data if isinstance(extra_data, dict) else None,
                outputs_to_execute=outputs_to_execute if isinstance(outputs_to_execute, list) else None,
            )
        except Exception as e:
            # Fallback: try to restore to back if the intended submit failed.
            restored = False
            try:
                _comfy_submit_prompt(
                    cfg.comfy_server,
                    prompt_obj,
                    front=False,
                    client_id=client_id,
                    extra_data=extra_data if isinstance(extra_data, dict) else None,
                    outputs_to_execute=outputs_to_execute if isinstance(outputs_to_execute, list) else None,
                )
                restored = True
            except Exception:
                restored = False
            detail = f"{e}; restored_to_back={str(restored).lower()}"
            return _json_response(
                self,
                502,
                {
                    "ok": False,
                    "error": "comfy_submit_failed",
                    "prompt_id": prompt_id,
                    "to": to_raw,
                    "detail": detail,
                    "server": comfy,
                },
            )

        new_prompt_id = ""
        if isinstance(submit, dict):
            new_prompt_id = str(submit.get("prompt_id") or "").strip()

        factory_rebind: Dict[str, Any] = {"ok": True, "factory_job": False}
        if new_prompt_id and new_prompt_id != prompt_id:
            try:
                d = _workspace_scripts_dir()
                if d.is_dir() and str(d) not in sys.path:
                    sys.path.insert(0, str(d))
                from shape_factory import rebind_job_after_prompt_move  # type: ignore
                from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

                data_root = resolve_shape_factory_data_root(repo_root=_repo_root())
                factory_rebind = rebind_job_after_prompt_move(
                    data_root=data_root,
                    old_prompt_id=prompt_id,
                    new_prompt_id=new_prompt_id,
                    status="queued",
                )
            except Exception as exc:
                factory_rebind = {
                    "ok": False,
                    "factory_job": False,
                    "error": "factory_rebind_failed",
                    "detail": str(exc),
                    "old_prompt_id": prompt_id,
                    "new_prompt_id": new_prompt_id,
                }
            _ledger_seed_new_prompt(
                old_pid=prompt_id,
                new_pid=new_prompt_id,
                prompt_obj=prompt_obj,
                extra=extra_data if isinstance(extra_data, dict) else None,
                outputs=outputs_to_execute if isinstance(outputs_to_execute, list) else None,
            )

        return _json_response(
            self,
            200,
            {
                "ok": True,
                "prompt_id": prompt_id,
                "new_prompt_id": new_prompt_id or None,
                "to": to_raw,
                "moved": True,
                "submit": submit,
                "factory_rebind": factory_rebind,
                "ledger_forgot_old": bool(ledger_forgot),
            },
        )

    def _handle_comfy_cancel(self) -> None:
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"error": "bad_json"})
        prompt_id = body.get("prompt_id")
        kind = body.get("kind")
        if not isinstance(prompt_id, str) or not prompt_id.strip():
            return _json_response(self, 400, {"error": "missing_prompt_id"})
        if kind not in ("pending", "running"):
            return _json_response(self, 400, {"error": "bad_kind", "expected": ["pending", "running"]})

        comfy = str(cfg.comfy_server).rstrip("/")
        try:
            if kind == "running":
                # ComfyUI interrupt cancels current execution (not a specific prompt_id).
                _http_void("POST", f"{comfy}/interrupt", None, timeout_s=10, retry_attempts=2)
                res = {"ok": True}
                return _json_response(self, 200, {"ok": True, "kind": kind, "prompt_id": prompt_id, "result": res})
            _http_void("POST", f"{comfy}/queue", {"delete": [prompt_id.strip()]}, timeout_s=10, retry_attempts=2)
            res = {"ok": True}
            return _json_response(self, 200, {"ok": True, "kind": kind, "prompt_id": prompt_id, "result": res})
        except Exception as e:
            return _json_response(self, 502, {"error": "comfy_cancel_failed", "detail": str(e), "server": comfy})

    def _handle_comfy_clear(self) -> None:
        cfg = self.server.cfg
        comfy = str(cfg.comfy_server).rstrip("/")
        try:
            _http_void("POST", f"{comfy}/queue", {"clear": True}, timeout_s=10, retry_attempts=2)
            res = {"ok": True}
            return _json_response(self, 200, {"ok": True, "result": res})
        except Exception as e:
            return _json_response(self, 502, {"error": "comfy_clear_failed", "detail": str(e), "server": comfy})

    def _handle_orchestrator_state_post(self) -> None:
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"error": "bad_json"})
        if not isinstance(body, dict):
            return _json_response(self, 400, {"error": "bad_state"})
        current = _default_orchestrator_state()
        next_state: Dict[str, Any] = {}
        for key in current.keys():
            v = body.get(key)
            if not isinstance(v, list):
                v = []
            next_state[key] = v
        try:
            _write_orchestrator_state(cfg.orchestrator_state_path, next_state)
        except Exception as e:
            return _json_response(self, 500, {"error": "write_failed", "detail": str(e)})
        return _json_response(self, 200, next_state)

    def _handle_orchestrator_saved_item_post(self) -> None:
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"error": "bad_json"})
        title = body.get("title")
        if not isinstance(title, str) or not title.strip():
            return _json_response(self, 400, {"error": "missing_title"})
        st = _read_orchestrator_state(cfg.orchestrator_state_path)
        now = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        item = {
            "id": f"saved_{int(_dt.datetime.utcnow().timestamp() * 1000)}",
            "prompt_id": body.get("prompt_id") if isinstance(body.get("prompt_id"), str) else None,
            "created_at": now,
            "title": title.strip(),
            "tags": body.get("tags") if isinstance(body.get("tags"), list) else [],
            "notes": body.get("notes") if isinstance(body.get("notes"), str) else "",
            "payload": body.get("payload") if isinstance(body.get("payload"), dict) else {},
        }
        st["saved_items"] = [item] + [x for x in st.get("saved_items", []) if isinstance(x, dict)]
        try:
            _write_orchestrator_state(cfg.orchestrator_state_path, st)
        except Exception as e:
            return _json_response(self, 500, {"error": "write_failed", "detail": str(e)})
        return _json_response(self, 200, item)

    def _handle_queue_ledger_control(self) -> None:
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"error": "bad_json"})
        action = body.get("action")
        if not isinstance(action, str) or not action.strip():
            return _json_response(self, 400, {"error": "missing_action"})
        action = action.strip().lower()

        if action in _QUEUE_OPS_ACTIONS:
            try:
                payload = _queue_ops_action(cfg, action)
            except Exception as e:
                return _json_response(
                    self,
                    500,
                    {"ok": False, "error": "queue_ops_failed", "action": action, "detail": str(e)},
                )
            if not isinstance(payload, dict):
                payload = {"ok": False, "error": "bad_ops_payload"}
            payload.setdefault("action", action)
            code = 200 if payload.get("ok") else 500
            return _json_response(self, code, payload)

        st = _read_queue_ledger_state(cfg.queue_ledger_state_path)
        if not st:
            return _json_response(
                self,
                404,
                {
                    "error": "ledger_state_missing",
                    "state_path": str(cfg.queue_ledger_state_path),
                },
            )

        if action == "pause":
            st["paused"] = True
        elif action == "resume":
            st["paused"] = False
        elif action == "drain-once":
            st["drain_once_requested_at"] = time.time()
        elif action == "clear":
            # Request the ledger process to drop mirrored restore state. Also clear on
            # disk immediately so status reflects intent even before the next poll.
            known = st.get("known") if isinstance(st.get("known"), dict) else {}
            backlog = st.get("backlog") if isinstance(st.get("backlog"), list) else []
            snap = st.get("last_snapshot") if isinstance(st.get("last_snapshot"), dict) else {}
            prev_running = snap.get("running") if isinstance(snap.get("running"), list) else []
            prev_pending = snap.get("pending") if isinstance(snap.get("pending"), list) else []
            cleared = {
                "known": len(known),
                "backlog": len(backlog),
                "snapshot": len(prev_running) + len(prev_pending),
            }
            st["clear_requested_at"] = time.time()
            st["last_snapshot"] = {"running": [], "pending": []}
            st["known"] = {}
            st["backlog"] = []
            st["restore_attempts"] = {}
            st["restore_last_ts"] = {}
            st["expected_add_until_ts"] = {}
            st["recent_unexpected_ts"] = []
            stats = st.get("stats") if isinstance(st.get("stats"), dict) else {}
            stats["cleared"] = int(stats.get("cleared") or 0) + 1
            st["stats"] = stats
        elif action == "reset-breaker":
            br = st.get("breaker")
            if not isinstance(br, dict):
                br = {}
            br["open"] = False
            br["reason"] = ""
            br["open_until_ts"] = 0.0
            st["breaker"] = br
            st["restore_failures_ts"] = []
        else:
            return _json_response(
                self,
                400,
                {
                    "error": "bad_action",
                    "expected": [
                        "pause",
                        "resume",
                        "drain-once",
                        "clear",
                        "reset-breaker",
                        "suspend",
                        "resume-ops",
                        "hourlies-on",
                        "hourlies-off",
                        "drain-on",
                        "drain-off",
                        "watch-on",
                        "watch-off",
                    ],
                },
            )

        st["updated_at"] = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            _write_queue_ledger_state(cfg.queue_ledger_state_path, st)
        except Exception as e:
            return _json_response(self, 500, {"error": "write_failed", "detail": str(e)})
        out: Dict[str, Any] = {"ok": True, "action": action, "paused": bool(st.get("paused"))}
        if action == "clear":
            out["cleared"] = cleared
            out["note"] = (
                "Cleared ledger restore state (known/backlog/snapshot). "
                "Does not clear Comfy's live queue; ledger will re-mirror whatever is still queued."
            )
        return _json_response(self, 200, out)

    def _handle_next_experiment(self) -> None:
        """
        Derive+submit the next experiment based on an anchor run's *output MP4*.

        The anchor run's MP4 contains the embedded prompt/workflow metadata, so using it
        as `base_mp4` makes the baseline run match the anchor exactly.
        """
        cfg = self.server.cfg
        body = self._read_request_json()
        if body is None:
            return _json_response(self, 400, {"error": "bad_json"})

        anchor = body.get("anchor")
        if not isinstance(anchor, dict):
            return _json_response(self, 400, {"error": "missing_anchor"})
        anchor_exp = anchor.get("exp_id")
        anchor_run = anchor.get("run_id")
        if not isinstance(anchor_exp, str) or not isinstance(anchor_run, str) or not anchor_exp.strip() or not anchor_run.strip():
            return _json_response(self, 400, {"error": "bad_anchor"})

        exp_id = anchor_exp.strip()
        run_id = anchor_run.strip()
        exp_dir = cfg.experiments_root / exp_id
        run_dir = exp_dir / "runs" / run_id
        if not run_dir.exists():
            return _json_response(self, 404, {"error": "run_not_found", "exp_id": exp_id, "run_id": run_id})

        # Pick base_mp4 as the anchor run's primary mp4 output.
        history_path = run_dir / "history.json"
        try:
            history = _read_json(history_path) if history_path.exists() else None
        except Exception:
            history = None
        outs = _extract_outputs_from_history(history)
        primary_vid, _primary_img = _pick_primary_media(outs)
        if not primary_vid:
            return _json_response(self, 400, {"error": "anchor_has_no_video", "exp_id": exp_id, "run_id": run_id})
        base_mp4 = _safe_join(cfg.output_root, primary_vid)
        if base_mp4 is None or not base_mp4.exists() or not base_mp4.is_file():
            return _json_response(self, 404, {"error": "anchor_video_not_found", "relpath": primary_vid})

        # Seed: request > prompt.json RandomNoise.noise_seed > manifest fixed_seed
        seed = _safe_int(body.get("seed"))
        if seed is None:
            try:
                prompt = _read_json(run_dir / "prompt.json")
            except Exception:
                prompt = None
            seed = _extract_seed_from_prompt(prompt)
        if seed is None:
            mf = _load_manifest(exp_dir) or {}
            seed = _safe_int(mf.get("fixed_seed"))
        if seed is None:
            return _json_response(self, 400, {"error": "missing_seed"})

        # Duration: request > manifest fixed_duration_sec > params.json > default(2.0)
        duration = _safe_float(body.get("duration_sec"))
        mf = _load_manifest(exp_dir) or {}
        if duration is None:
            duration = _safe_float(mf.get("fixed_duration_sec"))
        if duration is None:
            try:
                params = _read_json(run_dir / "params.json")
            except Exception:
                params = None
            if isinstance(params, dict):
                duration = _safe_float(params.get("duration_sec") or params.get("duration") or params.get("sec"))
        if duration is None:
            duration = 2.0

        new_exp_id = body.get("exp_id")
        if not isinstance(new_exp_id, str) or not new_exp_id.strip():
            new_exp_id = _slug(f"next_{exp_id}_{run_id}_{_now_stamp()}")
        else:
            new_exp_id = _slug(new_exp_id.strip())

        out_root = body.get("out_root")
        if not isinstance(out_root, str) or not out_root.strip():
            out_root = str(cfg.experiments_root)

        comfy_server = body.get("server")
        if not isinstance(comfy_server, str) or not comfy_server.strip():
            comfy_server = cfg.comfy_server

        baseline_first = body.get("baseline_first")
        baseline_first = True if baseline_first is None else bool(baseline_first)

        sweep = body.get("sweep") if body.get("sweep") is not None else {}
        if not isinstance(sweep, dict):
            return _json_response(self, 400, {"error": "bad_sweep"})

        max_runs = _safe_int(body.get("max_runs")) or 200

        def add_values(flag: str, xs: Any) -> List[str]:
            if xs is None:
                return []
            if isinstance(xs, (str, int, float)):
                xs = [xs]
            if not isinstance(xs, list):
                return []
            out: List[str] = []
            for x in xs:
                if isinstance(x, (int, float)):
                    out.append(str(x))
                elif isinstance(x, str) and x.strip():
                    out.append(x.strip())
            return [flag, *out] if out else []

        gen_cmd: List[str] = [
            sys.executable,
            str(cfg.tune_script),
            "generate",
            str(base_mp4),
            "--out-root",
            str(out_root),
            "--exp-id",
            str(new_exp_id),
            "--seed",
            str(int(seed)),
            "--duration",
            str(float(duration)),
            "--max-runs",
            str(int(max_runs)),
        ]
        if not baseline_first:
            gen_cmd.append("--no-baseline-first")

        # Supported sweeps (keys match tune_experiment.py CLI).
        gen_cmd += add_values("--speed", sweep.get("speed"))
        gen_cmd += add_values("--cfg", sweep.get("cfg"))
        gen_cmd += add_values("--denoise", sweep.get("denoise"))
        gen_cmd += add_values("--steps", sweep.get("steps"))
        gen_cmd += add_values("--teacache", sweep.get("teacache"))
        gen_cmd += add_values("--crf", sweep.get("crf"))
        gen_cmd += add_values("--pix-fmt", sweep.get("pix_fmt"))
        gen_cmd += add_values("--skip-blocks", sweep.get("skip_blocks"))
        gen_cmd += add_values("--skip-start", sweep.get("skip_start"))
        gen_cmd += add_values("--skip-end", sweep.get("skip_end"))
        gen_cmd += add_values("--ta-self-temporal", sweep.get("ta_self_temporal"))
        gen_cmd += add_values("--ta-cross-temporal", sweep.get("ta_cross_temporal"))

        try:
            gen = subprocess.run(gen_cmd, cwd=str(cfg.workspace_root), capture_output=True, text=True, timeout=300)
        except Exception as e:
            return _json_response(self, 500, {"error": "generate_failed", "detail": str(e)})
        if gen.returncode != 0:
            return _json_response(
                self,
                500,
                {"error": "generate_failed", "returncode": gen.returncode, "stdout": gen.stdout, "stderr": gen.stderr},
            )

        exp_dir_out = (gen.stdout or "").strip().splitlines()[-1].strip() if gen.stdout else ""
        if not exp_dir_out:
            exp_dir_out = str(Path(out_root) / new_exp_id)

        submit_all = bool(body.get("submit_all", True))
        no_wait = bool(body.get("no_wait", True))
        run_cmd: List[str] = [sys.executable, str(cfg.tune_script), "run", exp_dir_out, "--server", str(comfy_server)]
        if submit_all or no_wait:
            run_cmd.append("--submit-all")
        if no_wait:
            run_cmd.append("--no-wait")

        try:
            rr = subprocess.run(run_cmd, cwd=str(cfg.workspace_root), capture_output=True, text=True, timeout=300)
        except Exception as e:
            return _json_response(self, 500, {"error": "run_failed", "detail": str(e), "exp_dir": exp_dir_out})
        if rr.returncode != 0:
            return _json_response(
                self,
                500,
                {"error": "run_failed", "returncode": rr.returncode, "stdout": rr.stdout, "stderr": rr.stderr, "exp_dir": exp_dir_out},
            )

        return _json_response(
            self,
            200,
            {
                "ok": True,
                "anchor": {"exp_id": exp_id, "run_id": run_id, "base_mp4_relpath": primary_vid},
                "exp_id": new_exp_id,
                "exp_dir": exp_dir_out,
                "seed": int(seed),
                "duration_sec": float(duration),
                "sweep": sweep,
                "queued": bool(no_wait or submit_all),
                "stdout": rr.stdout,
                "stderr": rr.stderr,
            },
        )

    def _handle_files_get(self, rel: str) -> None:
        cfg = self.server.cfg
        rel = _normalize_rel_posix(rel.lstrip("/"))
        if not rel:
            return _json_response(self, 400, {"error": "bad_path"})
        in_rel = _discovery_workspace_input_relpath_for_source(cfg, rel) or rel
        full = _discovery_resolve_media_file(cfg, in_rel)
        if full is None:
            return _json_response(self, 404, {"error": "file_not_found", "relpath": rel})
        ctype, _enc = mimetypes.guess_type(str(full))
        if not ctype:
            ctype = "application/octet-stream"
        try:
            _stream_file(self, full, content_type=ctype, cache_control="public, max-age=60", allow_ranges=True)
        except Exception as e:
            return _json_response(self, 500, {"error": "read_failed", "detail": str(e)})

    def _handle_factory_asset_file_get(self, rel: str) -> None:
        cfg = self.server.cfg
        asset_id_raw = (rel.split("/", 1)[0] or "").strip()
        asset_id = _safe_int(asset_id_raw)
        if asset_id is None:
            return _json_response(self, 400, {"error": "bad_asset_id"})
        if not cfg.factory_db_path.exists():
            return _json_response(self, 404, {"error": "factory_db_missing"})

        con = sqlite3.connect(cfg.factory_db_path)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute("SELECT path, media_type FROM asset_items WHERE id = ?", (asset_id,)).fetchone()
        finally:
            con.close()
        if row is None:
            return _json_response(self, 404, {"error": "asset_not_found", "asset_id": asset_id})

        raw_path = row["path"]
        if not isinstance(raw_path, str) or not raw_path.strip():
            return _json_response(self, 404, {"error": "asset_path_missing", "asset_id": asset_id})
        suffix = Path(raw_path).suffix.lower()
        if suffix not in _FACTORY_ASSET_PREVIEW_EXTS:
            return _json_response(self, 415, {"error": "unsupported_asset_preview", "asset_id": asset_id})

        full = _resolve_factory_asset_file(cfg, raw_path)
        if not full.exists() or not full.is_file():
            return _json_response(self, 404, {"error": "asset_file_not_found", "asset_id": asset_id, "path": raw_path})

        ctype, _enc = mimetypes.guess_type(str(full))
        if not ctype:
            ctype = "application/octet-stream"
        try:
            _stream_file(self, full, content_type=ctype, cache_control="public, max-age=60", allow_ranges=True)
        except Exception as e:
            return _json_response(self, 500, {"error": "read_failed", "detail": str(e)})

    def _handle_static_get(self, path: str) -> None:
        cfg = self.server.cfg
        static_dir = cfg.static_dir
        if not static_dir.exists():
            return _text_response(
                self,
                200,
                (
                    "Experiments UI server is running, but React build output was not found.\n\n"
                    f"Expected static directory: {static_dir}\n"
                    "Build the web app (Vite) to populate this directory.\n"
                ),
            )

        if path == "/" or path == "":
            return self._serve_static_file("index.html")

        rel = _normalize_rel_posix(path)
        if not rel:
            rel = "index.html"

        target = _safe_join(static_dir, rel)
        if target is not None and target.exists() and target.is_file():
            return self._serve_static_file(rel)
        return self._serve_static_file("index.html")

    def _serve_static_file(self, rel: str) -> None:
        cfg = self.server.cfg
        static_dir = cfg.static_dir
        full = _safe_join(static_dir, rel)
        if full is None or not full.exists() or not full.is_file():
            return _json_response(self, 404, {"error": "static_not_found", "relpath": rel})
        ctype, _enc = mimetypes.guess_type(str(full))
        if not ctype:
            ctype = "application/octet-stream"
        try:
            _stream_file(self, full, content_type=ctype, cache_control="no-cache", allow_ranges=True)
        except Exception as e:
            return _json_response(self, 500, {"error": "read_failed", "detail": str(e)})


class ExperimentsServer(ThreadingHTTPServer):
    def __init__(self, server_address: Tuple[str, int], cfg: ServerConfig):
        super().__init__(server_address, Handler)
        self.cfg = cfg


def main() -> int:
    ap = argparse.ArgumentParser(description="Serve Experiments UI API + React static frontend")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=int(os.environ.get("EXPERIMENTS_UI_PORT", "8790")))
    ap.add_argument("--workspace-root", default=os.environ.get("WORKSPACE_PATH", ""))
    ap.add_argument("--experiments-root", default="")
    ap.add_argument("--output-root", default="")
    ap.add_argument("--static-dir", default="")
    ap.add_argument(
        "--wip-root",
        default="",
        help="Browse root for Create from WIP (default: <output>/output/wip). "
        "Relative to workspace unless absolute. Env: EXPERIMENTS_UI_WIP_ROOT.",
    )
    args = ap.parse_args()

    base = Path(args.workspace_root) if args.workspace_root else Path(__file__).resolve().parent.parent
    ws = _resolve_workspace_root(base)
    experiments_root = Path(args.experiments_root) if args.experiments_root else _prefer_flat_library_dir(ws / "output", "experiments")
    output_root = Path(args.output_root) if args.output_root else (ws / "output")
    wip_override = (args.wip_root or "").strip() or os.environ.get("EXPERIMENTS_UI_WIP_ROOT", "").strip()
    wip_root = _resolve_wip_root(ws, output_root, wip_override)
    static_dir = Path(args.static_dir) if args.static_dir else (ws / "experiments_ui" / "dist")
    orchestrator_state_path = ws / "output" / "orchestrator" / "state.json"
    exp_status = _prefer_flat_library_dir(output_root, "experiments") / "_status"
    queue_ledger_state_path = exp_status / "comfy_queue_ledger_state.json"
    queue_ledger_events_path = exp_status / "comfy_queue_ledger.jsonl"
    discovery_index_path = _output_status_dir(output_root) / "discovery_og_wip_index.json"
    factory_db_path = Path(
        os.environ.get("SNOWFLAKE_FACTORY_DB", str(ws / "comfyui_user" / "default" / "snowflake_factory.sqlite"))
    )
    # Runtime utilities live under workspace/scripts in this repo, but /workspace/scripts may be
    # occupied by a bind-mount of repo-level scripts. Prefer ws_scripts when present.
    tune_script = ws / "scripts" / "tune_experiment.py"
    alt_tune = ws / "ws_scripts" / "tune_experiment.py"
    if not tune_script.exists() and alt_tune.exists():
        tune_script = alt_tune
    comfy_server = os.environ.get("COMFYUI_SERVER", "http://127.0.0.1:8188")

    cfg = ServerConfig(
        workspace_root=ws,
        experiments_root=experiments_root,
        output_root=output_root,
        wip_root=wip_root,
        static_dir=static_dir,
        tune_script=tune_script,
        comfy_server=comfy_server,
        orchestrator_state_path=orchestrator_state_path,
        queue_ledger_state_path=queue_ledger_state_path,
        queue_ledger_events_path=queue_ledger_events_path,
        discovery_index_path=discovery_index_path,
        factory_db_path=factory_db_path,
        factory_browse_roots=_factory_browse_roots(ws, output_root),
    )
    server = ExperimentsServer((args.host, int(args.port)), cfg)
    try:
        d = _workspace_scripts_dir()
        if d.is_dir() and str(d) not in sys.path:
            sys.path.insert(0, str(d))
        from comfy_live_preview import start_bridge  # type: ignore

        start_bridge(str(cfg.comfy_server))
    except Exception as e:
        print(f"[experiments-ui] comfy live-preview bridge not started: {e}")
    print(f"[experiments-ui] listening on http://{args.host}:{args.port}")
    print(f"[experiments-ui] workspace_root={cfg.workspace_root}")
    print(f"[experiments-ui] experiments_root={cfg.experiments_root}")
    print(f"[experiments-ui] output_root={cfg.output_root}")
    print(f"[experiments-ui] wip_root={cfg.wip_root}")
    print(f"[experiments-ui] static_dir={cfg.static_dir}")
    print(f"[experiments-ui] orchestrator_state={cfg.orchestrator_state_path}")
    print(f"[experiments-ui] queue_ledger_state={cfg.queue_ledger_state_path}")
    print(f"[experiments-ui] discovery_index={cfg.discovery_index_path}")
    print(f"[experiments-ui] factory_db={cfg.factory_db_path}")
    print(f"[experiments-ui] factory_browse_roots={cfg.factory_browse_roots}")
    print(
        "[experiments-ui] discovery_routes=GET /api/discovery/library, /api/discovery/library/item, "
        "/api/discovery/trim, /api/discovery/embed-api-prompt, /api/discovery/workflow-facets, "
        "/api/discovery/asset-lineage, /api/discovery/asset-ratings, /api/discovery/asset-ratings/verify, "
        "/api/discovery/rating-sampler, GET /api/discovery/asset-audit, POST /api/discovery/asset-recover, "
        "POST /api/discovery/ensure-thumb, "
        "POST /api/discovery/library/ensure, "
        "POST /api/discovery/asset-ratings/set, POST /api/discovery/asset-appetite/set, "
        "GET/POST /api/discovery/disposition-catalog, GET /api/discovery/disposition-suggest, "
        "POST /api/discovery/asset-disposition/toggle, POST /api/discovery/asset-disposition/run-step, "
        "GET /api/discovery/identity-still/candidates, POST /api/discovery/identity-still/mint, "
        "GET /api/discovery/work-items, GET /api/discovery/work-items/pool, "
        "POST /api/discovery/work-items/create, POST /api/discovery/work-items/cancel, "
        "POST /api/discovery/work-items/priority, "
        "POST /api/discovery/asset-triage/complete, POST /api/discovery/asset-triage/complete-batch"
    )
    print("[experiments-ui] home_routes=GET /api/home/summary")
    print(
        "[experiments-ui] comfy_live_routes=GET /api/comfy/live-preview, GET /api/comfy/live-status, GET /api/comfy/logs"
    )
    print(        "[experiments-ui] shape_factory_routes=GET /api/shape-factory/map, GET /api/shape-factory/prompt-profile, GET /api/shape-factory/families, GET /api/shape-factory/work-products, GET /api/shape-factory/json-peek, GET /api/shape-factory/quarantine, GET /api/shape-factory/submit-attempts, POST /api/shape-factory/queue, POST /api/shape-factory/replay, POST /api/shape-factory/derive, POST /api/shape-factory/unqueue, POST /api/shape-factory/discard, POST /api/shape-factory/update-pending-trim, POST /api/shape-factory/quarantine/release")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

