"""Recent shape-factory work products + construction metadata for debug UI."""

from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

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


def list_shape_families(data_root: Path) -> List[Dict[str, Any]]:
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

    for path in sorted(shapes_dir.glob("*.shape.yaml")):
        slug = path.name[: -len(".shape.yaml")] if path.name.endswith(".shape.yaml") else path.stem
        shape_id = None
        family_slug = slug
        try:
            doc = load_yaml(path)
            if isinstance(doc, dict):
                shape_id = doc.get("shape_id")
                family_slug = str(doc.get("family_slug") or slug).strip() or slug
        except Exception:
            pass
        out.append({"slug": family_slug or slug, "shape_id": shape_id, "shape_path": str(path)})
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


def _binding_entry_from_meta(
    slot: str,
    meta: Dict[str, Any],
    *,
    data_root: Path,
    output_root: Path,
) -> Dict[str, Any]:
    abs_p = meta.get("path")
    rel = _relpath_under(output_root, abs_p)
    if rel is None:
        rel = _relpath_under(data_root, abs_p)
    entry: Dict[str, Any] = {
        "path": abs_p,
        "basename": _basename(abs_p),
        "relpath": rel,
        "url": _file_url(rel)
        if rel and str(abs_p or "").lower().endswith((".mp4", ".png", ".jpg", ".jpeg", ".webp", ".webm"))
        else None,
        "binding_type": meta.get("binding_type"),
        "role": meta.get("role"),
    }
    if str(slot) in {"source_video", "source_image", "start_image", "image"} and entry.get("relpath"):
        low = str(abs_p or "").lower()
        if low.endswith(".mp4"):
            entry["thumb_url"] = _file_url(_thumb_rel_for_video(entry["relpath"]))
        elif low.endswith((".png", ".jpg", ".jpeg", ".webp")):
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
    }
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

    def add(label: str, value: Any, *, json_path: Any = None, peek: Any = None) -> None:
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
        rows.append(row)

    add("Created", item.get("created_at"))
    add("Family", item.get("family_slug"))
    add("Status", item.get("status"))
    add("Pick mode", item.get("pick_mode"))
    add("Step", item.get("step"))
    add("Rating kind", item.get("rating_kind"))
    add("Disposition", item.get("disposition_entry"))
    add("Disposition note", item.get("disposition_note"))
    c = item.get("construction") if isinstance(item.get("construction"), dict) else {}
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
        add(
            f"Binding · {slot}",
            " · ".join(b for b in bits if b),
            json_path=meta.get("path") if str(meta.get("path") or "").lower().endswith(".json") else None,
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

    output_rel = None
    for abs_out in outputs_abs:
        output_rel = _relpath_under(output_root, abs_out)
        if output_rel:
            break
    # Fall back to output_prefix guess when submit hasn't recorded outputs yet.
    if not output_rel:
        prefix = str(job.get("output_prefix") or "").strip().replace("\\", "/")
        if prefix:
            for cand in (
                output_root / f"{prefix}_00001.mp4",
                output_root / f"{prefix}_PREVIEW_00001.mp4",
            ):
                if cand.is_file():
                    output_rel = cand.relative_to(output_root).as_posix()
                    break
            if not output_rel:
                stem = Path(prefix).name
                parent = (output_root / Path(prefix).parent).resolve()
                if parent.is_dir():
                    matches = sorted(parent.glob(f"{stem}*.mp4"))
                    if matches:
                        try:
                            output_rel = matches[0].resolve().relative_to(output_root).as_posix()
                        except Exception:
                            output_rel = None

    thumb_rel = _thumb_rel_for_video(output_rel)

    bindings_out = _bindings_from_job(job, data_root=data_root, output_root=output_root)
    prompt_profile = None
    for slot, entry in bindings_out.items():
        if slot == "prompt_profile":
            prompt_profile = _prompt_excerpt(
                entry.get("path"),
                data_root=data_root,
                output_root=output_root,
                workspace_root=output_root.parent,
            )
            break

    construction = _synthesize_construction(job)
    status = str(
        status_override
        or submit.get("status")
        or ("deposited" if deposit else "pending")
    )
    parent_output = job.get("parent_output") or construction.get("parent_output")
    parent_rel = _relpath_under(output_root, parent_output)
    parent_url = _file_url(parent_rel)
    parent_thumb = _file_url(_thumb_rel_for_video(parent_rel))
    # Queued/live jobs often lack parent_output — use source still/video as stand-in.
    if not parent_rel:
        src = bindings_out.get("source_video") or bindings_out.get("source_image") or {}
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

    item: Dict[str, Any] = {
        "job_key": job.get("job_key") or path.stem.replace(".job", ""),
        "job_path": str(path),
        "family_slug": fam,
        "created_at": job.get("created_at"),
        "pick_mode": job.get("pick_mode"),
        "pick_index": job.get("pick_index"),
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
        "prompt_id": submit.get("prompt_id"),
        "submitted_at": submit.get("submitted_at"),
        "deposited_at": deposit.get("deposited_at"),
        "output_relpath": output_rel,
        "output_url": _file_url(output_rel),
        "output_thumb_url": _file_url(thumb_rel),
        "bindings": bindings_out,
        "prompt_profile": prompt_profile,
        "shape_profile": shape_profile,
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

    families = list_shape_families(data_root)
    extend_family_defaults = list_extend_family_defaults(data_root)

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
        out.append({"prompt_id": pid.strip(), "status": status, "prompt": prompt})
    return out


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
) -> Dict[str, Any]:
    """
    Align factory ``job.json`` submit statuses with Comfy ``/queue`` (+ history).

    Comfy is canonical for queued/running. Jobs that claim in-flight but are gone
    from both queue and history are marked interrupted (or complete/error via history).
    """
    # Local import: shape_factory pulls heavier deps; keep work_products import light.
    from shape_factory import (  # type: ignore
        atomic_write_json,
        queue_prompt_id_buckets,
        update_job_status_from_comfy,
    )

    data_root = Path(data_root).expanduser().resolve()
    server = str(comfy_server or "").rstrip("/")
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
        "running_ids": len(running_ids),
        "pending_ids": len(pending_ids),
        "by_status": {},
    }
    if not jobs_root.is_dir():
        return summary

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
        in_comfy = bool(prompt_id) and (prompt_id in running_ids or prompt_id in pending_ids)
        if not in_comfy and before not in IN_FLIGHT_STATUSES:
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
        if after != before:
            summary["updated"] = int(summary["updated"]) + 1
            if persist:
                try:
                    atomic_write_json(path, job)
                except OSError:
                    summary["ok"] = False
                    summary["write_error"] = str(path)
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

    Matches existing items by ``prompt_id`` (promoting/updating status). When no
    factory job is in the current page, tries to locate the job file globally or
    synthesizes a live stub so the UI can show the latent preview.
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
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        pid = str(it.get("prompt_id") or "").strip()
        if pid and pid not in by_pid:
            by_pid[pid] = i

    family_slugs = [str(f.get("slug") or "") for f in (payload.get("families") or []) if isinstance(f, dict)]
    jobs_root = Path(data_root) / "shape_factory" / "jobs" if data_root else None
    out_root = Path(output_root).resolve() if output_root else None
    data_r = Path(data_root).resolve() if data_root else None
    live_items: List[Dict[str, Any]] = []
    used_indices: set[int] = set()

    for ent in entries:
        pid = ent["prompt_id"]
        status = ent["status"]
        if pid in by_pid:
            idx = by_pid[pid]
            used_indices.add(idx)
            row = dict(items[idx])
            row["status"] = status
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
                        row = _work_product_item_from_job(
                            jp,
                            found_job,
                            data_root=data_r,
                            output_root=out_root,
                            status_override=status,
                            live_from_comfy=True,
                        )
            elif out_root is not None and data_r is not None:
                row = _ensure_item_media_urls(row, data_root=data_r, output_root=out_root)
            live_items.append(row)
            continue

        found_path, found_job = (None, None)
        if jobs_root is not None:
            found_path, found_job = _find_job_by_prompt_id(jobs_root, pid)
        if found_path is not None and isinstance(found_job, dict):
            if out_root is not None and data_r is not None:
                row = _work_product_item_from_job(
                    found_path,
                    found_job,
                    data_root=data_r,
                    output_root=out_root,
                    status_override=status,
                    live_from_comfy=True,
                )
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
            continue

        live_items.append(
            _synthetic_live_work_product(
                prompt_id=pid,
                status=status,
                prompt=ent.get("prompt"),
                family_slugs=family_slugs,
                output_root=out_root,
            )
        )

    rest = [it for i, it in enumerate(items) if i not in used_indices]
    merged = live_items + rest
    # Keep roughly within limit + room for live rows.
    limit = int(payload.get("limit") or len(merged))
    payload["items"] = merged[: max(limit, len(live_items))]
    payload["count"] = len(payload["items"])
    payload["live_comfy_count"] = len(live_items)
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
