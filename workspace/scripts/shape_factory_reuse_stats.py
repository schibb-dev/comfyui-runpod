#!/usr/bin/env python3
"""Record reuse fan-out at seed and recipe layers.

Seed units: still content_id, clip, parent video, adhoc Use.
Recipe units: family, prompt variant, prompt text hash, family+prompt,
param overrides (frames/steps/overlap), stack, family+prompt+stack.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from shape_factory_clips import _job_source_clip_id, _vhs_window_is_weak_full_file
from shape_factory_owned_prompt import prompt_variant_slug
from shape_factory_work_products import job_is_hourly_product

RECIPE_PARAM_KEYS = ("frames", "steps", "overlap")
_SEED_LAYER_NAMES = ("still_content_id", "clip_id", "video_asset", "adhoc_use")
_RECIPE_LAYER_NAMES = (
    "family",
    "prompt_variant",
    "prompt_hash",
    "family_prompt",
    "param_override",
    "stack",
    "family_prompt_stack",
)

REUSE_STATS_BASENAME = "reuse_stats.json"
_SHA256_RE = re.compile(r"[0-9a-f]{64}", re.I)
_STILL_SLOTS = ("source_still", "source_image", "identity_anchor", "identity_still")
_VIDEO_SLOTS = ("source_video",)
_MIN_UNIQUE_FOR_RANK = 5
KEEPER_STARS = 4
OPERATOR_FAVOR_WEIGHT = 2
HOURLY_FAVOR_WEIGHT = 1
FAVOR_APPETITE = frozenset({"more", "fast_track"})
BLOCK_APPETITE = frozenset({"remove"})
# Soft prior: experiments stay in the mix; appetite (not volume) steers.
EXPERIMENT_WEIGHT = 0.25
STAR_NUDGE = 0.15
APPETITE_GUIDE_WEIGHT = {
    "less": 0.1,
    "neutral": EXPERIMENT_WEIGHT,
    "more": 2.5,
    "fast_track": 6.0,
    "remove": 0.0,
}
OPERATOR_APPETITE_MULT = 2.0
HOURLY_APPETITE_MULT = 1.0


def _binding_path(spec: Any) -> str:
    if isinstance(spec, str):
        return spec.strip()
    if isinstance(spec, dict):
        return str(spec.get("relpath") or spec.get("path") or spec.get("name") or "").strip()
    return ""


def content_id_from_name(path: str) -> Optional[str]:
    m = _SHA256_RE.search(Path(str(path or "")).name)
    return m.group(0).lower() if m else None


def _round_mark(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return str(value).strip()


def classify_job_reuse(job: Mapping[str, Any]) -> Dict[str, Any]:
    """Extract reuse identities from one job (layers are independent)."""
    bindings = job.get("bindings") if isinstance(job.get("bindings"), dict) else {}
    still_ids: List[str] = []
    seen_still: set[str] = set()
    for slot in _STILL_SLOTS:
        cid = content_id_from_name(_binding_path(bindings.get(slot)))
        if cid and cid not in seen_still:
            seen_still.add(cid)
            still_ids.append(cid)

    video_rels: List[str] = []
    for slot in _VIDEO_SLOTS:
        rel = _binding_path(bindings.get(slot)).replace("\\", "/")
        if rel:
            video_rels.append(rel)

    clip_id = _job_source_clip_id(dict(job))
    win = job.get("vhs_window") if isinstance(job.get("vhs_window"), dict) else {}
    adhoc_use = ""
    if not clip_id and not _vhs_window_is_weak_full_file(win):
        parent = video_rels[0] if video_rels else ""
        adhoc_use = "|".join(
            [
                parent,
                _round_mark(win.get("mark_in")),
                _round_mark(win.get("mark_out")),
                str(win.get("skip_first_frames") or ""),
                str(win.get("frame_load_cap") or ""),
            ]
        )

    asset_id = ""
    if video_rels:
        asset_id = content_id_from_name(video_rels[0]) or Path(video_rels[0]).name.lower()

    family = str(job.get("family_slug") or "").strip()
    adhoc = job.get("adhoc_overrides") if isinstance(job.get("adhoc_overrides"), dict) else {}
    stack = str(job.get("stack_id") or adhoc.get("stack") or "").strip()
    variant, prompt_hash = prompt_reuse_ids(job)
    param_sig = param_override_signature(adhoc)
    lora_sig = lora_override_signature(adhoc)
    family_prompt = f"{family}|{variant}" if family and variant else ""
    family_prompt_stack = f"{family_prompt}|{stack}" if family_prompt and stack else ""
    return {
        "still_ids": still_ids,
        "clip_id": clip_id or "",
        "asset_id": asset_id,
        "adhoc_use": adhoc_use,
        "family": family,
        "stack": stack,
        "prompt_variant": variant,
        "prompt_hash": prompt_hash,
        "family_prompt": family_prompt,
        "param_override": param_sig,
        "lora_override": lora_sig,
        "family_prompt_stack": family_prompt_stack,
        "override_keys": override_keys_used(adhoc),
    }


def job_output_paths(job: Mapping[str, Any]) -> List[str]:
    """This job's recorded outputs — not recipe_output_path (often the parent)."""
    paths: List[str] = []
    submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    deposit = job.get("deposit") if isinstance(job.get("deposit"), dict) else {}
    for src in (submit.get("outputs"), deposit.get("videos")):
        if not isinstance(src, list):
            continue
        for item in src:
            text = str(item or "").strip()
            if text and text not in paths:
                paths.append(text)
    finals = [p for p in paths if "_final" in Path(p).stem.lower()]
    return finals or paths


