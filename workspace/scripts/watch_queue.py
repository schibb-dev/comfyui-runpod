#!/usr/bin/env python3
"""
Asynchronous-ish queue manager for ComfyUI experiment runs.

This script "watches" one experiment directory (with manifest.json) OR an
experiments root directory containing many experiment subdirectories.

For each run directory:
  - if history.json exists: done
  - elif submit.json has prompt_id: poll /history/<prompt_id> and write history.json when available
  - else: submit prompt.json to /prompt and write submit.json

It keeps the system loosely coupled:
  - You can generate experiments at any time.
  - You can queue many runs and check later.
  - This watcher can run continuously and "catch up" across experiments.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


def _read_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


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


def _normalize_prompt_paths_for_linux(prompt_obj: Dict[str, Any]) -> None:
    """
    Normalize Windows-style backslashes in prompt inputs to forward slashes.

    This matters because many ComfyUI dropdown inputs (e.g. model names) are
    validated against lists that use "/" as the separator (even on Windows,
    most nodes treat subfolders as POSIX-style).

    We intentionally avoid touching absolute Windows paths (C:\\...) and UNC paths.
    """

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
    """
    Remove nodes that are not part of any executable graph.

    Prompts saved from the UI can include helper/UX nodes that are not connected to
    any output. If those nodes come from optional custom-node packs, ComfyUI will
    reject the whole prompt with missing_node_type even though they are unused.

    Strategy:
    - Build a dependency graph from link-style inputs: ["<src_id>", <out_index>]
    - Treat "roots" as sink nodes (no outgoing edges) with non-empty inputs
    - Keep the reverse-closure of roots (all dependencies)
    """

    # Build edges (src -> dst) for link inputs.
    incoming: Dict[str, Set[str]] = {str(k): set() for k in prompt_obj.keys()}
    outgoing: Dict[str, Set[str]] = {str(k): set() for k in prompt_obj.keys()}

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

    keep: Set[str] = set()
    stack: List[str] = [str(r) for r in roots]
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


def _write_json(p: Path, obj: Any, *, indent: int = 2) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=indent, ensure_ascii=False), encoding="utf-8")


def _utc_iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _read_json_dict(p: Path) -> Dict[str, Any]:
    try:
        obj = _read_json(p)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


class _Lock:
    def __init__(self, lock_path: Path, *, timeout_s: float = 5.0) -> None:
        self.lock_path = lock_path
        self.timeout_s = float(timeout_s)
        self._held = False

    def __enter__(self) -> "_Lock":
        deadline = time.time() + max(0.0, self.timeout_s)
        while True:
            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(fd, f"pid={os.getpid()} ts={time.time()}\n".encode("utf-8", "replace"))
                finally:
                    os.close(fd)
                self._held = True
                return self
            except FileExistsError:
                if time.time() >= deadline:
                    # Best-effort: proceed without a lock if we can't acquire quickly.
                    return self
                time.sleep(0.05 + random.random() * 0.15)

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self._held:
            return
        try:
            self.lock_path.unlink(missing_ok=True)
        except Exception:
            return


def _merge_json_dict(p: Path, patch: Dict[str, Any], *, indent: int = 2) -> Dict[str, Any]:
    # Avoid clobbering ws_event_tap timing fields due to concurrent writes:
    # metrics.json is written by both watch_queue.py and ws_event_tap.py.
    if p.name == "metrics.json":
        lock_path = p.with_suffix(p.suffix + ".lock")
        with _Lock(lock_path, timeout_s=5.0):
            base = _read_json_dict(p) if p.exists() else {}
            merged = {**base, **patch}
            _write_json(p, merged, indent=indent)
            return merged
    base = _read_json_dict(p) if p.exists() else {}
    merged = {**base, **patch}
    _write_json(p, merged, indent=indent)
    return merged


def _metrics_path(run_dir: Path) -> Path:
    return run_dir / "metrics.json"


def _http_json(method: str, url: str, payload: Optional[Dict[str, Any]] = None, timeout_s: int = 30) -> Any:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def _read_prompt_id_from_submit(submit_path: Path) -> Optional[str]:
    try:
        obj = _read_json(submit_path)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    pid = obj.get("prompt_id")
    return pid if isinstance(pid, str) and pid.strip() else None


@dataclass(frozen=True)
class RunRef:
    exp_id: str
    run_id: str
    exp_dir: Path
    run_dir: Path
    prompt_path: Path
    submit_path: Path
    history_path: Path


def _exp_id_for_dir(exp_dir: Path) -> str:
    mf = exp_dir / "manifest.json"
    if mf.exists():
        try:
            obj = _read_json(mf)
            if isinstance(obj, dict) and isinstance(obj.get("exp_id"), str) and obj["exp_id"].strip():
                return obj["exp_id"].strip()
        except Exception:
            pass
    return exp_dir.name


def _stable_json_sha256(obj: Any) -> Optional[str]:
    try:
        s = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except Exception:
        return None
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _workflow_info_for_exp(exp_dir: Path) -> Dict[str, Any]:
    """
    Best-effort workflow fingerprint for an experiment.

    Uses <exp_dir>/base/base.workflow.json which is created by tune_experiment.py.
    """
    wf_path = exp_dir / "base" / "base.workflow.json"
    if not wf_path.exists():
        return {"workflow_path": str(wf_path), "workflow_sha256": None, "workflow_id": None}
    try:
        wf = _read_json(wf_path)
    except Exception:
        return {"workflow_path": str(wf_path), "workflow_sha256": None, "workflow_id": None}
    if not isinstance(wf, dict):
        return {"workflow_path": str(wf_path), "workflow_sha256": None, "workflow_id": None}
    return {
        "workflow_path": str(wf_path),
        "workflow_sha256": _stable_json_sha256(wf),
        "workflow_id": wf.get("id") if isinstance(wf.get("id"), str) else None,
        "workflow_revision": wf.get("revision"),
        "workflow_last_node_id": wf.get("last_node_id"),
        "workflow_last_link_id": wf.get("last_link_id"),
    }


def _workflow_ui_for_run(r: "RunRef") -> Optional[Dict[str, Any]]:
    """
    Resolve the UI workflow JSON to embed in pnginfo when submitting this run.

    Prefers per-run workflow file (e.g. <stem>.workflow.<run_id>.json) so saved
    PNGs/MP4s carry the exact workflow for this run. Falls back to experiment
    base workflow if no per-run file exists.
    """
    # Per-run workflow written by tune_experiment generate: <stem>.workflow.<run_id>.json (exclude .cleaned)
    for p in r.run_dir.iterdir():
        if not p.is_file() or p.suffix.lower() != ".json":
            continue
        name = p.name
        if ".cleaned." in name:
            continue
        if name.endswith(f".workflow.{r.run_id}.json"):
            try:
                obj = _read_json(p)
                return obj if isinstance(obj, dict) else None
            except Exception:
                break
    # Fallback: experiment base workflow
    base_wf = r.exp_dir / "base" / "base.workflow.json"
    if base_wf.exists():
        try:
            obj = _read_json(base_wf)
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass
    return None


def _crash_dir_for(root_or_exp: Path) -> Path:
    """
    Write crash ledger under the experiments root:
      <experiments_root>/_crashes/
    """
    base = root_or_exp
    if (base / "manifest.json").exists():
        base = base.parent
    return base / "_crashes"


def _append_crash_event(*, root_or_exp: Path, event: Dict[str, Any]) -> None:
    """
    Append an event to an append-only JSONL crash ledger. Best-effort (never raises).
    """
    try:
        crash_dir = _crash_dir_for(root_or_exp)
        crash_dir.mkdir(parents=True, exist_ok=True)
        ledger = crash_dir / "crash_ledger.jsonl"
        e = dict(event)
        e.setdefault("ts", time.time())
        e.setdefault("at", _utc_iso(float(e["ts"])))
        with ledger.open("a", encoding="utf-8") as f:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    except Exception:
        return


def _iter_experiment_dirs(root_or_exp: Path) -> List[Path]:
    """
    If root_or_exp looks like an experiment dir (manifest.json present), return [it].
    Else treat it as a root and return child dirs that look like experiments.
    """
    p = root_or_exp
    if (p / "manifest.json").exists():
        return [p]

    if not p.exists() or not p.is_dir():
        return []

    out: List[Path] = []
    try:
        for child in [c for c in p.iterdir() if c.is_dir()]:
            if (child / "manifest.json").exists() and (child / "runs").exists():
                out.append(child)
    except Exception:
        return out
    # Default behavior: prioritize newer experiments first.
    # We use manifest.json mtime when available, otherwise fall back to directory mtime.
    def _mtime_key(d: Path) -> float:
        try:
            mf = d / "manifest.json"
            if mf.exists():
                return float(mf.stat().st_mtime)
            return float(d.stat().st_mtime)
        except Exception:
            return 0.0

    out.sort(key=_mtime_key, reverse=True)
    return out


def _iter_runs(exp_dir: Path) -> List[RunRef]:
    exp_id = _exp_id_for_dir(exp_dir)
    runs_dir = exp_dir / "runs"
    if not runs_dir.exists():
        return []

    out: List[RunRef] = []
    for run_dir in sorted([d for d in runs_dir.iterdir() if d.is_dir()], key=lambda x: x.name):
        prompt_path = run_dir / "prompt.json"
        if not prompt_path.exists():
            continue
        run_id = run_dir.name
        out.append(
            RunRef(
                exp_id=exp_id,
                run_id=run_id,
                exp_dir=exp_dir,
                run_dir=run_dir,
                prompt_path=prompt_path,
                submit_path=run_dir / "submit.json",
                history_path=run_dir / "history.json",
            )
        )
    return out


def _client_id(exp_id: str) -> str:
    # Keep this aligned with tune_experiment.py.
    #
    # IMPORTANT: ComfyUI's /ws stream can be scoped by clientId; using a stable client_id
    # for experiment tooling lets a single WS listener (ws_event_tap.py) observe execution
    # start/end for all experiment runs.
    #
    # We rely on prompt_id (submit.json) for exp_id/run_id correlation, so per-experiment
    # client_id is not required.
    _ = exp_id  # reserved for future use
    return "comfy_tool"


def _queue_prompt_ids(server: str, *, timeout_s: int = 5) -> Optional[Set[str]]:
    """
    Return set of prompt_id strings currently running or pending on the server.
    Returns None if the queue cannot be queried.
    """
    try:
        q = _http_json("GET", f"{server.rstrip('/')}/queue", None, timeout_s=timeout_s)
    except Exception:
        return None
    if not isinstance(q, dict):
        return None
    ids: Set[str] = set()
    for key in ("queue_running", "queue_pending"):
        arr = q.get(key)
        if not isinstance(arr, list):
            continue
        for item in arr:
            # item is usually [number, prompt_id, prompt, meta, outputs]
            if isinstance(item, list) and len(item) >= 2 and isinstance(item[1], str):
                ids.add(item[1])
    return ids


def _queue_is_empty(server: str, *, timeout_s: int = 5) -> Optional[bool]:
    try:
        q = _http_json("GET", f"{server.rstrip('/')}/queue", None, timeout_s=timeout_s)
    except Exception:
        return None
    if not isinstance(q, dict):
        return None
    running = q.get("queue_running")
    pending = q.get("queue_pending")
    if not isinstance(running, list) or not isinstance(pending, list):
        return None
    return len(running) == 0 and len(pending) == 0


def _has_outputs_for_run(exp_dir: Path, run_id: str) -> bool:
    """
    Heuristic: treat a run as complete if output files exist for it.

    In our experiments, outputs are written under the experiment dir itself,
    and filenames begin with `run_###_...`.
    """
    if not exp_dir.exists():
        return False
    # Look for at least one mp4, or failing that any image.
    pat_mp4 = re.compile(rf"^{re.escape(run_id)}_.*\.mp4$", re.IGNORECASE)
    pat_img = re.compile(rf"^{re.escape(run_id)}_.*\.(png|jpg|jpeg|webp)$", re.IGNORECASE)
    try:
        for p in exp_dir.rglob("*"):
            if not p.is_file():
                continue
            name = p.name
            if pat_mp4.match(name) or pat_img.match(name):
                return True
    except Exception:
        return False
    return False


def _has_video_for_run(exp_dir: Path, run_id: str) -> bool:
    """
    Return True if at least one mp4 output exists for run_id.

    Prefer fast glob in the experiment dir root; fall back to rglob.
    """
    if not exp_dir.exists():
        return False
    try:
        for p in exp_dir.glob(f"{run_id}_*.mp4"):
            if p.is_file():
                return True
    except Exception:
        pass

    pat_mp4 = re.compile(rf"^{re.escape(run_id)}_.*\.mp4$", re.IGNORECASE)
    try:
        for p in exp_dir.rglob("*"):
            if not p.is_file():
                continue
            if pat_mp4.match(p.name):
                return True
    except Exception:
        return False
    return False


_MEDIA_EXTS = {".mp4", ".png", ".webp", ".jpg", ".jpeg"}


def _infer_output_root(root_or_exp: Path, *, exp_dir: Path) -> Optional[Path]:
    """
    Infer /workspace/output given either:
      - experiments root: .../output/output/experiments
      - experiment dir:   .../output/output/experiments/<exp_id>

    We avoid hardcoding WORKSPACE_PATH so this also works in local layouts.
    """
    try:
        base = root_or_exp
        # if an experiment dir was passed, shift to experiments root
        if (base / "manifest.json").exists():
            base = base.parent
        # experiments_root = .../output/output/experiments
        experiments_root = base
        out_root = experiments_root.parent.parent
        return out_root.resolve()
    except Exception:
        # fallback: use exp_dir ancestry (most reliable)
        try:
            return exp_dir.parent.parent.parent.resolve()
        except Exception:
            return None


def _find_media_files_for_run(exp_dir: Path, run_id: str, *, max_files: int = 50) -> List[Path]:
    """
    Find output media files for a run (e.g. run_001_*.mp4/png/...).

    Prefer fast globs at exp root; fall back to rglob if needed.
    """
    out: List[Path] = []
    if not exp_dir.exists():
        return out

    # Fast path: most workflows write outputs directly under the experiment dir.
    for ext in sorted(_MEDIA_EXTS):
        try:
            for p in exp_dir.glob(f"{run_id}_*{ext}"):
                if p.is_file():
                    out.append(p)
                    if len(out) >= max_files:
                        return out
        except Exception:
            continue

    # Slow fallback: nested outputs.
    pat = re.compile(rf"^{re.escape(run_id)}_.*\.(mp4|png|jpg|jpeg|webp)$", re.IGNORECASE)
    try:
        for p in exp_dir.rglob("*"):
            try:
                if not p.is_file():
                    continue
            except Exception:
                continue
            if pat.match(p.name):
                out.append(p)
                if len(out) >= max_files:
                    break
    except Exception:
        return out

    return out


def _rel_subfolder_and_filename(*, output_root: Path, path: Path) -> Optional[Tuple[str, str]]:
    """
    Convert an absolute output path to (subfolder, filename) relative to output_root.
    """
    try:
        rel = path.resolve().relative_to(output_root.resolve())
    except Exception:
        return None
    rel_posix = str(rel).replace("\\", "/")
    # rel_posix is like "output/experiments/<exp>/run_001_foo.mp4"
    parent = str(Path(rel_posix).parent).replace("\\", "/")
    return ("" if parent == "." else parent, Path(rel_posix).name)


def _maybe_extract_prompt_workflow_from_media(media_paths: List[Path]) -> Tuple[Optional[Any], Optional[Any]]:
    """
    Best-effort: extract embedded prompt/workflow from companion PNG text chunks or MP4 tags.
    """
    try:
        from comfy_meta_lib import (
            extract_prompt_workflow_from_png_chunks,
            extract_prompt_workflow_from_tags,
            ffprobe_format_tags,
            read_png_text_chunks,
        )
    except Exception:
        return None, None

    # Prefer PNG (usually includes exact prompt/workflow).
    for p in media_paths:
        if p.suffix.lower() != ".png":
            continue
        try:
            chunks = read_png_text_chunks(p)
            pr, wf = extract_prompt_workflow_from_png_chunks(chunks)
            if pr is not None or wf is not None:
                return pr, wf
        except Exception:
            continue

    # Fallback: container tags on MP4 (requires ffprobe in PATH).
    for p in media_paths:
        if p.suffix.lower() != ".mp4":
            continue
        try:
            tags = ffprobe_format_tags(p)
            pr, wf = extract_prompt_workflow_from_tags(tags)
            if pr is not None or wf is not None:
                return pr, wf
        except Exception:
            continue

    return None, None


def _dummy_history_from_fs(
    *,
    root_or_exp: Path,
    r: "RunRef",
    prompt_id: Optional[str],
    media_paths: List[Path],
    include_media_metadata: bool,
) -> Optional[Dict[str, Any]]:
    """
    Create a minimal ComfyUI-like history dict from filesystem outputs.
    """
    output_root = _infer_output_root(root_or_exp, exp_dir=r.exp_dir)
    if output_root is None:
        return None

    items: List[Dict[str, Any]] = []
    for p in media_paths:
        rel = _rel_subfolder_and_filename(output_root=output_root, path=p)
        if rel is None:
            continue
        subfolder, filename = rel
        items.append(
            {
                "filename": filename,
                "subfolder": subfolder,
                "type": "output",
                "format": None,
                "frame_rate": None,
                "workflow": None,
                "fullpath": str(p),
            }
        )

    if not items:
        return None

    pr = None
    wf = None
    if include_media_metadata:
        pr, wf = _maybe_extract_prompt_workflow_from_media(media_paths)

    record: Dict[str, Any] = {
        "outputs": {"fs": {"fs": items}},
        "status": {
            "status_str": "success",
            "completed": True,
            "messages": [
                {
                    "type": "last_ditch_backfill",
                    "message": "history.json backfilled from filesystem outputs (ComfyUI /history unavailable)",
                }
            ],
        },
        "backfill": {
            "schema": 1,
            "at": _utc_iso(time.time()),
            "exp_id": r.exp_id,
            "run_id": r.run_id,
            "prompt_id": prompt_id,
            "media_count": len(items),
        },
    }
    if isinstance(pr, dict) and pr:
        record["prompt"] = pr
    if isinstance(wf, dict) and wf:
        record["workflow"] = wf

    key = (prompt_id or r.run_id).strip() if isinstance(prompt_id, str) and prompt_id.strip() else r.run_id
    return {key: record}


def _latest_mtime(paths: Iterable[Path]) -> Optional[float]:
    latest: Optional[float] = None
    for p in paths:
        try:
            if not p.exists():
                continue
            t = p.stat().st_mtime
        except Exception:
            continue
        latest = t if latest is None else max(latest, t)
    return latest


def _requeue_state_path(r: RunRef) -> Path:
    return r.run_dir / "requeue.json"


def _read_requeue_state(r: RunRef) -> Dict[str, Any]:
    p = _requeue_state_path(r)
    try:
        obj = _read_json(p)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    return {}


def _can_requeue(
    r: RunRef,
    *,
    now: float,
    max_requeues: int,
    cooldown_s: float,
) -> bool:
    st = _read_requeue_state(r)
    tries = st.get("requeue_count")
    if isinstance(tries, int) and tries >= max_requeues:
        return False
    last = st.get("last_requeue_ts")
    if isinstance(last, (int, float)) and cooldown_s > 0 and (now - float(last)) < cooldown_s:
        return False
    return True


def _mark_requeue_attempt(
    r: RunRef,
    *,
    now: float,
    reason: str,
    indent: int,
) -> None:
    st = _read_requeue_state(r)
    tries = st.get("requeue_count")
    tries_i = int(tries) if isinstance(tries, int) else 0
    st = {
        **st,
        "requeue_count": tries_i + 1,
        "last_requeue_ts": float(now),
        "last_requeue_reason": reason,
        "last_requeue_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }
    _write_json(_requeue_state_path(r), st, indent=indent)


def _archive_and_clear_for_requeue(r: RunRef, *, tag: str) -> None:
    """
    Archive submit/history (if present) and remove them so the run can be resubmitted.
    """
    ts = time.strftime("%Y%m%d-%H%M%S")
    for src, name in (
        (r.submit_path, f"submit.{tag}.{ts}.json"),
        (r.history_path, f"history.{tag}.{ts}.json"),
    ):
        try:
            if src.exists():
                dst = r.run_dir / name
                if not dst.exists():
                    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                src.unlink(missing_ok=True)
        except Exception:
            # best-effort; keep going
            pass


def _classify_runs(
    runs: Iterable[RunRef],
    *,
    now: float,
    complete_if_output: bool,
    resubmit_stale: bool,
    stale_seconds: float,
    queue_ids: Optional[Set[str]],
    queue_empty: Optional[bool],
    requeue_missing_video: bool,
    missing_video_grace_s: float,
    max_requeues: int,
    requeue_cooldown_s: float,
) -> Tuple[List[RunRef], List[Tuple[RunRef, str]], List[RunRef], List[RunRef], List[RunRef], int, int, List[RunRef]]:
    """
    Returns (pending_submit, pending_history, done, stale_to_resubmit, missing_video_to_requeue, done_by_outputs_count, missing_video_count).
    pending_history entries include (runref, prompt_id).
    """
    pending_submit: List[RunRef] = []
    pending_history: List[Tuple[RunRef, str]] = []
    done: List[RunRef] = []
    stale_to_resubmit: List[RunRef] = []
    missing_video_to_requeue: List[RunRef] = []
    done_by_outputs = 0
    missing_video = 0
    done_by_outputs_runs: List[RunRef] = []

    for r in runs:
        has_video = _has_video_for_run(r.exp_dir, r.run_id)
        if r.history_path.exists():
            if requeue_missing_video and not has_video:
                missing_video += 1
                last_touch = _latest_mtime([r.history_path, r.submit_path])
                age = (now - float(last_touch)) if last_touch is not None else (missing_video_grace_s + 1.0)
                if age >= missing_video_grace_s and _can_requeue(
                    r, now=now, max_requeues=max_requeues, cooldown_s=requeue_cooldown_s
                ):
                    missing_video_to_requeue.append(r)
                    continue
            done.append(r)
            continue
        if complete_if_output and _has_outputs_for_run(r.exp_dir, r.run_id):
            # If we only have images (or partial outputs) but no mp4, treat as failed and requeue.
            if requeue_missing_video and not has_video:
                missing_video += 1
                last_touch = _latest_mtime([r.submit_path])
                age = (now - float(last_touch)) if last_touch is not None else (missing_video_grace_s + 1.0)
                if age >= missing_video_grace_s and _can_requeue(
                    r, now=now, max_requeues=max_requeues, cooldown_s=requeue_cooldown_s
                ):
                    missing_video_to_requeue.append(r)
                    continue
            done.append(r)
            done_by_outputs += 1
            done_by_outputs_runs.append(r)
            continue
        if r.submit_path.exists():
            pid = _read_prompt_id_from_submit(r.submit_path)
            if pid:
                # If server queue indicates this prompt_id still exists, keep waiting.
                if queue_ids is not None and pid in queue_ids:
                    pending_history.append((r, pid))
                    continue
                # If server is empty and this looks stale, resubmit to revive after reboot.
                if resubmit_stale and queue_empty is True:
                    try:
                        age = now - r.submit_path.stat().st_mtime
                    except Exception:
                        age = stale_seconds + 1.0
                    if age >= stale_seconds:
                        stale_to_resubmit.append(r)
                        continue
                # Default: still try polling history (may appear later even if queue doesn't list it).
                pending_history.append((r, pid))
                continue
        pending_submit.append(r)

    return (
        pending_submit,
        pending_history,
        done,
        stale_to_resubmit,
        missing_video_to_requeue,
        done_by_outputs,
        missing_video,
        done_by_outputs_runs,
    )


def _summarize(exp_dirs: List[Path], *, pending_submit: int, pending_hist: int, done: int, inflight: int) -> str:
    exp_part = f"{len(exp_dirs)} exp" if len(exp_dirs) != 1 else "1 exp"
    return f"{exp_part} | inflight={inflight} | pending_submit={pending_submit} | pending_history={pending_hist} | done={done}"


def watch(
    *,
    root_or_exp: Path,
    server: str,
    poll_s: float,
    max_inflight: int,
    indent: int,
    once: bool,
    submit_timeout_s: int,
    history_timeout_s: int,
    queue_timeout_s: int,
    stale_seconds: float,
    resubmit_stale: bool,
    complete_if_output: bool,
    requeue_missing_video: bool,
    missing_video_grace_s: float,
    max_requeues: int,
    requeue_cooldown_s: float,
    backfill_history: bool = True,
    backfill_min_age_s: float = 1800.0,
    backfill_extract_media_metadata: bool = True,
    backfill_max_per_loop: int = 8,
) -> int:
    server = server.rstrip("/")

    loop = 0
    wf_cache: Dict[str, Dict[str, Any]] = {}
    last_server_unreachable_ts: float = 0.0
    server_unreachable_cooldown_s: float = 60.0
    while True:
        loop += 1
        exp_dirs = _iter_experiment_dirs(root_or_exp)
        all_runs: List[RunRef] = []
        for ed in exp_dirs:
            all_runs.extend(_iter_runs(ed))

        now = time.time()
        queue_ids = _queue_prompt_ids(server, timeout_s=queue_timeout_s) if resubmit_stale else None
        queue_empty = _queue_is_empty(server, timeout_s=queue_timeout_s) if resubmit_stale else None

        # If we can't query /queue, ComfyUI may be restarting/unreachable. Log with a cooldown.
        if resubmit_stale and queue_empty is None and (now - last_server_unreachable_ts) >= server_unreachable_cooldown_s:
            inflight_snapshot: List[Dict[str, Any]] = []
            try:
                for r in all_runs:
                    if r.submit_path.exists() and not r.history_path.exists():
                        pid = _read_prompt_id_from_submit(r.submit_path)
                        if pid:
                            inflight_snapshot.append({"exp_id": r.exp_id, "run_id": r.run_id, "prompt_id": pid})
            except Exception:
                inflight_snapshot = []
            _append_crash_event(
                root_or_exp=root_or_exp,
                event={
                    "kind": "server_unreachable",
                    "server": server,
                    "detail": "Failed to query /queue (ComfyUI may be restarting or unreachable).",
                    "inflight": inflight_snapshot[:200],
                },
            )
            last_server_unreachable_ts = now

        (
            pending_submit,
            pending_history,
            done,
            stale_to_resubmit,
            missing_video_to_requeue,
            done_by_outputs,
            missing_video,
            done_by_outputs_runs,
        ) = _classify_runs(
            all_runs,
            now=now,
            complete_if_output=complete_if_output,
            resubmit_stale=resubmit_stale,
            stale_seconds=stale_seconds,
            queue_ids=queue_ids,
            queue_empty=queue_empty,
            requeue_missing_video=requeue_missing_video,
            missing_video_grace_s=missing_video_grace_s,
            max_requeues=max_requeues,
            requeue_cooldown_s=requeue_cooldown_s,
        )

        # Last-ditch: some runs may have outputs on disk but no history.json (e.g. ComfyUI restarted and /history is gone).
        # If enabled, write a minimal backfilled history.json and record a "something is broken" crash event.
        backfilled_now = 0
        if backfill_history and complete_if_output and done_by_outputs_runs and backfill_max_per_loop > 0:
            for r in done_by_outputs_runs:
                if backfilled_now >= int(backfill_max_per_loop):
                    break
                if r.history_path.exists():
                    continue
                media_paths = _find_media_files_for_run(r.exp_dir, r.run_id)
                if not media_paths:
                    continue
                pid = _read_prompt_id_from_submit(r.submit_path) if r.submit_path.exists() else None
                # Don't backfill active queue entries.
                if pid and queue_ids is not None and pid in queue_ids:
                    continue
                # Age gate: only backfill if submit is old enough (when present).
                if r.submit_path.exists():
                    try:
                        age = float(now - float(r.submit_path.stat().st_mtime))
                    except Exception:
                        age = float("inf")
                    if age < float(backfill_min_age_s):
                        continue
                dummy = _dummy_history_from_fs(
                    root_or_exp=root_or_exp,
                    r=r,
                    prompt_id=pid,
                    media_paths=media_paths,
                    include_media_metadata=bool(backfill_extract_media_metadata),
                )
                if not isinstance(dummy, dict) or not dummy:
                    continue
                _write_json(r.history_path, dummy, indent=indent)
                backfilled_now += 1

                wf_key = str(r.exp_dir)
                if wf_key not in wf_cache:
                    wf_cache[wf_key] = _workflow_info_for_exp(r.exp_dir)
                _append_crash_event(
                    root_or_exp=root_or_exp,
                    event={
                        "kind": "history_backfilled_last_ditch",
                        "detail": "Outputs existed but history.json was missing; wrote dummy history from filesystem outputs.",
                        "exp_id": r.exp_id,
                        "run_id": r.run_id,
                        **wf_cache.get(wf_key, {}),
                        "prompt_id": pid,
                        "media_count": len(media_paths),
                        "min_age_seconds": float(backfill_min_age_s),
                        "run_dir": str(r.run_dir),
                    },
                )

        # Missing-video runs are treated as failed and requeued (with retry limits).
        if missing_video_to_requeue:
            for r in missing_video_to_requeue:
                wf_key = str(r.exp_dir)
                if wf_key not in wf_cache:
                    wf_cache[wf_key] = _workflow_info_for_exp(r.exp_dir)
                _archive_and_clear_for_requeue(r, tag="missing_video")
                _mark_requeue_attempt(r, now=now, reason="missing_video", indent=indent)
                _append_crash_event(
                    root_or_exp=root_or_exp,
                    event={
                        "kind": "missing_video_requeue",
                        "exp_id": r.exp_id,
                        "run_id": r.run_id,
                        **wf_cache.get(wf_key, {}),
                        "reason": "missing_video",
                        "run_dir": str(r.run_dir),
                    },
                )
            pending_submit = list(missing_video_to_requeue) + pending_submit

        # Convert stale items into pending_submit (we will archive old submit.json and resubmit).
        if stale_to_resubmit:
            pending_submit = list(stale_to_resubmit) + pending_submit
            for r in stale_to_resubmit:
                wf_key = str(r.exp_dir)
                if wf_key not in wf_cache:
                    wf_cache[wf_key] = _workflow_info_for_exp(r.exp_dir)
                pid = _read_prompt_id_from_submit(r.submit_path) if r.submit_path.exists() else None
                _append_crash_event(
                    root_or_exp=root_or_exp,
                    event={
                        "kind": "stale_resubmit",
                        "exp_id": r.exp_id,
                        "run_id": r.run_id,
                        **wf_cache.get(wf_key, {}),
                        "prompt_id": pid,
                        "reason": "stale_submit_queue_empty",
                        "run_dir": str(r.run_dir),
                    },
                )

        inflight = len(pending_history)
        if loop == 1 or not once:
            extra = f" | done_by_outputs={done_by_outputs}" if done_by_outputs else ""
            stale = f" | stale_resubmit={len(stale_to_resubmit)}" if stale_to_resubmit else ""
            miss = f" | missing_video={missing_video}" if missing_video else ""
            missrq = f" | missing_video_requeue={len(missing_video_to_requeue)}" if missing_video_to_requeue else ""
            qstat = ""
            if queue_empty is True:
                qstat = " | queue=empty"
            elif queue_empty is False:
                qstat = " | queue=busy"
            print(
                _summarize(
                    exp_dirs,
                    pending_submit=len(pending_submit),
                    pending_hist=len(pending_history),
                    done=len(done),
                    inflight=inflight,
                )
                + extra
                + stale
                + miss
                + missrq
                + qstat
            )

        # 1) Submit up to max_inflight.
        if max_inflight < 0:
            max_inflight = 0
        budget = max(0, max_inflight - inflight)

        submitted_now = 0
        for r in pending_submit:
            if budget <= 0:
                break
            try:
                # If this is a stale requeue, archive the old submit.json so we preserve provenance.
                if resubmit_stale and r.submit_path.exists() and not r.history_path.exists():
                    try:
                        ts = time.strftime("%Y%m%d-%H%M%S")
                        archive = r.run_dir / f"submit.stale.{ts}.json"
                        # Don't overwrite if called twice in same second.
                        if not archive.exists():
                            archive.write_text(r.submit_path.read_text(encoding="utf-8"), encoding="utf-8")
                        r.submit_path.unlink(missing_ok=True)
                    except Exception:
                        # If we can't archive, still attempt to resubmit.
                        pass

                prompt_obj = _read_json(r.prompt_path)
                if not isinstance(prompt_obj, dict):
                    raise RuntimeError("prompt.json is not a JSON object")
                # Drop unconnected helper nodes that can cause "missing_node_type" even when unused.
                _prune_dead_nodes(prompt_obj)
                # Fix common portability issue: prompts authored on Windows may use backslashes in
                # model names (e.g. "WAN\\foo.gguf"), but nodes validate against "/"-separated lists.
                _normalize_prompt_paths_for_linux(prompt_obj)
                # Build payload: include extra_pnginfo.workflow so ComfyUI SaveImage nodes embed UI workflow in outputs.
                payload: Dict[str, Any] = {"prompt": prompt_obj, "client_id": _client_id(r.exp_id)}
                workflow_ui = _workflow_ui_for_run(r)
                if isinstance(workflow_ui, dict) and workflow_ui:
                    payload["extra_data"] = {"extra_pnginfo": {"workflow": workflow_ui}}
                t0 = time.time()
                submit = _http_json(
                    "POST",
                    f"{server}/prompt",
                    payload,
                    timeout_s=submit_timeout_s,
                )
                t1 = time.time()
                pid = submit.get("prompt_id")
                if not isinstance(pid, str) or not pid.strip():
                    raise RuntimeError("submit response missing prompt_id")
                _write_json(r.submit_path, submit, indent=indent)

                wf_key = str(r.exp_dir)
                if wf_key not in wf_cache:
                    wf_cache[wf_key] = _workflow_info_for_exp(r.exp_dir)

                # Record node_errors immediately (some workflows fail fast at validation).
                node_errors = submit.get("node_errors") if isinstance(submit, dict) else None
                if isinstance(node_errors, dict) and node_errors:
                    _append_crash_event(
                        root_or_exp=root_or_exp,
                        event={
                            "kind": "submit_node_errors",
                            "exp_id": r.exp_id,
                            "run_id": r.run_id,
                            **wf_cache.get(wf_key, {}),
                            "prompt_id": pid,
                            "node_errors": node_errors,
                            "run_dir": str(r.run_dir),
                        },
                    )

                _merge_json_dict(
                    _metrics_path(r.run_dir),
                    {
                        "schema": 1,
                        "prompt_id": pid,
                        "submit_started_ts": float(t0),
                        "submitted_ts": float(t1),
                        "submitted_at": _utc_iso(float(t1)),
                        "submit_http_sec": float(max(0.0, t1 - t0)),
                    },
                    indent=indent,
                )
                budget -= 1
                submitted_now += 1
            except Exception as e:
                # Don't die on one bad run; keep watching others.
                detail = ""
                # If this is an HTTPError, include the response body for actionable diagnosis.
                if isinstance(e, urllib.error.HTTPError):
                    try:
                        body = e.read().decode("utf-8", "replace")
                        if body.strip():
                            detail = f" | body={body.strip()[:1500]}"
                    except Exception:
                        pass
                print(f"[watch-queue] submit failed: {r.exp_id}/{r.run_id}: {e}{detail}")
                wf_key = str(r.exp_dir)
                if wf_key not in wf_cache:
                    wf_cache[wf_key] = _workflow_info_for_exp(r.exp_dir)
                _append_crash_event(
                    root_or_exp=root_or_exp,
                    event={
                        "kind": "submit_failed",
                        "exp_id": r.exp_id,
                        "run_id": r.run_id,
                        **wf_cache.get(wf_key, {}),
                        "error": str(e) + (detail or ""),
                        "run_dir": str(r.run_dir),
                    },
                )

        # Refresh after submissions, so we can collect histories this loop.
        if submitted_now:
            now = time.time()
            queue_ids = _queue_prompt_ids(server, timeout_s=queue_timeout_s) if resubmit_stale else None
            queue_empty = _queue_is_empty(server, timeout_s=queue_timeout_s) if resubmit_stale else None
            (
                pending_submit,
                pending_history,
                done,
                stale_to_resubmit,
                missing_video_to_requeue,
                _done_by_outputs,
                _missing_video,
                _done_by_outputs_runs,
            ) = _classify_runs(
                all_runs,
                now=now,
                complete_if_output=complete_if_output,
                resubmit_stale=resubmit_stale,
                stale_seconds=stale_seconds,
                queue_ids=queue_ids,
                queue_empty=queue_empty,
                requeue_missing_video=requeue_missing_video,
                missing_video_grace_s=missing_video_grace_s,
                max_requeues=max_requeues,
                requeue_cooldown_s=requeue_cooldown_s,
            )

            if missing_video_to_requeue:
                for r in missing_video_to_requeue:
                    _archive_and_clear_for_requeue(r, tag="missing_video")
                    _mark_requeue_attempt(r, now=now, reason="missing_video", indent=indent)
                pending_submit = list(missing_video_to_requeue) + pending_submit

            if stale_to_resubmit:
                pending_submit = list(stale_to_resubmit) + pending_submit

        # 2) Poll history for all submitted-without-history runs.
        collected_now = 0
        for r, pid in pending_history:
            try:
                hist = _http_json("GET", f"{server}/history/{pid}", None, timeout_s=history_timeout_s)
                if hist:
                    collected_ts = time.time()
                    _write_json(r.history_path, hist, indent=indent)

                    # Track non-success statuses as workflow failures.
                    wf_key = str(r.exp_dir)
                    if wf_key not in wf_cache:
                        wf_cache[wf_key] = _workflow_info_for_exp(r.exp_dir)
                    record = hist.get(pid) if isinstance(hist, dict) and pid in hist else hist
                    if isinstance(record, dict):
                        st = record.get("status") if isinstance(record.get("status"), dict) else {}
                        status_str = st.get("status_str") if isinstance(st.get("status_str"), str) else None
                        completed = st.get("completed") if isinstance(st.get("completed"), bool) else None
                        if status_str and status_str != "success":
                            _append_crash_event(
                                root_or_exp=root_or_exp,
                                event={
                                    "kind": "run_failed",
                                    "exp_id": r.exp_id,
                                    "run_id": r.run_id,
                                    **wf_cache.get(wf_key, {}),
                                    "prompt_id": pid,
                                    "status_str": status_str,
                                    "completed": completed,
                                    "run_dir": str(r.run_dir),
                                },
                            )
                    mp = _metrics_path(r.run_dir)
                    m = _read_json_dict(mp) if mp.exists() else {}
                    submitted_ts = m.get("submitted_ts")
                    source = "metrics.json"
                    if not isinstance(submitted_ts, (int, float)):
                        try:
                            submitted_ts = float(r.submit_path.stat().st_mtime)
                            source = "submit.json_mtime"
                        except Exception:
                            submitted_ts = float(collected_ts)
                            source = "unknown"

                    # Prefer WebSocket-derived execution timing (written by ws_event_tap.py) when available.
                    # This is closer to "actual generation time" than submit->history polling latency.
                    active_started_ts = m.get("active_started_ts")
                    exec_started_ts = m.get("exec_started_ts")
                    exec_ended_ts = m.get("exec_ended_ts")
                    exec_ended_src = m.get("exec_ended_ts_source") if isinstance(m.get("exec_ended_ts_source"), str) else None

                    patch_extra: Dict[str, Any] = {}
                    active_runtime_sec: Optional[float] = None
                    active_runtime_src: Optional[str] = None
                    wall_runtime_sec: Optional[float] = None
                    wall_runtime_src: Optional[str] = None

                    # If we have active start, compute active runtime.
                    if isinstance(active_started_ts, (int, float)):
                        if isinstance(exec_ended_ts, (int, float)):
                            active_runtime_sec = float(max(0.0, float(exec_ended_ts) - float(active_started_ts)))
                            active_runtime_src = exec_ended_src or "ws"
                        else:
                            # WS end missing: record a clearly-labeled fallback end marker using history collection time.
                            if not isinstance(m.get("exec_ended_ts_fallback"), (int, float)):
                                patch_extra["exec_ended_ts_fallback"] = float(collected_ts)
                                patch_extra["exec_ended_at_fallback"] = _utc_iso(float(collected_ts))
                                patch_extra["exec_ended_ts_fallback_source"] = "history_collected_ts"
                            active_runtime_sec = float(max(0.0, float(collected_ts) - float(active_started_ts)))
                            active_runtime_src = "fallback_history_collected"

                    # If we have exec start, compute wall runtime.
                    if isinstance(exec_started_ts, (int, float)):
                        if isinstance(exec_ended_ts, (int, float)):
                            wall_runtime_sec = float(max(0.0, float(exec_ended_ts) - float(exec_started_ts)))
                            wall_runtime_src = exec_ended_src or "ws"
                        else:
                            if not isinstance(m.get("exec_ended_ts_fallback"), (int, float)):
                                patch_extra["exec_ended_ts_fallback"] = float(collected_ts)
                                patch_extra["exec_ended_at_fallback"] = _utc_iso(float(collected_ts))
                                patch_extra["exec_ended_ts_fallback_source"] = "history_collected_ts"
                            wall_runtime_sec = float(max(0.0, float(collected_ts) - float(exec_started_ts)))
                            wall_runtime_src = "fallback_history_collected"

                    # Back-compat: generation_time_sec remains the main field used by other tooling.
                    # Prefer active runtime when present; otherwise fall back to submit->history_collected latency.
                    if isinstance(active_runtime_sec, (int, float)):
                        gen_sec = float(active_runtime_sec)
                        gen_src = active_runtime_src or "ws"
                    else:
                        gen_sec = float(max(0.0, float(collected_ts) - float(submitted_ts)))
                        gen_src = source

                    _merge_json_dict(
                        mp,
                        {
                            "prompt_id": pid,
                            "history_collected_ts": float(collected_ts),
                            "history_collected_at": _utc_iso(float(collected_ts)),
                            "generation_time_sec": gen_sec,
                            # In watch mode, we don't know exact poll-start time for this run.
                            "wait_history_sec": gen_sec,
                            "generation_time_sec_source": gen_src,
                            "wait_history_sec_source": gen_src,
                            **(
                                {"active_runtime_sec": active_runtime_sec, "active_runtime_sec_source": active_runtime_src}
                                if isinstance(active_runtime_sec, (int, float))
                                else {}
                            ),
                            **(
                                {"wall_runtime_sec": wall_runtime_sec, "wall_runtime_sec_source": wall_runtime_src}
                                if isinstance(wall_runtime_sec, (int, float))
                                else {}
                            ),
                            **patch_extra,
                        },
                        indent=indent,
                    )
                    collected_now += 1
                else:
                    # Last-ditch: prompt is no longer in queue and outputs exist, but /history is empty.
                    # This often means ComfyUI restarted and forgot the prompt history.
                    if (
                        backfill_history
                        and complete_if_output
                        and (queue_ids is None or pid not in queue_ids)
                        and _has_outputs_for_run(r.exp_dir, r.run_id)
                    ):
                        # Age gate: avoid masking short-lived /history delays.
                        if r.submit_path.exists():
                            try:
                                age = float(time.time() - float(r.submit_path.stat().st_mtime))
                            except Exception:
                                age = float("inf")
                            if age < float(backfill_min_age_s):
                                continue
                        media_paths = _find_media_files_for_run(r.exp_dir, r.run_id)
                        if not media_paths:
                            continue
                        dummy = _dummy_history_from_fs(
                            root_or_exp=root_or_exp,
                            r=r,
                            prompt_id=pid,
                            media_paths=media_paths,
                            include_media_metadata=bool(backfill_extract_media_metadata),
                        )
                        if not isinstance(dummy, dict) or not dummy:
                            continue
                        _write_json(r.history_path, dummy, indent=indent)
                        collected_now += 1

                        wf_key = str(r.exp_dir)
                        if wf_key not in wf_cache:
                            wf_cache[wf_key] = _workflow_info_for_exp(r.exp_dir)
                        _append_crash_event(
                            root_or_exp=root_or_exp,
                            event={
                                "kind": "history_backfilled_last_ditch",
                                "detail": "ComfyUI /history returned empty for a submitted run; outputs exist, so wrote dummy history from filesystem outputs.",
                                "exp_id": r.exp_id,
                                "run_id": r.run_id,
                                **wf_cache.get(wf_key, {}),
                                "prompt_id": pid,
                                "media_count": len(media_paths),
                                "min_age_seconds": float(backfill_min_age_s),
                                "run_dir": str(r.run_dir),
                            },
                        )
            except Exception:
                # ignore transient errors
                pass

        if once:
            return 0

        # Sleep with a small nudge: if we just did work, tighten; otherwise use poll_s.
        if submitted_now or collected_now or backfilled_now:
            time.sleep(min(0.5, poll_s))
        else:
            time.sleep(poll_s)


def main() -> int:
    ap = argparse.ArgumentParser(description="Watch ComfyUI experiment runs and manage queue/history asynchronously")
    ap.add_argument(
        "root_or_exp",
        nargs="?",
        default="output/output/experiments",
        help="Experiment directory (with manifest.json) OR root directory containing experiments (default: output/output/experiments)",
    )
    ap.add_argument(
        "--limit-experiments",
        type=int,
        default=0,
        help="Only watch the N newest experiments (0=all). This helps prioritize new work.",
    )
    ap.add_argument("--server", default="http://127.0.0.1:8188", help="ComfyUI server base URL")
    ap.add_argument("--poll", type=float, default=2.0, help="Poll interval seconds")
    ap.add_argument(
        "--max-inflight",
        type=int,
        default=16,
        help="Max runs with submit.json but no history.json across watched experiments (default: 16)",
    )
    ap.add_argument(
        "--no-backfill-history",
        action="store_true",
        help="Disable last-ditch backfill: do not write dummy history.json from filesystem outputs when /history is missing/empty.",
    )
    ap.add_argument(
        "--backfill-min-age-seconds",
        type=float,
        default=1800.0,
        help="Only backfill runs whose submit.json is older than this many seconds (default: 1800).",
    )
    ap.add_argument(
        "--no-backfill-extract-media-metadata",
        action="store_true",
        help="Disable extracting embedded prompt/workflow metadata from PNG/MP4 when backfilling history.json.",
    )
    ap.add_argument(
        "--backfill-max-per-loop",
        type=int,
        default=8,
        help="Max dummy histories to write per watch loop (default: 8).",
    )
    ap.add_argument("--indent", type=int, default=2)
    ap.add_argument("--once", action="store_true", help="Run one iteration and exit (useful for status checks)")
    ap.add_argument("--submit-timeout", type=int, default=30, help="HTTP timeout (seconds) for POST /prompt")
    ap.add_argument("--history-timeout", type=int, default=10, help="HTTP timeout (seconds) for GET /history/<id>")
    ap.add_argument("--queue-timeout", type=int, default=5, help="HTTP timeout (seconds) for GET /queue")
    ap.add_argument(
        "--stale-seconds",
        type=float,
        default=300.0,
        help="If queue is empty and submit.json is older than this, resubmit the run (default: 300s).",
    )
    ap.add_argument(
        "--no-resubmit-stale",
        action="store_true",
        help="Disable stale resubmission (default: enabled).",
    )
    ap.add_argument(
        "--no-complete-if-output",
        action="store_true",
        help="Disable treating runs as complete when output files exist (default: enabled).",
    )
    ap.add_argument(
        "--no-requeue-missing-video",
        action="store_true",
        help="Disable requeuing runs that are missing expected mp4 outputs (default: enabled).",
    )
    ap.add_argument(
        "--missing-video-grace-seconds",
        type=float,
        default=180.0,
        help="Grace period before requeuing a run missing mp4 outputs (default: 180s).",
    )
    ap.add_argument(
        "--max-requeues",
        type=int,
        default=3,
        help="Max requeue attempts per run for missing outputs before giving up (default: 3).",
    )
    ap.add_argument(
        "--requeue-cooldown-seconds",
        type=float,
        default=300.0,
        help="Minimum time between requeue attempts for the same run (default: 300s).",
    )
    args = ap.parse_args()

    # If limiting, wrap root_or_exp to a temporary view by selecting newest dirs.
    root_or_exp = Path(args.root_or_exp)
    limit_exps = int(args.limit_experiments)
    if limit_exps > 0 and not (root_or_exp / "manifest.json").exists():
        # Convert a root directory into a smaller, newest-first list.
        # We do it by monkey-patching the directory list in the simplest way:
        # store the selected set in an env-like global for this process.
        selected = set(_iter_experiment_dirs(root_or_exp)[:limit_exps])
        orig_iter = _iter_experiment_dirs

        def _iter_experiment_dirs_limited(root_or_exp_inner: Path) -> List[Path]:
            xs = orig_iter(root_or_exp_inner)
            return [x for x in xs if x in selected]

        globals()["_iter_experiment_dirs"] = _iter_experiment_dirs_limited  # type: ignore[assignment]

    return watch(
        root_or_exp=root_or_exp,
        server=args.server,
        poll_s=float(args.poll),
        max_inflight=int(args.max_inflight),
        indent=int(args.indent),
        once=bool(args.once),
        submit_timeout_s=int(args.submit_timeout),
        history_timeout_s=int(args.history_timeout),
        queue_timeout_s=int(args.queue_timeout),
        stale_seconds=float(args.stale_seconds),
        resubmit_stale=not bool(args.no_resubmit_stale),
        complete_if_output=not bool(args.no_complete_if_output),
        requeue_missing_video=not bool(args.no_requeue_missing_video),
        missing_video_grace_s=float(args.missing_video_grace_seconds),
        max_requeues=int(args.max_requeues),
        requeue_cooldown_s=float(args.requeue_cooldown_seconds),
        backfill_history=not bool(args.no_backfill_history),
        backfill_min_age_s=float(args.backfill_min_age_seconds),
        backfill_extract_media_metadata=not bool(args.no_backfill_extract_media_metadata),
        backfill_max_per_loop=int(args.backfill_max_per_loop),
    )


if __name__ == "__main__":
    raise SystemExit(main())

