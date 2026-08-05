#!/usr/bin/env python3
"""
Hourly shape-factory planning: replay random previous runs (factory jobs + historical OG outputs).

Used by scripts/shape_factory_hourly.sh and shape_factory_map hourly preview.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from shape_factory import load_yaml, requires_by_slot
from shape_factory_heuristics import _og_group_id_from_relpath
from shape_factory_map import _combo_key_from_job_bindings, _combo_key_from_slot_paths
from shape_factory_prompt_recover import (
    extract_prompt_texts_from_ui_workflow,
    find_ui_node,
    load_workflow_json,
    prompt_binding_from_shape,
    widget_text,
    write_replay_prompt_profile,
)
from shape_factory_ratings import (
    default_appetite_index_path,
    default_ratings_index_path,
    lookup_output_appetite,
    lookup_output_rating,
)

_OG_DATE_RE = re.compile(r"(?:^|/)og/(\d{4}-\d{2}-\d{2})(?:/|$)")
_KNEEL_SOURCE_RE = re.compile(r"(?i)(?:^|[/_\-])x-?kneel|kneel-fb9|x-kneel")
_Y2025_FOLDER_RE = re.compile(r"(?:^|/)og/2025-\d{2}-\d{2}(?:/|$)")
_Y2025_NAME_RE = re.compile(r"(?:^|[^0-9])2025[-_]\d{2}[-_]\d{2}")


def _recipe_source_path(recipe: dict[str, Any]) -> str:
    picks = recipe.get("picks") if isinstance(recipe.get("picks"), dict) else {}
    for slot in ("source_video", "source_still", "source_video_ref"):
        raw = picks.get(slot)
        if raw:
            return str(raw)
    return ""


def _is_kneel_source(path: str) -> bool:
    text = str(path or "").replace("\\", "/")
    if not text:
        return False
    return bool(_KNEEL_SOURCE_RE.search(text) or _KNEEL_SOURCE_RE.search(Path(text).name))


def _is_2025_source(path: str) -> bool:
    text = str(path or "").replace("\\", "/")
    if not text:
        return False
    if _Y2025_FOLDER_RE.search(text):
        return True
    return bool(_Y2025_NAME_RE.search(Path(text).name))


def _source_promotion_mult(path: str) -> float:
    """Weight multiplier for preferred library sources (X-Kneel, 2025-era OG)."""
    kneel_b = max(1.0, float(os.environ.get("HOURLY_KNEEL_SOURCE_BOOST", "2.5")))
    y2025_b = max(1.0, float(os.environ.get("HOURLY_2025_SOURCE_BOOST", "2.0")))
    mult = 1.0
    if _is_kneel_source(path):
        mult *= kneel_b
    if _is_2025_source(path):
        mult *= y2025_b
    return mult


def _recipe_promotion_mult(recipe: dict[str, Any]) -> float:
    """Boost recipes whose source or output is an X-Kneel / 2025-era clip."""
    kneel_b = max(1.0, float(os.environ.get("HOURLY_KNEEL_SOURCE_BOOST", "2.5")))
    y2025_b = max(1.0, float(os.environ.get("HOURLY_2025_SOURCE_BOOST", "2.0")))
    paths = [_recipe_source_path(recipe), str(recipe.get("output_path") or "")]
    mult = 1.0
    if any(_is_kneel_source(p) for p in paths):
        mult *= kneel_b
    if any(_is_2025_source(p) for p in paths):
        mult *= y2025_b
    return mult


def _apply_source_promotion(
    recipes: List[dict[str, Any]],
    weights: List[float],
    weight_meta: Optional[List[dict[str, Any]]] = None,
) -> List[float]:
    """Amplify Kneel / 2025-era recipes in weighted selection."""
    out: List[float] = []
    for i, (recipe, weight) in enumerate(zip(recipes, weights)):
        mult = _recipe_promotion_mult(recipe)
        out.append(float(weight) * mult)
        if weight_meta is not None and i < len(weight_meta) and isinstance(weight_meta[i], dict) and mult != 1.0:
            weight_meta[i] = dict(weight_meta[i])
            weight_meta[i]["source_promotion_mult"] = round(mult, 3)
    return out


def _default_data_root() -> Path:
    repo = Path(__file__).resolve().parents[2]
    env = __import__("os").environ.get("SHAPE_FACTORY_DATA_ROOT", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p.resolve()
    return (repo / ".data").resolve()


def _default_job_dir(data_root: Path) -> Path:
    return data_root / "shape_factory" / "jobs"


def _prompt_binding(shape: dict[str, Any]) -> Optional[dict[str, Any]]:
    return prompt_binding_from_shape(shape)


def _find_node(workflow: dict[str, Any], node_id: int) -> Optional[dict[str, Any]]:
    return find_ui_node(workflow, node_id)


def _widget_text(workflow: dict[str, Any], node_id: int, widget_index: int = 0) -> str:
    return widget_text(workflow, node_id, widget_index)


def _vhs_video_path(workflow: dict[str, Any], node_id: int) -> str:
    node = _find_node(workflow, node_id)
    if not node:
        return ""
    widgets = node.get("widgets_values")
    if isinstance(widgets, dict):
        return str(widgets.get("video") or "")
    return ""


def _load_image_path(workflow: dict[str, Any], node_id: int) -> str:
    node = _find_node(workflow, node_id)
    if not node:
        return ""
    widgets = node.get("widgets_values")
    if isinstance(widgets, list) and widgets:
        return str(widgets[0] or "")
    return ""


def _resolve_media_path(raw: str, *, data_root: Path) -> Optional[Path]:
    """Resolve a job/workflow media path, including legacy nested ``output/output/`` layouts."""
    text = str(raw or "").strip().replace("\\", "/")
    if not text:
        return None
    try:
        from output_path_lib import flatten_output_prefix

        text = flatten_output_prefix(text)
    except Exception:
        while "output/output/" in text:
            text = text.replace("output/output/", "output/", 1)

    variants: List[str] = [text]
    if text.startswith("output/"):
        variants.append(text[len("output/") :])
    name = Path(text).name
    if name and name not in variants:
        variants.append(name)

    bind_roots: List[Path] = []
    for cand in (
        Path("/home/yuji/comfyui-runpod-data/output"),
        Path((__import__("os").environ.get("COMFYUI_BIND_OUTPUT_DIR") or "").strip()),
        data_root.parent.parent / "workspace" / "output",
        data_root / "output",
    ):
        try:
            if cand.is_dir():
                resolved = cand.resolve()
                if resolved not in bind_roots:
                    bind_roots.append(resolved)
        except Exception:
            continue

    candidates: List[Path] = []
    seen: Set[str] = set()

    def add(p: Path) -> None:
        key = str(p)
        if key in seen:
            return
        seen.add(key)
        candidates.append(p)

    add(Path(text).expanduser())
    for v in variants:
        add(Path(v).expanduser())
        add(data_root / v)
        add(data_root / "output" / v)
        for root in bind_roots:
            add(root / v)
            if v.startswith("output/"):
                add(root / v[len("output/") :])

    for cand in candidates:
        try:
            if cand.is_file():
                return cand.resolve()
        except Exception:
            continue

    # Prefer library-relative tails (og/YYYY-MM-DD/file.mp4) under bind roots.
    parts = text.split("/")
    for lib in ("og", "wip", "experiments"):
        if lib not in parts:
            continue
        i = parts.index(lib)
        rel_parts = parts[i:]
        for root in bind_roots:
            direct = root.joinpath(*rel_parts)
            try:
                if direct.is_file():
                    return direct.resolve()
            except Exception:
                continue
    return None


def _combo_slug(text: str, n: int = 12) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]


def _write_replay_prompt_profile(
    *,
    family: str,
    data_root: Path,
    label: str,
    positive: str,
    negative: str,
) -> Path:
    return write_replay_prompt_profile(
        family=family,
        data_root=data_root,
        label=label,
        positive=positive,
        negative=negative,
    )


def _picks_from_ui_workflow(
    workflow: dict[str, Any],
    *,
    shape: dict[str, Any],
    data_root: Path,
    label: str,
) -> Optional[Dict[str, Path]]:
    req_by_slot = requires_by_slot(shape)
    binding = _prompt_binding(shape)
    if not binding:
        return None

    positive, negative = extract_prompt_texts_from_ui_workflow(workflow, shape)
    if not positive.strip():
        return None

    picks: Dict[str, Path] = {}
    prompt_path = _write_replay_prompt_profile(
        family=str(shape.get("family_slug") or ""),
        data_root=data_root,
        label=label,
        positive=positive,
        negative=negative,
    )
    picks["prompt_profile"] = prompt_path

    for slot, req in req_by_slot.items():
        if slot == "prompt_profile":
            continue
        b = req.get("binding") if isinstance(req.get("binding"), dict) else {}
        btype = str(b.get("type") or "")
        node_id = int(b.get("node_id") or 0)
        raw = ""
        if btype == "vhs_load_video_path":
            raw = _vhs_video_path(workflow, node_id)
        elif btype == "load_image":
            raw = _load_image_path(workflow, node_id)
        else:
            continue
        resolved = _resolve_media_path(raw, data_root=data_root)
        if resolved is None:
            return None
        picks[slot] = resolved

    missing = [s for s, req in req_by_slot.items() if s not in picks and not req.get("optional")]
    if missing:
        return None
    return picks


def _load_workflow_json(path: Path) -> Optional[dict[str, Any]]:
    return load_workflow_json(path)


def _png_workflow_for_output(output_mp4: Path) -> Optional[dict[str, Any]]:
    try:
        from comfy_meta_lib import extract_prompt_workflow_from_png_chunks, read_png_text_chunks
    except ImportError:
        return None
    png = output_mp4.with_suffix(".png")
    if not png.is_file():
        return None
    try:
        chunks = read_png_text_chunks(png)
        _prompt, workflow = extract_prompt_workflow_from_png_chunks(chunks)
    except Exception:
        return None
    return workflow if isinstance(workflow, dict) else None


def _job_is_replayable(job: dict[str, Any]) -> bool:
    submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    if str(submit.get("status") or "") in {"complete", "queued", "running"}:
        return True
    deposit = job.get("deposit") if isinstance(job.get("deposit"), dict) else {}
    if deposit.get("videos"):
        return True
    return False


def _picks_from_job(
    job: dict[str, Any],
    *,
    shape: dict[str, Any],
    data_root: Path,
) -> Optional[Dict[str, Path]]:
    bindings = job.get("bindings") if isinstance(job.get("bindings"), dict) else {}
    if not bindings:
        return None

    family = str(shape.get("family_slug") or job.get("family_slug") or "")
    picks: Dict[str, Path] = {}
    workflow: Optional[dict[str, Any]] = None

    def _workflow() -> Optional[dict[str, Any]]:
        nonlocal workflow
        if workflow is not None:
            return workflow
        wf_path = str(job.get("generated_workflow_path") or "").strip()
        if not wf_path:
            return None
        workflow = _load_workflow_json(Path(wf_path))
        return workflow

    for slot, meta in bindings.items():
        if not isinstance(meta, dict):
            continue
        raw = str(meta.get("path") or "")
        if slot == "prompt_profile":
            p = Path(raw).expanduser() if raw else None
            if p is not None and p.is_file():
                picks[slot] = p.resolve()
                continue
            wf = _workflow()
            if wf is None:
                return None
            positive, negative = extract_prompt_texts_from_ui_workflow(wf, shape)
            if not positive.strip():
                return None
            picks[slot] = _write_replay_prompt_profile(
                family=family or str(shape.get("family_slug") or ""),
                data_root=data_root,
                label=str(job.get("job_key") or "job"),
                positive=positive,
                negative=negative,
            )
            continue
        resolved = _resolve_media_path(raw, data_root=data_root)
        if resolved is None:
            # Fall back to path embedded in the generated UI workflow.
            wf = _workflow()
            if wf is not None:
                req = (requires_by_slot(shape).get(slot) or {})
                b = req.get("binding") if isinstance(req.get("binding"), dict) else {}
                btype = str(b.get("type") or "")
                node_id = int(b.get("node_id") or 0)
                alt = ""
                if btype == "vhs_load_video_path":
                    alt = _vhs_video_path(wf, node_id)
                elif btype == "load_image":
                    alt = _load_image_path(wf, node_id)
                if alt:
                    resolved = _resolve_media_path(alt, data_root=data_root)
        if resolved is None:
            return None
        picks[slot] = resolved

    req_by_slot = requires_by_slot(shape)
    missing = [s for s, req in req_by_slot.items() if s not in picks and not req.get("optional")]
    if missing:
        return None
    return picks


def _recipe_from_picks(
    *,
    family: str,
    picks: Dict[str, Path],
    source: str,
    output_path: Optional[str] = None,
) -> dict[str, Any]:
    combo_key = _combo_key_from_slot_paths({slot: str(path) for slot, path in sorted(picks.items())})
    preview = {slot: path.name for slot, path in sorted(picks.items())}
    return {
        "family": family,
        "combo_key": combo_key,
        "picks": {slot: str(path) for slot, path in sorted(picks.items())},
        "bindings_preview": preview,
        "source": source,
        "output_path": output_path,
    }


def _default_og_root(data_root: Path) -> Path:
    env = __import__("os").environ.get("SHAPE_FACTORY_OG_ROOT", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p.resolve()
    candidates = [
        Path("/home/yuji/comfyui-runpod-data/output/og"),
        data_root.parent.parent / "output" / "og",
    ]
    for cand in candidates:
        if cand.is_dir():
            return cand.resolve()
    return candidates[0]


def _load_ratings_index(data_root: Path) -> Optional[dict[str, Any]]:
    try:
        from shape_factory_ratings import default_ratings_index_path
    except ImportError:
        return None
    path = default_ratings_index_path(_default_og_root(data_root))
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except Exception:
        return None


def _load_heuristics_index(data_root: Path) -> Optional[dict[str, Any]]:
    try:
        from shape_factory_heuristics import default_heuristics_index_path
    except ImportError:
        return None
    path = default_heuristics_index_path(_default_og_root(data_root))
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except Exception:
        return None


def _load_appetite_index(data_root: Path) -> Optional[dict[str, Any]]:
    path = default_appetite_index_path(_default_og_root(data_root))
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except Exception:
        return None


def _load_asset_tags(data_root: Path) -> Optional[dict[str, Any]]:
    path = _default_og_root(data_root).parent / "_status" / "asset_tags.json"
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except Exception:
        return None


def _tag_affinity_for_output(
    output_path: str,
    *,
    tags_doc: Optional[dict[str, Any]],
    heuristics_doc: Optional[dict[str, Any]],
) -> float:
    """Mean appetite of an output's tags (0 when unknown) — a minor derive-ranking term."""
    if not tags_doc or not heuristics_doc:
        return 0.0
    by_tag_app = heuristics_doc.get("by_tag_appetite") or {}
    by_gid = tags_doc.get("by_group_id") or {}
    if not isinstance(by_tag_app, dict) or not isinstance(by_gid, dict):
        return 0.0
    gid = _og_group_id_from_relpath(str(output_path or "")) or ""
    row = by_gid.get(gid)
    if not isinstance(row, dict):
        return 0.0
    vals: List[float] = []
    for tag in row.get("tags") or []:
        trow = by_tag_app.get(str(tag).lower())
        if isinstance(trow, dict) and trow.get("inferred") is not None:
            vals.append(float(trow["inferred"]))
    return sum(vals) / len(vals) if vals else 0.0


