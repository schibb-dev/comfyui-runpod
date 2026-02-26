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
import json
import mimetypes
import os
import posixpath
import re
import urllib.parse
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_int(x: Any) -> Optional[int]:
    try:
        return int(x)
    except Exception:
        return None


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
    # collapse //, /./, /../
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
    # convert posix relative path to local path parts
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
        # suffix bytes: last b bytes
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
    end: int
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
    """
    Extract a normalized list of outputs from ComfyUI history.json.

    In your observed history format, history.json is:
      { "<prompt_id>": { "outputs": { "<node_id>": { "gifs": [ {filename, subfolder, ...}, ...] } } } }
    """
    out: List[Dict[str, Any]] = []
    if not isinstance(history_obj, dict) or not history_obj:
        return out

    # Find the first prompt record. (Some history endpoints might return just the record; handle both.)
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
    """
    Return (primary_video_relpath, primary_image_relpath).
    """
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


@dataclass(frozen=True)
class ServerConfig:
    workspace_root: Path
    experiments_root: Path
    output_root: Path
    static_dir: Path


def _iter_experiments(experiments_root: Path) -> List[Path]:
    if not experiments_root.exists():
        return []
    out: List[Path] = []
    for child in sorted([p for p in experiments_root.iterdir() if p.is_dir()], key=lambda p: p.name):
        if (child / "manifest.json").exists():
            out.append(child)
    return out


def _load_manifest(exp_dir: Path) -> Optional[Dict[str, Any]]:
    mf = exp_dir / "manifest.json"
    if not mf.exists():
        return None
    try:
        obj = _read_json(mf)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _derive_source_image_from_source_video(
    exp_dir: Path, mf: Dict[str, Any], output_root: Path
) -> Optional[str]:
    """
    If manifest has no source_image, try to find the source image that was copied
    from the source video (base_mp4) into this experiment's inputs/ by tune_experiment.
    Only matches the image with the same stem as base_mp4. Returns None if not found
    (caller can treat as unknown).
    """
    if isinstance(mf.get("source_image"), str) and mf.get("source_image", "").strip():
        return mf.get("source_image", "").strip()
    base_mp4 = mf.get("base_mp4")
    if not isinstance(base_mp4, str) or not base_mp4.strip():
        return None
    stem = Path(base_mp4.replace("\\", "/")).stem
    inputs_dir = exp_dir / "inputs"
    if not inputs_dir.is_dir():
        return None
    for ext in (".png", ".jpg", ".jpeg"):
        candidate = inputs_dir / (stem + ext)
        if candidate.is_file():
            try:
                return str(candidate.relative_to(output_root)).replace("\\", "/")
            except ValueError:
                return str(candidate).replace("\\", "/")
    return None


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


