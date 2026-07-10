#!/usr/bin/env python3
"""
Hourly shape-factory planning: replay random previous runs (factory jobs + historical OG outputs).

Used by scripts/shape_factory_hourly.sh and shape_factory_map hourly preview.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from shape_factory import load_yaml, requires_by_slot
from shape_factory_heuristics import _og_group_id_from_relpath
from shape_factory_map import _combo_key_from_job_bindings, _combo_key_from_slot_paths
from shape_factory_ratings import (
    default_appetite_index_path,
    default_ratings_index_path,
    lookup_output_appetite,
    lookup_output_rating,
)


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
    for req in shape.get("requires") or []:
        if not isinstance(req, dict):
            continue
        binding = req.get("binding") if isinstance(req.get("binding"), dict) else {}
        if str(binding.get("type") or "") == "prompt_bundle":
            return binding
    return None


def _find_node(workflow: dict[str, Any], node_id: int) -> Optional[dict[str, Any]]:
    for node in workflow.get("nodes") or []:
        if isinstance(node, dict) and int(node.get("id") or -1) == int(node_id):
            return node
    return None


def _widget_text(workflow: dict[str, Any], node_id: int, widget_index: int = 0) -> str:
    node = _find_node(workflow, node_id)
    if not node:
        return ""
    widgets = node.get("widgets_values")
    if isinstance(widgets, list):
        if not widgets:
            return ""
        idx = min(max(0, widget_index), len(widgets) - 1)
        return str(widgets[idx] or "")
    if isinstance(widgets, dict):
        return str(widgets.get("text") or widgets.get("value") or "")
    return ""


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
    text = str(raw or "").strip().replace("\\", "/")
    if not text:
        return None
    p = Path(text).expanduser()
    if p.is_file():
        return p.resolve()
    candidates = [
        data_root / text,
        data_root / "output" / text,
        Path("/home/yuji/comfyui-runpod-data") / text,
        Path("/home/yuji/comfyui-runpod-data/output") / text.lstrip("/"),
    ]
    for cand in candidates:
        try:
            if cand.is_file():
                return cand.resolve()
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
    replay_dir = data_root / "pools" / family / "prompts" / "_replay"
    replay_dir.mkdir(parents=True, exist_ok=True)
    slug = _combo_slug(f"{label}\n{positive}\n{negative}")
    path = replay_dir / f"{slug}.json"
    if not path.is_file():
        doc = {"label": label, "positive": positive, "negative": negative}
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


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

    pos_spec = binding.get("positive") if isinstance(binding.get("positive"), dict) else {}
    neg_spec = binding.get("negative") if isinstance(binding.get("negative"), dict) else {}
    positive = _widget_text(workflow, int(pos_spec.get("node_id") or 0), int(pos_spec.get("widget_index") or 0))
    negative = _widget_text(workflow, int(neg_spec.get("node_id") or 0), int(neg_spec.get("widget_index") or 0))
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
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except Exception:
        return None


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

    family = str(shape.get("family_slug") or "")
    picks: Dict[str, Path] = {}
    for slot, meta in bindings.items():
        if not isinstance(meta, dict):
            continue
        raw = str(meta.get("path") or "")
        if slot == "prompt_profile":
            p = Path(raw)
            if not p.is_file():
                wf_path = str(job.get("generated_workflow_path") or "")
                workflow = _load_workflow_json(Path(wf_path)) if wf_path else None
                if workflow is None:
                    return None
                ui_picks = _picks_from_ui_workflow(
                    workflow,
                    shape=shape,
                    data_root=data_root,
                    label=str(job.get("job_key") or "job"),
                )
                if ui_picks is None:
                    return None
                return ui_picks
            picks[slot] = p.resolve()
            continue
        resolved = _resolve_media_path(raw, data_root=data_root)
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

    meta: dict[str, Any] = {"rating_effective": None, "evidence": []}
    explore_floor = float(__import__("os").environ.get("HOURLY_RATING_EXPLORE_FLOOR", "0.35"))
    if not ratings_doc:
        return explore_floor, meta
    out_row = lookup_output_rating(str(recipe.get("output_path") or ""), ratings_doc)
    if out_row and out_row.get("explicit") is not None:
        rating_value = float(out_row["explicit"])
        meta["rating_effective"] = rating_value
        meta["evidence"].append("output_explicit")
        normalized = max(0.0, min(5.0, rating_value))
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

    weights: List[float] = []
    weight_meta: List[dict[str, Any]] = []
    for recipe in recipes:
        rated_w, meta = _recipe_selection_weight(
            recipe, ratings_doc=ratings_doc, shape=shape, heuristics_doc=heuristics_doc, appetite_doc=appetite_doc
        )
        uniform_w = 1.0
        final_w = (1.0 - blend) * uniform_w + blend * rated_w
        weights.append(final_w)
        weight_meta.append(meta)

    rng = random.Random(int(cursor))
    recipe, recipe_index = _weighted_choice(recipes, weights, rng)
    picks = recipe.get("picks") if isinstance(recipe.get("picks"), dict) else {}
    sel_meta = weight_meta[recipe_index] if recipe_index < len(weight_meta) else {}

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
        "recipe_index": recipe_index,
        "selection_weight": round(weights[recipe_index], 3),
        "rating_effective": sel_meta.get("rating_effective"),
        "rating_evidence": sel_meta.get("evidence"),
        "ratings_index_loaded": ratings_doc is not None,
        "heuristics_index_loaded": heuristics_doc is not None,
        "appetite_index_loaded": appetite_doc is not None,
        "appetite": sel_meta.get("appetite"),
        "appetite_facet": sel_meta.get("appetite_facet"),
        "appetite_value": sel_meta.get("appetite_value"),
        "rating_blend": blend,
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
    }


def _derive_rewire(
    seed: dict[str, Any],
    *,
    facet: str,
    family: str,
    pool: List[dict[str, Any]],
    rng: random.Random,
) -> Tuple[dict[str, Any], str]:
    """
    Build a "do more WITH this" recipe from a seed + its facet.

    facet=source: hold source picks, vary processing (alt prompt on same source).
    facet=processing: hold prompt, vary source.
    facet=both: Extend (chain seed output into the video slot) when possible, else fall back to source.
    Returns (rewired_recipe, action) where action is "derive" or "extend".
    """
    seed_picks = seed.get("picks") if isinstance(seed.get("picks"), dict) else {}
    seed_out = str(seed.get("output_path") or "")

    def _rebuild(picks_map: Dict[str, str], *, output_path: Optional[str]) -> dict[str, Any]:
        picks_paths = {slot: Path(str(p)) for slot, p in picks_map.items()}
        rec = _recipe_from_picks(
            family=family,
            picks=picks_paths,
            source=f"derive:{facet}:{seed.get('source') or seed_out}",
            output_path=output_path,
        )
        return rec

    # Extend: chain the seed's output video into a video source slot.
    if facet == "both" and seed_out:
        video_slot = next((s for s in seed_picks if _is_video_slot(s)), None)
        if video_slot is not None:
            picks_map = dict(seed_picks)
            picks_map[video_slot] = seed_out
            return _rebuild(picks_map, output_path=None), "extend"
        facet = "source"  # no video slot -> fall back to vary-processing

    source_slots = [s for s in seed_picks if _is_source_slot(s)]
    prompt_slot = "prompt_profile" if "prompt_profile" in seed_picks else None

    if facet == "source" and source_slots and prompt_slot:
        # Same source, different prompt.
        seed_src = {s: str(seed_picks.get(s)) for s in source_slots}
        alts = [
            r for r in pool
            if isinstance(r.get("picks"), dict)
            and all(str(r["picks"].get(s)) == seed_src.get(s) for s in source_slots)
            and str(r["picks"].get(prompt_slot)) != str(seed_picks.get(prompt_slot))
        ]
        if alts:
            chosen = rng.choice(alts)
            return _rebuild({str(k): str(v) for k, v in chosen["picks"].items()}, output_path=None), "derive"

    if facet == "processing" and prompt_slot and source_slots:
        # Same prompt, different source.
        alts = [
            r for r in pool
            if isinstance(r.get("picks"), dict)
            and str(r["picks"].get(prompt_slot)) == str(seed_picks.get(prompt_slot))
            and any(str(r["picks"].get(s)) != str(seed_picks.get(s)) for s in source_slots)
        ]
        if alts:
            chosen = rng.choice(alts)
            return _rebuild({str(k): str(v) for k, v in chosen["picks"].items()}, output_path=None), "derive"

    # Degenerate fallback: reproduce the seed recipe (still "more with this").
    return _rebuild({str(k): str(v) for k, v in seed_picks.items()}, output_path=seed_out or None), "derive"


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
    """
    data_root = (data_root or _default_data_root()).resolve()
    shape_path = data_root / "shapes" / f"{family}.shape.yaml"
    shape = load_yaml(shape_path) if shape_path.is_file() else {}

    recipes = collect_replay_recipes(family, data_root=data_root, job_dir=job_dir)
    if not recipes:
        return {"ok": False, "error": "no_replay_recipes", "family": family, "recipe_count": 0}

    ratings_doc = _load_ratings_index(data_root)
    heuristics_doc = _load_heuristics_index(data_root)
    appetite_doc = _load_appetite_index(data_root)
    tags_doc = _load_asset_tags(data_root)
    if appetite_doc is None and heuristics_doc is None:
        return {"ok": False, "error": "no_appetite_signal", "family": family, "recipe_count": len(recipes)}

    seeds: List[dict[str, Any]] = []
    weights: List[float] = []
    fast_tracks: List[int] = []
    for recipe in recipes:
        info = _recipe_appetite(
            recipe, shape=shape, ratings_doc=ratings_doc, heuristics_doc=heuristics_doc, appetite_doc=appetite_doc
        )
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
        seeds.append({"recipe": recipe, "info": info})
        weights.append(weight)
        if info.get("fast_track"):
            fast_tracks.append(len(seeds) - 1)

    if not seeds:
        return {"ok": False, "error": "no_appetite_seeds", "family": family, "recipe_count": len(recipes)}

    rng = random.Random(int(cursor) ^ 0x0A9E)
    if fast_tracks:
        # Pin: choose only among fast_track seeds.
        pick_i = rng.choice(fast_tracks)
    else:
        _seed_rec, pick_i = _weighted_choice(seeds, weights, rng)
        pick_i = seeds.index(_seed_rec) if _seed_rec in seeds else pick_i

    chosen = seeds[pick_i]
    seed_recipe = chosen["recipe"]
    info = chosen["info"]
    facet = str(info.get("facet") or "both")

    rewired, action = _derive_rewire(seed_recipe, facet=facet, family=family, pool=recipes, rng=rng)

    return {
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
        "appetite_facet": facet,
        "appetite_value": info.get("value"),
        "appetite_evidence": info.get("evidence"),
        "tag_affinity": info.get("tag_affinity"),
        "fast_track": bool(info.get("fast_track")),
        "cursor": int(cursor),
        "recipe_count": len(recipes),
        "seed_count": len(seeds),
        "selection_weight": round(weights[pick_i], 3),
        "appetite_index_loaded": appetite_doc is not None,
        "heuristics_index_loaded": heuristics_doc is not None,
        "next_cursor": int(cursor) + 1,
    }


