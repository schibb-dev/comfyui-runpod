#!/usr/bin/env python3
"""
Shared helper to submit a single experiment run to ComfyUI: read prompt, POST, write submit.json and metrics.

Used by the experiment queue manager and optionally by tune_experiment (e.g. behind --submit-all).
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

# --- Helpers (minimal copy for standalone use) ---

_WIN_ABS_PATH_RE = re.compile(r"^[a-zA-Z]:\\\\")
_WIN_UNC_PATH_RE = re.compile(r"^\\\\\\\\")
_PATHLIKE_EXTS = (
    ".safetensors",
    ".ckpt",
    ".gguf",
    ".pt",
    ".pth",
    ".bin",
    ".yaml",
    ".yml",
    ".json",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".mp4",
)


def _read_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def _write_json(p: Path, obj: Any, *, indent: int = 2) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=indent, ensure_ascii=False), encoding="utf-8")


def _http_json(method: str, url: str, payload: Optional[Dict[str, Any]] = None, timeout_s: int = 30) -> Any:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def _utc_iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _read_json_dict(p: Path) -> Dict[str, Any]:
    try:
        obj = _read_json(p)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _merge_json_dict(p: Path, patch: Dict[str, Any], *, indent: int = 2) -> Dict[str, Any]:
    base = _read_json_dict(p) if p.exists() else {}
    merged = {**base, **patch}
    _write_json(p, merged, indent=indent)
    return merged


def _metrics_path(run_dir: Path) -> Path:
    return run_dir / "metrics.json"


def _read_prompt_id_from_submit(submit_path: Path) -> Optional[str]:
    try:
        obj = _read_json(submit_path)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    pid = obj.get("prompt_id")
    return pid if isinstance(pid, str) and pid.strip() else None


def _normalize_prompt_paths_for_linux(prompt_obj: Dict[str, Any]) -> None:
    def norm_str(s: str) -> str:
        if "\\" not in s:
            return s
        if _WIN_ABS_PATH_RE.match(s) or _WIN_UNC_PATH_RE.match(s):
            return s
        sl = s.lower()
        if any(ext in sl for ext in _PATHLIKE_EXTS):
            return s.replace("\\", "/")
        return s

    for _nid, node in prompt_obj.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for k, v in list(inputs.items()):
            if isinstance(v, str):
                nv = norm_str(v)
                if nv != v:
                    inputs[k] = nv


def _prune_dead_nodes(prompt_obj: Dict[str, Any]) -> int:
    incoming: Dict[str, set] = {str(k): set() for k in prompt_obj.keys()}
    outgoing: Dict[str, set] = {str(k): set() for k in prompt_obj.keys()}

    for dst_id, node in prompt_obj.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for _k, v in inputs.items():
            if isinstance(v, list) and len(v) >= 2:
                src_id = str(v[0])
                dst_id_s = str(dst_id)
                if src_id in prompt_obj:
                    outgoing[src_id].add(dst_id_s)
                    incoming[dst_id_s].add(src_id)

    def has_any_inputs(nid: str) -> bool:
        node = prompt_obj.get(nid)
        if not isinstance(node, dict):
            return False
        inputs = node.get("inputs")
        return isinstance(inputs, dict) and len(inputs) > 0

    roots = [nid for nid in prompt_obj.keys() if len(outgoing[str(nid)]) == 0 and has_any_inputs(str(nid))]
    if not roots:
        return 0

    keep = set()
    stack = [str(r) for r in roots]
    while stack:
        cur = stack.pop()
        if cur in keep:
            continue
        keep.add(cur)
        for src in incoming.get(cur, set()):
            if src not in keep:
                stack.append(src)

    removed = 0
    for nid in list(prompt_obj.keys()):
        if str(nid) not in keep:
            prompt_obj.pop(nid, None)
            removed += 1
    return removed


# --- Public API ---

def submit_run_to_comfyui(
    prompt_path: Path,
    run_dir: Path,
    server: str,
    *,
    client_id: str = "comfy_tool",
    timeout_s: int = 30,
) -> Optional[str]:
    """
    Read prompt.json, POST to server/prompt, write submit.json and metrics. Returns prompt_id or None.

    - If run_dir/history.json exists, does not submit; returns existing prompt_id from submit.json if present.
    - If submit.json already has prompt_id, returns it (and backfills metrics if missing).
    - Otherwise submits, writes submit.json and metrics, returns prompt_id.
    """
    run_dir = Path(run_dir)
    prompt_path = Path(prompt_path)
    server = server.rstrip("/")
    hist_path = run_dir / "history.json"
    submit_path = run_dir / "submit.json"

    if hist_path.exists():
        return _read_prompt_id_from_submit(submit_path) if submit_path.exists() else None

    prompt_id = _read_prompt_id_from_submit(submit_path) if submit_path.exists() else None
    if prompt_id:
        mp = _metrics_path(run_dir)
        if not mp.exists():
            try:
                submitted_ts = float(submit_path.stat().st_mtime)
            except Exception:
                submitted_ts = time.time()
            _merge_json_dict(
                mp,
                {
                    "prompt_id": prompt_id,
                    "submitted_ts": submitted_ts,
                    "submitted_at": _utc_iso(submitted_ts),
                    "submitted_at_source": "submit.json_mtime",
                },
                indent=2,
            )
        return prompt_id

    prompt_obj = _read_json(prompt_path)
    if not isinstance(prompt_obj, dict):
        raise RuntimeError(f"prompt.json is not a dict: {prompt_path}")
    _prune_dead_nodes(prompt_obj)
    _normalize_prompt_paths_for_linux(prompt_obj)

    run_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    submit = _http_json("POST", f"{server}/prompt", {"prompt": prompt_obj, "client_id": client_id}, timeout_s=timeout_s)
    t1 = time.time()
    prompt_id2 = submit.get("prompt_id")
    if not isinstance(prompt_id2, str) or not prompt_id2.strip():
        raise RuntimeError(f"Submit response missing prompt_id: {prompt_path}")
    _write_json(submit_path, submit, indent=2)
    _merge_json_dict(
        _metrics_path(run_dir),
        {
            "prompt_id": prompt_id2,
            "submit_started_ts": float(t0),
            "submitted_ts": float(t1),
            "submitted_at": _utc_iso(float(t1)),
            "submit_http_sec": float(max(0.0, t1 - t0)),
            "schema": 1,
        },
        indent=2,
    )
    return prompt_id2
