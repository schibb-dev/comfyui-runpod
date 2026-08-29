#!/usr/bin/env python3
"""
Assemble a read-only shape-factory map: pools, shapes, pipelines, jobs, queue, hourly state.

Used by GET /api/shape-factory/map in experiments_ui_server.py.
"""

from __future__ import annotations

import datetime as _dt
import itertools
import json
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from http_retry import http_json_with_retry

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


def resolve_shape_factory_data_root(*, repo_root: Path) -> Path:
    env = os.environ.get("SHAPE_FACTORY_DATA_ROOT", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p.resolve()
    return (repo_root / ".data").resolve()


def _utc_now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).replace(microsecond=0).isoformat()


def _load_yaml(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        return yaml.safe_load(text)
    return _load_yaml_minimal(text)


def _load_yaml_minimal(text: str) -> Dict[str, Any]:
    """Tiny fallback when PyYAML is unavailable — enough for our flat shape/pipeline files."""
    out: Dict[str, Any] = {}
    stack: List[Tuple[int, Dict[str, Any]]] = [(-1, out)]
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if line.endswith(":") and ":" == line.find(":"):
            key = line[:-1].strip()
            child: Dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip().strip("'\"")
            if val in {"true", "false"}:
                parent[key] = val == "true"
            elif val.isdigit():
                parent[key] = int(val)
            else:
                parent[key] = val
    return out


def _basename(path: str) -> str:
    return Path(str(path or "")).name


def _normalize_rel_posix(s: str) -> str:
    return str(s or "").replace("\\", "/").strip().lstrip("/")


# Host index paths often use comfyui-runpod-data while the API serves from output_root.
# Prefer flat bind layout: .../output/og/... -> og/...
_OUTPUT_OG_REL_RE = re.compile(r"/output/(?:output/)?(?:output/)?(og/.+)$", re.IGNORECASE)


def _relpath_guess_from_abs(abs_path: str) -> Optional[str]:
    norm = str(abs_path or "").replace("\\", "/")
    m = _OUTPUT_OG_REL_RE.search(norm)
    if m:
        return _normalize_rel_posix(m.group(1))
    m = re.search(r"/output/(og/.+)$", norm, re.IGNORECASE)
    if m:
        return _normalize_rel_posix(m.group(1))
    # Comfy input uploads (source stills for i2v). Host job files store absolute
    # paths like /home/.../comfyui-runpod-data/input/<name> or <ws>/input/<name>;
    # the API serves them from workspace_root/input via /files/input/<name>.
    m = re.search(r"/input/([^/].*)$", norm, re.IGNORECASE)
    if m:
        return _normalize_rel_posix(f"input/{m.group(1)}")
    return None


def resolve_output_relpath(
    abs_path: str,
    output_root: Path,
    *,
    wip_root: Optional[Path] = None,
    workspace_root: Optional[Path] = None,
) -> Optional[str]:
    """Map an on-disk artifact path to a relpath under output_root or workspace_root for /files/ URLs."""
    if not abs_path:
        return None
    p = Path(abs_path).expanduser()
    try:
        resolved = p.resolve()
    except Exception:
        resolved = p
    roots: List[Path] = [output_root.resolve()]
    try:
        roots.append((output_root / "output").resolve())
    except Exception:
        pass
    for root in roots:
        try:
            return _normalize_rel_posix(str(resolved.relative_to(root)))
        except ValueError:
            continue
    if workspace_root is not None:
        try:
            wr = workspace_root.resolve()
            return _normalize_rel_posix(str(resolved.relative_to(wr)))
        except ValueError:
            pass
    if wip_root is not None:
        try:
            wr = wip_root.resolve().relative_to(output_root.resolve()).as_posix().replace("\\", "/")
            sub = resolved.relative_to(wip_root.resolve()).as_posix().replace("\\", "/")
            if sub in ("", "."):
                return _normalize_rel_posix(wr)
            return _normalize_rel_posix(f"{wr}/{sub}")
        except ValueError:
            pass
    return _relpath_guess_from_abs(abs_path)


def abs_path_to_media(
    output_root: Path,
    abs_path: str,
    *,
    url_for: Optional[Callable[[str], str]] = None,
    wip_root: Optional[Path] = None,
    workspace_root: Optional[Path] = None,
    file_exists: Optional[Callable[[str], bool]] = None,
) -> Dict[str, Any]:
    """Resolve absolute artifact path to relpath + /files/ URL when under output or workspace."""
    out: Dict[str, Any] = {"path": abs_path, "basename": _basename(abs_path)}
    if not abs_path:
        return out
    rel = resolve_output_relpath(
        abs_path, output_root, wip_root=wip_root, workspace_root=workspace_root
    )
    if rel and (file_exists is None or file_exists(rel)):
        out["relpath"] = rel
        if url_for:
            out["url"] = url_for(rel)
        else:
            out["url"] = "/files/" + urllib.parse.quote(rel)
    return out


def _companion_png_for_member(member: Dict[str, Any], video_path: str) -> str:
    png = str(member.get("companion_png") or "").strip()
    if png:
        return png
    if video_path.lower().endswith(".mp4"):
        return video_path[:-4] + ".png"
    return ""


def _path_media_row(
    path: str,
    *,
    output_root: Path,
    url_for: Optional[Callable[[str], str]] = None,
    wip_root: Optional[Path] = None,
    workspace_root: Optional[Path] = None,
    file_exists: Optional[Callable[[str], bool]] = None,
    companion_png: str = "",
) -> Dict[str, Any]:
    path = str(path or "")
    media_kwargs = {
        "url_for": url_for,
        "wip_root": wip_root,
        "workspace_root": workspace_root,
        "file_exists": file_exists,
    }
    row: Dict[str, Any] = {"path": path, "basename": _basename(path)}
    media = abs_path_to_media(output_root, path, **media_kwargs)
    row.update({k: media[k] for k in ("relpath", "url") if k in media})
    png = companion_png or _companion_png_for_member({}, path)
    if png:
        thumb = abs_path_to_media(output_root, png, **media_kwargs)
        if thumb.get("url"):
            row["thumb_url"] = thumb["url"]
        if thumb.get("relpath"):
            row["thumb_relpath"] = thumb["relpath"]
    elif not row.get("thumb_url"):
        # Still / image bindings: the asset itself is the thumb.
        hint = str(row.get("relpath") or path).lower()
        if hint.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")) and row.get("url"):
            row["thumb_url"] = row["url"]
            if row.get("relpath"):
                row["thumb_relpath"] = row["relpath"]
    return row


def _member_preview(
    member: Dict[str, Any],
    *,
    output_root: Path,
    url_for: Optional[Callable[[str], str]] = None,
    wip_root: Optional[Path] = None,
    workspace_root: Optional[Path] = None,
    file_exists: Optional[Callable[[str], bool]] = None,
) -> Dict[str, Any]:
    path = str(member.get("path") or "")
    row: Dict[str, Any] = {
        "basename": _basename(path),
        "source": member.get("source"),
        "kind": member.get("kind"),
        "added_at": member.get("added_at"),
    }
    if member.get("job_key"):
        row["job_key"] = member.get("job_key")
    row.update(
        _path_media_row(
            path,
            output_root=output_root,
            url_for=url_for,
            wip_root=wip_root,
            workspace_root=workspace_root,
            file_exists=file_exists,
            companion_png=str(member.get("companion_png") or ""),
        )
    )
    return row


def _seed_source_media_ref(rel: str, *, url_for: Optional[Callable[[str], str]]) -> Dict[str, Any]:
    url = url_for(rel) if url_for else "/files/" + urllib.parse.quote(_normalize_rel_posix(rel))
    return {
        "relpath": rel,
        "url": url,
        "basename": _basename(rel),
        "source_kind": "still",
        "inferred": True,
    }


class _SeedSourceResolver:
    """
    Attach recovered source stills to seeded (job-less) deposit members.

    Fast path: a persisted ``factory_seed_sources.json`` lookup. Slow path
    (bounded per request): read the output's embedded prompt for its LoadImage,
    and cache the discovery so repeat views + heuristics reuse it.
    """

    def __init__(
        self,
        *,
        output_root: Path,
        workspace_root: Optional[Path],
        url_for: Optional[Callable[[str], str]],
        file_exists: Optional[Callable[[str], bool]],
        seed_sources_path: Optional[Path],
        infer_budget: int = 200,
    ) -> None:
        self.output_root = output_root.resolve()
        self.workspace_root = workspace_root
        self.url_for = url_for
        self.file_exists = file_exists
        self.seed_sources_path = seed_sources_path
        self.infer_budget = int(infer_budget)
        self._ffprobe = shutil.which("ffprobe")
        self.discovered: Dict[str, Any] = {}
        self._table: Dict[str, Any] = {}
        self._mod = None
        try:
            import shape_factory_seed_sources as mod  # type: ignore

            self._mod = mod
            if seed_sources_path is not None:
                self._table = dict(mod.load_seed_sources(seed_sources_path))
        except Exception:
            self._mod = None

    def resolve(self, member_row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if self._mod is None:
            return None
        rel = _normalize_rel_posix(str(member_row.get("relpath") or ""))
        if not rel or Path(rel).suffix.lower() != ".mp4":
            return None
        rec = self._table.get(rel)
        if rec is not None:
            src = rec.get("source_still_relpath")
            if src and (self.file_exists is None or self.file_exists(src)):
                return _seed_source_media_ref(src, url_for=self.url_for)
            return None
        if self.infer_budget <= 0:
            return None
        media_abs = self._mod._resolve_output_abs(rel, self.output_root)
        if media_abs is None:
            return None
        self.infer_budget -= 1
        now = self._mod._utc_now()
        info = self._mod.infer_source_still(media_abs, ffprobe=self._ffprobe)
        if not info:
            self._table[rel] = self.discovered[rel] = {"source_still_relpath": None, "updated_at": now}
            return None
        src_rel = self._mod.source_still_relpath(info["source_basename"])
        if src_rel and (self.file_exists is None or self.file_exists(src_rel)):
            self._table[rel] = self.discovered[rel] = {
                "source_still_relpath": src_rel,
                "source_basename": info["source_basename"],
                "evidence": info["evidence"],
                "updated_at": now,
            }
            return _seed_source_media_ref(src_rel, url_for=self.url_for)
        self._table[rel] = self.discovered[rel] = {
            "source_still_relpath": None,
            "source_basename": info["source_basename"],
            "missing_input": True,
            "updated_at": now,
        }
        return None

    def flush(self) -> None:
        if self._mod is None or self.seed_sources_path is None or not self.discovered:
            return
        try:
            self._mod.save_seed_sources(self.seed_sources_path, self._table)
        except Exception:
            pass


def _load_shape_doc(shape_path: Path) -> Dict[str, Any]:
    if not shape_path.is_file():
        return {}
    doc = _load_yaml(shape_path)
    return doc if isinstance(doc, dict) else {}


def _shape_summary(shape_path: Path, doc: Dict[str, Any]) -> Dict[str, Any]:
    requires = doc.get("requires") if isinstance(doc.get("requires"), list) else []
    slots: List[Dict[str, Any]] = []
    for req in requires:
        if not isinstance(req, dict):
            continue
        slot = req.get("slot")
        if not isinstance(slot, str):
            continue
        slots.append(
            {
                "slot": slot,
                "role": req.get("role"),
                "media": req.get("media"),
                "optional": bool(req.get("optional")),
            }
        )
    deposits = doc.get("deposits") if isinstance(doc.get("deposits"), dict) else {}
    deposit_targets: List[Dict[str, Any]] = []
    for slot, spec in deposits.items():
        if not isinstance(spec, dict):
            continue
        to_pool = spec.get("to_pool")
        if isinstance(to_pool, str):
            deposit_targets.append({"slot": slot, "to_pool": to_pool})
    return {
        "shape_path": str(shape_path),
        "shape_id": doc.get("shape_id"),
        "family_slug": doc.get("family_slug"),
        "graph_hash": doc.get("graph_hash"),
        "template": doc.get("template"),
        "primary_input": doc.get("primary_input"),
        "input_profile": doc.get("input_profile"),
        "chain_role": doc.get("chain_role"),
        "io_class": doc.get("io_class")
        or (
            {"still_prompt": "I2V", "video_prompt": "V2V", "video_identity_still_prompt": "VI2V"}.get(
                str(doc.get("input_profile") or "")
            )
        ),
        "requires": slots,
        "deposits": deposit_targets,
    }


def resolve_existing_path(
    path: str,
    *,
    output_root: Path,
    data_root: Path,
    workspace_root: Optional[Path] = None,
) -> Path:
    """Return the first filesystem path candidate that exists as a file."""
    candidates: List[str] = []
    seen: Set[str] = set()

    def add(p: str) -> None:
        n = str(p or "").replace("\\", "/").strip()
        if n and n not in seen:
            seen.add(n)
            candidates.append(n)

    for cand in _runtime_path_candidates(path, output_root=output_root, data_root=data_root):
        add(cand)
    if workspace_root is not None:
        ws = str(workspace_root.resolve()).replace("\\", "/").rstrip("/")
        s = str(path or "").replace("\\", "/")
        if s.startswith("/workspace/"):
            add(ws + s[len("/workspace") :])
        for host_prefix, suffix in (
            ("/home/yuji/comfyui-runpod-data/comfyui_user", "/comfyui_user"),
            ("/home/yuji/src/comfyui-runpod/workspace", ""),
        ):
            if s.startswith(host_prefix):
                add(ws + suffix + s[len(host_prefix) :])
    add(str(path or ""))
    # Accidental Windows/browser re-download names: also try without `` (1)``.
    try:
        from input_still_catalog import strip_download_copy_suffix  # type: ignore

        for cand in list(candidates):
            canon = strip_download_copy_suffix(cand)
            if canon and canon != cand:
                add(canon)
    except Exception:
        pass
    for cand in candidates:
        p = Path(cand).expanduser()
        if p.is_file():
            return p.resolve()
    raise FileNotFoundError(str(path or ""))


def _runtime_path_candidates(
    path_or_glob: str,
    *,
    output_root: Path,
    data_root: Optional[Path] = None,
) -> List[str]:
    s = str(path_or_glob or "").replace("\\", "/").strip()
    if not s:
        return []
    out: List[str] = []
    seen: Set[str] = set()

    def add(p: str) -> None:
        n = p.replace("\\", "/").strip()
        if n and n not in seen:
            seen.add(n)
            out.append(n)

    add(s)
    out_base = str(output_root.resolve()).replace("\\", "/").rstrip("/")
    for host_out in (
        "/home/yuji/comfyui-runpod-data/output",
        "/home/yuji/src/comfyui-runpod/workspace/output",
    ):
        if s.startswith(host_out):
            add(out_base + s[len(host_out) :])
    if s.startswith("/workspace/output/"):
        try:
            from output_path_lib import flatten_output_prefix  # type: ignore

            rel = flatten_output_prefix(s[len("/workspace/output/") :])
            if rel:
                add(f"{out_base}/{rel.lstrip('/')}")
        except Exception:
            add(f"{out_base}/{s[len('/workspace/output/') :].lstrip('/')}")
    if data_root is not None:
        data_base = str(data_root.resolve()).replace("\\", "/").rstrip("/")
        for host_data in (
            "/home/yuji/src/comfyui-runpod/.data",
            "/home/yuji/comfyui-runpod-data/.data",
        ):
            if s.startswith(host_data):
                add(data_base + s[len(host_data) :])
        if s.startswith("/workspace/.data/"):
            add(data_base + s[len("/workspace/.data") :])
        # Recover paths corrupted by hostify when scripts lived under /workspace/ws_scripts
        # (parents[2] → "/", so /workspace/.data/... became /.data/...).
        if s.startswith("/.data/") or s == "/.data":
            add(data_base + s[len("/.data") :] if s.startswith("/.data") else data_base)
    if s.startswith("/workspace/comfyui_user/"):
        rel = s[len("/workspace/comfyui_user") :]
        for host_user in (
            "/home/yuji/comfyui-runpod-data/comfyui_user",
            "/home/yuji/src/comfyui-runpod/workspace/comfyui_user",
        ):
            add(host_user + rel)

    # Input stills: Docker mounts COMFYUI_BIND_INPUT_DIR at both /workspace/input and
    # /ComfyUI/input. Host jobs often store the empty checkout path
    # (.../src/comfyui-runpod/workspace/input/<file>) while the real files live under
    # the bind dir (.../comfyui-runpod-data/input/<file>).
    bind_input = (
        os.environ.get("COMFYUI_BIND_INPUT_DIR", "").strip().replace("\\", "/").rstrip("/")
        or "/home/yuji/comfyui-runpod-data/input"
    )
    input_roots = (
        bind_input,
        "/home/yuji/comfyui-runpod-data/input",
        "/home/yuji/src/comfyui-runpod/workspace/input",
        "/workspace/input",
    )
    input_rel: Optional[str] = None
    for root in input_roots:
        prefix = root.rstrip("/") + "/"
        if s.startswith(prefix):
            input_rel = s[len(prefix) :]
            break
        if s == root.rstrip("/"):
            input_rel = ""
            break
    if input_rel is not None:
        for root in input_roots:
            add(root.rstrip("/") + (f"/{input_rel}" if input_rel else ""))
        # Bare basename under each root (covers "input/<name>" style leftovers).
        bn = Path(input_rel).name if input_rel else ""
        if bn:
            for root in input_roots:
                add(f"{root.rstrip('/')}/{bn}")
    return out


def _resolve_glob_paths(
    spec: Dict[str, Any],
    *,
    output_root: Path,
    data_root: Optional[Path] = None,
) -> List[Path]:
    pattern = str(spec.get("glob") or "").strip()
    if not pattern:
        return []
    try:
        from shape_factory import resolve_glob  # type: ignore
    except Exception:
        resolve_glob = None  # type: ignore

    for candidate in _runtime_path_candidates(pattern, output_root=output_root, data_root=data_root):
        attempt = dict(spec)
        attempt["glob"] = candidate
        paths: List[Path] = []
        if resolve_glob is not None:
            try:
                paths = resolve_glob(attempt)
            except Exception:
                paths = []
        if not paths:
            limit = spec.get("limit")
            glob_path = Path(candidate).expanduser()
            if "**" in candidate:
                root_str, _, rest = candidate.partition("**")
                root = Path(root_str.rstrip("/"))
                rest = rest.lstrip("/")
                paths = [p for p in root.rglob(rest) if p.is_file()]
            else:
                paths = [p for p in glob_path.parent.glob(glob_path.name) if p.is_file()]
            paths = sorted({p.resolve() for p in paths})
            if isinstance(limit, int) and limit > 0:
                paths = paths[:limit]
        if paths:
            return paths
    return []


def _resolve_dir_paths(
    spec: Dict[str, Any],
    *,
    output_root: Path,
    data_root: Optional[Path] = None,
) -> List[Path]:
    raw_dir = str(spec.get("dir") or "").strip()
    if not raw_dir:
        return []
    exts = {str(e).lower() for e in (spec.get("ext") or [".json"])}
    limit = spec.get("limit")
    for candidate in _runtime_path_candidates(raw_dir, output_root=output_root, data_root=data_root):
        root = Path(candidate).expanduser()
        if not root.is_dir():
            continue
        paths = sorted(
            p.resolve()
            for p in root.iterdir()
            if p.is_file() and p.suffix.lower() in exts
        )
        if isinstance(limit, int) and limit > 0:
            paths = paths[:limit]
        if paths:
            return paths
    return []


def _pool_slot_paths(
    pool_def: Dict[str, Any],
    *,
    output_root: Path,
    data_root: Optional[Path] = None,
) -> List[Path]:
    seen: Set[str] = set()
    out: List[Path] = []
    for spec in pool_def.get("members") if isinstance(pool_def.get("members"), list) else []:
        if not isinstance(spec, dict):
            continue
        batch: List[Path] = []
        if spec.get("kind") == "pool_index" or isinstance(spec.get("pool_id"), str):
            for mem in _pool_index_member_dicts(
                spec,
                limit=10_000,
                output_root=output_root,
                data_root=data_root,
            ):
                raw = mem.get("path")
                if isinstance(raw, str) and raw.strip():
                    batch.append(Path(raw).expanduser())
        elif spec.get("glob"):
            batch = _resolve_glob_paths(spec, output_root=output_root, data_root=data_root)
        elif spec.get("dir"):
            batch = _resolve_dir_paths(spec, output_root=output_root, data_root=data_root)
        for path in batch:
            key = str(path.expanduser().resolve())
            if key in seen:
                continue
            seen.add(key)
            out.append(Path(key))
    return out


# Short tokens in job_key / combo_key segments (future naming). Long forms still parse.
JOB_KEY_SLOT_ABBREV: Dict[str, str] = {
    "prompt_profile": "pp",
    "source_video": "src",
    "source_video_ref": "src_ref",
    "source_still": "still",
    "identity_anchor": "id",
    "source_image": "srcimg",
    "start_image": "start",
}


def job_key_slot_token(slot: str) -> str:
    """Canonical short label for a binding slot in job/combo keys."""
    s = str(slot or "").strip()
    return JOB_KEY_SLOT_ABBREV.get(s, s)


def normalize_combo_key(combo_key: str) -> str:
    """Rewrite legacy long slot labels to abbrevs so old/new keys compare equal."""
    raw = str(combo_key or "")
    if not raw:
        return ""
    for long, short in sorted(JOB_KEY_SLOT_ABBREV.items(), key=lambda kv: len(kv[0]), reverse=True):
        raw = re.sub(rf"(^|__){re.escape(long)}(?=-|__|$)", rf"\1{short}", raw)
    return raw


def _combo_key_from_slot_paths(slot_paths: Dict[str, str]) -> str:
    return "__".join(
        f"{job_key_slot_token(slot)}-{Path(path).stem}" for slot, path in sorted(slot_paths.items())
    )


def _combo_key_from_job_bindings(bindings: Dict[str, Any]) -> Optional[str]:
    if not isinstance(bindings, dict):
        return None
    slot_paths: Dict[str, str] = {}
    for slot, b in bindings.items():
        if not isinstance(b, dict):
            continue
        path = str(b.get("path") or "").strip()
        if path:
            slot_paths[str(slot)] = path
    if not slot_paths:
        return None
    return _combo_key_from_slot_paths(slot_paths)


def _primary_video_binding(bindings: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for slot in ("source_video", "source_video_ref", "source_still", "identity_anchor"):
        b = bindings.get(slot)
        if isinstance(b, dict) and (b.get("url") or b.get("thumb_url") or b.get("path")):
            return b
    for b in bindings.values():
        if isinstance(b, dict):
            hint = str(b.get("relpath") or b.get("path") or b.get("basename") or "")
            low = hint.lower()
            if low.endswith((".mp4", ".webm", ".mov", ".png", ".jpg", ".jpeg", ".webp")):
                return b
    return None


def _projected_pairs_for_family(
    pools_yaml: Path,
    shape_doc: Dict[str, Any],
    job_summaries: List[Dict[str, Any]],
    *,
    output_root: Path,
    data_root: Path,
    url_for: Optional[Callable[[str], str]] = None,
    wip_root: Optional[Path] = None,
    workspace_root: Optional[Path] = None,
    file_exists: Optional[Callable[[str], bool]] = None,
    limit: int = 48,
) -> List[Dict[str, Any]]:
    """Combinations from input pools (product) that have no job yet — possible future runs."""
    if not pools_yaml.is_file() or limit <= 0:
        return []
    pools_doc = _load_yaml(pools_yaml)
    if not isinstance(pools_doc, dict):
        return []
    pools = pools_doc.get("pools")
    if not isinstance(pools, dict):
        return []

    req_slots: Set[str] = set()
    for req in shape_doc.get("requires") or []:
        if isinstance(req, dict) and req.get("slot") and not req.get("optional"):
            req_slots.add(str(req["slot"]))

    pool_paths: Dict[str, List[Path]] = {}
    for name, pool_def in pools.items():
        if not isinstance(pool_def, dict):
            continue
        slot = str(pool_def.get("slot") or name)
        if req_slots and slot not in req_slots:
            continue
        paths = _pool_slot_paths(pool_def, output_root=output_root, data_root=data_root)
        if paths:
            pool_paths[slot] = paths

    if len(pool_paths) < 2:
        return []

    media_kwargs = {
        "url_for": url_for,
        "wip_root": wip_root,
        "workspace_root": workspace_root,
        "file_exists": file_exists,
    }

    existing: Set[str] = set()
    for job in job_summaries:
        ck = _combo_key_from_job_bindings(job.get("bindings") if isinstance(job.get("bindings"), dict) else {})
        if ck:
            existing.add(ck)

    slots = sorted(pool_paths.keys())
    lists = [pool_paths[s] for s in slots]
    out: List[Dict[str, Any]] = []
    scan_cap = max(limit * 8, limit)

    for i, tup in enumerate(itertools.product(*lists)):
        if i >= scan_cap:
            break
        picks = {slots[j]: tup[j] for j in range(len(slots))}
        slot_paths = {s: str(p) for s, p in picks.items()}
        combo_key = _combo_key_from_slot_paths(slot_paths)
        if combo_key in existing:
            continue

        bindings_preview: Dict[str, Any] = {}
        for slot, path in slot_paths.items():
            bindings_preview[slot] = _path_media_row(path, output_root=output_root, **media_kwargs)

        source = _primary_video_binding(bindings_preview)
        out.append(
            {
                "pair_key": f"future:{combo_key}",
                "combo_key": combo_key,
                "phase": "future",
                "gap": "output",
                "gap_note": "not run",
                "bindings": bindings_preview,
                "source": source,
            }
        )
        if len(out) >= limit:
            break

    return out


def _pool_index_member_dicts(
    spec: Dict[str, Any],
    *,
    limit: int,
    output_root: Path,
    data_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    pool_id = spec.get("pool_id")
    if not isinstance(pool_id, str) or not pool_id.strip():
        return []
    for index_path in _runtime_path_candidates(
        str(spec.get("glob") or ""),
        output_root=output_root,
        data_root=data_root,
    ):
        p = Path(index_path).expanduser()
        if not p.is_file():
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        pools = doc.get("pools")
        if not isinstance(pools, dict):
            continue
        pool = pools.get(pool_id)
        if not isinstance(pool, dict):
            continue
        members = pool.get("members") if isinstance(pool.get("members"), list) else []
        cap = spec.get("limit")
        take = int(cap) if isinstance(cap, int) and cap > 0 else int(limit)
        # Python slice `[-0:]` is the whole list — treat 0 as "no previews".
        if take <= 0:
            return []
        return [m for m in members[-take:] if isinstance(m, dict)]
    return []


def _input_pools_from_yaml(
    pools_yaml: Path,
    *,
    output_root: Path,
    data_root: Optional[Path] = None,
    members_limit: int = 24,
    url_for: Optional[Callable[[str], str]] = None,
    wip_root: Optional[Path] = None,
    workspace_root: Optional[Path] = None,
    file_exists: Optional[Callable[[str], bool]] = None,
) -> List[Dict[str, Any]]:
    if not pools_yaml.is_file():
        return []
    doc = _load_yaml(pools_yaml)
    if not isinstance(doc, dict):
        return []
    pools = doc.get("pools")
    if not isinstance(pools, dict):
        return []
    preview_kwargs = {
        "output_root": output_root,
        "url_for": url_for,
        "wip_root": wip_root,
        "workspace_root": workspace_root,
        "file_exists": file_exists,
    }
    out: List[Dict[str, Any]] = []
    ml = max(0, int(members_limit))
    for name, spec in pools.items():
        if not isinstance(spec, dict):
            continue
        members = spec.get("members") if isinstance(spec.get("members"), list) else []
        feeds_from: List[Dict[str, Any]] = []
        previews: List[Dict[str, Any]] = []
        if ml <= 0:
            for m in members:
                if not isinstance(m, dict):
                    continue
                if m.get("kind") == "pool_index" or isinstance(m.get("pool_id"), str):
                    feeds_from.append(
                        {
                            "pool_id": m.get("pool_id"),
                            "from_index": m.get("glob"),
                            "limit": m.get("limit"),
                        }
                    )
            out.append(
                {
                    "name": name,
                    "slot": spec.get("slot"),
                    "description": spec.get("description"),
                    "feeds_from": feeds_from or None,
                    "member_glob_count": sum(1 for m in members if isinstance(m, dict) and m.get("glob")),
                    "members_preview": [],
                    "member_preview_count": 0,
                }
            )
            continue
        for m in members:
            if not isinstance(m, dict):
                continue
            if m.get("kind") == "pool_index" or isinstance(m.get("pool_id"), str):
                feeds_from.append(
                    {
                        "pool_id": m.get("pool_id"),
                        "from_index": m.get("glob"),
                        "limit": m.get("limit"),
                    }
                )
                for mem in _pool_index_member_dicts(
                    m,
                    limit=ml,
                    output_root=output_root,
                    data_root=data_root,
                ):
                    previews.append(_member_preview(mem, **preview_kwargs))
                    if len(previews) >= ml:
                        break
                continue
            if m.get("glob"):
                for path in _resolve_glob_paths(m, output_root=output_root, data_root=data_root):
                    if path.suffix.lower() != ".mp4":
                        continue
                    previews.append(
                        _path_media_row(
                            str(path),
                            **preview_kwargs,
                        )
                    )
                    if len(previews) >= ml:
                        break
                continue
            if m.get("dir"):
                for path in _resolve_dir_paths(m, output_root=output_root, data_root=data_root):
                    previews.append(
                        {
                            "path": str(path),
                            "basename": _basename(str(path)),
                            "kind": "prompt",
                        }
                    )
                    if len(previews) >= ml:
                        break
        out.append(
            {
                "name": name,
                "slot": spec.get("slot"),
                "description": spec.get("description"),
                "feeds_from": feeds_from or None,
                "member_glob_count": sum(1 for m in members if isinstance(m, dict) and m.get("glob")),
                "members_preview": previews[:ml],
                "member_preview_count": len(previews),
            }
        )
    return out


def _deposit_pools_from_index(
    index_doc: Dict[str, Any],
    *,
    output_root: Path,
    members_limit: int,
    url_for: Optional[Callable[[str], str]] = None,
    wip_root: Optional[Path] = None,
    workspace_root: Optional[Path] = None,
    file_exists: Optional[Callable[[str], bool]] = None,
    seed_resolver: Optional["_SeedSourceResolver"] = None,
) -> List[Dict[str, Any]]:
    pools = index_doc.get("pools")
    if not isinstance(pools, dict):
        return []
    out: List[Dict[str, Any]] = []
    preview_kwargs = {
        "output_root": output_root,
        "url_for": url_for,
        "wip_root": wip_root,
        "workspace_root": workspace_root,
        "file_exists": file_exists,
    }

    def _preview(m: Dict[str, Any]) -> Dict[str, Any]:
        row = _member_preview(m, **preview_kwargs)
        # Recover embedded LoadImage still when the deposit has no source preview.
        # Also try when job_key is set but the job may be archived / not in the map
        # payload — otherwise chips show a blank left side.
        if seed_resolver is not None and not row.get("source_still"):
            ref = seed_resolver.resolve(row)
            if ref:
                row["source_still"] = ref
        return row

    ml = max(0, int(members_limit))
    for pool_id, spec in pools.items():
        if not isinstance(spec, dict):
            continue
        members = spec.get("members") if isinstance(spec.get("members"), list) else []
        # `members[-0:]` is the whole list in Python — skip previews when ml==0.
        preview = (
            [_preview(m) for m in members[-ml:] if isinstance(m, dict)] if ml > 0 else []
        )
        out.append(
            {
                "pool_id": pool_id,
                "slot": spec.get("slot"),
                "description": spec.get("description"),
                "member_count": len(members),
                "members_preview": preview,
                "latest_member": _member_preview(members[-1], **preview_kwargs)
                if ml > 0 and members and isinstance(members[-1], dict)
                else None,
            }
        )
    return out


def _load_jobs(jobs_root: Path) -> List[Dict[str, Any]]:
    if not jobs_root.is_dir():
        return []
    items: List[Dict[str, Any]] = []
    for family_dir in sorted(jobs_root.iterdir()):
        if not family_dir.is_dir():
            continue
        for job_path in sorted(family_dir.glob("*.job.json")):
            try:
                job = json.loads(job_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(job, dict):
                continue
            job["job_path"] = str(job_path)
            items.append(job)
    return items


def _job_status(job: Dict[str, Any]) -> str:
    submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    pid = submit.get("prompt_id")
    if not isinstance(pid, str) or not pid.strip():
        return "pending"
    return str(submit.get("status") or "queued")


def classify_job_kind(job: Dict[str, Any]) -> str:
    """Operator-facing origin label for map chips (hourly / ui / pipeline / …)."""
    key = str(job.get("job_key") or "")
    if key.startswith("hourly__"):
        return "hourly"
    if job.get("pipeline_id") or job.get("pipeline"):
        return "pipeline"
    if isinstance(job.get("adhoc_overrides"), dict) and job.get("adhoc_overrides"):
        return "ui"
    lower = key.lower()
    if "adhoc_ui" in lower or "__ui" in lower or "_ui" in lower:
        return "ui"
    pick = str(job.get("pick_mode") or "").strip().lower()
    if pick in {"replay", "derive", "extend"}:
        return pick
    return "factory"


def _job_mtime(job_or_summary: Dict[str, Any]) -> float:
    p = job_or_summary.get("job_path")
    try:
        return Path(str(p)).stat().st_mtime if p else 0.0
    except Exception:
        return 0.0


def select_job_summaries_for_map(
    job_summaries: List[Dict[str, Any]],
    families: List[Dict[str, Any]],
    *,
    jobs_per_family: int = 40,
    jobs_limit: int = 500,
) -> List[Dict[str, Any]]:
    """
    Prefer recent jobs *per family* and always include deposit-referenced keys.

    A global mtime top-N starves quieter families when one line (e.g. FB9_GEX)
    dominates recent activity — leaving Source→Output chips as "job not in list"
    even though the job file and bindings still exist.
    """
    per_family = max(1, int(jobs_per_family))
    hard_cap = max(per_family, int(jobs_limit))

    by_key: Dict[str, Dict[str, Any]] = {}
    by_family: Dict[str, List[Dict[str, Any]]] = {}
    for row in job_summaries:
        jk = row.get("job_key")
        if isinstance(jk, str) and jk.strip():
            by_key[jk] = row
        slug = row.get("family_slug")
        if isinstance(slug, str) and slug.strip():
            by_family.setdefault(slug, []).append(row)

    selected: Dict[str, Dict[str, Any]] = {}
    for _slug, rows in by_family.items():
        for row in rows[:per_family]:
            jk = row.get("job_key")
            if isinstance(jk, str) and jk.strip():
                selected[jk] = row

    for fam in families:
        for dep in fam.get("deposit_pools") or []:
            if not isinstance(dep, dict):
                continue
            for mem in dep.get("members_preview") or []:
                if not isinstance(mem, dict):
                    continue
                jk = mem.get("job_key")
                if isinstance(jk, str) and jk in by_key:
                    selected[jk] = by_key[jk]

    items = list(selected.values())
    items.sort(key=_job_mtime, reverse=True)
    if len(items) > hard_cap:
        # Keep deposit-referenced keys even under the cap.
        must: Dict[str, Dict[str, Any]] = {}
        for fam in families:
            for dep in fam.get("deposit_pools") or []:
                if not isinstance(dep, dict):
                    continue
                for mem in dep.get("members_preview") or []:
                    if not isinstance(mem, dict):
                        continue
                    jk = mem.get("job_key")
                    if isinstance(jk, str) and jk in selected:
                        must[jk] = selected[jk]
        rest = [r for r in items if r.get("job_key") not in must]
        out = list(must.values()) + rest[: max(0, hard_cap - len(must))]
        out.sort(key=_job_mtime, reverse=True)
        return out
    return items


def _job_summary(
    job: Dict[str, Any],
    *,
    output_root: Path,
    url_for: Optional[Callable[[str], str]] = None,
    wip_root: Optional[Path] = None,
    workspace_root: Optional[Path] = None,
    file_exists: Optional[Callable[[str], bool]] = None,
) -> Dict[str, Any]:
    submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    bindings = job.get("bindings") if isinstance(job.get("bindings"), dict) else {}
    media_kwargs = {
        "url_for": url_for,
        "wip_root": wip_root,
        "workspace_root": workspace_root,
        "file_exists": file_exists,
    }
    binding_preview: Dict[str, Any] = {}
    for slot, b in bindings.items():
        if not isinstance(b, dict):
            continue
        path = str(b.get("path") or "")
        row = _path_media_row(path, output_root=output_root, **media_kwargs)
        row["role"] = b.get("role")
        row["binding_type"] = b.get("binding_type")
        binding_preview[slot] = row
    outputs_raw = submit.get("outputs") if isinstance(submit.get("outputs"), list) else []
    outputs: List[Dict[str, Any]] = []
    for op in outputs_raw[:5]:
        if isinstance(op, str):
            outputs.append(_path_media_row(op, output_root=output_root, **media_kwargs))
    deposits = job.get("deposits") if isinstance(job.get("deposits"), dict) else {}
    deposit_to = None
    fv = deposits.get("final_video")
    if isinstance(fv, dict):
        deposit_to = fv.get("to_pool")
    timings = job.get("timings") if isinstance(job.get("timings"), dict) else {}
    exec_sec = None
    ex = timings.get("execution")
    if isinstance(ex, dict) and ex.get("sec") is not None:
        exec_sec = ex.get("sec")
    return {
        "job_key": job.get("job_key"),
        "family_slug": job.get("family_slug"),
        "status": _job_status(job),
        "job_kind": classify_job_kind(job),
        "graph_hash": job.get("graph_hash"),
        "shape_id": job.get("shape_id"),
        "prompt_id": submit.get("prompt_id"),
        "bindings": binding_preview,
        "deposit_to": deposit_to,
        "generated_workflow_path": job.get("generated_workflow_path"),
        "template_path": job.get("template_path"),
        "outputs": outputs,
        "exec_sec": exec_sec,
        "created_at": job.get("created_at"),
        "pick_index": job.get("pick_index"),
        "pick_mode": job.get("pick_mode"),
        "job_path": job.get("job_path"),
    }


def _fetch_comfy_queue(comfy_server: str, *, timeout_s: int = 8) -> Dict[str, Any]:
    url = str(comfy_server).rstrip("/") + "/queue"
    try:
        obj = http_json_with_retry(method="GET", url=url, timeout_s=timeout_s)
        if not isinstance(obj, dict):
            return {"ok": False, "error": "comfy_queue_non_object"}
        running = obj.get("queue_running") if isinstance(obj.get("queue_running"), list) else []
        pending = obj.get("queue_pending") if isinstance(obj.get("queue_pending"), list) else []
        return {"ok": True, "running_count": len(running), "pending_count": len(pending), "running": running, "pending": pending}
    except urllib.error.URLError as e:
        return {"ok": False, "error": "comfy_queue_fetch_failed", "detail": str(e)}
    except Exception as e:
        return {"ok": False, "error": "comfy_queue_fetch_failed", "detail": str(e)}


def _queue_prompt_ids(queue_doc: Dict[str, Any]) -> Set[str]:
    out: Set[str] = set()
    if not queue_doc.get("ok"):
        return out
    for key in ("running", "pending"):
        rows = queue_doc.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, list) or len(row) < 2:
                continue
            pid = row[1]
            if isinstance(pid, str) and pid.strip():
                out.add(pid.strip())
    return out


def _load_hourly_state(data_root: Path) -> Dict[str, Any]:
    path = data_root / "shape_factory" / "hourly-state.json"
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def _load_chain_manifest(data_root: Path) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    env = os.environ.get("CHAIN_MANIFEST", "").strip()
    default = data_root / "chains" / "best-examples.chain.yaml"
    path = Path(env).expanduser() if env else default
    if not path.is_file():
        return None, None
    doc = _load_yaml(path)
    return str(path), doc if isinstance(doc, dict) else None


def _predict_next_hourly_sample(
    hourly_state: Dict[str, Any],
    chain_doc: Optional[Dict[str, Any]],
    *,
    data_root: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    try:
        from shape_factory_hourly import predict_hourly_gex2  # type: ignore

        preview = predict_hourly_gex2(hourly_state, data_root=data_root)
        if preview:
            return preview
    except Exception:
        pass

    if not chain_doc:
        return None
    samples = [s for s in (chain_doc.get("samples") or []) if isinstance(s, dict) and not s.get("blocked")]
    if not samples:
        return None
    cursor = int(hourly_state.get("sample_cursor") or 0)
    idx = cursor % len(samples)
    sample = samples[idx]
    return {
        "cursor": cursor,
        "sample_index": idx,
        "sample_id": sample.get("id"),
        "pick_index": sample.get("pick_index", idx),
        "gex2_prompt": sample.get("gex2_prompt"),
        "note": sample.get("note"),
        "phase_if_idle": hourly_state.get("phase"),
    }


def _family_slug_from_shape_path(shape: Any) -> Optional[str]:
    if not isinstance(shape, str) or not shape.strip():
        return None
    name = Path(shape).name
    if name.endswith(".shape.yaml"):
        return name[: -len(".shape.yaml")]
    if name.endswith(".shape.yml"):
        return name[: -len(".shape.yml")]
    return None


def _pipeline_summaries(pipelines_root: Path) -> List[Dict[str, Any]]:
    if not pipelines_root.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for path in sorted(pipelines_root.glob("*.yaml")):
        doc = _load_yaml(path)
        if not isinstance(doc, dict):
            continue
        steps_in = doc.get("steps") if isinstance(doc.get("steps"), list) else []
        steps: List[Dict[str, Any]] = []
        for step in steps_in:
            if not isinstance(step, dict):
                continue
            binds = step.get("binds_override") if isinstance(step.get("binds_override"), dict) else {}
            sv = binds.get("source_video") if isinstance(binds.get("source_video"), dict) else {}
            deposits = step.get("deposits") if isinstance(step.get("deposits"), dict) else {}
            dep_pool = None
            fv = deposits.get("final_video")
            if isinstance(fv, str) and fv.startswith("pool:"):
                dep_pool = fv.split(":", 1)[1]
            shape_path = step.get("shape")
            steps.append(
                {
                    "id": step.get("id"),
                    "shape": shape_path,
                    "pools": step.get("pools"),
                    "pick": step.get("pick"),
                    "pick_index": step.get("pick_index"),
                    "family_slug": _family_slug_from_shape_path(shape_path),
                    "binds_from_pool": sv.get("pool"),
                    "binds_pick": sv.get("pick"),
                    "deposits_to": dep_pool,
                }
            )
        out.append(
            {
                "pipeline_id": doc.get("pipeline_id"),
                "description": doc.get("description"),
                "path": str(path),
                "input_guidance": doc.get("input_guidance"),
                "affinity": doc.get("affinity") if isinstance(doc.get("affinity"), list) else [],
                "steps": steps,
            }
        )
    return out


def _build_edges(families: List[Dict[str, Any]], pipelines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    edges: List[Dict[str, Any]] = []
    for fam in families:
        slug = fam.get("family_slug")
        if not isinstance(slug, str):
            continue
        shape_node = f"shape:{slug}"
        for inp in fam.get("input_pools") or []:
            if not isinstance(inp, dict):
                continue
            slot = inp.get("slot") or inp.get("name")
            feeds = inp.get("feeds_from")
            if isinstance(feeds, list) and feeds:
                for ff in feeds:
                    if not isinstance(ff, dict):
                        continue
                    pid = ff.get("pool_id")
                    if isinstance(pid, str):
                        edges.append(
                            {
                                "from": f"pool:{pid}",
                                "to": shape_node,
                                "kind": "binds",
                                "slot": slot,
                                "pick": ff.get("limit"),
                            }
                        )
            else:
                edges.append(
                    {
                        "from": f"input:{slug}:{slot}",
                        "to": shape_node,
                        "kind": "binds",
                        "slot": slot,
                    }
                )
        for dep in fam.get("deposit_pools") or []:
            if not isinstance(dep, dict):
                continue
            pid = dep.get("pool_id")
            if isinstance(pid, str):
                edges.append({"from": shape_node, "to": f"pool:{pid}", "kind": "deposit", "slot": dep.get("slot")})
    for pipe in pipelines:
        pipe_id = pipe.get("pipeline_id")
        steps = pipe.get("steps") if isinstance(pipe.get("steps"), list) else []
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            from_pool = step.get("binds_from_pool")
            shape_path = str(step.get("shape") or "")
            m = re.search(r"/([^/]+)\.shape\.yaml$", shape_path.replace("\\", "/"))
            slug = m.group(1) if m else None
            if slug and from_pool:
                edges.append(
                    {
                        "from": f"pool:{from_pool}",
                        "to": f"shape:{slug}",
                        "kind": "pipeline_binds",
                        "pipeline_id": pipe_id,
                        "step_id": step.get("id"),
                        "pick": step.get("binds_pick"),
                    }
                )
            if slug and step.get("deposits_to"):
                edges.append(
                    {
                        "from": f"shape:{slug}",
                        "to": f"pool:{step.get('deposits_to')}",
                        "kind": "pipeline_deposit",
                        "pipeline_id": pipe_id,
                        "step_id": step.get("id"),
                    }
                )
            if i > 0 and slug and from_pool:
                prev = steps[i - 1]
                if isinstance(prev, dict) and prev.get("deposits_to") == from_pool:
                    edges.append(
                        {
                            "from": f"pool:{from_pool}",
                            "to": f"shape:{slug}",
                            "kind": "pipeline_step_link",
                            "pipeline_id": pipe_id,
                            "from_step": prev.get("id"),
                            "to_step": step.get("id"),
                        }
                    )
    return edges


def build_shape_factory_map(
    *,
    data_root: Path,
    output_root: Path,
    comfy_server: str = "",
    members_limit: int = 24,
    jobs_limit: int = 500,
    jobs_per_family: int = 40,
    family_filter: Optional[str] = None,
    skip_queue: bool = False,
    url_for: Optional[Callable[[str], str]] = None,
    wip_root: Optional[Path] = None,
    workspace_root: Optional[Path] = None,
    file_exists: Optional[Callable[[str], bool]] = None,
    projected_pairs_limit: int = 48,
) -> Dict[str, Any]:
    pools_root = data_root / "pools"
    shapes_root = data_root / "shapes"
    jobs_root = data_root / "shape_factory" / "jobs"
    pipelines_root = data_root / "pipelines"

    seed_resolver: Optional[_SeedSourceResolver] = None
    try:
        import shape_factory_seed_sources as _sfss  # type: ignore

        og_root = output_root / "og"
        if not og_root.is_dir():
            og_root = output_root / "output" / "og"
        seed_resolver = _SeedSourceResolver(
            output_root=output_root,
            workspace_root=workspace_root,
            url_for=url_for,
            file_exists=file_exists,
            seed_sources_path=_sfss.default_seed_sources_path(og_root),
        )
    except Exception:
        seed_resolver = None

    families: List[Dict[str, Any]] = []
    shape_docs_by_slug: Dict[str, Dict[str, Any]] = {}
    if pools_root.is_dir():
        for family_dir in sorted(pools_root.iterdir()):
            if not family_dir.is_dir():
                continue
            index_path = family_dir / "index.json"
            pools_yaml = family_dir / "pools.yaml"
            if not index_path.is_file():
                continue
            try:
                index_doc = json.loads(index_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(index_doc, dict):
                continue
            shape_path_raw = index_doc.get("shape_path") or (index_doc.get("pools_yaml") and str(index_doc.get("pools_yaml")).replace("pools.yaml", "../shapes/"))
            shape_path = Path(str(shape_path_raw)) if shape_path_raw else shapes_root / f"{family_dir.name}.shape.yaml"
            if not shape_path.is_file():
                alt = shapes_root / f"{family_dir.name}.shape.yaml"
                if alt.is_file():
                    shape_path = alt
            shape_doc = _load_shape_doc(shape_path)
            family_slug = shape_doc.get("family_slug") or family_dir.name
            shape_docs_by_slug[str(family_slug)] = shape_doc
            if family_filter and str(family_slug) != family_filter:
                continue
            fam = {
                "family_slug": family_slug,
                "shape": _shape_summary(shape_path, shape_doc),
                "pools_yaml": str(pools_yaml) if pools_yaml.is_file() else None,
                "index_path": str(index_path),
                "index_updated_at": index_doc.get("updated_at"),
                "input_pools": _input_pools_from_yaml(
                    pools_yaml,
                    output_root=output_root,
                    data_root=data_root,
                    members_limit=max(0, int(members_limit)),
                    url_for=url_for,
                    wip_root=wip_root,
                    workspace_root=workspace_root,
                    file_exists=file_exists,
                ),
                "deposit_pools": _deposit_pools_from_index(
                    index_doc,
                    output_root=output_root,
                    members_limit=max(0, int(members_limit)),
                    url_for=url_for,
                    wip_root=wip_root,
                    workspace_root=workspace_root,
                    file_exists=file_exists,
                    seed_resolver=seed_resolver,
                ),
            }
            families.append(fam)

    pipelines = _pipeline_summaries(pipelines_root)
    edges = _build_edges(families, pipelines)

    all_jobs = _load_jobs(jobs_root)
    if family_filter:
        all_jobs = [j for j in all_jobs if str(j.get("family_slug") or "") == family_filter]

    counts: Dict[str, int] = {}
    for j in all_jobs:
        st = _job_status(j)
        counts[st] = counts.get(st, 0) + 1

    # Recent jobs first (by mtime of job file)
    all_jobs.sort(key=_job_mtime, reverse=True)
    job_summaries = [
        _job_summary(
            j,
            output_root=output_root,
            url_for=url_for,
            wip_root=wip_root,
            workspace_root=workspace_root,
            file_exists=file_exists,
        )
        for j in all_jobs
    ]

    jobs_by_family: Dict[str, List[Dict[str, Any]]] = {}
    for row in job_summaries:
        slug = row.get("family_slug")
        if isinstance(slug, str) and slug.strip():
            jobs_by_family.setdefault(slug, []).append(row)

    proj_cap = max(0, min(int(projected_pairs_limit), 200))
    for fam in families:
        slug = str(fam.get("family_slug") or "")
        pools_yaml = Path(str(fam.get("pools_yaml") or ""))
        shape_doc = shape_docs_by_slug.get(slug) or {}
        fam["projected_pairs"] = _projected_pairs_for_family(
            pools_yaml,
            shape_doc,
            jobs_by_family.get(slug, []),
            output_root=output_root,
            data_root=data_root,
            url_for=url_for,
            wip_root=wip_root,
            workspace_root=workspace_root,
            file_exists=file_exists,
            limit=proj_cap,
        )

    # Payload jobs: per-family recent + every deposit-preview job_key (not global top-N).
    job_items = select_job_summaries_for_map(
        job_summaries,
        families,
        jobs_per_family=max(1, int(jobs_per_family)),
        jobs_limit=max(1, int(jobs_limit)),
    )

    queue_doc: Dict[str, Any] = {"ok": False, "skipped": True} if skip_queue else _fetch_comfy_queue(comfy_server)
    if not skip_queue and not queue_doc.get("ok"):
        queue_doc["skipped"] = False

    prompt_to_job: Dict[str, Dict[str, Any]] = {}
    for row in job_summaries:
        pid = row.get("prompt_id")
        if isinstance(pid, str) and pid.strip():
            prompt_to_job[pid.strip()] = row

    queue_shape_factory: List[Dict[str, Any]] = []
    if queue_doc.get("ok"):
        for label in ("running", "pending"):
            rows = queue_doc.get(label)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, list) or len(row) < 2:
                    continue
                pid = row[1] if isinstance(row[1], str) else None
                if not pid:
                    continue
                entry: Dict[str, Any] = {"prompt_id": pid, "queue_state": label}
                if pid in prompt_to_job:
                    entry["job"] = prompt_to_job[pid]
                queue_shape_factory.append(entry)

    if seed_resolver is not None:
        seed_resolver.flush()

    hourly_state = _load_hourly_state(data_root)
    chain_path, chain_doc = _load_chain_manifest(data_root)
    next_sample = _predict_next_hourly_sample(hourly_state, chain_doc, data_root=data_root)

    pending_jobs = [j for j in job_summaries if j.get("status") == "pending"]
    active_jobs = [j for j in job_summaries if j.get("status") in {"queued", "running", "unknown"}]

    queue_ids = _queue_prompt_ids(queue_doc) if queue_doc.get("ok") else set()
    inflight_jobs = [
        j for j in job_summaries if isinstance(j.get("prompt_id"), str) and j.get("prompt_id") in queue_ids
    ]

    return {
        "ok": True,
        "schema_version": "comfyui-runpod.shape-factory-map.v0",
        "updated_at": _utc_now_iso(),
        "data_root": str(data_root),
        "paths": {
            "jobs": str(jobs_root),
            "pools": str(pools_root),
            "shapes": str(shapes_root),
            "pipelines": str(pipelines_root),
        },
        "families": families,
        "pipelines": pipelines,
        "edges": edges,
        "jobs": {
            "summary": counts,
            "total": len(all_jobs),
            "items": job_items,
            "returned": len(job_items),
            "jobs_per_family": max(1, int(jobs_per_family)),
            "pending_submit": pending_jobs[:20],
            "inflight": inflight_jobs[:20],
            "active": active_jobs[:20],
        },
        "queue": {
            **queue_doc,
            "shape_factory_matches": queue_shape_factory,
        },
        "hourly": {
            "state_path": str(data_root / "shape_factory" / "hourly-state.json"),
            "state": hourly_state,
            "chain_manifest": chain_path,
            "next_sample": next_sample,
        },
    }