def plan_hourly_step(
    *,
    cursor: int = 0,
    data_root: Optional[Path] = None,
    job_dir: Optional[Path] = None,
    family: str = "FB9_GEX2",
) -> Dict[str, Any]:
    """
    Choose the hourly action: Replay ("do more OF", quality) vs Derive ("do more WITH", appetite).

    fast_track appetite pins Derive; otherwise the split is deterministic per cursor via
    HOURLY_DERIVE_SHARE (default 0.5). Falls back to Replay when Derive has no seeds.
    """
    import os

    derive_share = float(os.environ.get("HOURLY_DERIVE_SHARE", "0.5"))
    derive_share = max(0.0, min(1.0, derive_share))

    derive = plan_hourly_derive(cursor=cursor, data_root=data_root, job_dir=job_dir, family=family)
    if derive.get("ok") and derive.get("fast_track"):
        derive["step"] = "derive"
        return derive

    want_derive = random.Random(int(cursor) ^ 0x5EED).random() < derive_share
    if derive.get("ok") and want_derive:
        derive["step"] = "derive"
        return derive

    replay = plan_hourly_replay(cursor=cursor, data_root=data_root, job_dir=job_dir, family=family)
    if replay.get("ok"):
        replay["step"] = "replay"
        return replay
    if derive.get("ok"):
        derive["step"] = "derive"
        return derive
    return replay


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
    """Map API / UI preview of the next hourly replay step."""
    cursor = int(hourly_state.get("sample_cursor") or 0)
    plan = plan_hourly_step(cursor=cursor, data_root=data_root, family="FB9_GEX2")
    if not plan.get("ok"):
        return {
            "cursor": cursor,
            "phase_if_idle": hourly_state.get("phase"),
            "error": plan.get("error"),
            "recipe_count": plan.get("recipe_count"),
        }
    preview = dict(plan)
    preview["phase_if_idle"] = hourly_state.get("phase")
    preview["gex2_prompt"] = (plan.get("bindings_preview") or {}).get("prompt_profile")
    preview["source_video"] = (plan.get("bindings_preview") or {}).get("source_video")
    preview["source_still"] = (plan.get("bindings_preview") or {}).get("source_still")
    preview["rating_effective"] = plan.get("rating_effective")
    preview["rating_evidence"] = plan.get("rating_evidence")
    preview["selection_weight"] = plan.get("selection_weight")
    preview["step"] = plan.get("step")
    preview["appetite"] = plan.get("appetite")
    preview["appetite_facet"] = plan.get("appetite_facet")
    preview["fast_track"] = plan.get("fast_track")
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

    s = sub.add_parser("plan-step", help="Print JSON plan for the next hourly step (replay OR derive)")
    s.add_argument("--cursor", type=int, default=None)
    s.add_argument("--state", type=Path, default=None)
    s.add_argument("--data-root", type=Path, default=None)
    s.add_argument("--family", default="FB9_GEX2")

    l = sub.add_parser("list-recipes", help="List replay recipe count for a family")
    l.add_argument("--data-root", type=Path, default=None)
    l.add_argument("--family", default="FB9_GEX2")

    args = p.parse_args()
    data_root = args.data_root.expanduser().resolve() if args.data_root else None

    if args.cmd == "list-recipes":
        recipes = collect_replay_recipes(str(args.family), data_root=data_root)
        print(json.dumps({"family": args.family, "recipe_count": len(recipes)}, indent=2))
        return 0

    if args.cmd in {"plan-gex2", "plan-replay", "plan-derive", "plan-step"}:
        cursor = args.cursor
        if cursor is None and args.state and args.state.is_file():
            state = json.loads(args.state.read_text(encoding="utf-8"))
            cursor = int(state.get("sample_cursor") or 0)
        if cursor is None:
            cursor = 0
        if args.cmd == "plan-derive":
            out = plan_hourly_derive(cursor=cursor, data_root=data_root, family=str(args.family))
        elif args.cmd == "plan-step":
            out = plan_hourly_step(cursor=cursor, data_root=data_root, family=str(args.family))
        else:
            out = plan_hourly_replay(cursor=cursor, data_root=data_root, family=str(args.family))
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