def _lookup_output_rating(output_path: str, ratings_doc: dict[str, Any]) -> Optional[dict[str, Any]]:
    return lookup_output_rating(output_path, ratings_doc)


def _recipe_selection_weight(
    recipe: dict[str, Any],
    *,
    ratings_doc: Optional[dict[str, Any]],
    shape: dict[str, Any],
    heuristics_doc: Optional[dict[str, Any]] = None,
    appetite_doc: Optional[dict[str, Any]] = None,
) -> Tuple[float, dict[str, Any]]:
    """Weight for weighted random replay selection (ratings + graph heuristics + light appetite)."""
    try:
        from shape_factory_heuristics import score_recipe

        return score_recipe(
            recipe,
            shape=shape,
            ratings_doc=ratings_doc,
            heuristics_doc=heuristics_doc,
            appetite_doc=appetite_doc,
        )
    except ImportError:
        pass

    from shape_factory_ratings import is_omit_quality_rating, is_usable_quality_rating

    meta: dict[str, Any] = {"rating_effective": None, "evidence": []}
    explore_floor = float(__import__("os").environ.get("HOURLY_RATING_EXPLORE_FLOOR", "0.35"))
    if not ratings_doc:
        return explore_floor, meta
    out_row = lookup_output_rating(str(recipe.get("output_path") or ""), ratings_doc)
    if out_row is not None and is_omit_quality_rating(out_row.get("explicit")):
        try:
            meta["explicit"] = int(out_row["explicit"])
        except (TypeError, ValueError):
            meta["explicit"] = 0
        meta["omit"] = True
        meta["rating_kind"] = "omit"
        meta["evidence"].append("output_omit")
        return 0.0, meta
    if out_row and is_usable_quality_rating(out_row.get("explicit")):
        rating_value = float(out_row["explicit"])
        meta["rating_effective"] = rating_value
        meta["rating_kind"] = "explicit"
        meta["evidence"].append("output_explicit")
        normalized = max(1.0, min(5.0, rating_value))
        return max(explore_floor, ((normalized - 1.0) / 4.0) ** 1.6 * 4.0 + 0.15), meta
    return explore_floor, meta


def _weighted_choice(
    recipes: List[dict[str, Any]],
    weights: List[float],
    rng: random.Random,
) -> Tuple[dict[str, Any], int]:
    total = sum(max(0.0, w) for w in weights)
    if total <= 0:
        idx = rng.randrange(len(recipes))
        return recipes[idx], idx
    pick = rng.random() * total
    acc = 0.0
    for idx, weight in enumerate(weights):
        acc += max(0.0, weight)
        if pick <= acc:
            return recipes[idx], idx
    return recipes[-1], len(recipes) - 1


def _is_top_of_hour(
    now: Optional[datetime] = None,
    *,
    window_minutes: Optional[int] = None,
) -> bool:
    """True near :00 so a top-of-hour tick can prefer recent 5★ keepers.

    The systemd timer fires at :30 (outside this window) so the normal
    predicted/derive/replay mix keeps exploring; keepers only win if a tick
    lands in the first ``HOURLY_TOP_OF_HOUR_MINUTES`` of the hour.
    """
    wall = now or datetime.now()
    raw = window_minutes
    if raw is None:
        raw = int(os.environ.get("HOURLY_TOP_OF_HOUR_MINUTES", "12"))
    window = max(0, min(59, int(raw)))
    return int(wall.minute) < window