class Handler(BaseHTTPRequestHandler):
    server: "ExperimentsServer"  # type: ignore[assignment]

    def log_message(self, format: str, *args: Any) -> None:
        # keep default logging but slightly quieter could be done here later
        super().log_message(format, *args)

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        cfg = self.server.cfg
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path or "/"

        if path.startswith("/api/"):
            return self._handle_api_get(path)

        if path.startswith("/files/"):
            rel = path[len("/files/") :]
            rel = urllib.parse.unquote(rel)
            return self._handle_files_get(rel)

        return self._handle_static_get(path)

    def do_HEAD(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        # Support HEAD for /files and static assets (useful for debugging + browser preflight behavior).
        self.do_GET()

    def _handle_api_get(self, path: str) -> None:
        cfg = self.server.cfg

        if path == "/api/experiments":
            exps: List[Dict[str, Any]] = []
            for exp_dir in _iter_experiments(cfg.experiments_root):
                mf = _load_manifest(exp_dir) or {}
                exp_id = mf.get("exp_id") if isinstance(mf.get("exp_id"), str) else exp_dir.name
                runs = _run_dirs(exp_dir)
                counts = {"total": len(runs), "complete": 0, "submitted": 0, "not_submitted": 0}
                for rd in runs:
                    counts[_run_status(rd)] += 1  # type: ignore[index]
                source_image = mf.get("source_image") if isinstance(mf.get("source_image"), str) else None
                if not source_image or not str(source_image).strip():
                    source_image = _derive_source_image_from_source_video(exp_dir, mf, cfg.output_root)
                exps.append(
                    {
                        "exp_id": exp_id,
                        "dir": str(exp_dir),
                        "created_at": mf.get("created_at"),
                        "source_image": source_image,
                        "base_mp4": mf.get("base_mp4"),
                        "fixed_seed": mf.get("fixed_seed"),
                        "fixed_duration_sec": mf.get("fixed_duration_sec"),
                        "sweep": mf.get("sweep") if isinstance(mf.get("sweep"), dict) else {},
                        "run_counts": counts,
                    }
                )
            return _json_response(self, 200, {"experiments": exps})

        m = re.match(r"^/api/experiments/([^/]+)$", path)
        if m:
            exp_id = m.group(1)
            exp_dir = cfg.experiments_root / exp_id
            mf = _load_manifest(exp_dir)
            if mf is None:
                return _json_response(self, 404, {"error": "experiment_not_found", "exp_id": exp_id})
            return _json_response(self, 200, {"manifest": mf})

        m = re.match(r"^/api/experiments/([^/]+)/runs$", path)
        if m:
            exp_id = m.group(1)
            exp_dir = cfg.experiments_root / exp_id
            mf = _load_manifest(exp_dir)
            if mf is None:
                return _json_response(self, 404, {"error": "experiment_not_found", "exp_id": exp_id})

            runs_out: List[Dict[str, Any]] = []
            for run_dir in _run_dirs(exp_dir):
                params_path = run_dir / "params.json"
                submit_path = run_dir / "submit.json"
                history_path = run_dir / "history.json"

                params = None
                submit = None
                history = None
                try:
                    params = _read_json(params_path) if params_path.exists() else None
                except Exception:
                    params = None
                try:
                    submit = _read_json(submit_path) if submit_path.exists() else None
                except Exception:
                    submit = None
                try:
                    history = _read_json(history_path) if history_path.exists() else None
                except Exception:
                    history = None

                prompt_id = submit.get("prompt_id") if isinstance(submit, dict) else None
                outs = _extract_outputs_from_history(history)
                primary_vid, primary_img = _pick_primary_media(outs)

                def url_for(relpath: Optional[str]) -> Optional[str]:
                    if not relpath:
                        return None
                    return "/files/" + urllib.parse.quote(relpath)

                status = _run_status(run_dir)
                # attempt to read success/failure from history
                status_str = None
                node_errors = None
                if isinstance(history, dict):
                    # dig status.status_str if present in any record
                    rec = None
                    if "status" in history and isinstance(history.get("status"), dict):
                        rec = history
                    else:
                        for _k, v in history.items():
                            if isinstance(v, dict) and isinstance(v.get("status"), dict):
                                rec = v
                                break
                    if isinstance(rec, dict):
                        st = rec.get("status")
                        if isinstance(st, dict):
                            status_str = st.get("status_str")
                    # node_errors may be in submit.json
                if isinstance(submit, dict):
                    node_errors = submit.get("node_errors")

                metrics = None
                metrics_path = run_dir / "metrics.json"
                if metrics_path.exists():
                    try:
                        metrics = _read_json(metrics_path)
                    except Exception:
                        metrics = None
                if not isinstance(metrics, dict):
                    metrics = None

                runs_out.append(
                    {
                        "exp_id": exp_id,
                        "run_id": run_dir.name,
                        "status": status,
                        "status_str": status_str,
                        "prompt_id": prompt_id,
                        "params": params if isinstance(params, dict) else {},
                        "metrics": metrics,
                        "outputs": [
                            {
                                **o,
                                "url": url_for(o.get("relpath") if isinstance(o, dict) else None),
                            }
                            for o in outs
                        ],
                        "primary_video": {"relpath": primary_vid, "url": url_for(primary_vid)},
                        "primary_image": {"relpath": primary_img, "url": url_for(primary_img)},
                        "node_errors": node_errors,
                    }
                )

            return _json_response(
                self,
                200,
                {
                    "exp_id": exp_id,
                    "manifest": {
                        "exp_id": mf.get("exp_id"),
                        "created_at": mf.get("created_at"),
                        "base_mp4": mf.get("base_mp4"),
                        "fixed_seed": mf.get("fixed_seed"),
                        "fixed_duration_sec": mf.get("fixed_duration_sec"),
                        "sweep": mf.get("sweep"),
                    },
                    "runs": runs_out,
                },
            )

        return _json_response(self, 404, {"error": "unknown_api_route", "path": path})

    def _handle_files_get(self, rel: str) -> None:
        cfg = self.server.cfg
        rel = _normalize_rel_posix(rel)
        if not rel:
            return _json_response(self, 400, {"error": "bad_path"})

        # Allow serving any file under output_root only.
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

        # Serve index.html for SPA routes.
        if path == "/" or path == "":
            return self._serve_static_file("index.html")

        rel = _normalize_rel_posix(path)
        if not rel:
            rel = "index.html"

        # If it exists, serve; else fallback to index.html (SPA routing).
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
    ap.add_argument(
        "--workspace-root",
        default=os.environ.get("WORKSPACE_PATH", ""),
        help="Workspace root directory (default: WORKSPACE_PATH env, or inferred from script location)",
    )
    ap.add_argument(
        "--experiments-root",
        default="",
        help="Root directory containing experiment subdirs (default: <workspace>/output/output/experiments)",
    )
    ap.add_argument(
        "--output-root",
        default="",
        help="Root directory for /files serving (default: <workspace>/output)",
    )
    ap.add_argument(
        "--static-dir",
        default="",
        help="Directory containing built React assets (default: <workspace>/experiments_ui/dist)",
    )
    args = ap.parse_args()

    if args.workspace_root:
        ws = Path(args.workspace_root)
    else:
        # scripts/experiments_ui_server.py -> workspace/scripts -> workspace
        ws = Path(__file__).resolve().parent.parent

    experiments_root = Path(args.experiments_root) if args.experiments_root else (ws / "output" / "output" / "experiments")
    output_root = Path(args.output_root) if args.output_root else (ws / "output")
    static_dir = Path(args.static_dir) if args.static_dir else (ws / "experiments_ui" / "dist")

    cfg = ServerConfig(
        workspace_root=ws,
        experiments_root=experiments_root,
        output_root=output_root,
        static_dir=static_dir,
    )

    server = ExperimentsServer((args.host, int(args.port)), cfg)
    print(f"[experiments-ui] listening on http://{args.host}:{args.port}")
    print(f"[experiments-ui] workspace_root={cfg.workspace_root}")
    print(f"[experiments-ui] experiments_root={cfg.experiments_root}")
    print(f"[experiments-ui] output_root={cfg.output_root}")
    print(f"[experiments-ui] static_dir={cfg.static_dir}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

