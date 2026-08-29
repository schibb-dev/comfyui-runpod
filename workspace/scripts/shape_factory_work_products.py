"""Recent shape-factory work products + construction metadata for debug UI."""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from shape_factory_flow import flow_phase, normalize_flow_status, remediation_actions

# Plan fields worth keeping on the job for construction debugging.
CONSTRUCTION_PLAN_KEYS: tuple[str, ...] = (
    "step",
    "derive_action",
    "source",
    "combo_key",
    "cursor",
    "next_cursor",
    "appetite",
    "appetite_facet",
    "appetite_value",
    "appetite_evidence",
    "tag_affinity",
    "fast_track",
    "selection_weight",
    "hold_axis",
    "hold_values",
    "hold_candidate_count",
    "hold_facet_constrained",
    "hold_fallback",
    "rating_kind",
    "rating_effective",
    "rating_evidence",
    "disposition_entry",
    "disposition_note",
    "parent_output",
    "recipe_count",
    "seed_count",
    "omit_excluded",
    "pool_source_count",
    "derive_attempts",
    "used_recent_fallback",
    "recent_combo_penalty",
    "upgraded_from",
    "family",
    "identity_anchor",
    "identity_evidence",
)


def construction_from_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Extract a compact construction blob from an hourly plan / picks-json doc."""
    out: Dict[str, Any] = {}
    for key in CONSTRUCTION_PLAN_KEYS:
        if key not in plan:
            continue
        val = plan.get(key)
        if val is None or val == "" or val == []:
            continue
        out[key] = val
    return out


def _basename(path: Any) -> str:
    s = str(path or "").strip()
    if not s:
        return ""
    return Path(s).name


def prefer_target_family(explicit: Any, inferred: Any = "") -> str:
    """Prefer an operator-selected family over the source job's family."""
    e = str(explicit or "").strip()
    if e:
        return e
    return str(inferred or "").strip()