def job_favor_signal(
    job: Mapping[str, Any],
    *,
    ratings_doc: Optional[Mapping[str, Any]] = None,
    appetite_doc: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Appetite is the favor signal; stars are a weak nudge only."""
    from shape_factory_ratings import (
        is_omit_quality_rating,
        is_usable_quality_rating,
        lookup_output_appetite,
        lookup_output_rating,
        normalize_appetite,
    )

    stars: Optional[float] = None
    appetite = ""
    omit = False
    for path in job_output_paths(job):
        if ratings_doc:
            row = lookup_output_rating(path, dict(ratings_doc))
            if isinstance(row, dict):
                if is_omit_quality_rating(row.get("explicit")):
                    omit = True
                elif is_usable_quality_rating(row.get("explicit")):
                    value = float(row["explicit"])
                    if stars is None or value > stars:
                        stars = value
        if appetite_doc and not appetite:
            ap_row = lookup_output_appetite(path, dict(appetite_doc))
            if isinstance(ap_row, dict):
                appetite = normalize_appetite(ap_row.get("appetite"))
    if omit or appetite in BLOCK_APPETITE:
        return {
            "favored": False,
            "blocked": True,
            "stars": stars,
            "appetite": appetite,
            "reasons": ["omit_or_remove"],
        }
    reasons: List[str] = []
    if appetite in FAVOR_APPETITE:
        reasons.append(f"appetite={appetite}")
    return {
        "favored": bool(reasons),
        "blocked": False,
        "stars": stars,
        "appetite": appetite,
        "reasons": reasons,
    }


def job_guide_weight(signal: Mapping[str, Any], *, hourly: bool) -> float:
    """Soft weights: experiments keep a floor; appetite pulls hard.

    Adhoc and hourly outliers stay eligible. They only steer when they
    pick up appetite (more / fast_track). Operator appetite outranks
    hourly appetite; unrated volume does not.
    """
    if signal.get("blocked"):
        return 0.0
    appetite = str(signal.get("appetite") or "")
    if appetite in FAVOR_APPETITE:
        base = float(APPETITE_GUIDE_WEIGHT.get(appetite) or 2.5)
        origin = OPERATOR_APPETITE_MULT if not hourly else HOURLY_APPETITE_MULT
        return base * origin
    if appetite == "less":
        return float(APPETITE_GUIDE_WEIGHT["less"])
    weight = EXPERIMENT_WEIGHT
    stars = signal.get("stars")
    try:
        if stars is not None and float(stars) >= KEEPER_STARS:
            weight = EXPERIMENT_WEIGHT + STAR_NUDGE
    except (TypeError, ValueError):
        pass
    return weight


def prompt_reuse_ids(job: Mapping[str, Any]) -> tuple[str, str]:
    """Return (variant slug, prompt content hash) for recipe reuse."""
    prompt = job.get("prompt") if isinstance(job.get("prompt"), dict) else {}
    binds = job.get("bindings") if isinstance(job.get("bindings"), dict) else {}
    profile = binds.get("prompt_profile")
    variant = prompt_variant_slug(
        prompt.get("slug"),
        prompt.get("label"),
        profile,
    )
    if not variant:
        for part in str(job.get("job_key") or "").split("__"):
            if part.lower().startswith(("pp-", "prompt_profile-")):
                variant = prompt_variant_slug(part)
                if variant:
                    break
    prompt_hash = str(prompt.get("content_hash") or "").strip().lower()
    return variant, prompt_hash


def param_override_signature(adhoc: Mapping[str, Any]) -> str:
    params = adhoc.get("parameters") if isinstance(adhoc.get("parameters"), dict) else {}
    parts: List[str] = []
    for key in RECIPE_PARAM_KEYS:
        raw = params.get(key)
        if raw in (None, ""):
            continue
        parts.append(f"{key}={raw}")
    return ",".join(parts)


def lora_override_signature(adhoc: Mapping[str, Any]) -> str:
    raw = adhoc.get("loras")
    entries = raw.get("entries") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        return ""
    on: List[str] = []
    for row in entries:
        if not isinstance(row, dict) or row.get("on") is False:
            continue
        name = str(row.get("lora") or row.get("name") or "").strip()
        if not name:
            continue
        strength = row.get("strength")
        on.append(f"{name}@{strength}" if strength not in (None, "") else name)
    return ",".join(sorted(on))


def override_keys_used(adhoc: Mapping[str, Any]) -> List[str]:
    keys: List[str] = []
    if adhoc.get("stack"):
        keys.append("stack")
    if adhoc.get("prompt_profile"):
        keys.append("prompt_profile")
    if adhoc.get("loras"):
        keys.append("loras")
    if adhoc.get("source_clip_id"):
        keys.append("source_clip_id")
    params = adhoc.get("parameters") if isinstance(adhoc.get("parameters"), dict) else {}
    for key in params:
        if params.get(key) in (None, ""):
            continue
        keys.append(f"parameters.{key}")
    return keys


def summarize_counts(counts: Mapping[str, int], *, top_n: int = 12) -> Dict[str, Any]:
    if not counts:
        return {
            "unique": 0,
            "uses": 0,
            "reused": 0,
            "once_only": 0,
            "reuse_rate": 0.0,
            "mean_fanout": 0.0,
            "mean_fanout_reused": 0.0,
            "top": [],
        }
    vals = [int(v) for v in counts.values()]
    unique = len(vals)
    uses = int(sum(vals))
    reused = sum(1 for v in vals if v >= 2)
    reused_uses = sum(v for v in vals if v >= 2)
    top = sorted(counts.items(), key=lambda kv: (-int(kv[1]), str(kv[0])))[:top_n]
    return {
        "unique": unique,
        "uses": uses,
        "reused": reused,
        "once_only": unique - reused,
        "reuse_rate": round(reused / unique, 4) if unique else 0.0,
        "mean_fanout": round(uses / unique, 3) if unique else 0.0,
        "mean_fanout_reused": round(reused_uses / reused, 3) if reused else 0.0,
        "top": [{"id": k, "uses": int(v)} for k, v in top],
    }


def rank_reuse_units(
    layers: Mapping[str, Mapping[str, Any]],
    *,
    min_unique: int = _MIN_UNIQUE_FOR_RANK,
    min_uses: int = 0,
) -> Dict[str, Any]:
    """Prefer high reuse_rate, scaled by log(unique) so tiny layers do not win."""
    scored: List[Dict[str, Any]] = []
    for name, summary in layers.items():
        unique = int(summary.get("unique") or 0)
        uses = int(summary.get("uses") or 0)
        if unique < min_unique or uses < min_uses:
            continue
        rate = float(summary.get("reuse_rate") or 0.0)
        score = rate * math.log1p(unique)
        scored.append(
            {
                "unit": name,
                "score": round(score, 4),
                "reuse_rate": rate,
                "unique": unique,
                "uses": uses,
                "mean_fanout": float(summary.get("mean_fanout") or 0.0),
            }
        )
    scored.sort(key=lambda row: (-float(row["score"]), -int(row["unique"]), str(row["unit"])))
    rule = f"highest reuse_rate * log1p(unique) among layers with >={min_unique} distinct ids"
    if min_uses:
        rule += f" and >={min_uses} job uses"
    return {
        "recommended": scored[0]["unit"] if scored else None,
        "ranking": scored,
        "rule": rule,
    }


def _empty_bucket() -> Dict[str, Any]:
    return {
        "stills": Counter(),
        "clips": Counter(),
        "assets": Counter(),
        "adhoc": Counter(),
        "families": Counter(),
        "stacks": Counter(),
        "variants": Counter(),
        "hashes": Counter(),
        "family_prompts": Counter(),
        "param_sigs": Counter(),
        "loras": Counter(),
        "recipes": Counter(),
        "override_keys": Counter(),
        "scanned": 0,
        "seeded": 0,
    }


def _add_row(bucket: Dict[str, Any], row: Mapping[str, Any]) -> None:
    bucket["scanned"] += 1
    hit = False
    for cid in row["still_ids"]:
        bucket["stills"][cid] += 1
        hit = True
    if row["clip_id"]:
        bucket["clips"][row["clip_id"]] += 1
        hit = True
    if row["asset_id"]:
        bucket["assets"][row["asset_id"]] += 1
        hit = True
    if row["adhoc_use"]:
        bucket["adhoc"][row["adhoc_use"]] += 1
        hit = True
    if row["family"]:
        bucket["families"][row["family"]] += 1
    if row["stack"]:
        bucket["stacks"][row["stack"]] += 1
    if row["prompt_variant"]:
        bucket["variants"][row["prompt_variant"]] += 1
    if row["prompt_hash"]:
        bucket["hashes"][row["prompt_hash"]] += 1
    if row["family_prompt"]:
        bucket["family_prompts"][row["family_prompt"]] += 1
    if row["param_override"]:
        bucket["param_sigs"][row["param_override"]] += 1
    if row["lora_override"]:
        bucket["loras"][row["lora_override"]] += 1
    if row["family_prompt_stack"]:
        bucket["recipes"][row["family_prompt_stack"]] += 1
    for key in row["override_keys"]:
        bucket["override_keys"][key] += 1
    if hit:
        bucket["seeded"] += 1


def summarize_bucket(bucket: Mapping[str, Any]) -> Dict[str, Any]:
    scanned = int(bucket.get("scanned") or 0)
    seed_layers = {
        "still_content_id": summarize_counts(bucket["stills"]),
        "clip_id": summarize_counts(bucket["clips"]),
        "video_asset": summarize_counts(bucket["assets"]),
        "adhoc_use": summarize_counts(bucket["adhoc"]),
    }
    recipe_layers = {
        "family": summarize_counts(bucket["families"]),
        "prompt_variant": summarize_counts(bucket["variants"]),
        "prompt_hash": summarize_counts(bucket["hashes"]),
        "family_prompt": summarize_counts(bucket["family_prompts"]),
        "param_override": summarize_counts(bucket["param_sigs"]),
        "stack": summarize_counts(bucket["stacks"]),
        "family_prompt_stack": summarize_counts(bucket["recipes"]),
        "lora_override": summarize_counts(bucket["loras"]),
    }
    return {
        "jobs_scanned": scanned,
        "jobs_with_seed": int(bucket.get("seeded") or 0),
        "seed_layers": seed_layers,
        "recipe_layers": recipe_layers,
        "layers": {**seed_layers, **recipe_layers},
        "override_keys": summarize_counts(bucket["override_keys"]),
        "optimal_unit": rank_reuse_units(seed_layers),
        "optimal_recipe_unit": rank_reuse_units(
            recipe_layers,
            min_uses=max(20, scanned // 10) if scanned else 0,
        ),
        "family_uses": dict(bucket["families"]),
        "family_prompt_uses": dict(bucket["family_prompts"]),
    }


def _layer_share_rows(summary: Mapping[str, Any], *, top_n: int = 12) -> List[Dict[str, Any]]:
    uses = int(summary.get("uses") or 0)
    rows = []
    for item in list(summary.get("top") or [])[:top_n]:
        n = int(item.get("uses") or 0)
        rows.append(
            {
                "id": item.get("id"),
                "uses": n,
                "share": round(n / uses, 4) if uses else 0.0,
            }
        )
    return rows


def _family_uses(cohort: Mapping[str, Any]) -> Dict[str, float]:
    raw = cohort.get("family_uses")
    if isinstance(raw, dict) and raw:
        return {str(k): float(v) for k, v in raw.items() if k}
    recipe = cohort.get("recipe_layers") if isinstance(cohort.get("recipe_layers"), dict) else {}
    family = recipe.get("family") if isinstance(recipe.get("family"), dict) else {}
    return {
        str(item.get("id")): float(item.get("uses") or 0)
        for item in (family.get("top") or [])
        if item.get("id")
    }


def guide_hourly_from_operator(
    operator: Mapping[str, Any],
    hourly: Mapping[str, Any],
    *,
    operator_weight: int = OPERATOR_FAVOR_WEIGHT,
    hourly_weight: int = HOURLY_FAVOR_WEIGHT,
    source: str = "operator",
    rule: str = "",
) -> Dict[str, Any]:
    """Blend a primary (operator) mix with a secondary (hourly) mix."""
    op_recipe = operator.get("recipe_layers") if isinstance(operator.get("recipe_layers"), dict) else {}
    hr_recipe = hourly.get("recipe_layers") if isinstance(hourly.get("recipe_layers"), dict) else {}
    op_family = op_recipe.get("family") if isinstance(op_recipe.get("family"), dict) else {}
    hr_family = hr_recipe.get("family") if isinstance(hr_recipe.get("family"), dict) else {}
    op_fp = op_recipe.get("family_prompt") if isinstance(op_recipe.get("family_prompt"), dict) else {}
    hr_fp = hr_recipe.get("family_prompt") if isinstance(hr_recipe.get("family_prompt"), dict) else {}

    op_uses_map = _family_uses(operator)
    hr_uses_map = _family_uses(hourly)
    op_total = sum(op_uses_map.values())
    hr_total = sum(hr_uses_map.values())
    names = sorted(set(op_uses_map) | set(hr_uses_map))
    blended: Dict[str, float] = {}
    for name in names:
        blended[name] = operator_weight * op_uses_map.get(name, 0) + hourly_weight * hr_uses_map.get(name, 0)
    blend_total = sum(blended.values()) or 1.0
    suggested_weights: List[Dict[str, Any]] = []
    for name, score in sorted(blended.items(), key=lambda kv: (-kv[1], kv[0])):
        if score <= 0:
            continue
        suggested_weights.append(
            {
                "family": name,
                "weight": max(1, round(100 * score / blend_total)),
                "operator_share": round(op_uses_map.get(name, 0) / op_total, 4) if op_total else 0.0,
                "hourly_share": round(hr_uses_map.get(name, 0) / hr_total, 4) if hr_total else 0.0,
                "blend_score": round(score, 3),
            }
        )
    drift = []
    for row in suggested_weights:
        delta = round(float(row["hourly_share"]) - float(row["operator_share"]), 4)
        if abs(delta) >= 0.05:
            drift.append(
                {
                    "family": row["family"],
                    "hourly_minus_operator": delta,
                    "direction": "hourly_over" if delta > 0 else "hourly_under",
                }
            )
    drift.sort(key=lambda r: -abs(float(r["hourly_minus_operator"])))
    return {
        "source": source,
        "operator_weight": operator_weight,
        "hourly_weight": hourly_weight,
        "rule": rule
        or (
            "Appetite is the strong prior. Operator more/fast_track outranks "
            "hourly appetite; unrated adhoc and hourly experiments keep a "
            f"low floor ({EXPERIMENT_WEIGHT}) so outliers stay possible."
        ),
        "optimal_seed_unit": (operator.get("optimal_unit") or {}).get("recommended"),
        "optimal_recipe_unit": (operator.get("optimal_recipe_unit") or {}).get("recommended"),
        "suggested_seed_family_weights": suggested_weights[:12],
        "operator_family_prompt_top": _layer_share_rows(op_fp),
        "hourly_family_prompt_top": _layer_share_rows(hr_fp),
        "family_drift": drift,
    }


def _ratings_paths_for_jobs(jobs_root: Path) -> tuple[Path, Path]:
    from shape_factory_ratings import default_appetite_index_path, default_ratings_index_path

    data_root = jobs_root
    if jobs_root.name == "jobs" and jobs_root.parent.name == "shape_factory":
        data_root = jobs_root.parent.parent
    og = Path("/home/yuji/comfyui-runpod-data/output/og")
    if not og.is_dir():
        og = data_root / "output" / "og"
    return default_ratings_index_path(og), default_appetite_index_path(og)


def load_favor_indexes(
    jobs_root: Path,
    *,
    ratings_doc: Optional[Mapping[str, Any]] = None,
    appetite_doc: Optional[Mapping[str, Any]] = None,
) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], str]:
    if ratings_doc is not None or appetite_doc is not None:
        return (
            dict(ratings_doc) if ratings_doc is not None else None,
            dict(appetite_doc) if appetite_doc is not None else None,
            "injected",
        )
    try:
        from shape_factory_ratings import load_appetite_doc, load_ratings_doc
    except Exception:
        return None, None, "ratings_import_failed"
    ratings_path, appetite_path = _ratings_paths_for_jobs(jobs_root)
    loaded_r: Optional[Dict[str, Any]] = None
    loaded_a: Optional[Dict[str, Any]] = None
    try:
        loaded_r = load_ratings_doc(ratings_path)
    except Exception:
        loaded_r = None
    try:
        loaded_a = load_appetite_doc(appetite_path)
    except Exception:
        loaded_a = None
    if loaded_r is None and loaded_a is None:
        return None, None, "ratings_missing"
    return loaded_r, loaded_a, "ratings_loaded"


def collect_reuse_stats(
    jobs_root: Path,
    *,
    limit: int = 0,
    ratings_doc: Optional[Mapping[str, Any]] = None,
    appetite_doc: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    root = Path(jobs_root).expanduser()
    all_b = _empty_bucket()
    hourly_b = _empty_bucket()
    operator_b = _empty_bucket()
    operator_fav_b = _empty_bucket()
    hourly_fav_b = _empty_bucket()
    favored_b = _empty_bucket()
    op_guide_families: Counter[str] = Counter()
    hr_guide_families: Counter[str] = Counter()
    parse_errors = 0
    loaded_r, loaded_a, favor_source = load_favor_indexes(
        root, ratings_doc=ratings_doc, appetite_doc=appetite_doc
    )

    paths: Iterable[Path] = root.rglob("*.job.json") if root.is_dir() else []
    for path in paths:
        if limit and all_b["scanned"] >= limit:
            break
        try:
            job = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            parse_errors += 1
            continue
        if not isinstance(job, dict):
            parse_errors += 1
            continue
        row = classify_job_reuse(job)
        favor = job_favor_signal(job, ratings_doc=loaded_r, appetite_doc=loaded_a)
        _add_row(all_b, row)
        hourly = job_is_hourly_product(job, path)
        if hourly:
            _add_row(hourly_b, row)
        else:
            _add_row(operator_b, row)
        if favor.get("favored"):
            _add_row(favored_b, row)
            if hourly:
                _add_row(hourly_fav_b, row)
            else:
                _add_row(operator_fav_b, row)
        weight = job_guide_weight(favor, hourly=hourly)
        family = str(row.get("family") or "")
        if family and weight > 0:
            if hourly:
                hr_guide_families[family] += weight
            else:
                op_guide_families[family] += weight

    all_s = summarize_bucket(all_b)
    hourly_s = summarize_bucket(hourly_b)
    operator_s = summarize_bucket(operator_b)
    operator_fav_s = summarize_bucket(operator_fav_b)
    hourly_fav_s = summarize_bucket(hourly_fav_b)
    favored_s = summarize_bucket(favored_b)
    have_appetite = operator_fav_s["jobs_scanned"] + hourly_fav_s["jobs_scanned"] > 0
    if have_appetite:
        guide = guide_hourly_from_operator(
            {"family_uses": dict(op_guide_families), "optimal_unit": operator_s["optimal_unit"],
             "optimal_recipe_unit": operator_s["optimal_recipe_unit"],
             "recipe_layers": operator_fav_s["recipe_layers"]},
            {"family_uses": dict(hr_guide_families),
             "recipe_layers": hourly_fav_s["recipe_layers"]},
            operator_weight=1,
            hourly_weight=1,
            source="appetite+experiment_floor",
        )
    else:
        guide = guide_hourly_from_operator(
            operator_s,
            hourly_s,
            source="operator",
        )
    recommended = operator_s
    note = (
        "Appetite (more/fast_track) is the strong steer — operator appetite "
        "first, hourly appetite second. Unrated adhoc and hourly experiments "
        f"keep a low floor ({EXPERIMENT_WEIGHT}) so outliers stay possible; "
        "they do not dominate unless they earn appetite."
    )
    if favor_source not in {"ratings_loaded", "injected"}:
        note += f" Favor indexes: {favor_source} — falling back to all operator submits."
    elif not have_appetite:
        note += " No appetite marks on outputs yet — experiment floor only; guide stays operator mix."
    return {
        "ok": True,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "jobs_root": str(root),
        "parse_errors": parse_errors,
        "note": note,
        "favor_source": favor_source,
        "jobs_scanned": all_s["jobs_scanned"],
        "jobs_with_seed": all_s["jobs_with_seed"],
        "jobs_hourly": hourly_s["jobs_scanned"],
        "jobs_operator": operator_s["jobs_scanned"],
        "jobs_operator_favored": operator_fav_s["jobs_scanned"],
        "jobs_hourly_favored": hourly_fav_s["jobs_scanned"],
        "jobs_favored": favored_s["jobs_scanned"],
        "layers": recommended["layers"],
        "seed_layers": recommended["seed_layers"],
        "recipe_layers": recommended["recipe_layers"],
        "override_keys": recommended["override_keys"],
        "optimal_unit": recommended["optimal_unit"],
        "optimal_recipe_unit": recommended["optimal_recipe_unit"],
        "guide_hourly": guide,
        "by_origin": {
            "all": all_s,
            "hourly": hourly_s,
            "operator": operator_s,
            "operator_favored": operator_fav_s,
            "hourly_favored": hourly_fav_s,
            "favored": favored_s,
        },
    }


def default_reuse_stats_path(*, data_root: Optional[Path] = None) -> Path:
    if data_root is None:
        data_root = Path(__file__).resolve().parents[2] / ".data"
    return Path(data_root).expanduser().resolve() / "shape_factory" / REUSE_STATS_BASENAME


def write_reuse_stats(payload: Mapping[str, Any], dest: Path) -> Path:
    dest = Path(dest).expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return dest


def _format_layer_table(layers: Mapping[str, Any]) -> List[str]:
    lines: List[str] = []
    for name, summary in layers.items():
        if not isinstance(summary, dict):
            continue
        lines.append(
            f"{name:22} unique={summary.get('unique'):<5} uses={summary.get('uses'):<5} "
            f"reused={summary.get('reused'):<5} rate={summary.get('reuse_rate'):<6} "
            f"fanout={summary.get('mean_fanout')}"
        )
    return lines


def _format_ranking(title: str, opt: Mapping[str, Any]) -> List[str]:
    ranking = opt.get("ranking") if isinstance(opt.get("ranking"), list) else []
    if not ranking:
        return []
    lines = ["", title]
    for row in ranking:
        lines.append(
            f"  {row.get('unit'):22} score={row.get('score'):<7} rate={row.get('reuse_rate')} "
            f"n={row.get('unique')}"
        )
    return lines


def _format_cohort(title: str, cohort: Mapping[str, Any]) -> List[str]:
    seed_opt = cohort.get("optimal_unit") if isinstance(cohort.get("optimal_unit"), dict) else {}
    recipe_opt = (
        cohort.get("optimal_recipe_unit") if isinstance(cohort.get("optimal_recipe_unit"), dict) else {}
    )
    seed_layers = cohort.get("seed_layers") if isinstance(cohort.get("seed_layers"), dict) else {}
    recipe_layers = cohort.get("recipe_layers") if isinstance(cohort.get("recipe_layers"), dict) else {}
    lines = [
        title,
        f"  jobs={cohort.get('jobs_scanned')}  seeded={cohort.get('jobs_with_seed')}",
        f"  optimal seed unit:   {seed_opt.get('recommended') or '(insufficient data)'}",
        f"  optimal recipe unit: {recipe_opt.get('recommended') or '(insufficient data)'}",
        "  seed",
        *[f"  {line}" for line in _format_layer_table(seed_layers)],
        "  recipe",
        *[f"  {line}" for line in _format_layer_table(recipe_layers)],
    ]
    lines.extend(f"  {line}" if line else "" for line in _format_ranking("seed ranking:", seed_opt))
    lines.extend(f"  {line}" if line else "" for line in _format_ranking("recipe ranking:", recipe_opt))
    return lines


def format_reuse_stats(payload: Mapping[str, Any]) -> str:
    by_origin = payload.get("by_origin") if isinstance(payload.get("by_origin"), dict) else {}
    guide = payload.get("guide_hourly") if isinstance(payload.get("guide_hourly"), dict) else {}
    lines = [
        f"reuse-stats  jobs={payload.get('jobs_scanned')}  "
        f"hourly={payload.get('jobs_hourly')}  operator={payload.get('jobs_operator')}  "
        f"favored={payload.get('jobs_favored')} "
        f"(op={payload.get('jobs_operator_favored')} hourly={payload.get('jobs_hourly_favored')})",
    ]
    note = str(payload.get("note") or "").strip()
    if note:
        lines.append(note)
    if guide:
        lines.append("")
        lines.append(f"guide hourlies from {guide.get('source') or 'operator'}")
        lines.append(f"  seed unit:   {guide.get('optimal_seed_unit') or '(insufficient data)'}")
        lines.append(f"  recipe unit: {guide.get('optimal_recipe_unit') or '(insufficient data)'}")
        weights = guide.get("suggested_seed_family_weights") or []
        if weights:
            lines.append("  suggested HOURLY_SEED_FAMILIES (appetite-weighted; experiments at floor)")
            for row in weights:
                lines.append(
                    f"    {row.get('family'):24} weight={row.get('weight'):<3} "
                    f"op={row.get('operator_share')} hourly={row.get('hourly_share')}"
                )
        drift = guide.get("family_drift") or []
        if drift:
            lines.append("  drift (hourly share minus operator share)")
            for row in drift:
                lines.append(
                    f"    {row.get('family'):24} {row.get('hourly_minus_operator'):+} "
                    f"{row.get('direction')}"
                )
        op_fp = guide.get("operator_family_prompt_top") or []
        if op_fp:
            lines.append("  favored operator family+prompt")
            for row in op_fp[:8]:
                lines.append(f"    {row.get('id'):40} share={row.get('share')} uses={row.get('uses')}")
        hr_fp = guide.get("hourly_family_prompt_top") or []
        if hr_fp:
            lines.append("  favored hourly family+prompt")
            for row in hr_fp[:6]:
                lines.append(f"    {row.get('id'):40} share={row.get('share')} uses={row.get('uses')}")
    operator_fav = by_origin.get("operator_favored") if isinstance(by_origin.get("operator_favored"), dict) else None
    hourly_fav = by_origin.get("hourly_favored") if isinstance(by_origin.get("hourly_favored"), dict) else None
    operator = by_origin.get("operator") if isinstance(by_origin.get("operator"), dict) else None
    hourly = by_origin.get("hourly") if isinstance(by_origin.get("hourly"), dict) else None
    if operator_fav and operator_fav.get("jobs_scanned"):
        lines.append("")
        lines.extend(_format_cohort("operator with appetite", operator_fav))
    if hourly_fav and hourly_fav.get("jobs_scanned"):
        lines.append("")
        lines.extend(_format_cohort("hourly with appetite", hourly_fav))
    if operator:
        lines.append("")
        lines.extend(_format_cohort("operator (all non-hourly)", operator))
    if hourly:
        lines.append("")
        lines.extend(_format_cohort("hourly planner (all; do not fit to this)", hourly))
    if not operator:
        lines.append("")
        lines.extend(
            _format_cohort(
                "all jobs",
                {
                    "jobs_scanned": payload.get("jobs_scanned"),
                    "jobs_with_seed": payload.get("jobs_with_seed"),
                    "seed_layers": payload.get("seed_layers"),
                    "recipe_layers": payload.get("recipe_layers"),
                    "optimal_unit": payload.get("optimal_unit"),
                    "optimal_recipe_unit": payload.get("optimal_recipe_unit"),
                },
            )
        )
    return "\n".join(lines)


def cmd_reuse_stats(args: argparse.Namespace) -> int:
    jobs_dir = Path(getattr(args, "jobs_dir", "") or "").expanduser()
    payload = collect_reuse_stats(jobs_dir, limit=int(getattr(args, "limit", 0) or 0))
    out = Path(getattr(args, "out", "") or "").expanduser() if getattr(args, "out", None) else None
    if getattr(args, "apply", False):
        dest = out or default_reuse_stats_path()
        write_reuse_stats(payload, dest)
        payload = dict(payload)
        payload["wrote"] = str(dest)
    elif out:
        write_reuse_stats(payload, out)
        payload = dict(payload)
        payload["wrote"] = str(out)
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2))
    else:
        print(format_reuse_stats(payload))
        if payload.get("wrote"):
            print(f"wrote {payload['wrote']}")
    return 0


def add_reuse_stats_subparser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "reuse-stats",
        help="Collect seed + recipe reuse (family, prompt, param overrides, stack)",
    )
    p.add_argument(
        "--jobs-dir",
        default="/home/yuji/src/comfyui-runpod/.data/shape_factory/jobs",
        help="Directory tree of *.job.json",
    )
    p.add_argument("--limit", type=int, default=0, help="Optional max jobs to scan")
    p.add_argument("--apply", action="store_true", help="Write snapshot to .data/shape_factory/reuse_stats.json")
    p.add_argument("--out", help="Write snapshot JSON to this path")
    p.add_argument("--json", action="store_true", help="Print full JSON instead of the table")
    p.set_defaults(func=cmd_reuse_stats)