def _parse_ts(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return None


def _og_path_date_ts(path_like: str) -> Optional[float]:
    m = _OG_DATE_RE.search(str(path_like or "").replace("\\", "/"))
    if not m:
        return None
    try:
        return datetime.fromisoformat(f"{m.group(1)}T12:00:00+00:00").timestamp()
    except Exception:
        return None


def archive_min_age_days() -> float:
    raw = os.environ.get("HOURLY_ARCHIVE_MIN_AGE_DAYS", "45").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 45.0


def archive_og_share() -> float:
    raw = os.environ.get("HOURLY_ARCHIVE_OG_SHARE", "0.20").strip()
    try:
        return max(0.0, min(1.0, float(raw)))
    except ValueError:
        return 0.20


def _path_looks_hourly(path_like: str) -> bool:
    text = str(path_like or "").replace("\\", "/")
    return "/hourly/" in text or text.startswith("hourly/") or "/hourly__" in text


def _og_paths_for_age(recipe_or_path: Any) -> List[str]:
    """Collect path-like strings that may carry an og/YYYY-MM-DD date."""
    if isinstance(recipe_or_path, dict):
        out: List[str] = []
        src = str(recipe_or_path.get("source") or "")
        if src.startswith("og:"):
            out.append(src[3:])
        out.append(str(recipe_or_path.get("output_path") or ""))
        out.append(_recipe_source_path(recipe_or_path))
        return [p for p in out if p]
    return [str(recipe_or_path or "")]


def _archive_age_days(path_like: str, *, now_ts: Optional[float] = None) -> Optional[float]:
    ts = _og_path_date_ts(path_like)
    if ts is None:
        return None
    now = float(now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp())
    return max(0.0, (now - ts) / 86400.0)


def _is_archive_og_path(path_like: str, *, now_ts: Optional[float] = None, min_age_days: Optional[float] = None) -> bool:
    """True for deep-archive / pre-factory OG media (old og/ date, not under /hourly/)."""
    text = str(path_like or "").replace("\\", "/")
    if not text or _path_looks_hourly(text):
        return False
    if "og/" not in text and not text.startswith("og:"):
        # Absolute paths may still contain /og/YYYY-MM-DD/
        if _og_path_date_ts(text) is None:
            return False
    age = _archive_age_days(text, now_ts=now_ts)
    if age is None:
        return False
    floor = archive_min_age_days() if min_age_days is None else max(0.0, float(min_age_days))
    return age >= floor


def _is_archive_og_recipe(
    recipe: dict[str, Any],
    *,
    now_ts: Optional[float] = None,
    min_age_days: Optional[float] = None,
) -> bool:
    """
    Older pre-factory / deep-archive OG generations.

    Matches deposit-pool recipes (``source`` starts with ``og:``) or any recipe whose
    output/source path sits under ``og/`` outside ``/hourly/`` and is old enough.
    """
    if not isinstance(recipe, dict):
        return False
    src = str(recipe.get("source") or "")
    paths = _og_paths_for_age(recipe)
    # Prefer deposit-ingest tag, but still require age + non-hourly.
    if src.startswith("og:") or any("og/" in p.replace("\\", "/") for p in paths):
        return any(_is_archive_og_path(p, now_ts=now_ts, min_age_days=min_age_days) for p in paths)
    return False


def _archive_age_spread_mult(recipe_or_path: Any, *, now_ts: Optional[float] = None) -> float:
    """Mild always-on boost for older archive OG (capped ~2×)."""
    paths = _og_paths_for_age(recipe_or_path)
    ages = [a for a in (_archive_age_days(p, now_ts=now_ts) for p in paths) if a is not None]
    if not ages:
        return 1.0
    if not any(_is_archive_og_path(p, now_ts=now_ts) for p in paths):
        return 1.0
    age = max(ages)
    # log2(1 + age/45) → ~1.0 at min age, ~2.0 around ~135 days, capped at 2×.
    return min(2.0, 1.0 + math.log2(1.0 + age / max(1.0, archive_min_age_days())))


def _apply_archive_age_spread(
    recipes: List[dict[str, Any]],
    weights: List[float],
    weight_meta: Optional[List[dict[str, Any]]] = None,
    *,
    now_ts: Optional[float] = None,
) -> List[float]:
    out: List[float] = []
    for i, (recipe, weight) in enumerate(zip(recipes, weights)):
        mult = _archive_age_spread_mult(recipe, now_ts=now_ts)
        out.append(float(weight) * mult)
        if (
            weight_meta is not None
            and i < len(weight_meta)
            and isinstance(weight_meta[i], dict)
            and mult != 1.0
        ):
            weight_meta[i] = dict(weight_meta[i])
            weight_meta[i]["archive_age_mult"] = round(mult, 3)
            weight_meta[i]["archive_og"] = True
    return out


def _want_archive_og_sample(cursor: int, *, salt: int = 0xA0C6) -> bool:
    share = archive_og_share()
    if share <= 0:
        return False
    if share >= 1:
        return True
    return random.Random(int(cursor) ^ int(salt)).random() < share


def _rating_event_ts(row: Optional[dict[str, Any]], *, output_path: str = "") -> Optional[float]:
    """Best-effort timestamp for when a keeper became / was marked 5★."""
    if isinstance(row, dict):
        for key in ("rated_at", "quality_updated_at"):
            ts = _parse_ts(row.get(key))
            if ts is not None:
                return ts
        for path_like in (output_path, row.get("short_key"), row.get("xmp")):
            ts = _og_path_date_ts(str(path_like or ""))
            if ts is not None:
                return ts
        xmp = str(row.get("xmp") or "").strip()
        if xmp:
            try:
                p = Path(xmp)
                if p.is_file():
                    return float(p.stat().st_mtime)
            except Exception:
                pass
    return _og_path_date_ts(output_path)


def _recent_five_star_multiplier(
    row: Optional[dict[str, Any]],
    *,
    output_path: str = "",
    now: Optional[datetime] = None,
    top_of_hour: Optional[bool] = None,
) -> float:
    """
    Weight multiplier for recent explicit 5★ keepers.

    Active only at top-of-hour (unless HOURLY_RECENT_5STAR_ALWAYS=1). Decays over
    HOURLY_RECENT_5STAR_DAYS (default 14).
    """
    always = str(os.environ.get("HOURLY_RECENT_5STAR_ALWAYS", "")).strip().lower() in {
        "1",
        "true",
        "yes",
    }
    toh = _is_top_of_hour(now) if top_of_hour is None else bool(top_of_hour)
    if not toh and not always:
        return 1.0
    if not isinstance(row, dict):
        return 1.0
    try:
        explicit = int(row.get("explicit"))
    except (TypeError, ValueError):
        return 1.0
    if explicit != 5:
        return 1.0
    now_dt = now or datetime.now(timezone.utc)
    now_ts = now_dt.timestamp() if now_dt.tzinfo else now_dt.replace(tzinfo=timezone.utc).timestamp()
    event_ts = _rating_event_ts(row, output_path=output_path)
    if event_ts is None:
        return 1.0
    age_days = max(0.0, (now_ts - event_ts) / 86400.0)
    window_days = max(0.1, float(os.environ.get("HOURLY_RECENT_5STAR_DAYS", "14")))
    if age_days > window_days:
        return 1.0
    boost = max(1.0, float(os.environ.get("HOURLY_RECENT_5STAR_BOOST", "10")))
    frac = 1.0 - (age_days / window_days)
    return 1.0 + (boost - 1.0) * max(0.0, min(1.0, frac))


def _apply_recent_five_star_bias(
    recipes: List[dict[str, Any]],
    weights: List[float],
    weight_meta: List[dict[str, Any]],
    ratings_doc: Optional[dict[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> Tuple[List[float], dict[str, Any]]:
    """Amplify recent 5★ recipes near the top of the hour; returns weights + bias stats."""
    top_of_hour = _is_top_of_hour(now)
    stats: dict[str, Any] = {
        "top_of_hour": top_of_hour,
        "recent_five_star_boosted": 0,
        "recent_five_star_max_mult": 1.0,
    }
    if not ratings_doc or not recipes:
        return list(weights), stats
    out = list(weights)
    for i, recipe in enumerate(recipes):
        out_path = str(recipe.get("output_path") or "")
        row = lookup_output_rating(out_path, ratings_doc) if out_path else None
        mult = _recent_five_star_multiplier(row, output_path=out_path, now=now, top_of_hour=top_of_hour)
        if mult > 1.0 + 1e-9:
            out[i] = float(out[i]) * mult
            stats["recent_five_star_boosted"] += 1
            stats["recent_five_star_max_mult"] = max(float(stats["recent_five_star_max_mult"]), mult)
            if i < len(weight_meta) and isinstance(weight_meta[i], dict):
                weight_meta[i] = dict(weight_meta[i])
                weight_meta[i]["recent_five_star_mult"] = round(mult, 3)
    return out, stats


def collect_pool_slot_members(
    family: str,
    slot: str,
    *,
    data_root: Optional[Path] = None,
) -> List[Path]:
    """Resolve ``pools.yaml`` members for a given slot (e.g. source_video / source_still)."""
    data_root = (data_root or _default_data_root()).resolve()
    pools_path = data_root / "pools" / family / "pools.yaml"
    if not pools_path.is_file():
        return []
    try:
        from shape_factory import resolve_pool_members
    except ImportError:
        return []
    pools_doc = load_yaml(pools_path)
    pools = pools_doc.get("pools") if isinstance(pools_doc.get("pools"), dict) else {}
    want = str(slot or "").strip()
    pool_def = pools.get(want) if want else None
    if not isinstance(pool_def, dict):
        for _name, cand in pools.items():
            if isinstance(cand, dict) and str(cand.get("slot") or "") == want:
                pool_def = cand
                break
    if not isinstance(pool_def, dict):
        return []
    try:
        return list(resolve_pool_members(pool_def))
    except Exception:
        return []


def collect_pool_source_videos(
    family: str,
    *,
    data_root: Optional[Path] = None,
) -> List[Path]:
    """
    Resolve primary media-source pool members for a family.

    Prefers ``source_video`` (v2v families); falls back to ``source_still`` (i2v).
    """
    vids = collect_pool_slot_members(family, "source_video", data_root=data_root)
    if vids:
        return vids
    return collect_pool_slot_members(family, "source_still", data_root=data_root)


def collect_replay_recipes(
    family: str,
    *,
    data_root: Optional[Path] = None,
    job_dir: Optional[Path] = None,
) -> List[dict[str, Any]]:
    """
    Gather replay recipes from:
    - completed / deposited shape_factory jobs
    - historical OG outputs indexed in deposit pools (e.g. early April FB9_GEX2 seeds)
    """
    data_root = (data_root or _default_data_root()).resolve()
    shape_path = data_root / "shapes" / f"{family}.shape.yaml"
    if not shape_path.is_file():
        raise FileNotFoundError(f"missing shape for family {family!r}")
    shape = load_yaml(shape_path)

    by_combo: Dict[str, dict[str, Any]] = {}

    def add_recipe(recipe: dict[str, Any]) -> None:
        ck = str(recipe.get("combo_key") or "")
        if not ck:
            return
        by_combo[ck] = recipe

    # 1) Factory jobs (recent replays)
    jobs_root = job_dir or (_default_job_dir(data_root) / family)
    if jobs_root.is_dir():
        for path in sorted(jobs_root.glob("*.job.json")):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not _job_is_replayable(job):
                continue
            picks = _picks_from_job(job, shape=shape, data_root=data_root)
            if not picks:
                continue
            add_recipe(
                _recipe_from_picks(
                    family=family,
                    picks=picks,
                    source=str(job.get("job_key") or path.stem),
                    output_path=(job.get("deposit") or {}).get("videos", [None])[0]
                    if isinstance(job.get("deposit"), dict)
                    else None,
                )
            )

    # 2) Deposit pool index — includes pre-factory OG runs (early April, etc.)
    index_path = data_root / "pools" / family / "index.json"
    if index_path.is_file():
        try:
            index_doc = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            index_doc = {}
        for _pool_id, pool in (index_doc.get("pools") or {}).items():
            if not isinstance(pool, dict):
                continue
            for member in pool.get("members") or []:
                if not isinstance(member, dict):
                    continue
                out_mp4 = str(member.get("path") or "")
                if not out_mp4.lower().endswith(".mp4"):
                    continue
                out_path = Path(out_mp4)
                if not out_path.is_file():
                    resolved = _resolve_media_path(out_mp4, data_root=data_root)
                    if resolved is None:
                        continue
                    out_path = resolved

                job_key = str(member.get("job_key") or "")
                if job_key and jobs_root.is_dir():
                    job_file = jobs_root / f"{job_key}.job.json"
                    if job_file.is_file():
                        continue  # already ingested from jobs

                workflow = _png_workflow_for_output(out_path)
                if workflow is None:
                    continue
                label = out_path.stem
                picks = _picks_from_ui_workflow(
                    workflow,
                    shape=shape,
                    data_root=data_root,
                    label=label,
                )
                if picks is None:
                    continue
                add_recipe(
                    _recipe_from_picks(
                        family=family,
                        picks=picks,
                        source=f"og:{out_path}",
                        output_path=str(out_path),
                    )
                )

    return list(by_combo.values())


def plan_hourly_replay(
    *,
    cursor: int = 0,
    data_root: Optional[Path] = None,
    job_dir: Optional[Path] = None,
    family: str = "FB9_GEX2",
) -> Dict[str, Any]:
    """Pick a previous run to reproduce, biased toward rated keepers when ratings_index exists."""
    data_root = (data_root or _default_data_root()).resolve()
    shape_path = data_root / "shapes" / f"{family}.shape.yaml"
    shape = load_yaml(shape_path) if shape_path.is_file() else {}

    recipes = collect_replay_recipes(family, data_root=data_root, job_dir=job_dir)
    if not recipes:
        return {
            "ok": False,
            "error": "no_replay_recipes",
            "family": family,
            "recipe_count": 0,
        }

    ratings_doc = _load_ratings_index(data_root)
    heuristics_doc = _load_heuristics_index(data_root)
    appetite_doc = _load_appetite_index(data_root)
    blend = float(__import__("os").environ.get("HOURLY_RATING_BLEND", "0.75"))
    blend = max(0.0, min(1.0, blend))
    recent = _recent_combo_keys(data_root=data_root, family=family)
    recent_sources = _recent_source_basenames(recent)

    weights: List[float] = []
    weight_meta: List[dict[str, Any]] = []
    for recipe in recipes:
        rated_w, meta = _recipe_selection_weight(
            recipe, ratings_doc=ratings_doc, shape=shape, heuristics_doc=heuristics_doc, appetite_doc=appetite_doc
        )
        # Omit (explicit: 0) must not keep residual uniform blend weight.
        if meta.get("omit"):
            final_w = 0.0
        else:
            uniform_w = 1.0
            final_w = (1.0 - blend) * uniform_w + blend * rated_w
        weights.append(final_w)
        weight_meta.append(meta)
    weights, five_star_stats = _apply_recent_five_star_bias(
        recipes, weights, weight_meta, ratings_doc
    )
    weights = _apply_recent_combo_penalty(recipes, weights, recent)
    weights = _apply_recent_source_penalty(recipes, weights, recent_sources)
    weights = _apply_source_promotion(recipes, weights, weight_meta)
    weights = _apply_archive_age_spread(recipes, weights, weight_meta)

    eligible_recipes: List[dict[str, Any]] = []
    eligible_weights: List[float] = []
    eligible_meta: List[dict[str, Any]] = []
    omit_count = 0
    for recipe, weight, meta in zip(recipes, weights, weight_meta):
        if meta.get("omit"):
            omit_count += 1
            continue
        eligible_recipes.append(recipe)
        eligible_weights.append(weight)
        eligible_meta.append(meta)

    if not eligible_recipes:
        return {
            "ok": False,
            "error": "no_eligible_replay_recipes",
            "family": family,
            "recipe_count": len(recipes),
            "omit_excluded": omit_count,
        }

    archive_idxs = [
        i for i, r in enumerate(eligible_recipes) if _is_archive_og_recipe(r)
    ]
    archive_forced = False
    if archive_idxs and _want_archive_og_sample(int(cursor)):
        eligible_recipes = [eligible_recipes[i] for i in archive_idxs]
        eligible_weights = [eligible_weights[i] for i in archive_idxs]
        eligible_meta = [eligible_meta[i] for i in archive_idxs]
        archive_forced = True

    rng = random.Random(int(cursor))
    recipe, recipe_index = _weighted_choice(eligible_recipes, eligible_weights, rng)
    picks = recipe.get("picks") if isinstance(recipe.get("picks"), dict) else {}
    sel_meta = eligible_meta[recipe_index] if recipe_index < len(eligible_meta) else {}

    return {
        "ok": True,
        "family": family,
        "pick_mode": "replay",
        "combo_key": recipe.get("combo_key"),
        "picks": picks,
        "bindings_preview": recipe.get("bindings_preview"),
        "source": recipe.get("source"),
        "output_path": recipe.get("output_path"),
        "cursor": int(cursor),
        "recipe_count": len(recipes),
        "eligible_recipe_count": len(eligible_recipes),
        "omit_excluded": omit_count,
        "recipe_index": recipe_index,
        "selection_weight": round(eligible_weights[recipe_index], 3),
        "rating_effective": sel_meta.get("rating_effective"),
        "rating_evidence": sel_meta.get("evidence"),
        "rating_kind": sel_meta.get("rating_kind") or ("explicit" if sel_meta.get("explicit") is not None else None),
        "ratings_index_loaded": ratings_doc is not None,
        "heuristics_index_loaded": heuristics_doc is not None,
        "appetite_index_loaded": appetite_doc is not None,
        "appetite": sel_meta.get("appetite"),
        "appetite_facet": sel_meta.get("appetite_facet"),
        "appetite_value": sel_meta.get("appetite_value"),
        "rating_blend": blend,
        "recent_combo_penalty": bool(recent),
        "recent_source_penalty": bool(recent_sources),
        "top_of_hour": bool(five_star_stats.get("top_of_hour")),
        "recent_five_star_boosted": int(five_star_stats.get("recent_five_star_boosted") or 0),
        "recent_five_star_max_mult": round(float(five_star_stats.get("recent_five_star_max_mult") or 1.0), 3),
        "recent_five_star_mult": sel_meta.get("recent_five_star_mult"),
        "archive_og_forced": archive_forced,
        "archive_og_candidate_count": len(archive_idxs),
        "archive_og": bool(sel_meta.get("archive_og") or _is_archive_og_recipe(recipe)),
        "archive_age_mult": sel_meta.get("archive_age_mult"),
        "next_cursor": int(cursor) + 1,
    }


_SOURCE_SLOT_HINTS = ("source_video", "source_still", "source_video_ref")


def _is_source_slot(slot: str) -> bool:
    s = str(slot or "").lower()
    return s in _SOURCE_SLOT_HINTS or "source" in s or "video" in s or "image" in s or "still" in s


def _is_video_slot(slot: str) -> bool:
    return "video" in str(slot or "").lower()


def _appetite_weight(value: float) -> float:
    """Derive-pass weight: appetite-dominant (2.5->~1.4, 4->~3.9, 5->~6)."""
    v = max(0.0, min(5.0, float(value)))
    return max(0.05, ((v - 1.0) / 4.0) ** 1.5 * 6.0)


def _recipe_appetite(
    recipe: dict[str, Any],
    *,
    shape: dict[str, Any],
    ratings_doc: Optional[dict[str, Any]],
    heuristics_doc: Optional[dict[str, Any]],
    appetite_doc: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve appetite (state/facet/value/fast_track) for a recipe via score_recipe meta."""
    try:
        from shape_factory_heuristics import score_recipe

        _w, meta = score_recipe(
            recipe,
            shape=shape,
            ratings_doc=ratings_doc,
            heuristics_doc=heuristics_doc,
            appetite_doc=appetite_doc,
        )
    except ImportError:
        meta = {}
    return {
        "appetite": meta.get("appetite"),
        "facet": meta.get("appetite_facet") or "both",
        "value": meta.get("appetite_value"),
        "fast_track": bool(meta.get("fast_track")),
        "evidence": meta.get("appetite_evidence"),
        "omit": bool(meta.get("omit")),
    }


def _pick_preferring_non_recent(
    candidates: List[Any],
    *,
    combo_for,
    recent: Set[str],
    rng: random.Random,
    weight_for: Optional[Any] = None,
) -> Optional[Any]:
    """Pick from candidates, strongly preferring ones whose combo_key is not recent."""
    if not candidates:
        return None
    fresh = [c for c in candidates if str(combo_for(c) or "") not in recent]
    pool = fresh or candidates
    if weight_for is None:
        return rng.choice(pool)
    weights = [max(0.01, float(weight_for(c))) for c in pool]
    picked, _ = _weighted_choice(pool, weights, rng)
    return picked


def _load_source_facets_doc(data_root: Path) -> Optional[dict[str, Any]]:
    try:
        from shape_factory_source_facets import default_source_facets_path, load_source_facets
    except ImportError:
        return None
    path = default_source_facets_path(_default_og_root(data_root))
    env = __import__("os").environ.get("SHAPE_FACTORY_SOURCE_FACETS", "").strip()
    if env:
        path = Path(env).expanduser()
    if not path.is_file():
        return None
    try:
        doc = load_source_facets(path)
        return doc if isinstance(doc, dict) else None
    except Exception:
        return None


def _hold_axes_from_env() -> Tuple[str, ...]:
    try:
        from shape_factory_source_facets import HOLD_AXES
    except ImportError:
        HOLD_AXES = ("appearance", "expression", "identity")
    raw = __import__("os").environ.get("HOURLY_HOLD_AXES", "").strip()
    if not raw:
        return tuple(HOLD_AXES)
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    return tuple(parts) if parts else tuple(HOLD_AXES)


def _derive_rewire(
    seed: dict[str, Any],
    *,
    facet: str,
    family: str,
    pool: List[dict[str, Any]],
    rng: random.Random,
    recent: Optional[Set[str]] = None,
    cursor: int = 0,
    facets_doc: Optional[dict[str, Any]] = None,
    extra_sources: Optional[List[str]] = None,
) -> Tuple[Optional[dict[str, Any]], str, dict[str, Any]]:
    """
    Build a "do more WITH this" recipe from a seed + its facet.

    facet=source: hold source picks, vary processing (alt prompt on same source).
    facet=processing: hold prompt, vary source (optionally within a similarity family).
    facet=both: Extend (chain seed output into the video slot) when possible, else fall back to source.

    Returns (rewired_recipe, action, meta). action is "derive" or "extend".
    Returns (None, "noop", meta) when no distinct rewire is possible.
    """
    seed_picks = seed.get("picks") if isinstance(seed.get("picks"), dict) else {}
    seed_out = str(seed.get("output_path") or "")
    seed_combo = str(seed.get("combo_key") or "")
    recent = recent or set()
    meta: dict[str, Any] = {}

    def _rebuild(picks_map: Dict[str, str], *, output_path: Optional[str], source_tag: str) -> dict[str, Any]:
        picks_paths = {slot: Path(str(p)) for slot, p in picks_map.items()}
        return _recipe_from_picks(
            family=family,
            picks=picks_paths,
            source=source_tag,
            output_path=output_path,
        )

    # Extend: chain the seed's output video into a video source slot.
    if facet == "both" and seed_out:
        video_slot = next((s for s in seed_picks if _is_video_slot(s)), None)
        if video_slot is not None:
            picks_map = dict(seed_picks)
            picks_map[video_slot] = seed_out
            rec = _rebuild(picks_map, output_path=None, source_tag=f"derive:extend:{seed.get('source') or seed_out}")
            if str(rec.get("combo_key") or "") != seed_combo:
                return rec, "extend", meta
        facet = "source"  # no useful extend -> fall back to vary-processing

    source_slots = [s for s in seed_picks if _is_source_slot(s)]
    prompt_slot = "prompt_profile" if "prompt_profile" in seed_picks else None

    if facet == "source" and source_slots and prompt_slot:
        # Same source, different prompt — synthesize from pool prompts (prefer non-recent).
        alt_prompts = sorted(
            {
                str(r["picks"].get(prompt_slot))
                for r in pool
                if isinstance(r.get("picks"), dict)
                and str(r["picks"].get(prompt_slot) or "")
                and str(r["picks"].get(prompt_slot)) != str(seed_picks.get(prompt_slot))
            }
        )

        def _combo_for_prompt(prompt_path: str) -> str:
            picks_map = {str(k): str(v) for k, v in seed_picks.items()}
            picks_map[prompt_slot] = prompt_path
            return str(
                _rebuild(
                    picks_map,
                    output_path=None,
                    source_tag=f"derive:source:{seed.get('source') or seed_out}",
                ).get("combo_key")
                or ""
            )

        chosen_prompt = _pick_preferring_non_recent(
            alt_prompts,
            combo_for=_combo_for_prompt,
            recent=recent,
            rng=rng,
        )
        if chosen_prompt:
            picks_map = {str(k): str(v) for k, v in seed_picks.items()}
            picks_map[prompt_slot] = chosen_prompt
            rec = _rebuild(
                picks_map,
                output_path=None,
                source_tag=f"derive:source:{seed.get('source') or seed_out}",
            )
            if str(rec.get("combo_key") or "") != seed_combo:
                return rec, "derive", meta

    if facet == "processing" and prompt_slot and source_slots:
        # Same prompt, different source. Prefer sources in the same similarity
        # family on a rotating hold axis, but never trap into a tiny family that
        # has already been exhausted recently — widen for source novelty.
        primary = source_slots[0]
        seed_source = str(seed_picks.get(primary) or "")
        all_alts_set: Set[str] = {
            str(r["picks"].get(primary))
            for r in pool
            if isinstance(r.get("picks"), dict)
            and str(r["picks"].get(primary) or "")
            and str(r["picks"].get(primary)) != seed_source
        }
        # Include pools.yaml source_video members (X-Kneel library, etc.) even if
        # they never appeared in a past recipe combo.
        for raw in extra_sources or []:
            s = str(raw or "").strip()
            if not s or s == seed_source:
                continue
            all_alts_set.add(s)
        all_alts = sorted(all_alts_set)
        recent_sources = _recent_source_basenames(recent)
        try:
            from shape_factory_source_facets import filter_sources_by_hold_axis, hold_axis_for_cursor
        except ImportError:
            filter_sources_by_hold_axis = None  # type: ignore
            hold_axis_for_cursor = None  # type: ignore

        hold_meta: dict[str, Any] = {"candidate_count_unfiltered": len(all_alts)}
        family_alts = list(all_alts)
        if filter_sources_by_hold_axis and hold_axis_for_cursor:
            hold_axis = hold_axis_for_cursor(int(cursor), axes=_hold_axes_from_env())
            family_alts, hold_meta = filter_sources_by_hold_axis(
                all_alts,
                seed_source=seed_source,
                hold_axis=hold_axis,
                facets_doc=facets_doc,
            )
        family_fresh = [s for s in family_alts if not _source_in_recent(s, recent_sources)]
        any_fresh = [s for s in all_alts if not _source_in_recent(s, recent_sources)]
        if family_fresh:
            alt_sources = family_fresh
            hold_meta["source_novelty"] = True
        elif any_fresh:
            alt_sources = any_fresh
            hold_meta["facet_constrained"] = False
            hold_meta["fallback"] = "widen_for_source_novelty"
            hold_meta["source_novelty"] = True
        else:
            alt_sources = family_alts or all_alts
            hold_meta["source_novelty"] = False

        archive_alts = [s for s in alt_sources if _is_archive_og_path(s)]
        hold_meta["archive_og_candidate_count"] = len(archive_alts)
        if archive_alts and _want_archive_og_sample(int(cursor), salt=0xD3A1):
            alt_sources = archive_alts
            hold_meta["archive_og_forced"] = True
        else:
            hold_meta["archive_og_forced"] = False

        hold_meta["candidate_count"] = len(alt_sources)
        meta.update(hold_meta)

        def _combo_for_source(source_path: str) -> str:
            picks_map = {str(k): str(v) for k, v in seed_picks.items()}
            picks_map[primary] = source_path
            return str(
                _rebuild(
                    picks_map,
                    output_path=None,
                    source_tag=f"derive:processing:{seed.get('source') or seed_out}",
                ).get("combo_key")
                or ""
            )

        def _source_weight(path: str) -> float:
            return float(_source_promotion_mult(path)) * float(_archive_age_spread_mult(path))

        chosen_source = _pick_preferring_non_recent(
            alt_sources,
            combo_for=_combo_for_source,
            recent=recent,
            rng=rng,
            weight_for=_source_weight,
        )
        if chosen_source:
            picks_map = {str(k): str(v) for k, v in seed_picks.items()}
            picks_map[primary] = chosen_source
            rec = _rebuild(
                picks_map,
                output_path=None,
                source_tag=f"derive:processing:{seed.get('source') or seed_out}",
            )
            if str(rec.get("combo_key") or "") != seed_combo:
                return rec, "derive", meta

    return None, "noop", meta


def _combo_key_from_job_key(job_key: str) -> str:
    """Strip hourly/family prefixes and timestamp suffixes from a job_key to a combo_key."""
    raw = str(job_key or "").strip()
    if "::" in raw:
        raw = raw.split("::", 1)[-1]
    if raw.startswith("hourly__"):
        raw = raw[len("hourly__") :]
    # Drop trailing __000_YYYYmmddHHMM / __000_h... style suffixes.
    raw = re.sub(r"__000_(?:h)?\d{8,}.*$", "", raw)
    return raw


def _recent_combo_keys(
    *,
    data_root: Path,
    family: str,
    limit: int = 48,
) -> Set[str]:
    """Combo keys from the most recent hourly jobs — used to avoid repeats."""
    out: Set[str] = set()
    jobs_root = _default_job_dir(data_root) / family
    if not jobs_root.is_dir():
        return out
    # Prefer hourly__* job files; fall back to any recent jobs if few hourlies exist.
    hourly_paths = sorted(
        jobs_root.glob("hourly__*.job.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    paths = hourly_paths[: max(1, int(limit))]
    if len(paths) < max(1, int(limit) // 2):
        extra = sorted(jobs_root.glob("*.job.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        seen = {p.resolve() for p in paths}
        for path in extra:
            if path.resolve() in seen:
                continue
            paths.append(path)
            if len(paths) >= max(1, int(limit)):
                break
    for path in paths:
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        ck = ""
        if isinstance(job.get("combo_key"), str) and job.get("combo_key"):
            ck = str(job["combo_key"])
        else:
            ck = _combo_key_from_job_key(str(job.get("job_key") or path.stem))
        if ck:
            out.add(ck)
    state_path = data_root / "shape_factory" / "hourly-state.json"
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            last = str(state.get("last_combo_key") or "").strip()
            if last:
                out.add(last)
        except Exception:
            pass
    return out


def _prompt_slug_from_combo(combo_key: str) -> str:
    raw = str(combo_key or "")
    if "prompt_profile-" not in raw:
        return ""
    rest = raw.split("prompt_profile-", 1)[1]
    return rest.split("__", 1)[0]


def _source_basename_from_combo(combo_key: str) -> str:
    raw = str(combo_key or "")
    if "source_video-" not in raw:
        return ""
    rest = raw.split("source_video-", 1)[1]
    # Combo keys omit extensions; recipe paths usually include .mp4 — compare basenames.
    return rest.split("__", 1)[0]


def _source_basename_from_path(path: str) -> str:
    name = Path(str(path or "")).name
    if name.lower().endswith((".mp4", ".webm", ".mov", ".mkv", ".png", ".jpg", ".jpeg", ".webp")):
        return Path(name).stem
    return name


def _recent_source_basenames(recent_combos: Set[str]) -> Set[str]:
    return {b for b in (_source_basename_from_combo(ck) for ck in recent_combos) if b}


def _apply_recent_combo_penalty(
    recipes: List[dict[str, Any]],
    weights: List[float],
    recent: Set[str],
    *,
    penalty: float = 0.08,
) -> List[float]:
    """Strongly downweight combos seen in recent hourly jobs."""
    if not recent:
        return weights
    pen = max(0.01, min(1.0, float(penalty)))
    out: List[float] = []
    for recipe, weight in zip(recipes, weights):
        ck = str(recipe.get("combo_key") or "")
        out.append(float(weight) * (pen if ck in recent else 1.0))
    return out


def _source_in_recent(path_or_stem: str, recent_sources: Set[str]) -> bool:
    src = _source_basename_from_path(path_or_stem)
    if not src or not recent_sources:
        return False
    if src in recent_sources:
        return True
    return any(src == rs or src.startswith(rs) or rs.startswith(src) for rs in recent_sources)


def _apply_recent_source_penalty(
    recipes: List[dict[str, Any]],
    weights: List[float],
    recent_sources: Set[str],
    *,
    penalty: float = 0.12,
) -> List[float]:
    """Downweight recipes whose source_video was used in recent hourlies (even with a new prompt)."""
    if not recent_sources:
        return weights
    pen = max(0.01, min(1.0, float(penalty)))
    out: List[float] = []
    for recipe, weight in zip(recipes, weights):
        picks = recipe.get("picks") if isinstance(recipe.get("picks"), dict) else {}
        hit = _source_in_recent(str(picks.get("source_video") or ""), recent_sources)
        out.append(float(weight) * (pen if hit else 1.0))
    return out


def plan_hourly_derive(
    *,
    cursor: int = 0,
    data_root: Optional[Path] = None,
    job_dir: Optional[Path] = None,
    family: str = "FB9_GEX2",
) -> Dict[str, Any]:
    """
    Appetite-driven "do more WITH this" step: pick a high-appetite asset and build from it.

    Uses the same recipe pool as replay, but selects seeds by appetite (not quality) and
    rewires per facet (source/processing/both -> Extend). fast_track seeds are pinned.
    Never returns a no-op exact replay of the seed — falls back to the caller (replay).
    """
    data_root = (data_root or _default_data_root()).resolve()
    shape_path = data_root / "shapes" / f"{family}.shape.yaml"
    shape = load_yaml(shape_path) if shape_path.is_file() else {}

    recipes = collect_replay_recipes(family, data_root=data_root, job_dir=job_dir)
    if not recipes:
        return {"ok": False, "error": "no_replay_recipes", "family": family, "recipe_count": 0}

    pool_source_paths = collect_pool_source_videos(family, data_root=data_root)
    pool_sources = [str(p) for p in pool_source_paths]

    ratings_doc = _load_ratings_index(data_root)
    heuristics_doc = _load_heuristics_index(data_root)
    appetite_doc = _load_appetite_index(data_root)
    tags_doc = _load_asset_tags(data_root)
    if appetite_doc is None and heuristics_doc is None:
        return {"ok": False, "error": "no_appetite_signal", "family": family, "recipe_count": len(recipes)}

    recent = _recent_combo_keys(data_root=data_root, family=family, limit=12)
    facets_doc = _load_source_facets_doc(data_root)
    seeds: List[dict[str, Any]] = []
    weights: List[float] = []
    fast_tracks: List[int] = []
    omit_count = 0
    for recipe in recipes:
        info = _recipe_appetite(
            recipe, shape=shape, ratings_doc=ratings_doc, heuristics_doc=heuristics_doc, appetite_doc=appetite_doc
        )
        # Quality omit (explicit: 0) — never seed derive even if appetite is high.
        if info.get("omit"):
            omit_count += 1
            continue
        value = info.get("value")
        if value is None or float(value) <= 2.5:  # only "more"/"fast_track" (above neutral)
            continue
        weight = _appetite_weight(float(value))
        # Minor tag-affinity nudge: bias toward content the user has appetite for.
        tag_aff = _tag_affinity_for_output(
            str(recipe.get("output_path") or ""), tags_doc=tags_doc, heuristics_doc=heuristics_doc
        )
        if tag_aff:
            info["tag_affinity"] = round(tag_aff, 3)
            weight *= max(0.5, min(1.5, 1.0 + 0.12 * (tag_aff - 2.5)))
        ck = str(recipe.get("combo_key") or "")
        if ck in recent:
            weight *= 0.08
        weight *= _recipe_promotion_mult(recipe)
        weight *= _archive_age_spread_mult(recipe)
        seeds.append({"recipe": recipe, "info": info})
        weights.append(weight)
        if info.get("fast_track"):
            fast_tracks.append(len(seeds) - 1)

    if not seeds:
        return {
            "ok": False,
            "error": "no_appetite_seeds",
            "family": family,
            "recipe_count": len(recipes),
            "omit_excluded": omit_count,
        }

    rng = random.Random(int(cursor) ^ 0x0A9E)
    promo_share = float(os.environ.get("HOURLY_PROMOTED_SOURCE_SHARE", "0.35"))
    promo_share = max(0.0, min(1.0, promo_share))
    # Try several seeds until rewire produces a distinct combo.
    attempt_order: List[int] = []
    if fast_tracks:
        shuffled_ft = list(fast_tracks)
        rng.shuffle(shuffled_ft)
        attempt_order.extend(shuffled_ft)
    remaining = [i for i in range(len(seeds)) if i not in set(attempt_order)]
    while remaining and len(attempt_order) < min(12, len(seeds)):
        rem_recipes = [seeds[i]["recipe"] for i in remaining]
        rem_weights = [weights[i] for i in remaining]
        _picked, local_i = _weighted_choice(rem_recipes, rem_weights, rng)
        chosen_i = remaining[local_i]
        attempt_order.append(chosen_i)
        remaining.pop(local_i)

    tried = 0
    fallback: Optional[Tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, int, dict[str, Any]]] = None
    for pick_i in attempt_order:
        chosen = seeds[pick_i]
        seed_recipe = chosen["recipe"]
        info = chosen["info"]
        facet = str(info.get("facet") or "both")
        # Periodically force processing so Kneel/2025 pool clips enter as source_video.
        if promo_share > 0 and pool_sources and rng.random() < promo_share:
            facet = "processing"
            info = dict(info)
            info["facet"] = facet
            info["promoted_source_override"] = True
        rewired, action, hold_meta = _derive_rewire(
            seed_recipe,
            facet=facet,
            family=family,
            pool=recipes,
            rng=rng,
            recent=recent,
            cursor=int(cursor),
            facets_doc=facets_doc,
            extra_sources=pool_sources,
        )
        tried += 1
        if rewired is None:
            continue
        ck = str(rewired.get("combo_key") or "")
        if not ck or ck == str(seed_recipe.get("combo_key") or ""):
            continue
        payload_bits = (rewired, seed_recipe, info, action, pick_i, hold_meta)
        if ck in recent:
            if fallback is None:
                fallback = payload_bits
            continue
        rewired, seed_recipe, info, action, pick_i, hold_meta = payload_bits
        out = {
            "ok": True,
            "family": family,
            "pick_mode": action,
            "derive_action": action,
            "combo_key": rewired.get("combo_key"),
            "picks": rewired.get("picks"),
            "bindings_preview": rewired.get("bindings_preview"),
            "source": rewired.get("source"),
            "output_path": rewired.get("output_path"),
            "parent_output": str(seed_recipe.get("output_path") or ""),
            "appetite": info.get("appetite"),
            "appetite_facet": str(info.get("facet") or facet),
            "appetite_value": info.get("value"),
            "appetite_evidence": info.get("evidence"),
            "tag_affinity": info.get("tag_affinity"),
            "fast_track": bool(info.get("fast_track")),
            "cursor": int(cursor),
            "recipe_count": len(recipes),
            "omit_excluded": omit_count,
            "pool_source_count": len(pool_sources),
            "seed_count": len(seeds),
            "selection_weight": round(weights[pick_i], 3),
            "appetite_index_loaded": appetite_doc is not None,
            "heuristics_index_loaded": heuristics_doc is not None,
            "source_facets_loaded": facets_doc is not None,
            "derive_attempts": tried,
            "recent_combo_penalty": True,
            "used_recent_fallback": False,
            "next_cursor": int(cursor) + 1,
        }
        if hold_meta:
            out["hold_axis"] = hold_meta.get("hold_axis")
            out["hold_values"] = hold_meta.get("hold_values")
            out["hold_candidate_count"] = hold_meta.get("candidate_count")
            out["hold_facet_constrained"] = hold_meta.get("facet_constrained")
            if hold_meta.get("fallback"):
                out["hold_fallback"] = hold_meta.get("fallback")
            if "archive_og_forced" in hold_meta:
                out["archive_og_forced"] = bool(hold_meta.get("archive_og_forced"))
            if hold_meta.get("archive_og_candidate_count") is not None:
                out["archive_og_candidate_count"] = hold_meta.get("archive_og_candidate_count")
        return out

    # Prefer failing over to replay rather than re-queueing a combo we just ran.
    if fallback is not None:
        return {
            "ok": False,
            "error": "derive_only_recent_combos",
            "family": family,
            "recipe_count": len(recipes),
            "seed_count": len(seeds),
            "derive_attempts": tried,
            "used_recent_fallback": True,
            "combo_key": (fallback[0] or {}).get("combo_key"),
            "hold_axis": (fallback[5] or {}).get("hold_axis"),
            "hold_values": (fallback[5] or {}).get("hold_values"),
            "source_facets_loaded": facets_doc is not None,
        }

    return {
        "ok": False,
        "error": "derive_no_distinct_combo",
        "family": family,
        "recipe_count": len(recipes),
        "seed_count": len(seeds),
        "derive_attempts": tried,
        "source_facets_loaded": facets_doc is not None,
    }


# Seed families for idle hourly ticks (weights sum to 100 by default).
_DEFAULT_SEED_FAMILY_WEIGHTS: Tuple[Tuple[str, int], ...] = (
    ("FB9_GEX2", 70),
    ("FB9_GEX", 30),
    # FaceBlast / Kneel seeds paused: Comfy LoadImage rejects many still paths;
    # keep Kneel→GEX2 chain follow-up, but do not generate new i2v seeds hourly.
)


def _seed_family_weights() -> List[Tuple[str, int]]:
    """Parse HOURLY_SEED_FAMILIES=Fam:weight,... or use defaults."""
    import os

    raw = os.environ.get("HOURLY_SEED_FAMILIES", "").strip()
    if not raw:
        return list(_DEFAULT_SEED_FAMILY_WEIGHTS)
    out: List[Tuple[str, int]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            name, w_s = part.rsplit(":", 1)
            try:
                w = max(1, int(w_s))
            except ValueError:
                w = 1
            out.append((name.strip(), w))
        else:
            out.append((part, 1))
    return out or list(_DEFAULT_SEED_FAMILY_WEIGHTS)


def select_seed_family(cursor: int = 0) -> str:
    """Deterministic weighted family pick for an idle seed tick."""
    weights = _seed_family_weights()
    rng = random.Random(int(cursor) ^ 0xFA21)
    total = sum(w for _, w in weights)
    pick = rng.random() * float(total)
    acc = 0.0
    for fam, w in weights:
        acc += float(w)
        if pick <= acc:
            return fam
    return weights[-1][0]


def _default_job_root(data_root: Optional[Path] = None) -> Path:
    data_root = (data_root or _default_data_root()).resolve()
    return _default_job_dir(data_root)


def find_gex2_needing_facial(
    *,
    data_root: Optional[Path] = None,
    job_dir: Optional[Path] = None,
) -> Optional[str]:
    """Return job_key of newest complete GEX2 whose deposit has no FACIAL child."""
    root = job_dir or _default_job_root(data_root)
    gex2_done: List[Tuple[str, str]] = []
    gex2_root = root / "FB9_GEX2"
    if gex2_root.is_dir():
        for path in sorted(gex2_root.glob("*.job.json")):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if job.get("status") != "complete":
                continue
            dep = job.get("deposit") if isinstance(job.get("deposit"), dict) else {}
            vids = dep.get("videos") or []
            if not vids:
                continue
            gex2_done.append((str(job.get("job_key") or path.stem), str(vids[-1])))
    facial_sources: Set[str] = set()
    facial_root = root / "FB9_GEX_FACIAL"
    if facial_root.is_dir():
        for path in facial_root.glob("*.job.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            sv = (job.get("bindings") or {}).get("source_video") or {}
            if isinstance(sv, dict):
                facial_sources.add(str(sv.get("path") or ""))
    for job_key, vid in reversed(gex2_done):
        if vid not in facial_sources:
            return job_key
    return None


def find_kneel_needing_gex2(
    *,
    data_root: Optional[Path] = None,
    job_dir: Optional[Path] = None,
) -> Optional[str]:
    """Return job_key of newest complete Kneel whose deposit is unused as a GEX2 source."""
    root = job_dir or _default_job_root(data_root)
    kneel_done: List[Tuple[str, str]] = []
    kneel_root = root / "X-KNEEL-FB9"
    if kneel_root.is_dir():
        for path in sorted(kneel_root.glob("*.job.json")):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if job.get("status") != "complete":
                continue
            dep = job.get("deposit") if isinstance(job.get("deposit"), dict) else {}
            vids = dep.get("videos") or []
            if not vids:
                continue
            kneel_done.append((str(job.get("job_key") or path.stem), str(vids[-1])))
    gex2_sources: Set[str] = set()
    gex2_root = root / "FB9_GEX2"
    if gex2_root.is_dir():
        for path in gex2_root.glob("*.job.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            sv = (job.get("bindings") or {}).get("source_video") or {}
            if isinstance(sv, dict):
                gex2_sources.add(str(sv.get("path") or ""))
    for job_key, vid in reversed(kneel_done):
        if vid not in gex2_sources:
            return job_key
    return None


def plan_pool_product_fallback(
    *,
    cursor: int = 0,
    data_root: Optional[Path] = None,
    family: str = "FB9_GEX2",
) -> Dict[str, Any]:
    """
    When a family has no recoverable recipes, sample one member per required slot
    from pools.yaml so hourly can still generate.
    """
    from shape_factory import resolve_pool_members

    data_root = (data_root or _default_data_root()).resolve()
    shape_path = data_root / "shapes" / f"{family}.shape.yaml"
    pools_path = data_root / "pools" / family / "pools.yaml"
    if not shape_path.is_file() or not pools_path.is_file():
        return {
            "ok": False,
            "error": "missing_shape_or_pools",
            "family": family,
            "recipe_count": 0,
        }

    shape = load_yaml(shape_path)
    pools_doc = load_yaml(pools_path)
    req_by_slot = requires_by_slot(shape)
    pools = pools_doc.get("pools") if isinstance(pools_doc.get("pools"), dict) else {}

    pool_paths: Dict[str, List[Path]] = {}
    for _name, pool_def in pools.items():
        if not isinstance(pool_def, dict):
            continue
        slot = str(pool_def.get("slot") or _name)
        if slot not in req_by_slot:
            continue
        try:
            members = list(resolve_pool_members(pool_def))
        except Exception:
            members = []
        if members:
            pool_paths[slot] = members

    missing = [s for s, req in req_by_slot.items() if s not in pool_paths and not req.get("optional")]
    if missing:
        return {
            "ok": False,
            "error": "pool_product_missing_slots",
            "family": family,
            "missing_slots": missing,
            "recipe_count": 0,
        }

    rng = random.Random(int(cursor) ^ 0xB00F)
    picks: Dict[str, Path] = {}
    for slot, members in sorted(pool_paths.items()):
        picks[slot] = members[rng.randrange(len(members))]

    recipe = _recipe_from_picks(
        family=family,
        picks=picks,
        source=f"pool_product:{family}",
        output_path=None,
    )
    return {
        "ok": True,
        "family": family,
        "pick_mode": "pool_product",
        "step": "pool_product",
        "combo_key": recipe.get("combo_key"),
        "picks": recipe.get("picks"),
        "bindings_preview": recipe.get("bindings_preview"),
        "source": recipe.get("source"),
        "output_path": None,
        "cursor": int(cursor),
        "recipe_count": 0,
        "pool_slots": {slot: len(members) for slot, members in pool_paths.items()},
        "next_cursor": int(cursor) + 1,
    }


def plan_hourly_predicted_derive(
    *,
    cursor: int = 0,
    data_root: Optional[Path] = None,
    job_dir: Optional[Path] = None,
    family: str = "FB9_GEX2",
) -> Dict[str, Any]:
    """
    Prediction-driven hourly: weight by inferred/pattern/lineage scores (not explicit
    stars), then **always** queue as derive so we can see how predictions hold up.
    """
    import os

    data_root = (data_root or _default_data_root()).resolve()
    shape_path = data_root / "shapes" / f"{family}.shape.yaml"
    shape = load_yaml(shape_path) if shape_path.is_file() else {}

    recipes = collect_replay_recipes(family, data_root=data_root, job_dir=job_dir)
    if not recipes:
        return {"ok": False, "error": "no_replay_recipes", "family": family, "recipe_count": 0}

    ratings_doc = _load_ratings_index(data_root)
    heuristics_doc = _load_heuristics_index(data_root)
    appetite_doc = _load_appetite_index(data_root)
    if ratings_doc is None and heuristics_doc is None:
        return {"ok": False, "error": "no_predicted_signal", "family": family, "recipe_count": len(recipes)}

    explore_floor = float(os.environ.get("HOURLY_RATING_EXPLORE_FLOOR", "0.35"))
    min_weight = float(os.environ.get("HOURLY_PREDICTED_MIN_WEIGHT", str(max(explore_floor + 0.15, 0.6))))
    pool_sources = [str(p) for p in collect_pool_source_videos(family, data_root=data_root)]
    recent = _recent_combo_keys(data_root=data_root, family=family, limit=12)
    facets_doc = _load_source_facets_doc(data_root)

    seeds: List[dict[str, Any]] = []
    weights: List[float] = []
    omit_count = 0
    for recipe in recipes:
        rated_w, meta = _recipe_selection_weight(
            recipe,
            ratings_doc=ratings_doc,
            shape=shape,
            heuristics_doc=heuristics_doc,
            appetite_doc=appetite_doc,
        )
        if meta.get("omit"):
            omit_count += 1
            continue
        # Prefer predicted/inferred evidence; skip pure explicit keepers (those stay in replay).
        kind = str(meta.get("rating_kind") or "")
        if kind == "explicit":
            continue
        if meta.get("rating_effective") is None and float(rated_w) <= explore_floor + 1e-9:
            continue
        if float(rated_w) < min_weight:
            continue
        weight = float(rated_w)
        ck = str(recipe.get("combo_key") or "")
        if ck in recent:
            weight *= 0.08
        weight *= _recipe_promotion_mult(recipe)
        weight *= _archive_age_spread_mult(recipe)
        seeds.append({"recipe": recipe, "meta": meta})
        weights.append(weight)

    if not seeds:
        return {
            "ok": False,
            "error": "no_predicted_seeds",
            "family": family,
            "recipe_count": len(recipes),
            "omit_excluded": omit_count,
        }

    rng = random.Random(int(cursor) ^ 0x9AED)
    promo_share = float(os.environ.get("HOURLY_PROMOTED_SOURCE_SHARE", "0.35"))
    promo_share = max(0.0, min(1.0, promo_share))
    attempt_order: List[int] = []
    remaining = list(range(len(seeds)))
    while remaining and len(attempt_order) < min(12, len(seeds)):
        rem_recipes = [seeds[i]["recipe"] for i in remaining]
        rem_weights = [weights[i] for i in remaining]
        _picked, local_i = _weighted_choice(rem_recipes, rem_weights, rng)
        chosen_i = remaining[local_i]
        attempt_order.append(chosen_i)
        remaining.pop(local_i)

    tried = 0
    fallback: Optional[Tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, int, dict[str, Any]]] = None
    for pick_i in attempt_order:
        chosen = seeds[pick_i]
        seed_recipe = chosen["recipe"]
        meta = chosen["meta"]
        # Predicted path always derives; use appetite facet when present else both.
        app = _recipe_appetite(
            seed_recipe,
            shape=shape,
            ratings_doc=ratings_doc,
            heuristics_doc=heuristics_doc,
            appetite_doc=appetite_doc,
        )
        facet = str(app.get("facet") or "both")
        if promo_share > 0 and pool_sources and rng.random() < promo_share:
            facet = "processing"
        rewired, action, hold_meta = _derive_rewire(
            seed_recipe,
            facet=facet,
            family=family,
            pool=recipes,
            rng=rng,
            recent=recent,
            cursor=int(cursor),
            facets_doc=facets_doc,
            extra_sources=pool_sources,
        )
        tried += 1
        if rewired is None:
            continue
        ck = str(rewired.get("combo_key") or "")
        if not ck or ck == str(seed_recipe.get("combo_key") or ""):
            continue
        # Force derive pick_mode even if rewire labeled extend.
        action = "derive"
        payload_bits = (rewired, seed_recipe, meta, action, pick_i, hold_meta)
        if ck in recent:
            if fallback is None:
                fallback = payload_bits
            continue
        rewired, seed_recipe, meta, action, pick_i, hold_meta = payload_bits
        out = {
            "ok": True,
            "family": family,
            "pick_mode": "derive",
            "derive_action": action,
            "step": "predicted_derive",
            "rating_kind": "predicted",
            "combo_key": rewired.get("combo_key"),
            "picks": rewired.get("picks"),
            "bindings_preview": rewired.get("bindings_preview"),
            "source": rewired.get("source"),
            "output_path": rewired.get("output_path"),
            "parent_output": str(seed_recipe.get("output_path") or ""),
            "rating_effective": meta.get("rating_effective"),
            "rating_evidence": meta.get("evidence"),
            "appetite": app.get("appetite"),
            "appetite_facet": facet,
            "appetite_value": app.get("value"),
            "appetite_evidence": app.get("evidence"),
            "cursor": int(cursor),
            "recipe_count": len(recipes),
            "omit_excluded": omit_count,
            "pool_source_count": len(pool_sources),
            "seed_count": len(seeds),
            "selection_weight": round(weights[pick_i], 3),
            "ratings_index_loaded": ratings_doc is not None,
            "heuristics_index_loaded": heuristics_doc is not None,
            "appetite_index_loaded": appetite_doc is not None,
            "source_facets_loaded": facets_doc is not None,
            "derive_attempts": tried,
            "recent_combo_penalty": True,
            "used_recent_fallback": False,
            "next_cursor": int(cursor) + 1,
        }
        if hold_meta:
            out["hold_axis"] = hold_meta.get("hold_axis")
            out["hold_values"] = hold_meta.get("hold_values")
            out["hold_candidate_count"] = hold_meta.get("candidate_count")
            out["hold_facet_constrained"] = hold_meta.get("facet_constrained")
            if hold_meta.get("fallback"):
                out["hold_fallback"] = hold_meta.get("fallback")
            if "archive_og_forced" in hold_meta:
                out["archive_og_forced"] = bool(hold_meta.get("archive_og_forced"))
            if hold_meta.get("archive_og_candidate_count") is not None:
                out["archive_og_candidate_count"] = hold_meta.get("archive_og_candidate_count")
        return out

    if fallback is not None:
        return {
            "ok": False,
            "error": "predicted_only_recent_combos",
            "family": family,
            "recipe_count": len(recipes),
            "seed_count": len(seeds),
            "derive_attempts": tried,
            "used_recent_fallback": True,
            "rating_kind": "predicted",
        }

    return {
        "ok": False,
        "error": "predicted_no_distinct_combo",
        "family": family,
        "recipe_count": len(recipes),
        "seed_count": len(seeds),
        "derive_attempts": tried,
        "omit_excluded": omit_count,
        "rating_kind": "predicted",
    }


def plan_hourly_step(
    *,
    cursor: int = 0,
    data_root: Optional[Path] = None,
    job_dir: Optional[Path] = None,
    family: str = "FB9_GEX2",
) -> Dict[str, Any]:
    """
    Choose the hourly action: Predicted-derive, Replay ("do more OF"), or Derive ("do more WITH").

    Predicted/inferred seeds are mixed in via HOURLY_PREDICTED_SHARE and always queued as
    derive so prediction quality can be judged on new outputs. Explicit omit stays excluded.

    Near wall-clock top-of-hour, prefer replay of recent 5★ keepers over the predicted/derive mix.
    """
    data_root = (data_root or _default_data_root()).resolve()
    derive_share = float(os.environ.get("HOURLY_DERIVE_SHARE", "0.5"))
    derive_share = max(0.0, min(1.0, derive_share))
    predicted_share = float(os.environ.get("HOURLY_PREDICTED_SHARE", "0.35"))
    predicted_share = max(0.0, min(1.0, predicted_share))
    top_of_hour = _is_top_of_hour()

    predicted = plan_hourly_predicted_derive(
        cursor=cursor, data_root=data_root, job_dir=job_dir, family=family
    )
    derive = plan_hourly_derive(cursor=cursor, data_root=data_root, job_dir=job_dir, family=family)
    # Pin fast_track only for a fresh combo whose prompt is not already dominating recent hourlies.
    if derive.get("ok") and derive.get("fast_track") and not derive.get("used_recent_fallback"):
        recent = _recent_combo_keys(data_root=data_root, family=family)
        prompt = _prompt_slug_from_combo(str(derive.get("combo_key") or ""))
        prompt_hits = sum(1 for ck in recent if _prompt_slug_from_combo(ck) == prompt) if prompt else 0
        if prompt_hits < 2:
            derive["step"] = "derive"
            derive["rating_kind"] = derive.get("rating_kind") or "appetite"
            derive["top_of_hour"] = top_of_hour
            return derive

    # Top-of-hour: skip predicted/derive lottery and favor quality replay (recent 5★ boost inside).
    if top_of_hour:
        replay = plan_hourly_replay(cursor=cursor, data_root=data_root, job_dir=job_dir, family=family)
        if replay.get("ok"):
            if str(replay.get("rating_kind") or "") == "predicted":
                upgraded = plan_hourly_predicted_derive(
                    cursor=cursor, data_root=data_root, job_dir=job_dir, family=family
                )
                if upgraded.get("ok"):
                    upgraded["step"] = "predicted_derive"
                    upgraded["upgraded_from"] = "replay"
                    upgraded["top_of_hour"] = True
                    return upgraded
            else:
                replay["step"] = "replay"
                replay["rating_kind"] = replay.get("rating_kind") or "explicit"
                replay["top_of_hour"] = True
                return replay

    want_predicted = random.Random(int(cursor) ^ 0x517A).random() < predicted_share
    if predicted.get("ok") and want_predicted:
        predicted["step"] = "predicted_derive"
        predicted["top_of_hour"] = top_of_hour
        return predicted

    want_derive = random.Random(int(cursor) ^ 0x5EED).random() < derive_share
    if derive.get("ok") and want_derive:
        derive["step"] = "derive"
        derive["rating_kind"] = derive.get("rating_kind") or "appetite"
        derive["top_of_hour"] = top_of_hour
        return derive

    replay = plan_hourly_replay(cursor=cursor, data_root=data_root, job_dir=job_dir, family=family)
    if replay.get("ok"):
        # Predicted/inferred winners must never exact-replay — always derive.
        if str(replay.get("rating_kind") or "") == "predicted":
            upgraded = plan_hourly_predicted_derive(
                cursor=cursor, data_root=data_root, job_dir=job_dir, family=family
            )
            if upgraded.get("ok"):
                upgraded["step"] = "predicted_derive"
                upgraded["upgraded_from"] = "replay"
                upgraded["top_of_hour"] = top_of_hour
                return upgraded
        replay["step"] = "replay"
        replay["rating_kind"] = replay.get("rating_kind") or "explicit"
        replay["top_of_hour"] = top_of_hour
        return replay
    if predicted.get("ok"):
        predicted["step"] = "predicted_derive"
        predicted["top_of_hour"] = top_of_hour
        return predicted
    if derive.get("ok"):
        derive["step"] = "derive"
        derive["rating_kind"] = derive.get("rating_kind") or "appetite"
        derive["top_of_hour"] = top_of_hour
        return derive

    fallback = plan_pool_product_fallback(cursor=cursor, data_root=data_root, family=family)
    if fallback.get("ok"):
        fallback["top_of_hour"] = top_of_hour
        return fallback
    # Prefer the more informative error from replay/derive when fallback also fails.
    if isinstance(replay, dict) and replay.get("error"):
        out = dict(replay)
        out["pool_product_error"] = fallback.get("error")
        out["predicted_error"] = predicted.get("error")
        out["top_of_hour"] = top_of_hour
        return out
    fallback["top_of_hour"] = top_of_hour
    return fallback


def plan_hourly_gex2(
    *,
    cursor: int = 0,
    data_root: Optional[Path] = None,
    job_dir: Optional[Path] = None,
    family: str = "FB9_GEX2",
) -> Dict[str, Any]:
    """Hourly GEX2 step: replay a random previous run for this family."""
    return plan_hourly_replay(cursor=cursor, data_root=data_root, job_dir=job_dir, family=family)


def predict_hourly_gex2(
    hourly_state: Dict[str, Any],
    *,
    data_root: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Map API / UI preview of the next hourly step (same family selection as the shell)."""
    cursor = int(hourly_state.get("sample_cursor") or 0)
    data_root = (data_root or _default_data_root()).resolve()
    job_root = _default_job_root(data_root)

    need_facial = find_gex2_needing_facial(data_root=data_root, job_dir=job_root)
    if need_facial:
        return {
            "cursor": cursor,
            "phase_if_idle": "facial",
            "family": "FB9_GEX_FACIAL",
            "pick_mode": "chain",
            "step": "chain_facial",
            "parent_job": need_facial,
            "ok": True,
        }

    need_kneel = find_kneel_needing_gex2(data_root=data_root, job_dir=job_root)
    if need_kneel:
        return {
            "cursor": cursor,
            "phase_if_idle": "gex2_from_kneel",
            "family": "FB9_GEX2",
            "pick_mode": "chain",
            "step": "chain_gex2_from_kneel",
            "parent_job": need_kneel,
            "ok": True,
        }

    family = select_seed_family(cursor)
    plan = plan_hourly_step(cursor=cursor, data_root=data_root, family=family)
    if not plan.get("ok"):
        return {
            "cursor": cursor,
            "phase_if_idle": hourly_state.get("phase"),
            "family": family,
            "error": plan.get("error"),
            "recipe_count": plan.get("recipe_count"),
        }
    preview = dict(plan)
    preview["phase_if_idle"] = hourly_state.get("phase")
    preview["family"] = family
    preview["gex2_prompt"] = (plan.get("bindings_preview") or {}).get("prompt_profile")
    preview["source_video"] = (plan.get("bindings_preview") or {}).get("source_video")
    preview["source_still"] = (plan.get("bindings_preview") or {}).get("source_still")
    preview["rating_effective"] = plan.get("rating_effective")
    preview["rating_evidence"] = plan.get("rating_evidence")
    preview["selection_weight"] = plan.get("selection_weight")
    preview["step"] = plan.get("step")
    preview["top_of_hour"] = plan.get("top_of_hour")
    preview["recent_five_star_boosted"] = plan.get("recent_five_star_boosted")
    preview["recent_five_star_mult"] = plan.get("recent_five_star_mult")
    preview["appetite"] = plan.get("appetite")
    preview["appetite_facet"] = plan.get("appetite_facet")
    preview["fast_track"] = plan.get("fast_track")
    preview["hold_axis"] = plan.get("hold_axis")
    preview["hold_values"] = plan.get("hold_values")
    preview["hold_facet_constrained"] = plan.get("hold_facet_constrained")
    return preview


# --- legacy product-grid helpers (kept for tests / manual use) ---

def product_combos_for_family(
    *,
    family: str,
    data_root: Optional[Path] = None,
) -> List[Dict[str, Path]]:
    from shape_factory import pick_combinations, resolve_pool_members

    data_root = (data_root or _default_data_root()).resolve()
    shape_path = data_root / "shapes" / f"{family}.shape.yaml"
    pools_path = data_root / "pools" / family / "pools.yaml"
    if not shape_path.is_file() or not pools_path.is_file():
        raise FileNotFoundError(f"missing shape or pools for family {family!r}")

    shape = load_yaml(shape_path)
    pools_doc = load_yaml(pools_path)
    req_by_slot = requires_by_slot(shape)

    pool_paths: Dict[str, List[Path]] = {}
    for _name, pool_def in (pools_doc.get("pools") or {}).items():
        if not isinstance(pool_def, dict):
            continue
        slot = str(pool_def.get("slot") or "")
        if slot not in req_by_slot:
            continue
        members = resolve_pool_members(pool_def)
        if members:
            pool_paths[slot] = members

    missing = [s for s, req in req_by_slot.items() if s not in pool_paths and not req.get("optional")]
    if missing:
        raise RuntimeError(f"pools missing required slots for {family}: {missing}")

    return pick_combinations(pool_paths, mode="product", limit=None)


def combo_key_from_picks(picks: Dict[str, Path]) -> str:
    return _combo_key_from_slot_paths({slot: str(path) for slot, path in sorted(picks.items())})


def existing_combo_keys(
    family: str,
    *,
    job_dir: Optional[Path] = None,
    data_root: Optional[Path] = None,
) -> Set[str]:
    data_root = (data_root or _default_data_root()).resolve()
    root = job_dir or (_default_job_dir(data_root) / family)
    out: Set[str] = set()
    if not root.is_dir():
        return out
    for path in root.glob("*.job.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        ck = _combo_key_from_job_bindings(job.get("bindings") if isinstance(job.get("bindings"), dict) else {})
        if ck:
            out.add(ck)
    return out


def pending_product_combos(
    family: str,
    *,
    data_root: Optional[Path] = None,
    job_dir: Optional[Path] = None,
) -> List[Tuple[int, Dict[str, Path], str]]:
    data_root = (data_root or _default_data_root()).resolve()
    combos = product_combos_for_family(family=family, data_root=data_root)
    existing = existing_combo_keys(family, job_dir=job_dir, data_root=data_root)
    pending: List[Tuple[int, Dict[str, Path], str]] = []
    for idx, picks in enumerate(combos):
        ck = combo_key_from_picks(picks)
        if ck not in existing:
            pending.append((idx, picks, ck))
    return pending


def queue_advance_decision(
    *,
    pending: int,
    queue_min: int = 1,
    queue_max: int = 2,
    factory_pending: int = 0,
) -> Dict[str, Any]:
    """
    Whether the hourly tick should *generate* another fill job.

    Hourlies fill Comfy only when there are no factory jobs still awaiting
    submit. Pending drain owns those; hourly tops up when the factory pending
    set is empty and Comfy waiting depth is below ``queue_min``.

    ``submit_slots`` still reflects room under ``queue_max`` so maintenance can
    push existing pending onto Comfy even when advance is blocked.
    """
    pending_i = max(0, int(pending))
    factory_pending_i = max(0, int(factory_pending))
    queue_min_i = max(0, int(queue_min))
    queue_max_i = max(queue_min_i, int(queue_max))
    submit_slots = max(0, queue_max_i - pending_i)
    base = {
        "pending": pending_i,
        "factory_pending": factory_pending_i,
        "queue_min": queue_min_i,
        "queue_max": queue_max_i,
        "submit_slots": submit_slots,
    }
    if factory_pending_i > 0:
        return {
            **base,
            "advance": False,
            "reason": "factory_pending",
        }
    if pending_i >= queue_max_i:
        return {
            **base,
            "advance": False,
            "reason": "at_max",
            "submit_slots": 0,
        }
    if pending_i >= queue_min_i:
        return {
            **base,
            "advance": False,
            "reason": "satisfied_min",
        }
    return {
        **base,
        "advance": True,
        "reason": "below_min",
    }


def count_factory_pending_submit(*, jobs_dir: Path) -> int:
    """Count factory job files eligible for ``submit --pending-only``."""
    from argparse import Namespace

    from shape_factory import iter_pending_submit_job_paths

    root = jobs_dir.expanduser().resolve()
    args = Namespace(job=None, jobs_dir=str(root), family=None, job_dir=str(root), limit=None)
    return len(iter_pending_submit_job_paths(args))


def main() -> int:
    import argparse

    p = argparse.ArgumentParser(description="Shape factory hourly planning")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("plan-gex2", help="Print JSON plan for next hourly GEX2 replay")
    g.add_argument("--cursor", type=int, default=None)
    g.add_argument("--state", type=Path, default=None, help="hourly-state.json path")
    g.add_argument("--data-root", type=Path, default=None)
    g.add_argument("--family", default="FB9_GEX2")

    r = sub.add_parser("plan-replay", help="Print JSON plan to replay a random previous run")
    r.add_argument("--cursor", type=int, default=None)
    r.add_argument("--state", type=Path, default=None)
    r.add_argument("--data-root", type=Path, default=None)
    r.add_argument("--family", default="FB9_GEX2")

    d = sub.add_parser("plan-derive", help="Print JSON plan to derive (do more WITH) a high-appetite asset")
    d.add_argument("--cursor", type=int, default=None)
    d.add_argument("--state", type=Path, default=None)
    d.add_argument("--data-root", type=Path, default=None)
    d.add_argument("--family", default="FB9_GEX2")

    pd = sub.add_parser(
        "plan-predicted",
        help="Print JSON plan: predicted/inferred seed always queued as derive",
    )
    pd.add_argument("--cursor", type=int, default=None)
    pd.add_argument("--state", type=Path, default=None)
    pd.add_argument("--data-root", type=Path, default=None)
    pd.add_argument("--family", default="FB9_GEX2")

    s = sub.add_parser("plan-step", help="Print JSON plan for the next hourly step (replay OR derive)")
    s.add_argument("--cursor", type=int, default=None)
    s.add_argument("--state", type=Path, default=None)
    s.add_argument("--data-root", type=Path, default=None)
    s.add_argument("--family", default="FB9_GEX2")

    l = sub.add_parser("list-recipes", help="List replay recipe count for a family")
    l.add_argument("--data-root", type=Path, default=None)
    l.add_argument("--family", default="FB9_GEX2")

    sf = sub.add_parser("select-family", help="Print weighted seed family for a cursor")
    sf.add_argument("--cursor", type=int, default=None)
    sf.add_argument("--state", type=Path, default=None)

    nf = sub.add_parser("need-facial", help="Print GEX2 job_key needing FACIAL child (or empty)")
    nf.add_argument("--data-root", type=Path, default=None)

    nk = sub.add_parser("need-gex2-from-kneel", help="Print Kneel job_key needing GEX2 child (or empty)")
    nk.add_argument("--data-root", type=Path, default=None)

    qp = sub.add_parser(
        "queue-policy",
        help="JSON: should hourly generate fill work? (Comfy waiting + factory pending)",
    )
    qp.add_argument("--pending", type=int, required=True, help="Comfy waiting-queue depth")
    qp.add_argument(
        "--factory-pending",
        type=int,
        default=None,
        help="Factory jobs awaiting submit (omit to count from --jobs-dir)",
    )
    qp.add_argument(
        "--jobs-dir",
        type=Path,
        default=None,
        help="Job root used when --factory-pending is omitted",
    )
    qp.add_argument("--queue-min", type=int, default=1)
    qp.add_argument("--queue-max", type=int, default=2)

    pc = sub.add_parser("pending-count", help="Count factory jobs awaiting submit")
    pc.add_argument("--jobs-dir", type=Path, required=True)

    args = p.parse_args()
    data_root = args.data_root.expanduser().resolve() if getattr(args, "data_root", None) else None

    if args.cmd == "list-recipes":
        recipes = collect_replay_recipes(str(args.family), data_root=data_root)
        print(json.dumps({"family": args.family, "recipe_count": len(recipes)}, indent=2))
        return 0

    if args.cmd == "select-family":
        cursor = args.cursor
        if cursor is None and args.state and args.state.is_file():
            state = json.loads(args.state.read_text(encoding="utf-8"))
            cursor = int(state.get("sample_cursor") or 0)
        if cursor is None:
            cursor = 0
        fam = select_seed_family(int(cursor))
        print(json.dumps({"cursor": int(cursor), "family": fam}, indent=2))
        return 0

    if args.cmd == "need-facial":
        key = find_gex2_needing_facial(data_root=data_root)
        if key:
            print(key)
        return 0

    if args.cmd == "need-gex2-from-kneel":
        key = find_kneel_needing_gex2(data_root=data_root)
        if key:
            print(key)
        return 0

    if args.cmd == "pending-count":
        n = count_factory_pending_submit(jobs_dir=Path(args.jobs_dir))
        print(n)
        return 0

    if args.cmd == "queue-policy":
        factory_pending = args.factory_pending
        if factory_pending is None:
            jobs_dir = args.jobs_dir
            if jobs_dir is None:
                from shape_factory import DEFAULT_JOB_DIR

                jobs_dir = DEFAULT_JOB_DIR
            factory_pending = count_factory_pending_submit(jobs_dir=Path(jobs_dir))
        out = queue_advance_decision(
            pending=int(args.pending),
            queue_min=int(args.queue_min),
            queue_max=int(args.queue_max),
            factory_pending=int(factory_pending),
        )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if args.cmd in {"plan-gex2", "plan-replay", "plan-derive", "plan-predicted", "plan-step"}:
        cursor = args.cursor
        if cursor is None and args.state and args.state.is_file():
            state = json.loads(args.state.read_text(encoding="utf-8"))
            cursor = int(state.get("sample_cursor") or 0)
        if cursor is None:
            cursor = 0
        if args.cmd == "plan-derive":
            out = plan_hourly_derive(cursor=cursor, data_root=data_root, family=str(args.family))
        elif args.cmd == "plan-predicted":
            out = plan_hourly_predicted_derive(cursor=cursor, data_root=data_root, family=str(args.family))
        elif args.cmd == "plan-step":
            out = plan_hourly_step(cursor=cursor, data_root=data_root, family=str(args.family))
        else:
            out = plan_hourly_replay(cursor=cursor, data_root=data_root, family=str(args.family))
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
