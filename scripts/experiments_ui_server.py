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
import datetime as _dt
import hashlib
import json
import math
import mimetypes
import os
import posixpath
import re
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


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _http_json(
    method: str,
    url: str,
    body: Optional[Dict[str, Any]] = None,
    *,
    timeout_s: int = 10,
) -> Any:
    method = (method or "GET").upper().strip()
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def _comfy_submit_prompt(
    comfy_server: str,
    prompt: Dict[str, Any],
    *,
    front: bool = False,
    client_id: str = "experiments-ui",
    timeout_s: int = 30,
) -> Any:
    """
    POST a workflow graph to ComfyUI /prompt (same payload shape as the UI uses for requeue).
    Returns Comfy's JSON body. Raises on network / HTTP / JSON errors (urllib / json).
    """
    comfy = str(comfy_server).rstrip("/")
    payload: Dict[str, Any] = {"prompt": prompt, "client_id": client_id}
    if front:
        payload["front"] = True
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


def _og_wip_library_roots(cfg: "ServerConfig") -> Tuple[Path, Path]:
    base = (cfg.output_root / "output").resolve()
    return (base / "og", base / "wip")


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
        "scan_ms": int((time.time() - t0) * 1000),
    }
    return built


def _load_discovery_index_disk(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        obj = _read_json(path)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


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
    Default: <output_root>/output/wip
    override: absolute path, or path relative to workspace_root (e.g. output/output/og).
    """
    o = (override or "").strip()
    if not o:
        return (output_root / "output" / "wip").resolve()
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


def _pick_primary_media(outputs: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    vid = None
    img = None
    for o in outputs:
        rel = o.get("relpath")
        if not isinstance(rel, str):
            continue
        l = rel.lower()
        if vid is None and l.endswith(".mp4"):
            vid = rel
        if img is None and (l.endswith(".png") or l.endswith(".webp") or l.endswith(".jpg") or l.endswith(".jpeg")):
            img = rel
    return vid, img


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
        if ctype in ("VHS_LoadVideo", "LoadVideo"):
            video = inputs.get("video") or inputs.get("path")
            if isinstance(video, str) and video.strip():
                return (video.strip().replace("\\", "/"), "video")
    return (None, None)


def _extract_key_params_from_prompt(prompt_obj: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not isinstance(prompt_obj, dict):
        return out
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
    return out


def _guess_workflow_name(prompt_obj: Any, raw_item: Any) -> Optional[str]:
    if isinstance(raw_item, list) and len(raw_item) >= 4 and isinstance(raw_item[3], dict):
        meta = raw_item[3]
        extra = meta.get("extra_pnginfo")
        if isinstance(extra, dict):
            wf = extra.get("workflow")
            if isinstance(wf, dict):
                name = wf.get("name")
                if isinstance(name, str) and name.strip():
                    return name.strip()
    if isinstance(prompt_obj, dict):
        first_types: List[str] = []
        for _nid, node in prompt_obj.items():
            if not isinstance(node, dict):
                continue
            ct = node.get("class_type")
            if isinstance(ct, str) and ct.strip():
                first_types.append(ct.strip())
            if len(first_types) >= 3:
                break
        if first_types:
            return " + ".join(first_types)
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


def _write_queue_ledger_state(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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

        if path == "/api/discovery/trim":
            return self._handle_discovery_trim_get(q)

        if path == "/api/discovery/embed-api-prompt":
            return self._handle_discovery_embed_api_prompt_get(q)

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
            if isinstance(queue_obj, dict):
                for key, out in (("queue_running", comfy_running), ("queue_pending", comfy_pending)):
                    items = queue_obj.get(key)
                    if not isinstance(items, list):
                        continue
                    for it in items:
                        pid = None
                        prompt_obj: Optional[Dict[str, Any]] = None
                        if isinstance(it, list) and len(it) >= 2 and isinstance(it[1], str):
                            pid = it[1]
                            if len(it) >= 3 and isinstance(it[2], dict):
                                prompt_obj = it[2]
                        mapped = prompt_to_run.get(pid) if isinstance(pid, str) and pid else None
                        input_rel, input_kind = _extract_input_media_from_prompt(prompt_obj)
                        input_url = None
                        if isinstance(input_rel, str):
                            normalized = _normalize_rel_posix(input_rel)
                            if normalized:
                                full = _safe_join(cfg.output_root, normalized)
                                if full is not None and full.exists() and full.is_file():
                                    input_url = "/files/" + urllib.parse.quote(normalized)
                        workflow_name = _guess_workflow_name(prompt_obj, it)
                        key_params = _extract_key_params_from_prompt(prompt_obj)
                        out.append(
                            {
                                "prompt_id": pid,
                                "raw": it,
                                "external": mapped is None,
                                "exp_id": mapped.get("exp_id") if isinstance(mapped, dict) else None,
                                "run_id": mapped.get("run_id") if isinstance(mapped, dict) else None,
                                "workflow_name": workflow_name,
                                "input_media_relpath": input_rel,
                                "input_media_url": input_url,
                                "input_media_kind": input_kind,
                                "key_params": key_params,
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
            try:
                hist_obj = _http_json("GET", f"{comfy}/history", timeout_s=10)
            except Exception as e:
                return _json_response(self, 502, {"error": "comfy_history_fetch_failed", "detail": str(e), "items": []})
            items_out: List[Dict[str, Any]] = []
            if isinstance(hist_obj, dict):
                for pid, record in hist_obj.items():
                    if not isinstance(pid, str):
                        continue
                    outs = _extract_outputs_from_history(record)
                    pv, pi = _pick_primary_media(outs)
                    def _mk_url(rel: Optional[str]) -> Optional[str]:
                        if not isinstance(rel, str) or not rel:
                            return None
                        norm = _normalize_rel_posix(rel)
                        if not norm:
                            return None
                        full = _safe_join(cfg.output_root, norm)
                        if full is None or not full.exists() or not full.is_file():
                            return None
                        return "/files/" + urllib.parse.quote(norm)
                    items_out.append(
                        {
                            "prompt_id": pid,
                            "status": "complete",
                            "primary_video_url": _mk_url(pv),
                            "primary_image_url": _mk_url(pi),
                            "outputs": [{**o, "url": _mk_url(o.get("relpath"))} for o in outs],
                        }
                    )
            items_out = items_out[:limit]
            return _json_response(self, 200, {"items": items_out})

        if path == "/api/orchestrator/state":
            st = _read_orchestrator_state(cfg.orchestrator_state_path)
            return _json_response(self, 200, st)

        if path == "/api/queue/ledger-status":
            st = _read_queue_ledger_state(cfg.queue_ledger_state_path)
            out = {
                "enabled": True,
                "state_path": str(cfg.queue_ledger_state_path),
                "events_path": str(cfg.queue_ledger_events_path),
                "mode": st.get("mode"),
                "updated_at": st.get("updated_at"),
                "paused": bool(st.get("paused")),
                "pending_target": st.get("pending_target"),
                "backlog_count": len(st.get("backlog", [])) if isinstance(st.get("backlog"), list) else 0,
                "breaker": st.get("breaker") if isinstance(st.get("breaker"), dict) else {"open": False},
                "stats": st.get("stats") if isinstance(st.get("stats"), dict) else {},
            }
            return _json_response(self, 200, out)

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
        return _json_response(self, 404, {"error": "unknown_api_route", "path": path})

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
        payload: Dict[str, Any]
        from_cache = False
        if refresh or not idx_path.exists():
            try:
                payload = _build_discovery_og_wip_index(cfg)
                _atomic_write_json(idx_path, payload)
            except Exception as e:
                return _json_response(self, 500, {"error": "discovery_scan_failed", "detail": str(e)})
        else:
            loaded = _load_discovery_index_disk(idx_path)
            if loaded is None:
                try:
                    payload = _build_discovery_og_wip_index(cfg)
                    _atomic_write_json(idx_path, payload)
                except Exception as e:
                    return _json_response(self, 500, {"error": "discovery_scan_failed", "detail": str(e)})
            else:
                payload = loaded
                from_cache = True

        # Regroup when on-disk index predates (lib, exact-stem) merge for mp4+png pairs.
        try:
            if int(payload.get("version") or 0) < 5:
                payload = _build_discovery_og_wip_index(cfg)
                _atomic_write_json(idx_path, payload)
                from_cache = False
        except Exception as e:
            return _json_response(self, 500, {"error": "discovery_scan_failed", "detail": str(e)})

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
            "items": filtered,
        }
        for it in out["items"]:
            if isinstance(it, dict):
                rp = str(it.get("relpath") or "")
                it["url"] = "/files/" + urllib.parse.quote(rp, safe="") if rp else ""
                vr = it.get("video_relpath")
                tr = it.get("thumb_relpath")
                it["video_url"] = (
                    "/files/" + urllib.parse.quote(str(vr), safe="")
                    if isinstance(vr, str) and vr.strip()
                    else None
                )
                it["thumb_url"] = (
                    "/files/" + urllib.parse.quote(str(tr), safe="")
                    if isinstance(tr, str) and tr.strip()
                    else None
                )
        return _json_response(self, 200, out)

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
                res = _http_json("POST", f"{comfy}/interrupt", None, timeout_s=10)
                return _json_response(self, 200, {"ok": True, "kind": kind, "prompt_id": prompt_id, "result": res})
            res = _http_json("POST", f"{comfy}/queue", {"delete": [prompt_id.strip()]}, timeout_s=10)
            return _json_response(self, 200, {"ok": True, "kind": kind, "prompt_id": prompt_id, "result": res})
        except Exception as e:
            return _json_response(self, 502, {"error": "comfy_cancel_failed", "detail": str(e), "server": comfy})

    def _handle_comfy_clear(self) -> None:
        cfg = self.server.cfg
        comfy = str(cfg.comfy_server).rstrip("/")
        try:
            res = _http_json("POST", f"{comfy}/queue", {"clear": True}, timeout_s=10)
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
                    "expected": ["pause", "resume", "drain-once", "reset-breaker"],
                },
            )

        st["updated_at"] = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            _write_queue_ledger_state(cfg.queue_ledger_state_path, st)
        except Exception as e:
            return _json_response(self, 500, {"error": "write_failed", "detail": str(e)})
        return _json_response(self, 200, {"ok": True, "action": action, "paused": bool(st.get("paused"))})

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
        rel = _normalize_rel_posix(rel)
        if not rel:
            return _json_response(self, 400, {"error": "bad_path"})
        full = _safe_join(cfg.output_root, rel)
        if full is None or not full.exists() or not full.is_file():
            return _json_response(self, 404, {"error": "file_not_found", "relpath": rel})
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
    experiments_root = Path(args.experiments_root) if args.experiments_root else (ws / "output" / "output" / "experiments")
    output_root = Path(args.output_root) if args.output_root else (ws / "output")
    wip_override = (args.wip_root or "").strip() or os.environ.get("EXPERIMENTS_UI_WIP_ROOT", "").strip()
    wip_root = _resolve_wip_root(ws, output_root, wip_override)
    static_dir = Path(args.static_dir) if args.static_dir else (ws / "experiments_ui" / "dist")
    orchestrator_state_path = ws / "output" / "orchestrator" / "state.json"
    queue_ledger_state_path = ws / "output" / "output" / "experiments" / "_status" / "comfy_queue_ledger_state.json"
    queue_ledger_events_path = ws / "output" / "output" / "experiments" / "_status" / "comfy_queue_ledger.jsonl"
    discovery_index_path = ws / "output" / "output" / "_status" / "discovery_og_wip_index.json"
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
    )
    server = ExperimentsServer((args.host, int(args.port)), cfg)
    print(f"[experiments-ui] listening on http://{args.host}:{args.port}")
    print(f"[experiments-ui] workspace_root={cfg.workspace_root}")
    print(f"[experiments-ui] experiments_root={cfg.experiments_root}")
    print(f"[experiments-ui] output_root={cfg.output_root}")
    print(f"[experiments-ui] wip_root={cfg.wip_root}")
    print(f"[experiments-ui] static_dir={cfg.static_dir}")
    print(f"[experiments-ui] orchestrator_state={cfg.orchestrator_state_path}")
    print(f"[experiments-ui] queue_ledger_state={cfg.queue_ledger_state_path}")
    print(f"[experiments-ui] discovery_index={cfg.discovery_index_path}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

