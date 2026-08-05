#!/usr/bin/env python3
"""
Bootstrap human ratings with stratified sampler sessions.

Sessions mix easy thumbs-down, easy thumbs-up, and middle — not a queue of only
hard borderline cases. After you rate, rebuild ratings + heuristics.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from correlate_output_ratings import parse_xmp_rating
from shape_factory_heuristics import (
    LineageGraph,
    _og_group_id_from_relpath,
    _source_group_ids,
    default_heuristics_index_path,
    default_lineage_edges_path,
)
from shape_factory_ratings import (
    APPETITE_SCORE,
    axes_complete,
    default_appetite_index_path,
    default_ratings_index_path,
    lookup_output_appetite,
    lookup_output_rating,
    normalize_axes_map,
    utc_now,
)
from shape_factory_disposition import (
    default_disposition_index_path,
    disposition_for_item,
    is_retired_disposition,
    lookup_output_disposition,
)
from shape_factory_triage import (
    default_triage_index_path,
    needs_triage_item,
    triage_for_item,
)

SAMPLER_SCHEMA_VERSION = 8

# Selection behavior for rating sessions (UI + API ``mode``).
SELECTION_MODES = frozenset({"mixed", "random", "search", "latest"})

# Marathon session mix: many easy rejects, few easy keepers, moderate middle (not hard-tail).
DEFAULT_SESSION_MIX: Dict[str, float] = {
    "easy_down": 0.45,
    "easy_up": 0.15,
    "middle": 0.40,
}
# Max share of vision-flagged borderline items in the middle slice.
MAX_HARD_MIDDLE_SHARE = 0.12

# Where ComfyUI vision captions / scores land when batch-indexed (future Florence pass).
VISION_SCORES_BASENAME = "vision_scores.json"


def default_discovery_index_path(og_root: Path) -> Path:
    return og_root.resolve().parent / "_status" / "discovery_og_wip_index.json"


def default_sampler_state_path(og_root: Path) -> Path:
    return og_root.resolve().parent / "_status" / "rating_sampler_state.json"


def default_sampler_sessions_dir(og_root: Path) -> Path:
    return og_root.resolve().parent / "_status" / "rating_sampler_sessions"


def default_vision_scores_path(og_root: Path) -> Path:
    return og_root.resolve().parent / "_status" / VISION_SCORES_BASENAME


def _load_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _rated_output_keys(ratings_doc: Optional[dict[str, Any]]) -> Set[str]:
    keys: Set[str] = set()
    if not ratings_doc:
        return keys
    table = ratings_doc.get("by_output_relpath") or {}
    if not isinstance(table, dict):
        return keys
    for key, row in table.items():
        if isinstance(row, dict) and row.get("explicit") is not None:
            keys.add(str(key).strip().replace("\\", "/"))
            short = str(row.get("short_key") or "").strip()
            if short:
                keys.add(short)
    return keys


def _og_mp4_path(og_root: Path, relpath: str) -> Optional[Path]:
    raw = str(relpath or "").strip().replace("\\", "/").lstrip("/")
    if not raw:
        return None
    if raw.startswith("output/"):
        raw = raw[len("output/") :]
    candidates = [
        og_root / raw,
        og_root.parent / raw,
        og_root / Path(raw).name,
    ]
    for cand in candidates:
        if cand.is_file():
            return cand.resolve()
    return None


def _is_rated_item(
    item: dict[str, Any],
    rated_keys: Set[str],
    ratings_doc: Optional[dict[str, Any]],
    *,
    og_root: Optional[Path] = None,
) -> bool:
    """True when an explicit quality rating exists (index keys, ratings row, or on-disk XMP)."""
    rel = str(item.get("relpath") or "").strip().replace("\\", "/")
    if not rel:
        return True
    if rel in rated_keys:
        return True
    row = lookup_output_rating(rel, ratings_doc or {})
    if isinstance(row, dict) and row.get("explicit") is not None:
        return True
    group_id = str(item.get("group_id") or "")
    stem = Path(rel).stem.lower()
    for key in (rel, f"og/{rel.split('/og/', 1)[-1]}" if "/og/" in rel else "", f"output/og/{stem}"):
        if key and key in rated_keys:
            return True
    # Disk XMP beside mp4 (canonical when index lags)
    if og_root is not None:
        mp4 = _og_mp4_path(og_root, rel)
        if mp4 is not None:
            for suffix in (".XMP", ".xmp"):
                xmp = mp4.with_suffix(suffix)
                if xmp.is_file() and parse_xmp_rating(xmp) is not None:
                    return True
    _ = group_id
    return False


def _load_vision_scores(path: Path) -> dict[str, Any]:
    doc = _load_json(path)
    if not doc:
        return {}
    table = doc.get("by_group_id") or doc.get("scores") or doc
    return table if isinstance(table, dict) else {}


@dataclass
class RatingCandidate:
    relpath: str
    group_id: str
    predicted_score: float
    heuristic_confidence: float
    evidence: List[str] = field(default_factory=list)
    signals: Dict[str, Any] = field(default_factory=dict)
    vision_recommended: bool = False
    vision_reasons: List[str] = field(default_factory=list)
    discovery_href: str = ""
    session_bucket: str = "middle"
    appetite: Optional[str] = None
    appetite_facet: Optional[str] = None
    disposition_markers: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    needs_triage: bool = True
    last_triaged_at: Optional[str] = None
    triage_pass_count: int = 0
    mtime: float = 0.0
    thumb_relpath: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "relpath": self.relpath,
            "group_id": self.group_id,
            "predicted_score": round(self.predicted_score, 3),
            "heuristic_confidence": round(self.heuristic_confidence, 3),
            "evidence": self.evidence,
            "signals": self.signals,
            "vision_recommended": self.vision_recommended,
            "vision_reasons": self.vision_reasons,
            "discovery_href": self.discovery_href,
            "session_bucket": self.session_bucket,
            "appetite": self.appetite,
            "appetite_facet": self.appetite_facet,
            "disposition_markers": self.disposition_markers,
            "tags": self.tags,
            "needs_triage": self.needs_triage,
            "last_triaged_at": self.last_triaged_at,
            "triage_pass_count": self.triage_pass_count,
            "mtime": self.mtime,
            "thumb_relpath": self.thumb_relpath,
        }


def normalize_selection_mode(raw: Optional[str]) -> str:
    """Map API/CLI aliases onto a canonical selection mode."""
    m = str(raw or "mixed").strip().lower()
    if m in ("heuristic", "stratified", "balanced", ""):
        return "mixed"
    if m in SELECTION_MODES:
        return m
    return "mixed"


def _item_matches_query(item: dict[str, Any], query: str) -> bool:
    q = str(query or "").strip().lower()
    if not q:
        return True
    rel = str(item.get("relpath") or "").replace("\\", "/")
    hay = " ".join(
        [
            rel,
            Path(rel).name,
            str(item.get("group_id") or ""),
            str(item.get("name") or ""),
            " ".join(str(t) for t in (item.get("tags") or []) if str(t).strip()),
        ]
    ).lower()
    return all(tok in hay for tok in q.split())


def _discovery_href(relpath: str) -> str:
    norm = relpath.strip().replace("\\", "/").lstrip("/")
    return f"/discovery?relpath={norm}" if norm else "/discovery"


def _graph_pattern_score(
    workflow_fingerprint: Optional[str],
    heuristics_doc: Optional[dict[str, Any]],
) -> Tuple[float, List[str]]:
    if not heuristics_doc or not workflow_fingerprint:
        return 0.0, []
    wf = str(workflow_fingerprint).strip().lower()
    if not wf:
        return 0.0, []
    by_pattern = heuristics_doc.get("by_pattern") or {}
    if not isinstance(by_pattern, dict):
        return 0.0, []
    best = 0.0
    evidence: List[str] = []
    for key, row in by_pattern.items():
        if not isinstance(row, dict):
            continue
        gh = str(row.get("graph_hash") or "")
        if gh and gh.lower().startswith(wf[:16]) or wf.startswith(str(key).replace("graph:", "")[:16]):
            val = float(row.get("inferred") or 0)
            if val > best:
                best = val
                evidence.append(f"pattern:{key}")
    return best, evidence


def _sibling_keeper_boost(
    group_id: str,
    lineage: LineageGraph,
    rated_by_gid: Dict[str, int],
) -> Tuple[float, List[str]]:
    """Unrated item shares a parent with an explicitly highly rated sibling."""
    boost = 0.0
    evidence: List[str] = []
    for parent in lineage.parents_of.get(group_id, []):
        siblings = lineage.children_of.get(parent, [])
        rated_high = [rated_by_gid[s] for s in siblings if s != group_id and rated_by_gid.get(s, 0) >= 4]
        if not rated_high:
            continue
        avg = statistics.mean(rated_high)
        boost = max(boost, avg * 0.85)
        evidence.append(f"rated_sibling_under:{parent} n={len(rated_high)}")
    return boost, evidence


def _vision_gap_reasons(
    *,
    has_embed: bool,
    graph_score: float,
    lineage_score: float,
    sibling_boost: float,
    pattern_n: int,
    vision_score: Optional[float],
) -> List[str]:
    reasons: List[str] = []
    if vision_score is None and has_embed:
        if sibling_boost >= 3.5 and graph_score < 3.0:
            reasons.append("sibling_keeper_but_weak_graph_join")
        if lineage_score >= 3.5 and graph_score < 2.5:
            reasons.append("lineage_uplift_without_workflow_pattern")
        if graph_score < 2.0 and has_embed:
            reasons.append("unclassified_workflow_with_embed")
        if 0 < pattern_n < 3 and graph_score >= 4.0:
            reasons.append("promising_pattern_needs_confirmation")
    return reasons


def _item_tags(rel: str, group_id: str, tags_doc: Optional[dict[str, Any]]) -> List[str]:
    if not tags_doc:
        return []
    by_gid = tags_doc.get("by_group_id") or {}
    if not isinstance(by_gid, dict):
        return []
    row = by_gid.get(group_id) if group_id else None
    if not isinstance(row, dict):
        row = by_gid.get(_og_group_id_from_relpath(rel) or "")
    if isinstance(row, dict):
        return [str(t).strip().lower() for t in (row.get("tags") or []) if str(t).strip()]
    return []


def score_unrated_candidate(
    item: dict[str, Any],
    *,
    lineage: LineageGraph,
    heuristics_doc: Optional[dict[str, Any]],
    ratings_doc: Optional[dict[str, Any]],
    rated_by_gid: Dict[str, int],
    vision_scores: Optional[dict[str, Any]] = None,
    appetite_doc: Optional[dict[str, Any]] = None,
    disposition_doc: Optional[dict[str, Any]] = None,
    triage_doc: Optional[dict[str, Any]] = None,
    tags_doc: Optional[dict[str, Any]] = None,
) -> RatingCandidate:
    rel = str(item.get("relpath") or "").strip().replace("\\", "/")
    group_id = str(item.get("group_id") or _og_group_id_from_relpath(rel) or "")
    evidence: List[str] = []
    signals: dict[str, Any] = {}

    lineage_score = 0.0
    by_lineage = (heuristics_doc or {}).get("by_group_lineage") or {}
    if group_id and isinstance(by_lineage, dict):
        row = by_lineage.get(group_id)
        if isinstance(row, dict) and row.get("inferred") is not None:
            lineage_score = float(row["inferred"])
            evidence.append(f"lineage_group:{group_id}")
            signals["lineage"] = lineage_score

    parent_uplift = 0.0
    for parent in lineage.parents_of.get(group_id, []):
        row = by_lineage.get(parent) if isinstance(by_lineage, dict) else None
        if isinstance(row, dict) and row.get("inferred") is not None:
            val = float(row["inferred"])
            parent_uplift = max(parent_uplift, val * 0.7)
            evidence.append(f"parent_lineage:{parent}")
    signals["parent_lineage"] = parent_uplift

    sibling_boost, sib_ev = _sibling_keeper_boost(group_id, lineage, rated_by_gid)
    evidence.extend(sib_ev)
    signals["sibling_boost"] = sibling_boost

    graph_score, pat_ev = _graph_pattern_score(item.get("workflow_fingerprint"), heuristics_doc)
    evidence.extend(pat_ev)
    signals["pattern"] = graph_score

    pattern_n = 0
    if pat_ev:
        by_pattern = (heuristics_doc or {}).get("by_pattern") or {}
        key = pat_ev[0].split(":", 1)[-1]
        prow = by_pattern.get(key) if isinstance(by_pattern, dict) else None
        if isinstance(prow, dict):
            pattern_n = int(prow.get("n") or 0)
    signals["pattern_n"] = pattern_n

    source_score = 0.0
    if ratings_doc:
        by_source = ratings_doc.get("by_source_basename") or {}
        # Use lineage parent basename hints when available
        for parent in lineage.parents_of.get(group_id, []):
            bn = parent.split(":")[-1] if ":" in parent else parent
            for gid in _source_group_ids(bn):
                _ = gid
            row = by_source.get(bn) if isinstance(by_source, dict) else None
            if isinstance(row, dict) and row.get("inferred") is not None:
                source_score = max(source_score, float(row["inferred"]))
                evidence.append(f"source_inferred:{bn}")

    vision_score: Optional[float] = None
    if vision_scores and group_id:
        raw = vision_scores.get(group_id) or vision_scores.get(rel)
        if isinstance(raw, dict):
            vision_score = raw.get("keeper_score") or raw.get("score")
        elif raw is not None:
            vision_score = float(raw)
    if vision_score is not None:
        signals["vision"] = vision_score
        evidence.append("vision_score")

    # Appetite ("do more WITH this") + tag affinity: a MINOR nudge so directions you
    # already want surface a little sooner for quality rating (does not dominate).
    appetite_state: Optional[str] = None
    appetite_facet: Optional[str] = None
    app_row = lookup_output_appetite(rel, appetite_doc or {}) if appetite_doc else None
    if isinstance(app_row, dict) and app_row.get("appetite"):
        appetite_state = str(app_row.get("appetite"))
        appetite_facet = str(app_row.get("facet") or "both")
        signals["appetite"] = APPETITE_SCORE.get(appetite_state, 0.0)

    tags = _item_tags(rel, group_id, tags_doc)
    tag_affinity = 0.0
    by_tag_app = (heuristics_doc or {}).get("by_tag_appetite") or {}
    if tags and isinstance(by_tag_app, dict) and by_tag_app:
        vals = [float(by_tag_app[t]["inferred"]) for t in tags if isinstance(by_tag_app.get(t), dict) and by_tag_app[t].get("inferred") is not None]
        if vals:
            tag_affinity = statistics.mean(vals)
            signals["tag_affinity"] = round(tag_affinity, 3)
            evidence.append("tag_affinity")

    pattern_appetite = 0.0
    by_pat_app = (heuristics_doc or {}).get("by_pattern_appetite") or {}
    by_lin_app = (heuristics_doc or {}).get("by_group_lineage_appetite") or {}
    if group_id and isinstance(by_lin_app, dict):
        lrow = by_lin_app.get(group_id)
        if isinstance(lrow, dict) and lrow.get("inferred") is not None:
            pattern_appetite = max(pattern_appetite, float(lrow["inferred"]))
    if pattern_appetite or tag_affinity:
        signals["appetite_rollup"] = round(max(pattern_appetite, tag_affinity), 3)

    # Direction bias: appetite/tag signals map [1..5] -> small +/- around neutral (2.5).
    direction = 0.0
    for val, weight in ((signals.get("appetite"), 0.20), (tag_affinity, 0.12), (pattern_appetite, 0.10)):
        if val:
            direction += weight * (float(val) - 2.5)

    # Predicted keeper likelihood (for bucketing — sessions stratify, not top-k only).
    exploitation = max(lineage_score, parent_uplift, sibling_boost, graph_score, source_score)
    if vision_score is not None:
        exploitation = max(exploitation, float(vision_score))

    predicted = max(0.0, exploitation + direction)
    signal_vals = [v for v in (lineage_score, sibling_boost, graph_score, source_score) if v > 0]
    if vision_score is not None:
        signal_vals.append(float(vision_score))
    if len(signal_vals) >= 2:
        spread = max(signal_vals) - min(signal_vals)
        confidence = max(0.25, min(0.95, 1.0 - spread / 5.0))
    elif signal_vals:
        confidence = 0.55
    else:
        confidence = 0.15

    has_embed = bool(item.get("has_embedded_prompt"))
    vision_reasons = _vision_gap_reasons(
        has_embed=has_embed,
        graph_score=graph_score,
        lineage_score=max(lineage_score, parent_uplift),
        sibling_boost=sibling_boost,
        pattern_n=pattern_n,
        vision_score=vision_score,
    )

    tags = _item_tags(rel, group_id, tags_doc)

    disp_markers: List[str] = []
    if disposition_doc:
        disp = disposition_for_item(item, disposition_doc)
        raw_markers = disp.get("disposition_markers") or []
        if isinstance(raw_markers, list):
            disp_markers = [str(m) for m in raw_markers]

    triage_info = triage_for_item(item, triage_doc, disposition_doc=disposition_doc)
    needs = needs_triage_item(item, triage_doc=triage_doc, disposition_doc=disposition_doc)

    try:
        mtime = float(item.get("mtime") or 0)
    except (TypeError, ValueError):
        mtime = 0.0

    thumb_raw = item.get("thumb_relpath")
    thumb_relpath = str(thumb_raw).strip().replace("\\", "/") if isinstance(thumb_raw, str) and thumb_raw.strip() else None

    return RatingCandidate(
        relpath=rel,
        group_id=group_id,
        predicted_score=predicted,
        heuristic_confidence=confidence,
        evidence=evidence,
        signals=signals,
        vision_recommended=bool(vision_reasons),
        vision_reasons=vision_reasons,
        discovery_href=_discovery_href(rel),
        appetite=appetite_state,
        appetite_facet=appetite_facet,
        disposition_markers=disp_markers,
        tags=tags,
        needs_triage=needs,
        last_triaged_at=triage_info.get("last_triaged_at"),
        triage_pass_count=int(triage_info.get("triage_pass_count") or 0),
        mtime=mtime,
        thumb_relpath=thumb_relpath,
    )


def _build_rated_by_gid(ratings_doc: Optional[dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    if not ratings_doc:
        return out
    table = ratings_doc.get("by_output_relpath") or {}
    if not isinstance(table, dict):
        return out
    seen: Set[str] = set()
    for _key, row in table.items():
        if not isinstance(row, dict) or row.get("explicit") is None:
            continue
        short = str(row.get("short_key") or _key)
        if short in seen:
            continue
        seen.add(short)
        gid = _og_group_id_from_relpath(short)
        if gid:
            out[gid] = int(row["explicit"])
    return out


def _session_mix_from_env() -> Dict[str, float]:
    import os

    mix = dict(DEFAULT_SESSION_MIX)
    for key in mix:
        raw = os.environ.get(f"SAMPLER_MIX_{key.upper()}", "").strip()
        if not raw:
            continue
        try:
            mix[key] = float(raw)
        except ValueError:
            pass
    total = sum(mix.values())
    if total <= 0:
        return dict(DEFAULT_SESSION_MIX)
    return {k: v / total for k, v in mix.items()}


def _is_hard_borderline(cand: RatingCandidate) -> bool:
    """Ambiguous tail — useful later for vision, not for bulk HITL sessions."""
    if not cand.vision_recommended:
        return False
    if cand.heuristic_confidence >= 0.55:
        return False
    return 2.2 <= cand.predicted_score <= 4.2


def _stratified_session_pick(
    candidates: List[RatingCandidate],
    *,
    limit: int,
    seed: int = 0,
    mix: Optional[Dict[str, float]] = None,
) -> List[RatingCandidate]:
    """
    Build a marathon-friendly queue: easy downs, easy ups, middle — interleaved.

    Uses percentiles on predicted_score so buckets adapt to the corpus. Hard
    borderline (vision + low confidence) capped in the middle slice.
    """
    if not candidates or limit <= 0:
        return []

    mix = mix or _session_mix_from_env()
    rng = random.Random(int(seed))
    sorted_c = sorted(candidates, key=lambda c: (c.predicted_score, c.group_id))
    n = len(sorted_c)

    n_down = max(0, int(round(limit * mix.get("easy_down", 0.45))))
    n_up = max(0, int(round(limit * mix.get("easy_up", 0.15))))
    n_mid = max(0, limit - n_down - n_up)

    down_pool = sorted_c[: max(1, n * 38 // 100)]
    up_pool = sorted_c[max(0, n * 88 // 100) :]
    mid_lo = n * 28 // 100
    mid_hi = max(mid_lo + 1, n * 72 // 100)
    mid_pool = list(sorted_c[mid_lo:mid_hi])
    mid_plain = [c for c in mid_pool if not _is_hard_borderline(c)]
    if len(mid_plain) < n_mid:
        mid_plain = mid_pool

    hard_cap = max(1, int(round(n_mid * MAX_HARD_MIDDLE_SHARE)))
    mid_hard = [c for c in mid_plain if _is_hard_borderline(c)]
    mid_easy = [c for c in mid_plain if not _is_hard_borderline(c)]
    rng.shuffle(mid_easy)
    rng.shuffle(mid_hard)
    mid_pick = mid_easy[: max(0, n_mid - min(len(mid_hard), hard_cap))]
    if len(mid_pick) < n_mid:
        mid_pick.extend(mid_hard[: n_mid - len(mid_pick)])
    if len(mid_pick) < n_mid:
        remainder = [c for c in sorted_c if c not in mid_pick]
        rng.shuffle(remainder)
        mid_pick.extend(remainder[: n_mid - len(mid_pick)])

    def draw(pool: List[RatingCandidate], k: int, exclude: Set[str]) -> List[RatingCandidate]:
        rng.shuffle(pool)
        out: List[RatingCandidate] = []
        for cand in pool:
            if len(out) >= k:
                break
            if cand.group_id in exclude:
                continue
            out.append(cand)
            exclude.add(cand.group_id)
        return out

    used: Set[str] = set()
    picked_down = draw(down_pool, n_down, used)
    picked_up = draw(up_pool, n_up, used)
    picked_mid = draw(mid_pick, n_mid, used)

    if len(picked_down) + len(picked_up) + len(picked_mid) < limit:
        filler_pool = [c for c in sorted_c if c.group_id not in used]
        rng.shuffle(filler_pool)
        for cand in filler_pool:
            if len(picked_down) + len(picked_up) + len(picked_mid) >= limit:
                break
            if cand.predicted_score <= sorted_c[n * 38 // 100].predicted_score + 0.01:
                cand.session_bucket = "easy_down"
                picked_down.append(cand)
            elif cand.predicted_score >= sorted_c[max(0, n * 88 // 100)].predicted_score - 0.01:
                cand.session_bucket = "easy_up"
                picked_up.append(cand)
            else:
                cand.session_bucket = "middle"
                picked_mid.append(cand)
            used.add(cand.group_id)

    for cand in picked_down:
        cand.session_bucket = "easy_down"
    for cand in picked_up:
        cand.session_bucket = "easy_up"
    for cand in picked_mid:
        cand.session_bucket = "middle"

    # Interleave: mostly quick rejects, sprinkle middle + occasional keeper anchor.
    cycle = ["easy_down", "easy_down", "middle", "easy_down", "middle", "easy_up"]
    buckets: Dict[str, List[RatingCandidate]] = {
        "easy_down": picked_down,
        "easy_up": picked_up,
        "middle": picked_mid,
    }
    indices = {k: 0 for k in buckets}
    interleaved: List[RatingCandidate] = []
    ci = 0
    while len(interleaved) < limit:
        progressed = False
        for _ in range(len(cycle)):
            bucket = cycle[ci % len(cycle)]
            ci += 1
            idx = indices[bucket]
            pool = buckets[bucket]
            if idx < len(pool):
                interleaved.append(pool[idx])
                indices[bucket] = idx + 1
                progressed = True
                if len(interleaved) >= limit:
                    break
        if not progressed:
            break

    if len(interleaved) < limit:
        seen_gid = {c.group_id for c in interleaved}
        rest = [c for b in buckets.values() for c in b if c.group_id not in seen_gid]
        rng.shuffle(rest)
        for cand in rest:
            if len(interleaved) >= limit:
                break
            interleaved.append(cand)
            seen_gid.add(cand.group_id)

    return interleaved[:limit]


def _pick_by_mode(
    candidates: List[RatingCandidate],
    *,
    mode: str,
    limit: int,
    seed: int = 0,
    mix: Optional[Dict[str, float]] = None,
    query: str = "",
) -> List[RatingCandidate]:
    """Select a batch according to selection mode."""
    mode = normalize_selection_mode(mode)
    if not candidates or limit <= 0:
        return []
    if mode == "search" and not str(query or "").strip():
        return []
    if mode == "random":
        rng = random.Random(int(seed))
        pool = list(candidates)
        rng.shuffle(pool)
        out = pool[:limit]
        for c in out:
            c.session_bucket = "middle"
        return out
    if mode == "latest":
        out = sorted(candidates, key=lambda c: (-float(c.mtime or 0), c.group_id))[:limit]
        for c in out:
            c.session_bucket = "middle"
        return out
    if mode == "search":
        # Query already filtered the pool; prefer recent matches, then score.
        out = sorted(
            candidates,
            key=lambda c: (-float(c.mtime or 0), -float(c.predicted_score), c.group_id),
        )[:limit]
        for c in out:
            c.session_bucket = "middle"
        return out
    return _stratified_session_pick(candidates, limit=limit, seed=seed, mix=mix)


def _is_retired_item(item: dict[str, Any], disposition_doc: Optional[dict[str, Any]]) -> bool:
    if not disposition_doc:
        return False
    rel = str(item.get("relpath") or "").strip().replace("\\", "/")
    row = lookup_output_disposition(rel, disposition_doc)
    if not row:
        return False
    markers = row.get("markers") or []
    return is_retired_disposition(markers if isinstance(markers, list) else [])


def item_has_explicit_quality(
    item: dict[str, Any],
    *,
    ratings_doc: Optional[dict[str, Any]] = None,
    rated_keys: Optional[Set[str]] = None,
    og_root: Optional[Path] = None,
) -> bool:
    """
    True when all three quality axes are set (1–5).

    Legacy lone ``explicit`` / on-disk XMP without ``axes`` does **not** count —
    those clips stay in the rate pool until axes are filled.
    """
    _ = (rated_keys, og_root)
    rel = str(item.get("relpath") or item.get("video_relpath") or "").strip().replace("\\", "/")
    if not rel:
        return True
    row = lookup_output_rating(rel, ratings_doc or {})
    if not isinstance(row, dict):
        return False
    return axes_complete(normalize_axes_map(row.get("axes")))


def item_has_appetite(item: dict[str, Any], appetite_doc: Optional[dict[str, Any]]) -> bool:
    rel = str(item.get("relpath") or item.get("video_relpath") or "").strip().replace("\\", "/")
    if not rel or not appetite_doc:
        return False
    row = lookup_output_appetite(rel, appetite_doc)
    if not isinstance(row, dict):
        return False
    return bool(str(row.get("appetite") or "").strip())


def is_rating_complete(
    item: dict[str, Any],
    *,
    ratings_doc: Optional[dict[str, Any]] = None,
    appetite_doc: Optional[dict[str, Any]] = None,
    rated_keys: Optional[Set[str]] = None,
    og_root: Optional[Path] = None,
) -> bool:
    """Rating activity is done when all quality axes and appetite are set."""
    return item_has_explicit_quality(
        item, ratings_doc=ratings_doc, rated_keys=rated_keys, og_root=og_root
    ) and item_has_appetite(item, appetite_doc)


def needs_rating_item(
    item: dict[str, Any],
    *,
    ratings_doc: Optional[dict[str, Any]] = None,
    appetite_doc: Optional[dict[str, Any]] = None,
    disposition_doc: Optional[dict[str, Any]] = None,
    rated_keys: Optional[Set[str]] = None,
    og_root: Optional[Path] = None,
) -> bool:
    """True when the rate queue should still show this clip (missing quality and/or appetite)."""
    if _is_retired_item(item, disposition_doc):
        return False
    return not is_rating_complete(
        item,
        ratings_doc=ratings_doc,
        appetite_doc=appetite_doc,
        rated_keys=rated_keys,
        og_root=og_root,
    )


def collect_needs_triage_video_items(
    discovery_doc: dict[str, Any],
    *,
    library: str = "og",
    disposition_doc: Optional[dict[str, Any]] = None,
    triage_doc: Optional[dict[str, Any]] = None,
) -> List[dict[str, Any]]:
    """Legacy triage pool (disposition-oriented). Prefer ``collect_needs_rating_video_items`` for rate queue."""
    items = discovery_doc.get("items") or []
    out: List[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("library") or "") != library:
            continue
        rel = str(item.get("relpath") or "")
        if not rel.lower().endswith(".mp4"):
            continue
        if not needs_triage_item(item, triage_doc=triage_doc, disposition_doc=disposition_doc):
            continue
        out.append(item)
    return out


def collect_needs_rating_video_items(
    discovery_doc: dict[str, Any],
    *,
    library: str = "og",
    ratings_doc: Optional[dict[str, Any]] = None,
    appetite_doc: Optional[dict[str, Any]] = None,
    disposition_doc: Optional[dict[str, Any]] = None,
    rated_keys: Optional[Set[str]] = None,
    og_root: Optional[Path] = None,
    include_done: bool = False,
    query: Optional[str] = None,
) -> List[dict[str, Any]]:
    """Videos for the rate-queue pool (incomplete by default; optionally all + search)."""
    keys = rated_keys if rated_keys is not None else _rated_output_keys(ratings_doc)
    items = discovery_doc.get("items") or []
    out: List[dict[str, Any]] = []
    q = str(query or "").strip()
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("library") or "") != library:
            continue
        rel = str(item.get("relpath") or "")
        if not rel.lower().endswith(".mp4"):
            continue
        if not include_done:
            if not needs_rating_item(
                item,
                ratings_doc=ratings_doc,
                appetite_doc=appetite_doc,
                disposition_doc=disposition_doc,
                rated_keys=keys,
                og_root=og_root,
            ):
                continue
        if q and not _item_matches_query(item, q):
            continue
        out.append(item)
    return out


def collect_unrated_video_items(
    discovery_doc: dict[str, Any],
    *,
    rated_keys: Set[str],
    ratings_doc: Optional[dict[str, Any]],
    library: str = "og",
    og_root: Optional[Path] = None,
    disposition_doc: Optional[dict[str, Any]] = None,
    triage_doc: Optional[dict[str, Any]] = None,
    appetite_doc: Optional[dict[str, Any]] = None,
    include_done: bool = False,
    query: Optional[str] = None,
) -> List[dict[str, Any]]:
    """Rate-queue pool: missing quality and/or appetite (not disposition/triage-gated)."""
    _ = triage_doc
    return collect_needs_rating_video_items(
        discovery_doc,
        library=library,
        ratings_doc=ratings_doc,
        appetite_doc=appetite_doc,
        disposition_doc=disposition_doc,
        rated_keys=rated_keys,
        og_root=og_root,
        include_done=include_done,
        query=query,
    )


def sample_rating_queue(
    *,
    og_root: Path,
    limit: int = 20,
    discovery_index: Optional[Path] = None,
    ratings_index: Optional[Path] = None,
    heuristics_index: Optional[Path] = None,
    lineage_edges: Optional[Path] = None,
    vision_scores: Optional[Path] = None,
    exclude_presented: bool = False,
    sampler_state: Optional[Path] = None,
    seed: int = 0,
    min_predicted: float = 0.0,
    mode: str = "mixed",
    query: Optional[str] = None,
    include_done: bool = False,
) -> dict[str, Any]:
    og_root = og_root.expanduser().resolve()
    discovery_path = (discovery_index or default_discovery_index_path(og_root)).resolve()
    ratings_path = (ratings_index or default_ratings_index_path(og_root)).resolve()
    heuristics_path = (heuristics_index or default_heuristics_index_path(og_root)).resolve()
    lineage_path = (lineage_edges or default_lineage_edges_path(og_root)).resolve()
    vision_path = (vision_scores or default_vision_scores_path(og_root)).resolve()
    state_path = (sampler_state or default_sampler_state_path(og_root)).resolve()

    selection_mode = normalize_selection_mode(mode)
    query_s = str(query or "").strip()
    # Score floor only applies to the stratified mix; other modes want the full pool.
    effective_min = float(min_predicted) if selection_mode == "mixed" else 0.0

    discovery_doc = _load_json(discovery_path)
    if not discovery_doc:
        return {"ok": False, "error": "discovery_index_missing", "path": str(discovery_path)}

    ratings_doc = _load_json(ratings_path)
    heuristics_doc = _load_json(heuristics_path)
    appetite_path = default_appetite_index_path(og_root)
    appetite_doc = _load_json(appetite_path)
    disposition_path = default_disposition_index_path(og_root)
    disposition_doc = _load_json(disposition_path)
    triage_path = default_triage_index_path(og_root)
    triage_doc = _load_json(triage_path)
    tags_path = og_root.parent / "_status" / "asset_tags.json"
    tags_doc = _load_json(tags_path)
    lineage = LineageGraph.load(lineage_path)
    vision_table = _load_vision_scores(vision_path)
    rated_keys = _rated_output_keys(ratings_doc)
    rated_by_gid = _build_rated_by_gid(ratings_doc)

    presented: Set[str] = set()
    if exclude_presented:
        state = _load_json(state_path) or {}
        for gid in state.get("presented_group_ids") or []:
            if isinstance(gid, str):
                presented.add(gid)

    pool_query = query_s if selection_mode == "search" else None
    needs_rating = collect_unrated_video_items(
        discovery_doc,
        rated_keys=rated_keys,
        ratings_doc=ratings_doc,
        og_root=og_root,
        disposition_doc=disposition_doc,
        triage_doc=triage_doc,
        appetite_doc=appetite_doc,
        include_done=include_done,
        query=pool_query,
    )
    candidates: List[RatingCandidate] = []
    for item in needs_rating:
        gid = str(item.get("group_id") or "")
        if exclude_presented and gid and gid in presented:
            continue
        cand = score_unrated_candidate(
            item,
            lineage=lineage,
            heuristics_doc=heuristics_doc,
            ratings_doc=ratings_doc,
            rated_by_gid=rated_by_gid,
            vision_scores=vision_table,
            appetite_doc=appetite_doc,
            disposition_doc=disposition_doc,
            triage_doc=triage_doc,
            tags_doc=tags_doc,
        )
        if cand.predicted_score >= effective_min:
            candidates.append(cand)

    session_mix = _session_mix_from_env()
    picked = _pick_by_mode(
        candidates,
        mode=selection_mode,
        limit=limit,
        seed=seed,
        mix=session_mix,
        query=query_s,
    )

    bucket_counts = defaultdict(int)
    for c in picked:
        bucket_counts[c.session_bucket] += 1

    vision_queue = [c for c in picked if c.vision_recommended][: max(3, limit // 15)]

    next_steps = [
        "Work the interleaved queue: set Subject / Render / Action stars and appetite on each clip.",
        "Disposition is optional here — it routes later work, it does not finish rating.",
        "Dismiss batch commits clips that have all three quality axes and appetite; the rest return to the pool.",
        "python3 shape_factory.py ratings build",
        "python3 shape_factory.py heuristics build",
    ]
    if selection_mode == "search" and not query_s:
        next_steps.insert(0, "Enter a search query to populate the Search queue.")

    session = {
        "version": SAMPLER_SCHEMA_VERSION,
        "created_at": utc_now(),
        "ok": True,
        "og_root": str(og_root),
        "selection_mode": selection_mode,
        "include_done": bool(include_done),
        "query": query_s,
        "session_mix": session_mix if selection_mode == "mixed" else None,
        "request": {
            "limit": int(limit),
            "mode": selection_mode,
            "query": query_s,
            "include_done": bool(include_done),
            "min_predicted": effective_min,
            "seed": int(seed),
        },
        "stats": {
            "needs_rating_videos": len(needs_rating),
            "needs_triage_videos": len(needs_rating),
            "unrated_videos": len(needs_rating),
            "scored_pool": len(candidates),
            "selected": len(picked),
            "bucket_easy_down": bucket_counts.get("easy_down", 0),
            "bucket_easy_up": bucket_counts.get("easy_up", 0),
            "bucket_middle": bucket_counts.get("middle", 0),
            "vision_recommended": sum(1 for c in picked if c.vision_recommended),
            "vision_priority_shortlist": len(vision_queue),
        },
        "inputs": {
            "discovery_index": str(discovery_path),
            "ratings_index": str(ratings_path) if ratings_path.is_file() else None,
            "heuristics_index": str(heuristics_path) if heuristics_path.is_file() else None,
            "lineage_edges": str(lineage_path) if lineage_path.is_file() else None,
            "vision_scores": str(vision_path) if vision_path.is_file() else None,
            "appetite_index": str(appetite_path) if appetite_path.is_file() else None,
            "disposition_index": str(disposition_path) if disposition_path.is_file() else None,
            "triage_index": str(triage_path) if triage_path.is_file() else None,
            "asset_tags": str(tags_path) if tags_path.is_file() else None,
        },
        "candidates": [c.to_dict() for c in picked],
        "vision_priority": [c.to_dict() for c in vision_queue],
        "next_steps": next_steps,
    }
    return session


def persist_rating_session(
    session: dict[str, Any],
    *,
    sessions_dir: Optional[Path] = None,
    og_root: Optional[Path] = None,
    update_state: bool = True,
) -> Path:
    og_root = (og_root or Path(session.get("og_root") or "/home/yuji/comfyui-runpod-data/output/og")).resolve()
    out_dir = (sessions_dir or default_sampler_sessions_dir(og_root)).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"session_{stamp}.json"
    out_path.write_text(json.dumps(session, indent=2), encoding="utf-8")

    if update_state and session.get("ok"):
        state_path = default_sampler_state_path(og_root)
        state = _load_json(state_path) or {"presented_group_ids": [], "sessions": []}
        gids = state.get("presented_group_ids")
        if not isinstance(gids, list):
            gids = []
        for cand in session.get("candidates") or []:
            if isinstance(cand, dict):
                gid = str(cand.get("group_id") or "")
                if gid and gid not in gids:
                    gids.append(gid)
        sessions = state.get("sessions")
        if not isinstance(sessions, list):
            sessions = []
        sessions.append({"path": str(out_path), "created_at": session.get("created_at"), "n": len(session.get("candidates") or [])})
        state["presented_group_ids"] = gids[-5000:]
        state["sessions"] = sessions[-100:]
        state["updated_at"] = utc_now()
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return out_path


def analyze_vision_gaps(session: dict[str, Any]) -> dict[str, Any]:
    """Summarize where ComfyUI vision (Florence, etc.) would help most."""
    counts: Dict[str, int] = defaultdict(int)
    for cand in session.get("candidates") or []:
        if not isinstance(cand, dict):
            continue
        for reason in cand.get("vision_reasons") or []:
            counts[str(reason)] += 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    return {
        "ok": bool(session.get("ok")),
        "vision_recommended_total": sum(counts.values()),
        "reasons": [{"reason": r, "count": n} for r, n in ranked],
        "guidance": [
            "Florence/caption pass highest value on sibling_keeper_but_weak_graph_join (human would rate faster after auto-caption).",
            "unclassified_workflow_with_embed: run graph_fingerprint + CLIP/Florence batch on embed-bearing PNG companions.",
            "promising_pattern_needs_confirmation: cheap keeper classifier on thumb/frame before bothering you.",
        ],
    }


def cmd_rating_sampler_sample(args: argparse.Namespace) -> int:
    og_root = Path(args.root).expanduser().resolve()
    session = sample_rating_queue(
        og_root=og_root,
        limit=int(args.limit),
        exclude_presented=not args.include_presented,
        seed=int(args.seed),
        min_predicted=float(args.min_predicted),
        mode=str(getattr(args, "mode", "mixed") or "mixed"),
        query=str(getattr(args, "query", "") or ""),
        include_done=bool(getattr(args, "include_done", False)),
    )
    if not session.get("ok"):
        print(json.dumps(session, indent=2))
        return 1
    if args.out:
        out = Path(args.out).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(session, indent=2), encoding="utf-8")
        print(f"Wrote {out}")
    else:
        out_path = persist_rating_session(session, og_root=og_root, update_state=not args.no_state)
        print(f"Wrote {out_path}")
    stats = session.get("stats") or {}
    print(
        f"selected={stats.get('selected')} vision_recommended={stats.get('vision_recommended')} "
        f"unrated_pool={stats.get('unrated_videos')}"
    )
    gaps = analyze_vision_gaps(session)
    if gaps.get("reasons"):
        print("vision gaps:", json.dumps(gaps["reasons"][:5]))
    if args.print_candidates:
        for i, cand in enumerate(session.get("candidates") or [], 1):
            if not isinstance(cand, dict):
                continue
            print(
                f"{i:2}. score={cand.get('predicted_score')} "
                f"conf={cand.get('heuristic_confidence')} "
                f"{cand.get('relpath')} "
                f"vision={'Y' if cand.get('vision_recommended') else 'N'}"
            )
    return 0


def cmd_rating_sampler_gaps(args: argparse.Namespace) -> int:
    og_root = Path(args.root).expanduser().resolve()
    session_path = Path(args.session).expanduser().resolve() if args.session else None
    if session_path is None or not session_path.is_file():
        sessions_dir = default_sampler_sessions_dir(og_root)
        paths = sorted(sessions_dir.glob("session_*.json")) if sessions_dir.is_dir() else []
        if not paths:
            print("error: no sampler sessions found", file=__import__("sys").stderr)
            return 1
        session_path = paths[-1]
    session = json.loads(session_path.read_text(encoding="utf-8"))
    payload = analyze_vision_gaps(session)
    payload["session"] = str(session_path)
    print(json.dumps(payload, indent=2))
    return 0


def add_rating_sampler_subparser(sub: argparse._SubParsersAction) -> None:
    sampler = sub.add_parser(
        "rating-sampler",
        help="Sample unrated videos to rate next (heuristic keeper guesses)",
    )
    sampler_sub = sampler.add_subparsers(dest="rating_sampler_cmd", required=True)

    sample = sampler_sub.add_parser("sample", help="Build a rating queue session JSON")
    sample.add_argument("--root", default="/home/yuji/comfyui-runpod-data/output/og")
    sample.add_argument("--limit", type=int, default=100)
    sample.add_argument("--min-predicted", type=float, default=0.0, help="Min score to enter pool (0 = all unrated)")
    sample.add_argument("--seed", type=int, default=0)
    sample.add_argument(
        "--mode",
        default="mixed",
        choices=sorted(SELECTION_MODES),
        help="Selection behavior: mixed (stratified), random, search, latest",
    )
    sample.add_argument("--query", default="", help="Search query (used when --mode search)")
    sample.add_argument(
        "--include-done",
        action="store_true",
        help="Include previously rated or disposed/retired items in the pool",
    )
    sample.add_argument("--out", default=None, help="Write session here instead of _status/rating_sampler_sessions/")
    sample.add_argument("--include-presented", action="store_true", help="Allow repeats from prior sessions")
    sample.add_argument("--no-state", action="store_true", help="Do not update rating_sampler_state.json")
    sample.add_argument("--print-candidates", action="store_true")
    sample.set_defaults(func=cmd_rating_sampler_sample)

    gaps = sampler_sub.add_parser("gaps", help="Analyze where vision/LLM judgment helps most")
    gaps.add_argument("--root", default="/home/yuji/comfyui-runpod-data/output/og")
    gaps.add_argument("--session", default=None, help="Session JSON (default: latest)")
    gaps.set_defaults(func=cmd_rating_sampler_gaps)
