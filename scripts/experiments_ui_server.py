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
- static dist: /workspace/experiments_ui/dist
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import mimetypes
import os
import posixpath
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


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


class Handler(BaseHTTPRequestHandler):
    server: "ExperimentsServer"  # type: ignore[assignment]

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path or "/"

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
        if not path.startswith("/api/"):
            return _json_response(self, 404, {"error": "not_found"})
        return self._handle_api_post(path)

    def _handle_api_get(self, path: str, query: str) -> None:
        cfg = self.server.cfg
        q = urllib.parse.parse_qs(query or "", keep_blank_values=True)

        if path == "/api/wip":
            return self._handle_wip_get(q)

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
                        if isinstance(it, list) and len(it) >= 2 and isinstance(it[1], str):
                            pid = it[1]
                        mapped = prompt_to_run.get(pid) if isinstance(pid, str) and pid else None
                        out.append(
                            {
                                "prompt_id": pid,
                                "raw": it,
                                "external": mapped is None,
                                "exp_id": mapped.get("exp_id") if isinstance(mapped, dict) else None,
                                "run_id": mapped.get("run_id") if isinstance(mapped, dict) else None,
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

        if path == "/api/experiments":
            # Optional: use server-level cache (invalidated on create-experiment)
            srv = self.server
            if getattr(srv, "_experiments_cache", None) is not None:
                return _json_response(self, 200, srv._experiments_cache)
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
            if srv is not None:
                srv._experiments_cache = payload
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
        if path == "/api/queue/comfy-cancel":
            return self._handle_comfy_cancel()
        if path == "/api/queue/comfy-clear":
            return self._handle_comfy_clear()
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
                    try:
                        rel = child.relative_to(cfg.output_root)
                        path_posix = str(rel).replace("\\", "/")
                    except ValueError:
                        path_posix = f"output/output/wip/{child.name}"
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
            try:
                rel = f.relative_to(cfg.output_root)
                relpath = str(rel).replace("\\", "/")
            except ValueError:
                relpath = f"output/output/wip/{dir_param}/{f.name}"
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

        # Invalidate experiments+relations cache so next GET /api/experiments sees the new experiment
        if hasattr(self.server, "_experiments_cache"):
            self.server._experiments_cache = None

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
        payload: Dict[str, Any] = {"prompt": prompt_obj, "client_id": "experiments-ui"}
        if front:
            payload["front"] = True
        try:
            submit = _http_json("POST", f"{comfy}/prompt", payload, timeout_s=30)
        except Exception as e:
            return _json_response(self, 502, {"error": "comfy_submit_failed", "detail": str(e), "server": comfy})

        try:
            (run_dir / "submit.json").write_text(json.dumps(submit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as e:
            return _json_response(self, 500, {"error": "write_submit_failed", "detail": str(e)})

        return _json_response(self, 200, {"ok": True, "exp_id": exp_id, "run_id": run_id, "front": front, "submit": submit})

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
        self._experiments_cache: Optional[Dict[str, Any]] = None  # experiments + relations; cleared on create-experiment


def main() -> int:
    ap = argparse.ArgumentParser(description="Serve Experiments UI API + React static frontend")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=int(os.environ.get("EXPERIMENTS_UI_PORT", "8790")))
    ap.add_argument("--workspace-root", default=os.environ.get("WORKSPACE_PATH", ""))
    ap.add_argument("--experiments-root", default="")
    ap.add_argument("--output-root", default="")
    ap.add_argument("--static-dir", default="")
    args = ap.parse_args()

    base = Path(args.workspace_root) if args.workspace_root else Path(__file__).resolve().parent.parent
    ws = _resolve_workspace_root(base)
    experiments_root = Path(args.experiments_root) if args.experiments_root else (ws / "output" / "output" / "experiments")
    output_root = Path(args.output_root) if args.output_root else (ws / "output")
    wip_root = output_root / "output" / "wip"
    static_dir = Path(args.static_dir) if args.static_dir else (ws / "experiments_ui" / "dist")
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
    )
    server = ExperimentsServer((args.host, int(args.port)), cfg)
    print(f"[experiments-ui] listening on http://{args.host}:{args.port}")
    print(f"[experiments-ui] workspace_root={cfg.workspace_root}")
    print(f"[experiments-ui] experiments_root={cfg.experiments_root}")
    print(f"[experiments-ui] output_root={cfg.output_root}")
    print(f"[experiments-ui] wip_root={cfg.wip_root}")
    print(f"[experiments-ui] static_dir={cfg.static_dir}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