def list_shape_families(
    data_root: Path,
    *,
    workspace_root: Optional[Path] = None,
    output_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Scan ``data_root/shapes/*.shape.yaml`` for family picker options."""
    shapes_dir = Path(data_root) / "shapes"
    out: List[Dict[str, Any]] = []
    if not shapes_dir.is_dir():
        return out
    try:
        from shape_factory import load_yaml
    except ImportError:
        import yaml  # type: ignore

        def load_yaml(path: Path) -> dict:  # type: ignore
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    vhs_defaults_fn = None
    if workspace_root is not None and output_root is not None:
        try:
            from shape_factory_queue import vhs_loader_defaults_for_shape
        except ImportError:
            vhs_loader_defaults_for_shape = None  # type: ignore
        vhs_defaults_fn = vhs_loader_defaults_for_shape

    for path in sorted(shapes_dir.glob("*.shape.yaml")):
        slug = path.name[: -len(".shape.yaml")] if path.name.endswith(".shape.yaml") else path.stem
        shape_id = None
        family_slug = slug
        doc: Dict[str, Any] = {}
        try:
            loaded = load_yaml(path)
            if isinstance(loaded, dict):
                doc = loaded
                shape_id = doc.get("shape_id")
                family_slug = str(doc.get("family_slug") or slug).strip() or slug
        except Exception:
            pass
        row: Dict[str, Any] = {"slug": family_slug or slug, "shape_id": shape_id, "shape_path": str(path)}
        if doc:
            for key in ("primary_input", "input_profile", "chain_role", "io_class"):
                if doc.get(key) is not None and str(doc.get(key)).strip():
                    row[key] = str(doc.get(key)).strip()
            if "io_class" not in row and row.get("input_profile"):
                from shape_factory_vocab import io_class_for_profile

                io = io_class_for_profile(str(row["input_profile"]))
                if io:
                    row["io_class"] = io
        if vhs_defaults_fn is not None and doc:
            try:
                row["vhs_defaults"] = vhs_defaults_fn(
                    doc,
                    data_root=Path(data_root),
                    workspace_root=Path(workspace_root),
                    output_root=Path(output_root),
                )
            except Exception:
                row["vhs_defaults"] = {"skip_first_frames": 0, "frame_load_cap": 0}
        out.append(row)
    # Dedupe by slug (prefer first).
    seen: set[str] = set()
    uniq: List[Dict[str, Any]] = []
    for row in out:
        s = str(row.get("slug") or "")
        if not s or s in seen:
            continue
        seen.add(s)
        uniq.append(row)
    return uniq


def is_extend_family_option(row: Dict[str, Any]) -> bool:
    """Mirror Submit UI: video Extend targets (V2V / VI2V / extend role), not I2V/still."""
    slug = str(row.get("slug") or "").strip()
    if not slug:
        return False
    role = str(row.get("chain_role") or "").strip().lower()
    if role == "extend":
        return True
    if role == "origin":
        return False
    io = str(row.get("io_class") or "").strip().upper()
    if io in {"V2V", "VI2V", "EXT"}:
        return True
    if io == "I2V":
        return False
    sid = str(row.get("shape_id") or "").strip().lower()
    if not sid:
        return True
    if "i2v" in sid and "vi2v" not in sid:
        return False
    if "still" in sid and "identity" not in sid:
        return False
    return "v2v" in sid or "vi2v" in sid or "facial" in sid or "source" in sid or "identity" in sid


def _shapes_pipelines_fingerprint(data_root: Path) -> str:
    """Cheap config stamp for client/session cache invalidation."""
    latest = 0.0
    count = 0
    for root in (Path(data_root) / "shapes", Path(data_root) / "pipelines"):
        if not root.is_dir():
            continue
        for pattern in ("*.yaml", "*.yml"):
            for path in root.glob(pattern):
                try:
                    latest = max(latest, float(path.stat().st_mtime))
                    count += 1
                except OSError:
                    continue
    return f"{count}:{int(latest)}"


def list_submit_family_sets(
    data_root: Path,
    *,
    workspace_root: Optional[Path] = None,
    output_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Config-only picker sets for Submit (extend / vary / derive).

    Independent of jobs / Comfy reconcile — safe to cache aggressively.
    """
    families = list_shape_families(
        data_root,
        workspace_root=workspace_root,
        output_root=output_root,
    )
    extend = [f for f in families if is_extend_family_option(f)]
    # Vary/derive currently share the full family catalog; keep discrete lists for caching.
    vary = list(families)
    derive = list(families)
    return {
        "ok": True,
        "schema_version": "comfyui-runpod.submit-families.v0",
        "fingerprint": _shapes_pipelines_fingerprint(data_root),
        "families": families,
        "sets": {
            "extend": extend,
            "vary": vary,
            "derive": derive,
        },
        "extend_family_defaults": list_extend_family_defaults(data_root),
    }


def _family_slug_from_shape_ref(shape: Any) -> str:
    """Best-effort family slug from a pipeline step `shape` path or slug."""
    raw = str(shape or "").strip()
    if not raw:
        return ""
    name = Path(raw).name
    if name.endswith(".shape.yaml"):
        return name[: -len(".shape.yaml")]
    if name.endswith(".shape.yml"):
        return name[: -len(".shape.yml")]
    if "/" not in raw and "\\" not in raw and raw:
        return raw
    return Path(raw).stem


def list_extend_family_defaults(data_root: Path) -> Dict[str, str]:
    """Map source family → next-step family from ``data_root/pipelines/*.yaml``.

    Used as the Extend picker default (Vary stays on the source family).
    First pipeline edge wins when multiple pipelines define the same source.
    """
    pipelines_root = Path(data_root) / "pipelines"
    out: Dict[str, str] = {}
    if not pipelines_root.is_dir():
        return out
    try:
        from shape_factory import load_yaml
    except ImportError:
        import yaml  # type: ignore

        def load_yaml(path: Path) -> dict:  # type: ignore
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    for path in sorted(pipelines_root.glob("*.yaml")):
        try:
            doc = load_yaml(path)
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        steps = doc.get("steps") if isinstance(doc.get("steps"), list) else []
        slugs: List[str] = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            slug = _family_slug_from_shape_ref(step.get("shape"))
            if slug:
                slugs.append(slug)
        for i in range(len(slugs) - 1):
            src, nxt = slugs[i], slugs[i + 1]
            if src and nxt and src != nxt and src not in out:
                out[src] = nxt
    return out


# Shape-contract role labels (pipeline wiring; see *.shape.yaml `requires` / `produces`).
ROLE_GLOSS: dict[str, str] = {
    "A": "still/image input",
    "B": "video input",
    "C": "prompt/text",
    "X": "work product (output; may feed the next stage)",
}


def _format_role(role: Any) -> str:
    r = str(role or "").strip()
    if not r:
        return ""
    gloss = ROLE_GLOSS.get(r.upper()) or ROLE_GLOSS.get(r)
    if gloss:
        return f"role={r} ({gloss})"
    return f"role={r}"


def _relpath_under(root: Path, abs_path: Any) -> Optional[str]:
    """Return path relative to ``root``, remapping host↔container output aliases."""
    raw = str(abs_path or "").strip().replace("\\", "/")
    if not raw:
        return None
    try:
        root_r = root.resolve()
    except Exception:
        root_r = root

    candidates: List[str] = [raw]
    root_s = str(root_r).replace("\\", "/").rstrip("/")
    # Absolute paths written on the host often don't resolve under /workspace/output.
    for host_out in (
        "/home/yuji/comfyui-runpod-data/output",
        "/home/yuji/src/comfyui-runpod/workspace/output",
        "/workspace/output",
    ):
        if raw == host_out or raw.startswith(host_out + "/"):
            rel = raw[len(host_out) :].lstrip("/")
            candidates.append(f"{root_s}/{rel}" if rel else root_s)
    if raw.startswith("output/"):
        candidates.append(f"{root_s}/{raw[len('output/'):]}")
    if not raw.startswith("/"):
        candidates.append(f"{root_s}/{raw.lstrip('/')}")

    for cand in candidates:
        try:
            p = Path(cand).expanduser()
            if not p.is_absolute():
                p = root_r / p
            rel = p.resolve().relative_to(root_r).as_posix()
            return rel
        except Exception:
            continue
    return None


def _keeper_output_rel(
    paths: List[str],
    *,
    output_root: Path,
    job: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Pick the final/produce-node video; never prefer a ``_PREVIEW`` sibling."""
    cleaned = [str(p).strip() for p in paths if str(p).strip()]
    if not cleaned:
        return None
    picked: List[str] = list(cleaned)
    try:
        from shape_factory import select_final_output_paths

        selected = select_final_output_paths([Path(p) for p in cleaned], job=job)
        if selected:
            picked = [str(p) for p in selected]
    except Exception:
        non_preview = [p for p in cleaned if "_preview" not in Path(p).stem.lower()]
        finals = [p for p in (non_preview or cleaned) if "_final" in Path(p).stem.lower()]
        picked = finals or non_preview or cleaned
    for abs_out in picked:
        rel = _relpath_under(output_root, abs_out)
        if rel:
            return rel
    return None


def _file_url(rel: Optional[str]) -> Optional[str]:
    if not rel:
        return None
    return "/files/" + urllib.parse.quote(rel.replace("\\", "/").lstrip("/"), safe="")


def _thumb_rel_for_video(video_rel: Optional[str]) -> Optional[str]:
    if not video_rel:
        return None
    if video_rel.lower().endswith(".mp4"):
        return video_rel[:-4] + ".png"
    return None


_MEDIA_FILE_EXTS = (".mp4", ".webm", ".mov", ".png", ".jpg", ".jpeg", ".webp", ".gif")
_VIDEO_FILE_EXTS = (".mp4", ".webm", ".mov")
_IMAGE_FILE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def _binding_media_relpath(abs_p: Any, *, data_root: Path, output_root: Path) -> Optional[str]:
    """Map a binding path to a /files/-servable relpath (output/… or input/…)."""
    raw = str(abs_p or "").strip()
    if not raw:
        return None
    rel = _relpath_under(output_root, raw)
    if rel is None:
        rel = _relpath_under(data_root, raw)
    if rel is not None:
        return rel
    # Host bind-dir / checkout input aliases → input/<name>
    try:
        from shape_factory_map import _relpath_guess_from_abs  # type: ignore
    except Exception:
        _relpath_guess_from_abs = None  # type: ignore
    if _relpath_guess_from_abs is not None:
        guessed = _relpath_guess_from_abs(raw)
        if guessed:
            return guessed
    # Basename lookup under known input roots (empty workspace/input → bind dir).
    bn = Path(raw.replace("\\", "/")).name
    if not bn or bn == raw.rstrip("/"):
        return None
    input_roots = (
        Path(os.environ.get("COMFYUI_BIND_INPUT_DIR") or "/home/yuji/comfyui-runpod-data/input"),
        Path("/home/yuji/comfyui-runpod-data/input"),
        Path("/home/yuji/src/comfyui-runpod/workspace/input"),
        data_root / "input",
        output_root.parent / "input",
    )
    for root in input_roots:
        try:
            cand = Path(root).expanduser() / bn
            if cand.is_file():
                return f"input/{bn}"
        except Exception:
            continue
    return None


def _binding_entry_from_meta(
    slot: str,
    meta: Dict[str, Any],
    *,
    data_root: Path,
    output_root: Path,
) -> Dict[str, Any]:
    abs_p = meta.get("path")
    rel = _binding_media_relpath(abs_p, data_root=data_root, output_root=output_root)
    low = str(abs_p or rel or "").lower()
    is_media = low.endswith(_MEDIA_FILE_EXTS)
    entry: Dict[str, Any] = {
        "path": abs_p,
        "basename": _basename(abs_p),
        "relpath": rel,
        "url": _file_url(rel) if rel and is_media else None,
        "binding_type": meta.get("binding_type"),
        "role": meta.get("role"),
    }
    if rel and is_media:
        if low.endswith(_VIDEO_FILE_EXTS):
            entry["thumb_url"] = _file_url(_thumb_rel_for_video(rel))
        elif low.endswith(_IMAGE_FILE_EXTS):
            entry["thumb_url"] = entry.get("url")
    return entry


def _bindings_from_job(
    job: Dict[str, Any],
    *,
    data_root: Path,
    output_root: Path,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    raw = job.get("bindings") if isinstance(job.get("bindings"), dict) else {}
    for slot, meta in raw.items():
        if isinstance(meta, dict):
            out[str(slot)] = _binding_entry_from_meta(str(slot), meta, data_root=data_root, output_root=output_root)
    return out


def _prompt_doc_for_job(job: Dict[str, Any], job_path: Path) -> Optional[Dict[str, Any]]:
    """Load the Comfy API prompt used for this job (sibling .prompt.json or submit path)."""
    candidates: List[Path] = []
    submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    for raw in (
        submit.get("prompt_path"),
        job.get("prompt_path"),
        job_path.with_name(job_path.name.replace(".job.json", ".prompt.json")),
    ):
        text = str(raw or "").strip()
        if not text:
            continue
        p = Path(text).expanduser()
        if p.is_file():
            candidates.append(p)
    # Prefer sibling next to job when present.
    sibling = job_path.with_name(job_path.name.replace(".job.json", ".prompt.json"))
    if sibling.is_file():
        candidates.insert(0, sibling)
    seen: set[str] = set()
    for path in candidates:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(doc, dict):
            return doc
    return None


_VHS_LOAD_CLASS_TYPES: frozenset[str] = frozenset(
    {
        "VHS_LoadVideoPath",
        "VHS_LoadVideo",
        "VHS_LoadVideoFFmpegPath",
        "VHS_LoadVideoFFmpeg",
        "LoadVideo",
    }
)


def _vhs_window_from_inputs(inputs: Any) -> Optional[Dict[str, int]]:
    """Parse skip/cap from a node inputs or widgets_values dict."""
    if not isinstance(inputs, dict):
        return None
    if inputs.get("skip_first_frames") is None and inputs.get("frame_load_cap") is None:
        return None
    out: Dict[str, int] = {"skip_first_frames": 0, "frame_load_cap": 0}
    try:
        if inputs.get("skip_first_frames") is not None:
            out["skip_first_frames"] = max(0, int(inputs["skip_first_frames"]))
    except (TypeError, ValueError):
        pass
    try:
        if inputs.get("frame_load_cap") is not None:
            out["frame_load_cap"] = max(0, int(inputs["frame_load_cap"]))
    except (TypeError, ValueError):
        pass
    return out


def _applied_vhs_window_from_prompt(prompt: Optional[Dict[str, Any]]) -> Optional[Dict[str, int]]:
    """First VHS video-loader skip/cap from a Comfy API prompt."""
    if not isinstance(prompt, dict):
        return None
    for node in prompt.values():
        if not isinstance(node, dict):
            continue
        if str(node.get("class_type") or "") not in _VHS_LOAD_CLASS_TYPES:
            continue
        win = _vhs_window_from_inputs(node.get("inputs"))
        if win is not None:
            return win
        # Node present but no skip/cap keys — still report zeros so callers know a loader exists.
        return {"skip_first_frames": 0, "frame_load_cap": 0}
    return None


def _applied_vhs_window_from_workflow(workflow: Optional[Dict[str, Any]]) -> Optional[Dict[str, int]]:
    """First VHS video-loader skip/cap from a LiteGraph workflow (widgets_values)."""
    if not isinstance(workflow, dict):
        return None
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        return None
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if str(node.get("type") or "") not in _VHS_LOAD_CLASS_TYPES:
            continue
        widgets = node.get("widgets_values")
        win = _vhs_window_from_inputs(widgets)
        if win is not None:
            return win
        # Some exports stash skip/cap under videopreview.params only.
        if isinstance(widgets, dict):
            preview = widgets.get("videopreview")
            params = preview.get("params") if isinstance(preview, dict) else None
            win = _vhs_window_from_inputs(params)
            if win is not None:
                return win
        return {"skip_first_frames": 0, "frame_load_cap": 0}
    return None


def _workflow_doc_for_job(job: Dict[str, Any], job_path: Path) -> Optional[Dict[str, Any]]:
    """Load the generated LiteGraph workflow for this job when present on disk."""
    candidates: List[Path] = []
    for raw in (
        job.get("generated_workflow_path"),
        job.get("workflow_path"),
        job_path.with_name(job_path.name.replace(".job.json", ".workflow.json")),
    ):
        text = str(raw or "").strip()
        if not text:
            continue
        p = Path(text).expanduser()
        if p.is_file():
            candidates.append(p)
    sibling = job_path.with_name(job_path.name.replace(".job.json", ".workflow.json"))
    if sibling.is_file():
        candidates.insert(0, sibling)
    seen: set[str] = set()
    for path in candidates:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(doc, dict) and isinstance(doc.get("nodes"), list):
            return doc
    return None


def _applied_vhs_window_from_job(job: Dict[str, Any], job_path: Path) -> Optional[Dict[str, int]]:
    win = job.get("vhs_window") if isinstance(job.get("vhs_window"), dict) else None
    if isinstance(win, dict) and (
        win.get("skip_first_frames") is not None or win.get("frame_load_cap") is not None
    ):
        parsed = _vhs_window_from_inputs(win)
        if parsed is not None:
            return parsed
    from_prompt = _applied_vhs_window_from_prompt(_prompt_doc_for_job(job, job_path))
    if from_prompt is not None:
        return from_prompt
    return _applied_vhs_window_from_workflow(_workflow_doc_for_job(job, job_path))


def _ensure_item_media_urls(
    item: Dict[str, Any],
    *,
    data_root: Path,
    output_root: Path,
) -> Dict[str, Any]:
    """Fill missing binding url/thumb_url fields (e.g. host paths under Docker)."""
    row = dict(item)
    binds_in = row.get("bindings") if isinstance(row.get("bindings"), dict) else {}
    if binds_in:
        fixed: Dict[str, Any] = {}
        for slot, meta in binds_in.items():
            if not isinstance(meta, dict):
                continue
            # Prefer re-deriving from path so host→container remap applies.
            if meta.get("path"):
                fixed[str(slot)] = _binding_entry_from_meta(
                    str(slot), meta, data_root=data_root, output_root=output_root
                )
            else:
                fixed[str(slot)] = dict(meta)
        row["bindings"] = fixed
    parent = row.get("parent_output")
    if parent and not row.get("parent_output_thumb_url"):
        parent_rel = _relpath_under(output_root, parent)
        row["parent_output_relpath"] = parent_rel
        row["parent_output_url"] = _file_url(parent_rel)
        row["parent_output_thumb_url"] = _file_url(_thumb_rel_for_video(parent_rel))
    return row


def _source_media_from_prompt(
    prompt: Any,
    *,
    output_root: Path,
) -> Optional[Dict[str, Any]]:
    """Best-effort source video/image from a Comfy API prompt graph."""
    if not isinstance(prompt, dict):
        return None
    for node in prompt.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        for key in ("video", "image", "file_path", "path", "url"):
            raw = str(inputs.get(key) or "").strip()
            if not raw:
                continue
            low = raw.lower()
            if not low.endswith((".mp4", ".webm", ".png", ".jpg", ".jpeg", ".webp")):
                continue
            # Prompt paths often look like output/og/... or og/...
            cleaned = raw.replace("\\", "/")
            if cleaned.startswith("output/"):
                cleaned = cleaned[len("output/") :]
            rel = _relpath_under(output_root, cleaned) or _relpath_under(
                output_root, f"/workspace/output/{cleaned.lstrip('/')}"
            )
            if rel is None and not cleaned.startswith("/"):
                rel = cleaned.lstrip("/")
            if not rel:
                continue
            entry: Dict[str, Any] = {
                "path": raw,
                "basename": _basename(raw),
                "relpath": rel,
                "url": _file_url(rel),
            }
            if rel.lower().endswith(".mp4"):
                entry["thumb_url"] = _file_url(_thumb_rel_for_video(rel))
            else:
                entry["thumb_url"] = entry["url"]
            return entry
    return None

_EXPLICIT_WEIGHT_LINE = re.compile(
    r"^\(+(.+):([0-9]+(?:\.[0-9]+)?)\)+$",
    re.DOTALL,
)
_NESTED_EMPHASIS_LINE = re.compile(
    r"^(\(+)(.+)(\)+)$",
    re.DOTALL,
)
_EXPLICIT_WEIGHT_INLINE = re.compile(
    r"\(([^():]+?):([0-9]+(?:\.[0-9]+)?)\)",
)
_NESTED_EMPHASIS_INLINE = re.compile(
    r"(\(+)([^()]+)(\)+)",
)


def decode_prompt_markup(text: str) -> List[Dict[str, Any]]:
    """
    Decode A1111/Comfy attention markup into table rows.

    - ``(clause:1.8)`` → weight 1.8, plain clause text
    - ``((clause))`` → weight 1.1**depth
    - mixed lines → strip markup; weight = max emphasis found (else 1.0)
    """
    rows: List[Dict[str, Any]] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        rows.append(_decode_prompt_line(line))
    return rows


def _format_prompt_weight(weight: Any) -> str:
    try:
        w = float(weight)
    except (TypeError, ValueError):
        return "1"
    if abs(w - round(w)) < 1e-9:
        return str(int(round(w)))
    # Trim trailing zeros but keep at least one decimal when needed.
    text = f"{w:.4f}".rstrip("0").rstrip(".")
    return text or "1"


def _weight_is_unity(weight: Any) -> bool:
    try:
        return abs(float(weight) - 1.0) < 1e-6
    except (TypeError, ValueError):
        return True


def encode_prompt_markup(rows: Any) -> str:
    """
    Encode chunk rows into canonical multiline prompt text.

    - weight ≈ 1 → plain ``text`` line
    - otherwise → ``(text:weight)``
    Empty / blank texts are skipped.
    """
    lines: List[str] = []
    if not isinstance(rows, list):
        return ""
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        # Escape bare newlines inside a clause so one row stays one line.
        text = " ".join(text.splitlines()).strip()
        if not text:
            continue
        w = row.get("weight", 1.0)
        if _weight_is_unity(w):
            lines.append(text)
        else:
            lines.append(f"({text}:{_format_prompt_weight(w)})")
    return "\n".join(lines)


def _decode_prompt_line(line: str) -> Dict[str, Any]:
    m = _EXPLICIT_WEIGHT_LINE.fullmatch(line)
    if m:
        return {
            "text": m.group(1).strip(),
            "weight": float(m.group(2)),
            "raw": line,
        }

    m = _NESTED_EMPHASIS_LINE.fullmatch(line)
    if (
        m
        and len(m.group(1)) == len(m.group(3))
        and not re.search(r":[0-9]+(?:\.[0-9]+)?\s*\)\s*$", line)
    ):
        depth = len(m.group(1))
        return {
            "text": m.group(2).strip(),
            "weight": round(1.1**depth, 3),
            "raw": line,
        }

    weights: List[float] = [1.0]

    def _explicit(mo: re.Match[str]) -> str:
        weights.append(float(mo.group(2)))
        return mo.group(1)

    text = _EXPLICIT_WEIGHT_INLINE.sub(_explicit, line)

    def _nested(mo: re.Match[str]) -> str:
        left, body, right = mo.group(1), mo.group(2), mo.group(3)
        if len(left) != len(right):
            return mo.group(0)
        weights.append(1.1 ** len(left))
        return body

    prev = None
    while prev != text:
        prev = text
        text = _NESTED_EMPHASIS_INLINE.sub(_nested, text)

    return {
        "text": text.strip(),
        "weight": round(max(weights), 3),
        "raw": line,
    }


def _resolve_readable_path(
    raw_path: Any,
    *,
    data_root: Optional[Path] = None,
    output_root: Optional[Path] = None,
    workspace_root: Optional[Path] = None,
) -> Optional[Path]:
    raw = str(raw_path or "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    if p.is_file():
        return p.resolve()
    if data_root is None or output_root is None:
        return None
    try:
        from shape_factory_map import resolve_existing_path

        return resolve_existing_path(
            raw,
            output_root=output_root,
            data_root=data_root,
            workspace_root=workspace_root,
        )
    except Exception:
        return None


def _slot_rows(entries: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not isinstance(entries, list):
        return rows
    for ent in entries:
        if not isinstance(ent, dict):
            continue
        binding = ent.get("binding") if isinstance(ent.get("binding"), dict) else {}
        role = str(ent.get("role") or "").strip()
        rows.append(
            {
                "slot": ent.get("slot"),
                "role": role,
                "role_gloss": ROLE_GLOSS.get(role.upper()) or ROLE_GLOSS.get(role),
                "media": ent.get("media"),
                "binding_type": binding.get("type") or binding.get("node_type"),
                "node_id": binding.get("node_id"),
            }
        )
    return rows


def _shape_view(
    shape_path: Any,
    *,
    data_root: Optional[Path] = None,
    output_root: Optional[Path] = None,
    workspace_root: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    raw = str(shape_path or "").strip()
    if not raw:
        return None
    p = _resolve_readable_path(
        raw,
        data_root=data_root,
        output_root=output_root,
        workspace_root=workspace_root,
    )
    if p is None or not p.is_file():
        return {"path": raw, "basename": Path(raw).name, "missing": True}
    try:
        from shape_factory import load_yaml
    except ImportError:
        import yaml  # type: ignore

        def load_yaml(path: Path) -> dict:  # type: ignore
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    try:
        doc = load_yaml(p)
    except Exception as e:
        return {"path": raw, "basename": p.name, "error": str(e)}
    if not isinstance(doc, dict):
        return {"path": raw, "basename": p.name, "error": "not_a_mapping"}

    deposits_rows: List[Dict[str, Any]] = []
    deposits = doc.get("deposits")
    if isinstance(deposits, dict):
        for slot, meta in deposits.items():
            if isinstance(meta, dict):
                deposits_rows.append({"slot": slot, "to_pool": meta.get("to_pool")})
            else:
                deposits_rows.append({"slot": slot, "to_pool": meta})

    text = p.read_text(encoding="utf-8", errors="replace")
    return {
        "path": raw,
        "basename": p.name,
        "shape_id": doc.get("shape_id"),
        "family_slug": doc.get("family_slug"),
        "graph_hash": doc.get("graph_hash"),
        "primary_input": doc.get("primary_input"),
        "input_profile": doc.get("input_profile"),
        "chain_role": doc.get("chain_role"),
        "io_class": doc.get("io_class"),
        "template": doc.get("template"),
        "template_basename": _basename(doc.get("template")),
        "output_prefix_root": doc.get("output_prefix_root"),
        "requires": _slot_rows(doc.get("requires")),
        "produces": _slot_rows(doc.get("produces")),
        "deposits": deposits_rows,
        "text": text,
    }


def _prompt_excerpt(
    prompt_path: Any,
    *,
    data_root: Optional[Path] = None,
    output_root: Optional[Path] = None,
    workspace_root: Optional[Path] = None,
    max_chars: int = 280,
) -> Optional[Dict[str, Any]]:
    raw = str(prompt_path or "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    if not p.is_file() and data_root is not None and output_root is not None:
        try:
            from shape_factory_map import resolve_existing_path

            p = resolve_existing_path(
                raw,
                output_root=output_root,
                data_root=data_root,
                workspace_root=workspace_root,
            )
        except Exception:
            pass
    if not p.is_file():
        return {"path": raw, "basename": Path(raw).name, "missing": True}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"path": raw, "basename": p.name, "error": str(e)}
    if not isinstance(doc, dict):
        return {"path": raw, "basename": p.name}
    positive = str(doc.get("positive") or "")
    negative = str(doc.get("negative") or "")
    out: Dict[str, Any] = {
        "path": raw,
        "basename": p.name,
        "label": doc.get("label"),
        "positive": positive,
        "negative": negative,
        "positive_rows": decode_prompt_markup(positive),
        "negative_rows": decode_prompt_markup(negative),
        "snowflake": False,
    }
    try:
        from shape_factory_owned_prompt import prompt_content_hash

        out["content_hash"] = prompt_content_hash(positive, negative)
    except Exception:
        pass
    if positive:
        out["positive_excerpt"] = positive if len(positive) <= max_chars else positive[: max_chars - 1] + "…"
        out["positive_chars"] = len(positive)
    if negative:
        out["negative_excerpt"] = negative if len(negative) <= 120 else negative[:119] + "…"
        out["negative_chars"] = len(negative)
    return out


def _synthesize_construction(job: Dict[str, Any]) -> Dict[str, Any]:
    """Best-effort construction view for jobs that predate the construction blob."""
    existing = job.get("construction")
    if isinstance(existing, dict) and existing:
        return dict(existing)
    out: Dict[str, Any] = {}
    for key in (
        "pick_mode",
        "rating_kind",
        "disposition_entry",
        "disposition_note",
        "parent_output",
    ):
        if job.get(key) not in (None, ""):
            out[key] = job.get(key)
    # Infer a coarse step label when missing.
    if "step" not in out:
        rk = str(job.get("rating_kind") or "")
        pm = str(job.get("pick_mode") or "")
        if rk == "predicted":
            out["step"] = "predicted_derive"
        elif pm in {"derive", "extend"}:
            out["step"] = "derive"
        elif pm:
            out["step"] = pm
    return out


def _detail_rows(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flat labeled rows for the debug details panel (first-pass presentation)."""
    rows: List[Dict[str, Any]] = []

    def add(
        label: str,
        value: Any,
        *,
        json_path: Any = None,
        peek: Any = None,
        thumb_url: Any = None,
        asset_url: Any = None,
        relpath: Any = None,
    ) -> None:
        if value is None or value == "" or value == []:
            return
        if isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False, default=str)
        else:
            text = str(value)
        row: Dict[str, Any] = {"label": label, "value": text}
        jp = str(json_path or "").strip()
        if not jp and text.lower().endswith(".json"):
            # Absolute / long paths that are themselves JSON files.
            if "/" in text or "\\" in text:
                jp = text
        if jp.lower().endswith(".json"):
            row["json_path"] = jp
        pk = str(peek or "").strip()
        if pk:
            row["peek"] = pk
        tu = str(thumb_url or "").strip()
        if tu:
            row["thumb_url"] = tu
        au = str(asset_url or "").strip()
        if au:
            row["asset_url"] = au
        rp = str(relpath or "").strip().replace("\\", "/")
        if rp:
            row["relpath"] = rp
        rows.append(row)

    add("Created", item.get("created_at"))
    add("Family", item.get("family_slug"))
    add("Status", item.get("status"))
    add("Error", item.get("error"))
    add("Error node", item.get("error_node"))
    seed_val = item.get("noise_seed")
    if seed_val is None:
        c0 = item.get("construction") if isinstance(item.get("construction"), dict) else {}
        seed_val = c0.get("noise_seed") if c0.get("noise_seed") is not None else c0.get("seed")
    add("Seed", seed_val)
    seed_mode = item.get("seed_mode")
    if seed_mode is None:
        c0 = item.get("construction") if isinstance(item.get("construction"), dict) else {}
        seed_mode = c0.get("seed_mode")
    add("Seed mode", seed_mode)
    timing = item.get("timing") if isinstance(item.get("timing"), dict) else {}
    if timing:
        add("Exec", timing.get("label") or timing.get("exec_sec"))
        if timing.get("exec_sec") is not None:
            add("Exec sec", timing.get("exec_sec"))
        if timing.get("wait_sec") is not None:
            add("Queue wait sec", timing.get("wait_sec"))
        if timing.get("wall_sec") is not None:
            add("Wall sec", timing.get("wall_sec"))
        if timing.get("load_sec") is not None:
            add("Model load sec", timing.get("load_sec"))
        if timing.get("unload_to_reload_sec") is not None:
            add("Unload→reload sec", timing.get("unload_to_reload_sec"))
        if timing.get("load_models"):
            add("Models loaded", timing.get("load_models"))
        if timing.get("load_count") is not None:
            add("Model load count", timing.get("load_count"))
        if timing.get("unload_event_count") is not None:
            add("Unload events", timing.get("unload_event_count"))
        if timing.get("sec_per_frame") is not None:
            add("Sec per frame", timing.get("sec_per_frame"))
        if timing.get("frames") is not None:
            add("Workload frames", timing.get("frames"))
        if timing.get("terminal"):
            add("Exec terminal", timing.get("terminal"))
    add("Pick mode", item.get("pick_mode"))
    add("Step", item.get("step"))
    add("Rating kind", item.get("rating_kind"))
    add("Disposition", item.get("disposition_entry"))
    add("Disposition note", item.get("disposition_note"))
    c = item.get("construction") if isinstance(item.get("construction"), dict) else {}
    if c.get("frames_before") is not None or c.get("frames_after") is not None:
        add("Frames before→after", f"{c.get('frames_before')}→{c.get('frames_after')}")
    add("Derive action", c.get("derive_action"))
    add("Plan source tag", c.get("source"))
    add("Combo key", item.get("combo_key") or c.get("combo_key"))
    add("Cursor", c.get("cursor"))
    add("Appetite", c.get("appetite"))
    add("Appetite facet", c.get("appetite_facet"))
    add("Appetite value", c.get("appetite_value"))
    add("Appetite evidence", c.get("appetite_evidence"))
    add("Tag affinity", c.get("tag_affinity"))
    add("Fast track", c.get("fast_track"))
    add("Selection weight", c.get("selection_weight"))
    add("Hold axis", c.get("hold_axis"))
    add("Hold values", c.get("hold_values"))
    add("Hold candidates", c.get("hold_candidate_count"))
    add("Hold facet constrained", c.get("hold_facet_constrained"))
    add("Hold fallback", c.get("hold_fallback"))
    add("Used recent fallback", c.get("used_recent_fallback"))
    add("Derive attempts", c.get("derive_attempts"))
    add("Recipe pool size", c.get("recipe_count"))
    add("Seed count", c.get("seed_count"))
    add("Upgraded from", c.get("upgraded_from"))
    add("Parent output", item.get("parent_output") or c.get("parent_output"))
    shape = item.get("shape_profile") if isinstance(item.get("shape_profile"), dict) else {}
    has_shape_peek = bool(shape) and not shape.get("missing")
    add(
        "Shape",
        item.get("shape_id") or shape.get("shape_id"),
        peek="shape" if has_shape_peek else None,
    )
    add(
        "Shape path",
        shape.get("basename") or _basename(item.get("shape_path")) or item.get("shape_path"),
        peek="shape" if has_shape_peek else None,
        json_path=None,
    )
    add(
        "Template",
        item.get("template_basename") or item.get("template_path"),
        json_path=item.get("template_path"),
    )
    add(
        "Generated workflow",
        _basename(item.get("generated_workflow_path")) or item.get("generated_workflow_path"),
        json_path=item.get("generated_workflow_path"),
    )
    applied = item.get("applied_vhs") if isinstance(item.get("applied_vhs"), dict) else {}
    if applied:
        add("VHS skip_first_frames", applied.get("skip_first_frames"))
        add("VHS frame_load_cap", applied.get("frame_load_cap"))
    add("Graph hash", item.get("graph_hash"))
    add("Job key", item.get("job_key"))
    add("Job file", _basename(item.get("job_path")) or item.get("job_path"), json_path=item.get("job_path"))
    add(
        "Comfy prompt ID",
        item.get("prompt_id"),
    )
    add(
        "Comfy submit JSON",
        _basename(item.get("prompt_path")) or item.get("prompt_path"),
        json_path=item.get("prompt_path"),
    )
    add("Output prefix", item.get("output_prefix"))

    bindings = item.get("bindings") if isinstance(item.get("bindings"), dict) else {}
    for slot, meta in sorted(bindings.items()):
        if not isinstance(meta, dict):
            add(f"Binding · {slot}", meta)
            continue
        bits = [
            str(meta.get("basename") or _basename(meta.get("path")) or ""),
            f"type={meta.get('binding_type')}" if meta.get("binding_type") else "",
            _format_role(meta.get("role")),
        ]
        path_s = str(meta.get("path") or "")
        add(
            f"Binding · {slot}",
            " · ".join(b for b in bits if b) or path_s or slot,
            json_path=path_s if path_s.lower().endswith(".json") else None,
            thumb_url=meta.get("thumb_url"),
            asset_url=meta.get("url"),
            relpath=meta.get("relpath"),
        )

    prompt = item.get("prompt_profile")
    if isinstance(prompt, dict):
        # Catalog name vs profile JSON — full positive/negative render via peek in the UI.
        add("Prompt name", prompt.get("label"))
        add(
            "Prompt profile",
            prompt.get("basename") or prompt.get("path"),
            json_path=prompt.get("path"),
            peek="prompt",
        )

    return rows


def iter_job_paths(jobs_root: Path, *, hourly_only: bool = True) -> Iterable[Path]:
    if not jobs_root.is_dir():
        return []
    pattern = "hourly__*.job.json" if hourly_only else "*.job.json"
    return jobs_root.rglob(pattern)


def job_is_hourly_product(job: Dict[str, Any], job_path: Optional[Path] = None) -> bool:
    """
    True when this job was produced by the hourly planner.

    Uses the ``hourly__`` job_key / filename prefix — not a substring match on
    source paths (UI derivatives of hourly videos often embed ``hourly`` in the
    binding name without being hourly runs themselves).
    """
    key = str(job.get("job_key") or "").strip()
    if key.startswith("hourly__"):
        return True
    if job_path is not None:
        name = Path(job_path).name
        if name.startswith("hourly__") and (
            name.endswith(".job.json") or name.endswith(".job.json.discarded")
        ):
            return True
    return False


def _parse_created_at_ts(raw: Any) -> Optional[float]:
    """Parse job created_at / submitted_at ISO strings to a unix timestamp."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return None
    try:
        from datetime import datetime

        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return None


def _timings_sidecar_path(job_path: Path) -> Path:
    name = job_path.name
    if name.endswith(".job.json"):
        return job_path.with_name(name[: -len(".job.json")] + ".timings.json")
    return job_path.with_suffix(".timings.json")


def _merge_job_timings(job: Dict[str, Any], job_path: Path) -> Dict[str, Any]:
    """Prefer the timings sidecar when present (often richer than inline job.timings)."""
    inline = job.get("timings") if isinstance(job.get("timings"), dict) else {}
    side_path = _timings_sidecar_path(job_path)
    if not side_path.is_file():
        return dict(inline)
    try:
        side = json.loads(side_path.read_text(encoding="utf-8"))
    except Exception:
        return dict(inline)
    if not isinstance(side, dict):
        return dict(inline)
    # Sidecar wins for overlapping keys; fill gaps from inline.
    out = dict(side)
    for key, value in inline.items():
        if key not in out or out.get(key) in (None, {}, []):
            out[key] = value
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            merged = dict(value)
            merged.update(out[key])
            out[key] = merged
    return out


def _timing_summary(timings: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Compact timing payload for the work-products UI."""
    if not isinstance(timings, dict) or not timings:
        return None
    execution = timings.get("execution") if isinstance(timings.get("execution"), dict) else {}
    queue = timings.get("queue") if isinstance(timings.get("queue"), dict) else {}
    totals = timings.get("totals") if isinstance(timings.get("totals"), dict) else {}
    efficiency = timings.get("efficiency") if isinstance(timings.get("efficiency"), dict) else {}
    workload = timings.get("workload") if isinstance(timings.get("workload"), dict) else {}
    models = timings.get("models") if isinstance(timings.get("models"), dict) else {}
    model_totals = models.get("totals") if isinstance(models.get("totals"), dict) else {}

    def _f(v: Any) -> Optional[float]:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        return None

    exec_sec = _f(execution.get("sec"))
    wait_sec = _f(queue.get("wait_sec"))
    wall_sec = _f(totals.get("submit_to_complete_sec"))
    load_sec = _f(model_totals.get("load_sec"))
    unload_to_reload_sec = _f(model_totals.get("unload_to_reload_sec"))
    frames = workload.get("frames")
    try:
        frames_i = int(frames) if frames is not None else None
    except (TypeError, ValueError):
        frames_i = None
    steps = workload.get("steps")
    try:
        steps_i = int(steps) if steps is not None else None
    except (TypeError, ValueError):
        steps_i = None
    overlap = workload.get("overlap")
    try:
        overlap_i = int(overlap) if overlap is not None else None
        if overlap_i is not None and overlap_i < 0:
            overlap_i = None
    except (TypeError, ValueError):
        overlap_i = None
    terminal = execution.get("terminal")
    if terminal is not None:
        terminal = str(terminal)
    err = bool(execution.get("error")) or (
        str(terminal or "").lower() in {"error", "interrupted"}
    )
    sec_per_frame = _f(efficiency.get("exec_sec_per_frame")) if not err else None

    if (
        exec_sec is None
        and wait_sec is None
        and wall_sec is None
        and frames_i is None
        and load_sec is None
        and unload_to_reload_sec is None
    ):
        return None

    def _fmt_dur(sec: float, suffix: str) -> str:
        if sec < 90:
            return f"{sec:.0f}s {suffix}"
        if sec < 3600:
            return f"{sec / 60:.1f}m {suffix}"
        return f"{sec / 3600:.2f}h {suffix}"

    # Human label for chips / headers.
    parts: List[str] = []
    if exec_sec is not None:
        parts.append(_fmt_dur(exec_sec, "exec"))
    if wait_sec is not None and wait_sec >= 1:
        parts.append(_fmt_dur(wait_sec, "queue"))
    if load_sec is not None and load_sec >= 0.5:
        parts.append(_fmt_dur(load_sec, "load"))
    if unload_to_reload_sec is not None and unload_to_reload_sec >= 0.5:
        parts.append(_fmt_dur(unload_to_reload_sec, "unload→reload"))
    if sec_per_frame is not None and sec_per_frame > 0.05:
        parts.append(f"{sec_per_frame:.1f}s/frame")

    load_names: List[str] = []
    for row in models.get("loads") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if name and name not in load_names:
            load_names.append(name)

    return {
        "exec_sec": exec_sec,
        "wait_sec": wait_sec,
        "wall_sec": wall_sec,
        "load_sec": load_sec,
        "unload_to_reload_sec": unload_to_reload_sec,
        "load_count": model_totals.get("load_count"),
        "unload_event_count": model_totals.get("unload_event_count"),
        "load_models": load_names or None,
        "frames": frames_i,
        "steps": steps_i,
        "overlap": overlap_i,
        "sec_per_frame": sec_per_frame,
        "terminal": terminal,
        "error": err or None,
        "source": execution.get("source"),
        "label": " · ".join(parts) if parts else None,
    }


def _job_recency_ts(path: Path) -> float:
    """Prefer job created_at over file mtime (backfills/rewrites inflate mtime)."""
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        job = None
    if isinstance(job, dict):
        ts = _parse_created_at_ts(job.get("created_at"))
        if ts is not None:
            return ts
        submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
        ts = _parse_created_at_ts(submit.get("submitted_at"))
        if ts is not None:
            return ts
    try:
        return float(path.stat().st_mtime)
    except Exception:
        return 0.0


def _work_product_item_from_job(
    path: Path,
    job: Dict[str, Any],
    *,
    data_root: Path,
    output_root: Path,
    work_items_doc: Any = None,
    work_items_for_item: Any = None,
    status_override: Optional[str] = None,
    live_from_comfy: bool = False,
) -> Dict[str, Any]:
    """Build a full work-product row from a job file (shared by list + live-attach)."""
    data_root = data_root.resolve()
    output_root = output_root.resolve()
    fam = str(job.get("family_slug") or path.parent.name or "")

    submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    deposit = job.get("deposit") if isinstance(job.get("deposit"), dict) else {}
    outputs_abs: List[str] = []
    for src in (submit.get("outputs"), deposit.get("videos")):
        if isinstance(src, list):
            for x in src:
                s = str(x or "").strip()
                if s and s not in outputs_abs:
                    outputs_abs.append(s)

    output_rel = _keeper_output_rel(outputs_abs, output_root=output_root, job=job)
    # Fall back to output_prefix guess when submit hasn't recorded outputs yet.
    if not output_rel:
        prefix = str(job.get("output_prefix") or "").strip().replace("\\", "/")
        if prefix:
            cands = [
                output_root / f"{prefix}_FINAL_00001.mp4",
                output_root / f"{prefix}_00002.mp4",
                output_root / f"{prefix}_00001.mp4",
            ]
            stem = Path(prefix).name
            parent = (output_root / Path(prefix).parent).resolve()
            if parent.is_dir():
                cands.extend(sorted(parent.glob(f"{stem}*.mp4")))
            existing = [p for p in cands if p.is_file()]
            output_rel = _keeper_output_rel(
                [str(p) for p in existing], output_root=output_root, job=job
            )

    thumb_rel = _thumb_rel_for_video(output_rel)

    bindings_out = _bindings_from_job(job, data_root=data_root, output_root=output_root)
    prompt_profile = None
    try:
        from shape_factory_owned_prompt import (
            ensure_owned_prompt_from_bindings,
            get_owned_prompt,
            owned_prompt_to_excerpt,
        )

        owned = get_owned_prompt(job) or ensure_owned_prompt_from_bindings(
            job, data_root=data_root
        )
        if owned is not None:
            prompt_profile = owned_prompt_to_excerpt(owned, data_root=data_root)
    except Exception:
        prompt_profile = None
    if prompt_profile is None:
        for slot, entry in bindings_out.items():
            if slot == "prompt_profile":
                prompt_profile = _prompt_excerpt(
                    entry.get("path"),
                    data_root=data_root,
                    output_root=output_root,
                    workspace_root=output_root.parent,
                )
                break

    params_profile = None
    try:
        from shape_factory_owned_params import owned_params_to_profile

        params_profile = owned_params_to_profile(job, data_root=data_root, job_path=path)
    except Exception:
        params_profile = None

    construction = _synthesize_construction(job)
    status = str(
        status_override
        or submit.get("status")
        or ("deposited" if deposit else "pending")
    )
    error_text = str(submit.get("error") or "").strip() or None
    comfy_err = submit.get("comfy_error") if isinstance(submit.get("comfy_error"), dict) else None
    if comfy_err:
        try:
            from shape_factory import format_history_error_text

            full = format_history_error_text(comfy_err)
            if full and (not error_text or len(full) > len(error_text)):
                error_text = full
        except Exception:
            pass
    if not error_text and str(status).lower() == "interrupted":
        error_text = str(submit.get("interrupted_reason") or "").strip() or None
    parent_output = job.get("parent_output") or construction.get("parent_output")
    parent_rel = _relpath_under(output_root, parent_output)
    parent_url = _file_url(parent_rel)
    parent_thumb = _file_url(_thumb_rel_for_video(parent_rel))
    # Queued/live jobs often lack parent_output — use source still/video as stand-in.
    # Still-source shapes (BounceDanceA, FB8*, …) bind `source_still`, not `source_image`.
    if not parent_rel:
        src = (
            bindings_out.get("source_video")
            or bindings_out.get("source_image")
            or bindings_out.get("source_still")
            or bindings_out.get("identity_still")
            or bindings_out.get("identity_anchor")
            or bindings_out.get("start_image")
            or {}
        )
        if isinstance(src, dict) and (src.get("relpath") or src.get("url") or src.get("thumb_url")):
            parent_output = parent_output or src.get("path")
            parent_rel = src.get("relpath")
            parent_url = src.get("url")
            parent_thumb = src.get("thumb_url")

    shape_profile = _shape_view(
        job.get("shape_path"),
        data_root=data_root,
        output_root=output_root,
        workspace_root=output_root.parent,
    )

    # Media meta for trim UI (fps / frame_count / duration). Prefer job probes; fall back later in UI.
    media_meta: Dict[str, Any] = {}
    timings = _merge_job_timings(job, path)
    probes = []
    outs = timings.get("outputs") if isinstance(timings.get("outputs"), dict) else {}
    if isinstance(outs.get("probes"), list):
        probes = outs["probes"]
    fps = None
    frame_count = None
    duration = None
    for probe_row in probes:
        if not isinstance(probe_row, dict):
            continue
        probe = probe_row.get("probe") if isinstance(probe_row.get("probe"), dict) else {}
        if probe.get("avg_frame_rate") and fps is None:
            try:
                from shape_factory_queue import parse_avg_frame_rate

                fps = parse_avg_frame_rate(probe.get("avg_frame_rate"))
            except Exception:
                fps = None
        if probe.get("frame_count") is not None and frame_count is None:
            try:
                frame_count = int(probe["frame_count"])
            except (TypeError, ValueError):
                pass
        if probe.get("duration") is not None and duration is None:
            try:
                duration = float(probe["duration"])
            except (TypeError, ValueError):
                pass
    workload = timings.get("workload") if isinstance(timings.get("workload"), dict) else {}
    if frame_count is None and workload.get("output_frame_count") is not None:
        try:
            frame_count = int(workload["output_frame_count"])
        except (TypeError, ValueError):
            pass
    if fps or frame_count or duration:
        media_meta = {
            "fps": fps or 18.0,
            "frame_count": frame_count,
            "duration": duration,
        }

    timing = _timing_summary(timings)

    applied_vhs = _applied_vhs_window_from_job(job, path)

    noise_seed = None
    try:
        from shape_factory_queue import extract_job_noise_seed

        noise_seed = extract_job_noise_seed(job, path)
    except Exception:
        noise_seed = None
    seed_mode = None
    if isinstance(construction, dict):
        seed_mode = construction.get("seed_mode")
        if noise_seed is None:
            for key in ("noise_seed", "seed", "used_seed"):
                raw = construction.get(key)
                if isinstance(raw, bool):
                    continue
                if isinstance(raw, int):
                    noise_seed = int(raw)
                    break
                if isinstance(raw, float) and float(raw).is_integer():
                    noise_seed = int(raw)
                    break

    item: Dict[str, Any] = {
        "job_key": job.get("job_key") or path.stem.replace(".job", ""),
        "job_path": str(path),
        "family_slug": fam,
        "created_at": job.get("created_at"),
        "pick_mode": job.get("pick_mode"),
        "pick_index": job.get("pick_index"),
        "is_hourly": job_is_hourly_product(job, path),
        "rating_kind": job.get("rating_kind") or construction.get("rating_kind"),
        "disposition_entry": job.get("disposition_entry")
        or (deposit.get("disposition") or {}).get("entry")
        or construction.get("disposition_entry"),
        "disposition_note": job.get("disposition_note") or construction.get("disposition_note"),
        "step": construction.get("step"),
        "combo_key": construction.get("combo_key"),
        "parent_output": parent_output,
        "parent_output_relpath": parent_rel,
        "parent_output_url": parent_url,
        "parent_output_thumb_url": parent_thumb,
        "shape_id": job.get("shape_id"),
        "shape_path": job.get("shape_path"),
        "template_path": job.get("template_path"),
        "template_basename": _basename(job.get("template_path")),
        "generated_workflow_path": job.get("generated_workflow_path"),
        "prompt_path": submit.get("prompt_path"),
        "graph_hash": job.get("graph_hash"),
        "output_prefix": job.get("output_prefix"),
        "status": status,
        "flow_state": normalize_flow_status(status),
        "flow_phase": flow_phase(status),
        "remediation_actions": list(remediation_actions(status, prompt_id=submit.get("prompt_id"))),
        "flow_events": submit.get("flow_events") if isinstance(submit.get("flow_events"), list) else [],
        "prompt_id": submit.get("prompt_id"),
        "submitted_at": submit.get("submitted_at"),
        "deposited_at": deposit.get("deposited_at"),
        "error": error_text,
        "error_node": submit.get("error_node"),
        "error_type": submit.get("error_type"),
        "comfy_error": comfy_err,
        "output_relpath": output_rel,
        "output_url": _file_url(output_rel),
        "output_thumb_url": _file_url(thumb_rel),
        "bindings": bindings_out,
        "prompt_profile": prompt_profile,
        "params_profile": params_profile,
        "shape_profile": shape_profile,
        "media_meta": media_meta or None,
        "timing": timing,
        "applied_vhs": applied_vhs,
        "noise_seed": noise_seed,
        "seed_mode": seed_mode,
        "construction": construction,
        "warnings": job.get("warnings") or [],
    }
    if live_from_comfy:
        item["live_from_comfy"] = True
    if work_items_doc is not None and work_items_for_item is not None and output_rel:
        try:
            enrich = work_items_for_item({"relpath": output_rel}, work_items_doc)
            item.update(enrich)
        except Exception:
            pass
    item["details"] = _detail_rows(item)
    return item


def list_recent_work_products(
    *,
    data_root: Path,
    output_root: Path,
    limit: int = 40,
    hourly_only: bool = True,
    family: Optional[str] = None,
) -> Dict[str, Any]:
    """
    List recent factory jobs as work products with viewer URLs + construction details.

    Prefer jobs that have outputs; still include queued/incomplete so the pipeline
    can be inspected mid-flight.
    """
    data_root = data_root.resolve()
    output_root = output_root.resolve()
    jobs_root = data_root / "shape_factory" / "jobs"
    limit = max(1, min(200, int(limit)))

    paths = list(iter_job_paths(jobs_root, hourly_only=hourly_only))
    # Newest-first by job created_at (not file mtime — deposit/backfill rewrites bump mtime).
    paths.sort(key=_job_recency_ts, reverse=True)

    work_items_doc = None
    work_items_for_item = None
    try:
        from shape_factory_work_items import (  # type: ignore
            default_work_items_index_path,
            load_work_items_doc,
            work_items_for_item as _work_items_for_item,
        )

        wi_path = output_root / "_status" / "work_items_index.json"
        if not wi_path.is_file():
            # Fallback: parent of og/ when output_root itself is the library root.
            og = output_root / "og"
            if og.is_dir():
                wi_path = default_work_items_index_path(og)
        if wi_path.is_file():
            work_items_doc = load_work_items_doc(wi_path)
            work_items_for_item = _work_items_for_item
    except Exception:
        work_items_doc = None
        work_items_for_item = None

    items: List[Dict[str, Any]] = []
    for path in paths:
        if len(items) >= limit:
            break
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(job, dict):
            continue
        fam = str(job.get("family_slug") or path.parent.name or "")
        if family and fam != family:
            continue
        items.append(
            _work_product_item_from_job(
                path,
                job,
                data_root=data_root,
                output_root=output_root,
                work_items_doc=work_items_doc,
                work_items_for_item=work_items_for_item,
            )
        )

    families = list_shape_families(
        data_root,
        workspace_root=output_root.parent,
        output_root=output_root,
    )
    extend_family_defaults = list_extend_family_defaults(data_root)

    try:
        from shape_factory_markers import attach_markers_to_work_products

        attach_markers_to_work_products(items, output_root=output_root)
    except Exception:
        for it in items:
            if isinstance(it, dict):
                it.setdefault("markers", {})

    return {
        "ok": True,
        "schema_version": "comfyui-runpod.work-products.v0",
        "data_root": str(data_root),
        "jobs_root": str(jobs_root),
        "hourly_only": bool(hourly_only),
        "family": family,
        "limit": limit,
        "count": len(items),
        "families": families,
        "extend_family_defaults": extend_family_defaults,
        "items": items,
    }


def _comfy_queue_entries(queue_rows: Any, *, status: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(queue_rows, list):
        return out
    for row in queue_rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        pid = row[1]
        if not isinstance(pid, str) or not pid.strip():
            continue
        prompt = row[2] if len(row) >= 3 and isinstance(row[2], dict) else None
        extra = row[3] if len(row) >= 4 and isinstance(row[3], dict) else None
        job_key = _job_key_from_comfy_extra(extra) or _job_key_from_filename_prefix(prompt)
        entry: Dict[str, Any] = {"prompt_id": pid.strip(), "status": status, "prompt": prompt}
        if isinstance(extra, dict):
            entry["extra_data"] = extra
        if job_key:
            entry["job_key"] = job_key
        out.append(entry)
    return out


def _job_key_from_comfy_extra(extra: Any) -> Optional[str]:
    """Factory submits set workflow_name / name to the job_key (survives ledger restore)."""
    if not isinstance(extra, dict):
        return None
    for k in ("workflow_name", "name", "filename"):
        raw = str(extra.get(k) or "").strip()
        if raw.endswith(".json"):
            raw = raw[: -len(".json")]
        key = _factory_job_key_heuristic(raw)
        if key:
            return key
    png = extra.get("extra_pnginfo") if isinstance(extra.get("extra_pnginfo"), dict) else {}
    wf = png.get("workflow") if isinstance(png.get("workflow"), dict) else {}
    return _factory_job_key_heuristic(str(wf.get("name") or "").strip())


def _factory_job_key_heuristic(name: str) -> Optional[str]:
    text = str(name or "").strip()
    if not text:
        return None
    if text.startswith("client:") or text.startswith("graph ("):
        return None
    if "__" in text or text.startswith("hourly"):
        return text
    return None


def _job_key_from_filename_prefix(prompt: Any) -> Optional[str]:
    """Best-effort: output filename_prefix often ends with the job_key basename."""
    prefix = _filename_prefix_from_prompt(prompt)
    if not prefix:
        return None
    base = Path(str(prefix).replace("\\", "/")).name.strip()
    return _factory_job_key_heuristic(base)


def _live_queue_by_job_key(
    queue_running: Any = None,
    queue_pending: Any = None,
) -> Dict[str, Dict[str, Any]]:
    """Map factory job_key → live Comfy queue entry (running preferred over pending)."""
    out: Dict[str, Dict[str, Any]] = {}
    for ent in _comfy_queue_entries(queue_pending, status="queued") + _comfy_queue_entries(
        queue_running, status="running"
    ):
        key = str(ent.get("job_key") or "").strip()
        if key:
            out[key] = ent
    return out


def _rebind_job_prompt_id_to_live(
    job: Dict[str, Any],
    *,
    live_prompt_id: str,
    live_status: str,
) -> bool:
    """
    Point ``job['submit']`` at a recovered Comfy prompt_id (ledger restore assigns a new id).

    Returns True when the job document changed.
    """
    submit = job.get("submit") if isinstance(job.get("submit"), dict) else None
    if submit is None:
        return False
    new_pid = str(live_prompt_id or "").strip()
    if not new_pid:
        return False
    old_pid = str(submit.get("prompt_id") or "").strip()
    before = str(submit.get("status") or "").strip().lower()
    target = str(live_status or "").strip().lower() or "queued"
    if target not in {"queued", "running"}:
        target = "queued"
    changed = False
    if old_pid != new_pid:
        if old_pid:
            submit["previous_prompt_id"] = old_pid
        submit["prompt_id"] = new_pid
        submit["prompt_id_rebound_at"] = _utc_now_iso()
        submit["prompt_id_rebound_reason"] = "matched_live_queue_by_job_key"
        changed = True
    if before != target or before == "interrupted":
        submit["status"] = target
        changed = True
    # Clear stale interrupt markers once we know Comfy still has the work.
    for k in ("interrupted_at", "interrupted_reason"):
        if k in submit:
            submit.pop(k, None)
            changed = True
    job["submit"] = submit
    return changed


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _filename_prefix_from_prompt(prompt: Any) -> str:
    if not isinstance(prompt, dict):
        return ""
    best = ""
    best_score = -1
    for node in prompt.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        raw = str(inputs.get("filename_prefix") or "").strip()
        if not raw:
            continue
        score = 0
        if inputs.get("save_output") is True:
            score += 10
        norm = raw.replace("\\", "/")
        if "/og/" in f"/{norm}/" or norm.startswith("og/"):
            score += 5
        if "PREVIEW" in raw.upper():
            score -= 3
        score += min(len(raw), 40) / 40.0
        if score > best_score:
            best_score = score
            best = raw
    return best


def _family_from_output_prefix(prefix: str, family_slugs: Iterable[str]) -> str:
    name = Path(str(prefix or "").replace("\\", "/")).name
    blob = str(prefix or "")
    upper = f"{name} {blob}".upper()
    # Strong cues first — filenames often use FB9_GEX2_FACIAL even when family is FB9_GEX_FACIAL.
    if "FACIAL" in upper:
        for slug in family_slugs:
            if slug and "FACIAL" in str(slug).upper():
                return str(slug)
        return "FB9_GEX_FACIAL"
    # Prefer longest known slug match.
    hit = ""
    for slug in sorted((str(s or "") for s in family_slugs), key=len, reverse=True):
        if slug and (slug in name or slug in blob):
            hit = slug
            break
    if hit:
        return hit
    if "GEX2" in upper:
        return "FB9_GEX2"
    if "FACEBLAST" in upper:
        return "FB9-FaceBlast"
    if "GEX" in upper:
        return "FB9_GEX"
    return ""


def _find_job_by_prompt_id(jobs_root: Path, prompt_id: str) -> Tuple[Optional[Path], Optional[Dict[str, Any]]]:
    pid = str(prompt_id or "").strip()
    if not pid or not jobs_root.is_dir():
        return None, None
    for path in jobs_root.rglob("*.job.json"):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        if pid not in text:
            continue
        try:
            job = json.loads(text)
        except Exception:
            continue
        if not isinstance(job, dict):
            continue
        submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
        if str(submit.get("prompt_id") or "").strip() == pid:
            return path, job
    return None, None


def _synthetic_live_work_product(
    *,
    prompt_id: str,
    status: str,
    prompt: Any,
    family_slugs: Iterable[str],
    output_root: Optional[Path] = None,
) -> Dict[str, Any]:
    prefix = _filename_prefix_from_prompt(prompt)
    family = _family_from_output_prefix(prefix, family_slugs)
    short = prompt_id[:12]
    item: Dict[str, Any] = {
        "job_key": f"live__{short}",
        "family_slug": family or None,
        "created_at": None,
        "status": status,
        "prompt_id": prompt_id,
        "output_prefix": prefix or None,
        "output_relpath": None,
        "output_url": None,
        "output_thumb_url": None,
        "live_from_comfy": True,
        "construction": {"step": "live", "source": "comfy_queue"},
        "bindings": {},
    }
    applied_vhs = _applied_vhs_window_from_prompt(prompt if isinstance(prompt, dict) else None)
    if applied_vhs is not None:
        item["applied_vhs"] = applied_vhs
    if output_root is not None:
        src = _source_media_from_prompt(prompt, output_root=output_root)
        if src:
            slot = "source_video" if str(src.get("relpath") or "").lower().endswith(".mp4") else "source_image"
            item["bindings"] = {slot: src}
            item["parent_output"] = src.get("path")
            item["parent_output_relpath"] = src.get("relpath")
            item["parent_output_url"] = src.get("url")
            item["parent_output_thumb_url"] = src.get("thumb_url")
    item["details"] = _detail_rows(item)
    return item


# Statuses that mean "should still be on Comfy /queue" until proven otherwise.
IN_FLIGHT_STATUSES: frozenset[str] = frozenset(
    {"queued", "running", "submitted", "unknown"}
)


def _queue_prompt_id_sets(
    queue_running: Any = None,
    queue_pending: Any = None,
) -> Tuple[set[str], set[str]]:
    running = {e["prompt_id"] for e in _comfy_queue_entries(queue_running, status="running")}
    pending = {e["prompt_id"] for e in _comfy_queue_entries(queue_pending, status="queued")}
    return running, pending


def reconcile_inflight_jobs_with_comfy(
    *,
    data_root: Path,
    comfy_server: str,
    queue_running: Any = None,
    queue_pending: Any = None,
    persist: bool = True,
    repo_root: Optional[Path] = None,
    workspace_root: Optional[Path] = None,
    output_root: Optional[Path] = None,
    auto_retry_oom: bool = True,
) -> Dict[str, Any]:
    """
    Align factory ``job.json`` submit statuses with Comfy ``/queue`` (+ history).

    Comfy is canonical for queued/running. Jobs that claim in-flight but are gone
    from both queue and history are marked interrupted (or complete/error via history).

    When ``auto_retry_oom`` is true, extend jobs that fail with Comfy OOM spawn one
    shorter extend replay (see ``maybe_auto_retry_oom_extend``).
    """
    # Local import: shape_factory pulls heavier deps; keep work_products import light.
    from shape_factory import (  # type: ignore
        atomic_write_json,
        queue_prompt_id_buckets,
        update_job_status_from_comfy,
    )

    data_root = Path(data_root).expanduser().resolve()
    server = str(comfy_server or "").rstrip("/")
    live_by_key = _live_queue_by_job_key(queue_running, queue_pending)
    if queue_running is None and queue_pending is None:
        if not server:
            return {"ok": False, "error": "missing_comfy_server", "checked": 0, "updated": 0}
        running_ids, pending_ids = queue_prompt_id_buckets(server)
    else:
        running_ids, pending_ids = _queue_prompt_id_sets(queue_running, queue_pending)

    jobs_root = data_root / "shape_factory" / "jobs"
    summary: Dict[str, Any] = {
        "ok": True,
        "checked": 0,
        "updated": 0,
        "rebound": 0,
        "running_ids": len(running_ids),
        "pending_ids": len(pending_ids),
        "by_status": {},
        "oom_retries": 0,
    }
    if not jobs_root.is_dir():
        return summary

    # Paths for optional OOM auto-retry spawning.
    rr = Path(repo_root).expanduser().resolve() if repo_root else data_root.parent
    wr = Path(workspace_root).expanduser().resolve() if workspace_root else (
        rr / "workspace" if (rr / "workspace").is_dir() else rr
    )
    if output_root is not None:
        oroot = Path(output_root).expanduser().resolve()
    else:
        env_out = os.environ.get("COMFYUI_BIND_OUTPUT_DIR", "").strip()
        oroot = Path(env_out).expanduser().resolve() if env_out else (wr / "output")

    by_status: Dict[str, int] = {}
    for path in jobs_root.glob("*/*.job.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(job, dict):
            continue
        submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
        if not submit:
            continue
        before = str(submit.get("status") or "").strip().lower()
        prompt_id = str(submit.get("prompt_id") or "").strip()
        job_key = str(job.get("job_key") or path.stem.replace(".job", "")).strip()
        live_ent = live_by_key.get(job_key) if job_key else None
        rebound = False
        if isinstance(live_ent, dict):
            live_pid = str(live_ent.get("prompt_id") or "").strip()
            live_st = str(live_ent.get("status") or "").strip().lower()
            # Ledger restore after Comfy restart assigns a new prompt_id; rebind by job_key.
            if live_pid and (live_pid != prompt_id or before == "interrupted"):
                rebound = _rebind_job_prompt_id_to_live(
                    job,
                    live_prompt_id=live_pid,
                    live_status=live_st or "queued",
                )
                if rebound:
                    submit = job.get("submit") if isinstance(job.get("submit"), dict) else submit
                    prompt_id = str(submit.get("prompt_id") or "").strip()
                    before = str(submit.get("status") or "").strip().lower()
                    summary["rebound"] = int(summary.get("rebound") or 0) + 1

        in_comfy = bool(prompt_id) and (prompt_id in running_ids or prompt_id in pending_ids)
        needs_error_backfill = (
            before == "error"
            and bool(prompt_id)
            and not str(submit.get("error") or "").strip()
            and not in_comfy
        )
        if not rebound and not in_comfy and before not in IN_FLIGHT_STATUSES and not needs_error_backfill:
            continue
        if not prompt_id:
            continue

        summary["checked"] = int(summary["checked"]) + 1
        new_status = update_job_status_from_comfy(
            job,
            server=server or "http://127.0.0.1:8188",
            data_root=data_root,
            running_ids=running_ids,
            pending_ids=pending_ids,
        )
        after = str((job.get("submit") or {}).get("status") or new_status).strip().lower()
        by_status[after] = by_status.get(after, 0) + 1
        after_submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
        error_filled = needs_error_backfill and bool(str(after_submit.get("error") or "").strip())
        if after != before or error_filled or rebound:
            summary["updated"] = int(summary["updated"]) + 1
            if error_filled:
                summary["errors_backfilled"] = int(summary.get("errors_backfilled") or 0) + 1
            if persist:
                try:
                    atomic_write_json(path, job)
                except OSError:
                    summary["ok"] = False
                    summary["write_error"] = str(path)

        if auto_retry_oom and after == "error" and server:
            try:
                from shape_factory_queue import maybe_auto_retry_oom_extend  # type: ignore

                retry_out = maybe_auto_retry_oom_extend(
                    job,
                    path,
                    repo_root=rr,
                    workspace_root=wr,
                    output_root=oroot,
                    comfy_server=server,
                    persist=persist,
                )
                if isinstance(retry_out, dict) and retry_out.get("ok") and retry_out.get("oom_auto_retry"):
                    summary["oom_retries"] = int(summary.get("oom_retries") or 0) + 1
                    spawned = summary.setdefault("oom_retry_jobs", [])
                    if isinstance(spawned, list):
                        spawned.append(
                            {
                                "from": job.get("job_key"),
                                "to": retry_out.get("job_key"),
                                "frames_after": retry_out.get("frames_after"),
                            }
                        )
            except Exception as e:
                summary.setdefault("oom_retry_errors", []).append(
                    {"job_key": job.get("job_key"), "detail": str(e)[:240]}
                )
    summary["by_status"] = by_status
    return summary


def demote_stale_inflight_items(
    payload: Dict[str, Any],
    *,
    queue_running: Any = None,
    queue_pending: Any = None,
) -> Dict[str, Any]:
    """
    Display-layer safety: items that claim queued/running but are not on Comfy
    ``/queue`` are demoted so the UI never shows ghost live rows.
    """
    if not isinstance(payload, dict) or not payload.get("ok"):
        return payload
    running_ids, pending_ids = _queue_prompt_id_sets(queue_running, queue_pending)
    live = running_ids | pending_ids
    demoted = 0
    items = list(payload.get("items") or [])
    for it in items:
        if not isinstance(it, dict):
            continue
        if it.get("live_from_comfy"):
            continue
        pid = str(it.get("prompt_id") or "").strip()
        st = str(it.get("status") or "").strip().lower()
        if st not in IN_FLIGHT_STATUSES:
            continue
        if pid and pid in live:
            continue
        # Prefer interrupted when we know it left the queue; keep pending if never submitted.
        it["status"] = "interrupted" if pid else "pending"
        it["live_from_comfy"] = False
        demoted += 1
    payload["items"] = items
    payload["comfy_demoted_stale"] = demoted
    return payload


def attach_live_comfy_queue(
    payload: Dict[str, Any],
    *,
    queue_running: Any = None,
    queue_pending: Any = None,
    data_root: Optional[Path] = None,
    output_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Ensure Comfy running/pending prompts appear at the top of work-products.

    Matches existing items by ``prompt_id``, then by factory ``job_key`` (from
    Comfy ``workflow_name`` — needed after ledger restore assigns a new prompt id).
    When no factory job is in the current page, tries to locate the job file
    globally or synthesizes a live stub so the UI can show the latent preview.
    """
    if not isinstance(payload, dict) or not payload.get("ok"):
        return payload
    entries = _comfy_queue_entries(queue_running, status="running") + _comfy_queue_entries(
        queue_pending, status="queued"
    )
    if not entries:
        payload["live_comfy_count"] = 0
        return payload

    items = list(payload.get("items") or [])
    by_pid: Dict[str, int] = {}
    by_job_key: Dict[str, int] = {}
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        pid = str(it.get("prompt_id") or "").strip()
        if pid and pid not in by_pid:
            by_pid[pid] = i
        jk = str(it.get("job_key") or "").strip()
        if jk and jk not in by_job_key:
            by_job_key[jk] = i

    family_slugs = [str(f.get("slug") or "") for f in (payload.get("families") or []) if isinstance(f, dict)]
    jobs_root = Path(data_root) / "shape_factory" / "jobs" if data_root else None
    out_root = Path(output_root).resolve() if output_root else None
    data_r = Path(data_root).resolve() if data_root else None
    live_items: List[Dict[str, Any]] = []
    used_indices: set[int] = set()
    emitted_live_job_keys: set[str] = set()
    emitted_live_prompt_ids: set[str] = set()

    def _promote_on_page_row(idx: int, *, status: str, prompt_id: str, ent: Dict[str, Any]) -> Dict[str, Any]:
        used_indices.add(idx)
        row = dict(items[idx])
        row["status"] = status
        row["prompt_id"] = prompt_id
        row["live_from_comfy"] = True
        # Rebuild when the on-page row looks stripped (common for non-hourly live jobs).
        job_path_raw = str(row.get("job_path") or "").strip()
        needs_full = not row.get("graph_hash") or not row.get("shape_profile")
        if needs_full and job_path_raw and out_root is not None and data_r is not None:
            jp = Path(job_path_raw)
            if jp.is_file():
                try:
                    found_job = json.loads(jp.read_text(encoding="utf-8"))
                except Exception:
                    found_job = None
                if isinstance(found_job, dict):
                    # Keep UI in sync with the live (possibly rebound) prompt id.
                    submit = found_job.get("submit") if isinstance(found_job.get("submit"), dict) else {}
                    if str(submit.get("prompt_id") or "").strip() != prompt_id:
                        _rebind_job_prompt_id_to_live(
                            found_job, live_prompt_id=prompt_id, live_status=status
                        )
                    row = _work_product_item_from_job(
                        jp,
                        found_job,
                        data_root=data_r,
                        output_root=out_root,
                        status_override=status,
                        live_from_comfy=True,
                    )
                    row["prompt_id"] = prompt_id
        elif out_root is not None and data_r is not None:
            row = _ensure_item_media_urls(row, data_root=data_r, output_root=out_root)
        if not isinstance(row.get("applied_vhs"), dict):
            live_vhs = _applied_vhs_window_from_prompt(
                ent.get("prompt") if isinstance(ent.get("prompt"), dict) else None
            )
            if live_vhs is not None:
                row["applied_vhs"] = live_vhs
                row["details"] = _detail_rows(row)
        return row

    for ent in entries:
        pid = ent["prompt_id"]
        status = ent["status"]
        ent_job_key = str(ent.get("job_key") or "").strip()
        if pid in emitted_live_prompt_ids:
            continue
        if ent_job_key and ent_job_key in emitted_live_job_keys:
            continue

        if pid in by_pid and by_pid[pid] not in used_indices:
            row = _promote_on_page_row(by_pid[pid], status=status, prompt_id=pid, ent=ent)
            live_items.append(row)
            emitted_live_prompt_ids.add(pid)
            row_job_key = str(row.get("job_key") or "").strip()
            if row_job_key:
                emitted_live_job_keys.add(row_job_key)
            continue

        if ent_job_key and ent_job_key in by_job_key and by_job_key[ent_job_key] not in used_indices:
            row = _promote_on_page_row(by_job_key[ent_job_key], status=status, prompt_id=pid, ent=ent)
            live_items.append(row)
            emitted_live_prompt_ids.add(pid)
            row_job_key = str(row.get("job_key") or "").strip()
            if row_job_key:
                emitted_live_job_keys.add(row_job_key)
            continue

        found_path, found_job = (None, None)
        if jobs_root is not None:
            found_path, found_job = _find_job_by_prompt_id(jobs_root, pid)
            if found_path is None and ent_job_key:
                # Recovered prompt: old prompt_id on disk, new id on Comfy — match by job_key.
                for path in jobs_root.glob(f"*/{ent_job_key}.job.json"):
                    try:
                        loaded = json.loads(path.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    if isinstance(loaded, dict):
                        found_path, found_job = path, loaded
                        break
        if found_path is not None and isinstance(found_job, dict):
            if out_root is not None and data_r is not None:
                if str((found_job.get("submit") or {}).get("prompt_id") or "").strip() != pid:
                    _rebind_job_prompt_id_to_live(
                        found_job, live_prompt_id=pid, live_status=status
                    )
                row = _work_product_item_from_job(
                    found_path,
                    found_job,
                    data_root=data_r,
                    output_root=out_root,
                    status_override=status,
                    live_from_comfy=True,
                )
                row["prompt_id"] = pid
            else:
                row = {
                    "job_key": found_job.get("job_key") or found_path.stem.replace(".job", ""),
                    "job_path": str(found_path),
                    "family_slug": str(found_job.get("family_slug") or found_path.parent.name or "") or None,
                    "created_at": found_job.get("created_at"),
                    "status": status,
                    "prompt_id": pid,
                    "live_from_comfy": True,
                    "construction": _synthesize_construction(found_job),
                }
                row["details"] = _detail_rows(row)
            live_items.append(row)
            emitted_live_prompt_ids.add(pid)
            row_job_key = str(row.get("job_key") or "").strip()
            if row_job_key:
                emitted_live_job_keys.add(row_job_key)
            continue

        row = _synthetic_live_work_product(
            prompt_id=pid,
            status=status,
            prompt=ent.get("prompt"),
            family_slugs=family_slugs,
            output_root=out_root,
        )
        live_items.append(row)
        emitted_live_prompt_ids.add(pid)
        row_job_key = str(row.get("job_key") or "").strip()
        if row_job_key:
            emitted_live_job_keys.add(row_job_key)

    rest = [it for i, it in enumerate(items) if i not in used_indices]
    merged = live_items + rest
    # Keep roughly within limit + room for live rows.
    limit = int(payload.get("limit") or len(merged))
    payload["items"] = merged[: max(limit, len(live_items))]
    payload["count"] = len(payload["items"])
    payload["live_comfy_count"] = len(live_items)
    return payload


def _history_prompt_obj(record: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(record, dict):
        return None
    raw = record.get("prompt")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list) and len(raw) >= 3 and isinstance(raw[2], dict):
        return raw[2]
    return None


def _history_extra_data(record: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(record, dict):
        return None
    raw = record.get("prompt")
    if isinstance(raw, list) and len(raw) >= 4 and isinstance(raw[3], dict):
        return raw[3]
    extra = record.get("extra") or record.get("extra_data")
    return extra if isinstance(extra, dict) else None


def _history_queue_index(record: Any) -> int:
    if not isinstance(record, dict):
        return -1
    raw = record.get("prompt")
    if isinstance(raw, list) and raw and isinstance(raw[0], (int, float)):
        return int(raw[0])
    return -1


DISMISSALS_BASENAME = "work_products_dismissed.json"


def work_products_dismissals_path(data_root: Path, output_root: Optional[Path] = None) -> Path:
    """
    Prefer the canonical runtime path under ``shape_factory/``.

    Falls back to ``jobs/`` or ``output/_status/`` when the preferred parent is
    not writable (legacy container mounts that only exposed ``jobs`` RW).
    """
    data_root = Path(data_root).expanduser().resolve()
    candidates: List[Path] = [
        data_root / "shape_factory" / DISMISSALS_BASENAME,
        data_root / "shape_factory" / "jobs" / DISMISSALS_BASENAME,
    ]
    if output_root is not None:
        candidates.append(Path(output_root).expanduser().resolve() / "_status" / DISMISSALS_BASENAME)
    for path in candidates:
        parent = path.parent
        if not parent.is_dir():
            continue
        probe = parent / f".wp_dismiss_write_probe_{os.getpid()}"
        try:
            probe.write_text("ok", encoding="utf-8")
            try:
                probe.unlink(missing_ok=True)
            except TypeError:
                if probe.is_file():
                    probe.unlink()
            return path
        except OSError:
            try:
                if probe.is_file():
                    probe.unlink()
            except OSError:
                pass
            continue
    return candidates[0]


def _dismissal_read_candidates(data_root: Path, output_root: Optional[Path] = None) -> List[Path]:
    data_root = Path(data_root).expanduser().resolve()
    out: List[Path] = [
        data_root / "shape_factory" / DISMISSALS_BASENAME,
        data_root / "shape_factory" / "jobs" / DISMISSALS_BASENAME,
    ]
    if output_root is not None:
        out.append(Path(output_root).expanduser().resolve() / "_status" / DISMISSALS_BASENAME)
    return out


def load_work_products_dismissals(
    data_root: Path,
    output_root: Optional[Path] = None,
) -> Dict[str, Any]:
    empty = {"prompt_ids": [], "job_keys": [], "entries": []}
    for path in _dismissal_read_candidates(data_root, output_root):
        if not path.is_file():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        pids = [str(x).strip() for x in (doc.get("prompt_ids") or []) if str(x).strip()]
        keys = [str(x).strip() for x in (doc.get("job_keys") or []) if str(x).strip()]
        entries = [e for e in (doc.get("entries") or []) if isinstance(e, dict)]
        return {
            "prompt_ids": pids,
            "job_keys": keys,
            "entries": entries,
            "updated_at": doc.get("updated_at"),
            "path": str(path),
        }
    return empty


def save_work_products_dismissals(
    data_root: Path,
    doc: Dict[str, Any],
    output_root: Optional[Path] = None,
) -> Path:
    path = work_products_dismissals_path(data_root, output_root=output_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = {
        "schema_version": "comfyui-runpod.work-products-dismissed.v0",
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "prompt_ids": sorted(set(str(x).strip() for x in (doc.get("prompt_ids") or []) if str(x).strip())),
        "job_keys": sorted(set(str(x).strip() for x in (doc.get("job_keys") or []) if str(x).strip())),
        "entries": list(doc.get("entries") or [])[-500:],
    }
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def is_work_product_dismissed(
    dismissals: Dict[str, Any],
    *,
    prompt_id: Optional[str] = None,
    job_key: Optional[str] = None,
) -> bool:
    pid = str(prompt_id or "").strip()
    jk = str(job_key or "").strip()
    pids = set(dismissals.get("prompt_ids") or [])
    keys = set(dismissals.get("job_keys") or [])
    if pid and pid in pids:
        return True
    if jk and jk in keys:
        return True
    return False


def dismiss_history_work_product(
    *,
    data_root: Path,
    prompt_id: Optional[str] = None,
    job_key: Optional[str] = None,
    reason: str = "user_dismissed_history",
    output_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Hide a Comfy-history failure stub that has no factory ``.job.json``.

    Workbench synthesizes these from ``/history``; discard cannot rename a missing
    job file, so we persist a dismissal list and filter on the next load.
    """
    pid = str(prompt_id or "").strip() or None
    jk = str(job_key or "").strip() or None
    if not pid and not jk:
        return {"ok": False, "error": "missing_prompt_or_job_key"}
    data_root = Path(data_root).expanduser().resolve()
    out_root = Path(output_root).expanduser().resolve() if output_root else None
    doc = load_work_products_dismissals(data_root, output_root=out_root)
    pids = list(doc.get("prompt_ids") or [])
    keys = list(doc.get("job_keys") or [])
    entries = list(doc.get("entries") or [])
    if pid and pid not in pids:
        pids.append(pid)
    if jk and jk not in keys:
        keys.append(jk)
    entries.append(
        {
            "prompt_id": pid,
            "job_key": jk,
            "reason": str(reason or "user_dismissed_history"),
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )
    path = save_work_products_dismissals(
        data_root,
        {"prompt_ids": pids, "job_keys": keys, "entries": entries},
        output_root=out_root,
    )
    return {
        "ok": True,
        "dismissed": True,
        "history_stub": True,
        "prompt_id": pid,
        "job_key": jk,
        "dismissals_path": str(path),
        "reason": str(reason or "user_dismissed_history"),
    }


def attach_comfy_history_failures(
    payload: Dict[str, Any],
    *,
    history: Any,
    data_root: Optional[Path] = None,
    output_root: Optional[Path] = None,
    max_failures: int = 40,
) -> Dict[str, Any]:
    """
    Merge recent Comfy history errors/interrupts into work-products.

    Queue history shows every failed prompt; Workbench previously only listed
    factory ``.job.json`` files (+ live queue). This attaches matching factory
    jobs and synthesizes stubs for history-only failures so the lists align.
    """
    if not isinstance(payload, dict) or not payload.get("ok"):
        return payload
    if not isinstance(history, dict) or not history:
        payload["history_failure_count"] = 0
        return payload

    try:
        from shape_factory import extract_history_execution_error, format_history_error_text
    except ImportError:
        payload["history_failure_count"] = 0
        return payload

    items = list(payload.get("items") or [])
    by_pid: Dict[str, int] = {}
    by_job_key: Dict[str, int] = {}
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        pid = str(it.get("prompt_id") or "").strip()
        if pid and pid not in by_pid:
            by_pid[pid] = i
        jk = str(it.get("job_key") or "").strip()
        if jk and jk not in by_job_key:
            by_job_key[jk] = i

    family_slugs = [str(f.get("slug") or "") for f in (payload.get("families") or []) if isinstance(f, dict)]
    jobs_root = Path(data_root) / "shape_factory" / "jobs" if data_root else None
    out_root = Path(output_root).resolve() if output_root else None
    data_r = Path(data_root).resolve() if data_root else None
    dismissals = (
        load_work_products_dismissals(data_r, output_root=out_root)
        if data_r is not None
        else {"prompt_ids": [], "job_keys": []}
    )

    ordered = sorted(
        ((pid, record) for pid, record in history.items() if isinstance(pid, str) and isinstance(record, dict)),
        key=lambda kv: _history_queue_index(kv[1]),
        reverse=True,
    )

    failure_rows: List[Dict[str, Any]] = []
    touched = 0
    dismissed_skipped = 0
    for pid, record in ordered:
        if len(failure_rows) >= max(1, int(max_failures)):
            break
        err = extract_history_execution_error(record)
        if not err:
            continue
        kind = str(err.get("kind") or "")
        status = "interrupted" if kind == "execution_interrupted" else "error"
        # Skip pure successes that somehow produced an empty fallback
        st = record.get("status") if isinstance(record.get("status"), dict) else {}
        if st.get("completed") is True and kind == "status_fallback":
            continue
        error_text = format_history_error_text(err) or str(err.get("exception_message") or "").strip()
        error_node = str(err.get("node_type") or err.get("node_id") or "").strip() or None
        prompt = _history_prompt_obj(record)
        extra = _history_extra_data(record)
        job_key = _job_key_from_comfy_extra(extra) or _job_key_from_filename_prefix(prompt) or ""

        if is_work_product_dismissed(dismissals, prompt_id=pid, job_key=job_key or None):
            dismissed_skipped += 1
            continue

        def _apply_error(row: Dict[str, Any]) -> Dict[str, Any]:
            row = dict(row)
            row["status"] = status
            row["prompt_id"] = pid
            row["error"] = error_text or row.get("error")
            if error_node:
                row["error_node"] = error_node
            row["error_type"] = err.get("exception_type") or row.get("error_type")
            row["comfy_error"] = err
            row["history_from_comfy"] = True
            row["live_from_comfy"] = False
            row["details"] = _detail_rows(row)
            return row

        # Already on the page — enrich in place (don't duplicate).
        if pid in by_pid:
            idx = by_pid[pid]
            items[idx] = _apply_error(items[idx])
            touched += 1
            continue
        if job_key and job_key in by_job_key:
            idx = by_job_key[job_key]
            items[idx] = _apply_error(items[idx])
            touched += 1
            continue

        found_path, found_job = (None, None)
        if jobs_root is not None:
            found_path, found_job = _find_job_by_prompt_id(jobs_root, pid)
            if found_path is None and job_key:
                for path in jobs_root.glob(f"*/{job_key}.job.json"):
                    try:
                        loaded = json.loads(path.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    if isinstance(loaded, dict):
                        found_path, found_job = path, loaded
                        break
        if found_path is not None and isinstance(found_job, dict) and out_root is not None and data_r is not None:
            # Persist error onto submit so later scans keep the text.
            submit = found_job.get("submit") if isinstance(found_job.get("submit"), dict) else {}
            submit = dict(submit)
            submit["status"] = status
            submit["prompt_id"] = pid
            try:
                from shape_factory import apply_history_error_to_submit

                apply_history_error_to_submit(submit, record)
            except Exception:
                submit["error"] = error_text
                submit["comfy_error"] = err
            found_job = dict(found_job)
            found_job["submit"] = submit
            try:
                found_path.write_text(json.dumps(found_job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except Exception:
                pass
            row = _work_product_item_from_job(
                found_path,
                found_job,
                data_root=data_r,
                output_root=out_root,
                status_override=status,
            )
            row = _apply_error(row)
            failure_rows.append(row)
            continue

        # History-only failure (no factory job file) — still show in Workbench.
        stub = _synthetic_live_work_product(
            prompt_id=pid,
            status=status,
            prompt=prompt,
            family_slugs=family_slugs,
            output_root=out_root,
        )
        stub["job_key"] = job_key or f"history__{pid[:12]}"
        stub["construction"] = {"step": "history", "source": "comfy_history"}
        failure_rows.append(_apply_error(stub))

    if failure_rows:
        # Failures first (newest history), then existing items (live already prepended earlier).
        merged = failure_rows + items
        limit = int(payload.get("limit") or len(merged))
        # Keep room for attached failures even when over limit.
        payload["items"] = merged[: max(limit, len(failure_rows))]
        payload["count"] = len(payload["items"])
    else:
        payload["items"] = items
        payload["count"] = len(items)

    payload["history_failure_count"] = len(failure_rows) + touched
    payload["history_dismissed_skipped"] = dismissed_skipped
    return payload


def peek_json_file(
    path: str,
    *,
    data_root: Path,
    output_root: Path,
    workspace_root: Optional[Path] = None,
    max_bytes: int = 256_000,
) -> Dict[str, Any]:
    """
    Read a JSON file for the work-products tooltip viewer.

    Resolves host↔container path aliases via shape_factory_map.resolve_existing_path,
    then requires the file to sit under an allowlisted root.
    """
    raw = str(path or "").strip()
    if not raw:
        return {"ok": False, "error": "missing_path"}
    if not raw.lower().endswith(".json"):
        return {"ok": False, "error": "not_json", "path": raw}

    data_root = data_root.resolve()
    output_root = output_root.resolve()
    ws = (workspace_root or output_root.parent).resolve()

    try:
        from shape_factory_map import resolve_existing_path
    except ImportError:
        resolve_existing_path = None  # type: ignore

    resolved: Optional[Path] = None
    if resolve_existing_path:
        try:
            resolved = resolve_existing_path(
                raw,
                output_root=output_root,
                data_root=data_root,
                workspace_root=ws,
            )
        except FileNotFoundError:
            resolved = None
    if resolved is None:
        cand = Path(raw).expanduser()
        if cand.is_file():
            resolved = cand.resolve()
    if resolved is None or not resolved.is_file():
        return {"ok": False, "error": "not_found", "path": raw}

    allow_roots = [
        data_root,
        output_root,
        ws,
        ws / "comfyui_user",
        ws / ".data",
    ]
    # Host bind siblings often used in job metadata.
    for extra in (
        Path("/home/yuji/src/comfyui-runpod/.data"),
        Path("/home/yuji/comfyui-runpod-data"),
        Path("/home/yuji/src/comfyui-runpod/workspace"),
    ):
        if extra.exists():
            allow_roots.append(extra.resolve())

    allowed = False
    resolved_s = str(resolved)
    for root in allow_roots:
        try:
            resolved.relative_to(root.resolve())
            allowed = True
            break
        except Exception:
            continue
    if not allowed:
        return {"ok": False, "error": "path_not_allowed", "path": raw, "resolved": resolved_s}

    try:
        size = resolved.stat().st_size
        data = resolved.read_bytes()
    except OSError as e:
        return {"ok": False, "error": "read_failed", "detail": str(e), "path": raw}

    truncated = False
    if len(data) > max_bytes:
        data = data[:max_bytes]
        truncated = True

    text = data.decode("utf-8", errors="replace")
    pretty = text
    parse_error = None
    if truncated:
        pretty = text + "\n… [truncated]"
        parse_error = "truncated"
    else:
        try:
            parsed = json.loads(text)
            pretty = json.dumps(parsed, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            parse_error = str(e)

    return {
        "ok": True,
        "path": raw,
        "resolved": resolved_s,
        "basename": resolved.name,
        "bytes": size,
        "truncated": truncated,
        "max_bytes": max_bytes,
        "parse_error": parse_error,
        "text": pretty,
    }
