#!/usr/bin/env python3
"""
Graph-aware heuristic scores for shape-factory selection.

Builds on ratings_index.json + discovery_lineage_edges.json to bias:
  - workflow + prompt patterns (shape_recipe / graph_hash + prompt profile)
  - assets that are ancestors of highly rated outputs (lineage uplift)

The index is derived and rebuildable; LLM judgment can plug in later as another
signal layer with the same score_recipe() interface.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from shape_factory_ratings import (
    APPETITE_SCORE,
    AggBucket,
    build_job_output_index,
    default_appetite_index_path,
    default_ratings_index_path,
    is_omit_quality_rating,
    is_usable_quality_rating,
    utc_now,
    _lookup_job_meta,
    _norm_path_key,
)

HEURISTICS_SCHEMA_VERSION = 1

# Provenance quality for lineage edge propagation (see docs/RATINGS_V1_PLAN.md).
EDGE_EVIDENCE_WEIGHT: Dict[str, float] = {
    "queue": 1.0,
    "shape_factory_deposit": 1.0,
    "png_prompt_source_path": 0.9,
    "discovery_lineage_persisted": 0.85,
    "basename_heuristic": 0.5,
    "inferred": 0.5,
}


def default_lineage_edges_path(og_root: Path) -> Path:
    return og_root.resolve().parent / "_status" / "discovery_lineage_edges.json"


def default_heuristics_index_path(og_root: Path) -> Path:
    return og_root.resolve().parent / "_status" / "heuristics_index.json"


def _og_group_id_from_relpath(relpath: str, *, library: str = "og") -> Optional[str]:
    raw = str(relpath or "").strip().replace("\\", "/")
    if not raw:
        return None
    name = Path(raw).name
    if not name:
        return None
    stem = Path(name).stem.lower()
    if not stem:
        return None
    lib = library.strip().lower() or "og"
    return f"{lib}:stem:{stem}"


def _input_group_id_from_basename(basename: str) -> Optional[str]:
    bn = Path(str(basename or "").strip()).name
    if not bn:
        return None
    return f"input:{bn}"


def _source_group_ids(basename: str) -> List[str]:
    """Candidate lineage group ids for a source pick basename."""
    bn = Path(str(basename or "").strip()).name
    if not bn:
        return []
    gids: List[str] = []
    seen: Set[str] = set()

    def add(g: Optional[str]) -> None:
        if g and g not in seen:
            seen.add(g)
            gids.append(g)

    add(_input_group_id_from_basename(bn))
    add(_og_group_id_from_relpath(bn))
    stem = Path(bn).stem.lower()
    add(f"og:stem:{stem}")
    add(f"input:{stem}")
    return gids


def _output_group_ids(output_path: str, output_row: Optional[dict[str, Any]] = None) -> List[str]:
    gids: List[str] = []
    seen: Set[str] = set()

    def add(g: Optional[str]) -> None:
        if g and g not in seen:
            seen.add(g)
            gids.append(g)

    if output_row:
        short = str(output_row.get("short_key") or "").strip()
        if short:
            add(_og_group_id_from_relpath(short))
            if short.startswith("og/"):
                add(_og_group_id_from_relpath(short, library="og"))
    raw = str(output_path or "").strip().replace("\\", "/")
    if raw:
        add(_og_group_id_from_relpath(raw))
        if "/og/" in raw:
            tail = raw.split("/og/", 1)[-1]
            add(_og_group_id_from_relpath(f"og/{tail}"))
    return gids


def _edge_weight(edge: dict[str, Any]) -> float:
    evidence = str(edge.get("evidence") or "inferred").strip().lower()
    for key, weight in EDGE_EVIDENCE_WEIGHT.items():
        if key in evidence:
            return weight
    return EDGE_EVIDENCE_WEIGHT.get(evidence, 0.75)


@dataclass
class LineageGraph:
    edges: List[dict[str, Any]] = field(default_factory=list)
    parents_of: Dict[str, List[str]] = field(default_factory=dict)
    children_of: Dict[str, List[str]] = field(default_factory=dict)
    edge_weight_by_pair: Dict[Tuple[str, str], float] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "LineageGraph":
        doc: dict[str, Any] = {}
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    doc = loaded
            except (OSError, json.JSONDecodeError):
                pass
        edges = doc.get("edges")
        if not isinstance(edges, list):
            edges = []
        return cls.from_edges(edges)

    @classmethod
    def from_edges(cls, edges: Iterable[Any]) -> "LineageGraph":
        g = cls()
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            parent = str(edge.get("parent_group_id") or "").strip()
            child = str(edge.get("child_group_id") or "").strip()
            if not parent or not child:
                continue
            g.edges.append(edge)
            g.parents_of.setdefault(child, []).append(parent)
            g.children_of.setdefault(parent, []).append(child)
            w = _edge_weight(edge)
            pair = (child, parent)
            g.edge_weight_by_pair[pair] = max(g.edge_weight_by_pair.get(pair, 0.0), w)
        return g

    def ancestors(self, gid: str, *, max_depth: int = 8) -> List[Tuple[str, int]]:
        """Return (ancestor_group_id, hop_distance) BFS upstream."""
        if not gid:
            return []
        out: List[Tuple[str, int]] = []
        seen: Set[str] = {gid}
        frontier: List[Tuple[str, int]] = [(gid, 0)]
        while frontier:
            cur, depth = frontier.pop(0)
            if depth >= max_depth:
                continue
            for parent in self.parents_of.get(cur, []):
                if parent in seen:
                    continue
                seen.add(parent)
                hop = depth + 1
                out.append((parent, hop))
                frontier.append((parent, hop))
        return out

    def descendants(self, gid: str, *, max_depth: int = 8) -> List[Tuple[str, int]]:
        if not gid:
            return []
        out: List[Tuple[str, int]] = []
        seen: Set[str] = {gid}
        frontier: List[Tuple[str, int]] = [(gid, 0)]
        while frontier:
            cur, depth = frontier.pop(0)
            if depth >= max_depth:
                continue
            for child in self.children_of.get(cur, []):
                if child in seen:
                    continue
                seen.add(child)
                hop = depth + 1
                out.append((child, hop))
                frontier.append((child, hop))
        return out


def _shape_recipe_key(family: str, prompt_profile: str) -> Optional[str]:
    fam = str(family or "").strip()
    profile = Path(str(prompt_profile or "").strip()).stem
    if not fam or not profile:
        return None
    return f"{fam}+{profile}"


@dataclass
class _FloatAgg:
    values: List[float] = field(default_factory=list)

    def add(self, value: float) -> None:
        if value > 0:
            self.values.append(float(value))

    def to_row(self, *, extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        if not self.values:
            return {}
        out: dict[str, Any] = {
            "inferred": round(statistics.mean(self.values), 2),
            "n": len(self.values),
            "keepers_4plus": sum(1 for v in self.values if v >= 4.0),
        }
        if extra:
            out.update(extra)
        return out


def _rating_to_weight(rating: float, *, explore_floor: float = 0.35) -> float:
    # Ratings are nominally 1–5. Zeros (and other sub-1 values) appear in the index as
    # placeholders; clamp to [1, 5] before the power curve so we never do
    # (-x)**1.6 → complex, which breaks max() comparisons in score_recipe.
    normalized = max(1.0, min(5.0, float(rating)))
    return max(explore_floor, ((normalized - 1.0) / 4.0) ** 1.6 * 4.0 + 0.15)


def build_heuristics_index(
    *,
    ratings_doc: dict[str, Any],
    lineage_graph: LineageGraph,
    jobs_root: Optional[Path] = None,
    appetite_doc: Optional[dict[str, Any]] = None,
    tags_doc: Optional[dict[str, Any]] = None,
    data_root: Optional[Path] = None,
    out_path: Optional[Path] = None,
    ancestor_decay: float = 0.85,
    max_lineage_hops: int = 6,
) -> dict[str, Any]:
    """
    Derive graph heuristics from ratings + lineage (+ appetite + tags).

    Quality ("do more OF") rollups:
      by_pattern: workflow+prompt aggregates (shape_recipe when known, else graph_hash).
      by_group_lineage: ancestor credit from rated descendants walking the lineage graph.

    Appetite ("do more WITH") rollups, facet-routed:
      by_pattern_appetite: fed by outputs with facet processing/both.
      by_group_lineage_appetite: fed by outputs with facet source/both.
      by_tag_appetite: appetite-weighted tag affinity (Slice 6, when tags_doc present).
    """
    by_output = ratings_doc.get("by_output_relpath") or {}
    if not isinstance(by_output, dict):
        by_output = {}

    pattern_buckets: Dict[str, AggBucket] = defaultdict(AggBucket)
    pattern_meta: Dict[str, dict[str, Any]] = {}
    lineage_buckets: Dict[str, _FloatAgg] = defaultdict(_FloatAgg)
    lineage_meta: Dict[str, dict[str, Any]] = defaultdict(
        lambda: {"descendant_rated_outputs": 0, "max_descendant_explicit": None}
    )

    seen_outputs: Set[str] = set()
    for key, row in by_output.items():
        if not isinstance(row, dict):
            continue
        # Unrated (None) and omit (explicit <= 0) never train pattern/lineage buckets.
        if not is_usable_quality_rating(row.get("explicit")):
            continue
        dedupe = str(row.get("short_key") or key)
        if dedupe in seen_outputs:
            continue
        seen_outputs.add(dedupe)

        rating = int(row["explicit"])
        gh = str(row.get("graph_hash") or "").strip()
        recipe = str(row.get("shape_recipe") or "").strip()

        if recipe:
            pattern_buckets[recipe].add(rating)
            pattern_meta.setdefault(recipe, {"kind": "shape_recipe", "shape_recipe": recipe})
        elif gh:
            pattern_key = f"graph:{gh[:16]}"
            pattern_buckets[pattern_key].add(rating)
            pattern_meta.setdefault(pattern_key, {"kind": "graph_hash", "graph_hash": gh})

        seed_gids = _output_group_ids(str(key), row)
        if not seed_gids and row.get("short_key"):
            seed_gids = _output_group_ids(str(row["short_key"]), row)
        for seed_gid in seed_gids:
            for ancestor_gid, hop in lineage_graph.ancestors(seed_gid, max_depth=max_lineage_hops):
                pair = (seed_gid, ancestor_gid)
                edge_w = lineage_graph.edge_weight_by_pair.get(pair, 0.85)
                credit = float(rating) * (ancestor_decay ** hop) * edge_w
                if credit <= 0:
                    continue
                lineage_buckets[ancestor_gid].add(credit)
                meta = lineage_meta[ancestor_gid]
                meta["descendant_rated_outputs"] = int(meta.get("descendant_rated_outputs") or 0) + 1
                prev_max = meta.get("max_descendant_explicit")
                if prev_max is None or rating > int(prev_max):
                    meta["max_descendant_explicit"] = rating

    # Enrich patterns from factory jobs (even when outputs are not yet rated).
    if jobs_root and jobs_root.is_dir():
        for job_path in sorted(jobs_root.rglob("*.job.json")):
            try:
                job = json.loads(job_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(job, dict):
                continue
            family = str(job.get("family_slug") or "").strip()
            bindings = job.get("bindings") if isinstance(job.get("bindings"), dict) else {}
            prompt_raw = bindings.get("prompt_profile") or bindings.get("gex2_prompt")
            recipe = _shape_recipe_key(family, str(prompt_raw or ""))
            gh = str(job.get("graph_hash") or "").strip()
            if recipe and gh:
                pattern_meta.setdefault(
                    recipe,
                    {"kind": "shape_recipe", "shape_recipe": recipe, "graph_hash": gh},
                )

    # --- Appetite ("do more WITH") rollups, facet-routed ---
    pattern_appetite: Dict[str, _FloatAgg] = defaultdict(_FloatAgg)
    lineage_appetite: Dict[str, _FloatAgg] = defaultdict(_FloatAgg)
    tag_appetite: Dict[str, _FloatAgg] = defaultdict(_FloatAgg)
    appetite_outputs_used = 0

    app_by_output = (appetite_doc or {}).get("by_output_relpath") or {}
    if isinstance(app_by_output, dict) and app_by_output:
        # Resolve appetite outputs -> pattern via the job output index (appetite-marked
        # outputs are frequently unrated, so ratings_doc alone will not have them).
        job_index: Dict[str, Any] = {}
        if jobs_root and data_root:
            try:
                job_index = build_job_output_index(Path(jobs_root), Path(data_root))
            except Exception:
                job_index = {}

        def _pattern_for_appetite(key: str, row: dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
            # Prefer a rated row (carries graph_hash/shape_recipe), else the job index.
            for k in (key, str(row.get("short_key") or "")):
                r = by_output.get(k) if isinstance(by_output, dict) else None
                if isinstance(r, dict):
                    return (str(r.get("shape_recipe") or "") or None, str(r.get("graph_hash") or "") or None)
            if job_index and data_root:
                lk: List[str] = []
                for k in (key, str(row.get("short_key") or "")):
                    if k:
                        lk.extend(_norm_path_key(k, Path(data_root)))
                meta = _lookup_job_meta(lk, job_index) or {}
                return (meta.get("shape_recipe"), meta.get("graph_hash"))
            return (None, None)

        seen_appetite: Set[str] = set()
        tags_by = (tags_doc or {}).get("by_group_id") or {}
        for key, row in app_by_output.items():
            if not isinstance(row, dict):
                continue
            appetite = str(row.get("appetite") or "").strip()
            if not appetite:
                continue
            dedupe = str(row.get("short_key") or key)
            if dedupe in seen_appetite:
                continue
            seen_appetite.add(dedupe)
            score = float(row.get("score") or APPETITE_SCORE.get(appetite, 0.0))
            if score <= 0:
                continue
            appetite_outputs_used += 1
            facet = str(row.get("facet") or "both").strip().lower() or "both"

            if facet in ("processing", "both"):
                recipe, gh = _pattern_for_appetite(str(key), row)
                if recipe:
                    pattern_appetite[recipe].add(score)
                elif gh:
                    pattern_appetite[f"graph:{gh[:16]}"].add(score)

            if facet in ("source", "both"):
                seed_gids = _output_group_ids(str(key), row)
                if not seed_gids and row.get("short_key"):
                    seed_gids = _output_group_ids(str(row["short_key"]), row)
                for seed_gid in seed_gids:
                    for ancestor_gid, hop in lineage_graph.ancestors(seed_gid, max_depth=max_lineage_hops):
                        edge_w = lineage_graph.edge_weight_by_pair.get((seed_gid, ancestor_gid), 0.85)
                        credit = score * (ancestor_decay ** hop) * edge_w
                        if credit > 0:
                            lineage_appetite[ancestor_gid].add(credit)

            # Tag affinity (Slice 6): credit appetite to the output's tags.
            if isinstance(tags_by, dict) and tags_by:
                gid = str(row.get("group_id") or _og_group_id_from_relpath(str(row.get("short_key") or key)) or "")
                tag_row = tags_by.get(gid) if gid else None
                if isinstance(tag_row, dict):
                    for tag in tag_row.get("tags") or []:
                        t = str(tag).strip().lower()
                        if t:
                            tag_appetite[t].add(score)

    doc: dict[str, Any] = {
        "version": HEURISTICS_SCHEMA_VERSION,
        "updated_at": utc_now(),
        "stats": {
            "rated_outputs_used": len(seen_outputs),
            "lineage_edges": len(lineage_graph.edges),
            "patterns": 0,
            "lineage_groups": 0,
            "appetite_outputs_used": appetite_outputs_used,
            "pattern_appetite": 0,
            "lineage_appetite": 0,
            "tag_appetite": 0,
        },
        "by_pattern": {},
        "by_group_lineage": {},
        "by_pattern_appetite": {},
        "by_group_lineage_appetite": {},
        "by_tag_appetite": {},
    }

    for pattern_key, bucket in sorted(
        pattern_buckets.items(), key=lambda kv: (-statistics.mean(kv[1].ratings), -len(kv[1].ratings))
    ):
        row = bucket.to_inferred(extra=pattern_meta.get(pattern_key))
        doc["by_pattern"][pattern_key] = row

    for gid, bucket in sorted(
        lineage_buckets.items(),
        key=lambda kv: (-statistics.mean(kv[1].values), -len(kv[1].values)),
    ):
        extra = dict(lineage_meta.get(gid) or {})
        extra["group_id"] = gid
        row = bucket.to_row(extra=extra)
        if row:
            doc["by_group_lineage"][gid] = row

    for pattern_key, bucket in sorted(
        pattern_appetite.items(), key=lambda kv: (-statistics.mean(kv[1].values), -len(kv[1].values))
    ):
        extra = dict(pattern_meta.get(pattern_key) or {})
        row = bucket.to_row(extra=extra or None)
        if row:
            doc["by_pattern_appetite"][pattern_key] = row

    for gid, bucket in sorted(
        lineage_appetite.items(), key=lambda kv: (-statistics.mean(kv[1].values), -len(kv[1].values))
    ):
        row = bucket.to_row(extra={"group_id": gid})
        if row:
            doc["by_group_lineage_appetite"][gid] = row

    for tag, bucket in sorted(
        tag_appetite.items(), key=lambda kv: (-statistics.mean(kv[1].values), -len(kv[1].values))
    ):
        row = bucket.to_row(extra={"tag": tag})
        if row:
            doc["by_tag_appetite"][tag] = row

    doc["stats"]["patterns"] = len(doc["by_pattern"])
    doc["stats"]["lineage_groups"] = len(doc["by_group_lineage"])
    doc["stats"]["pattern_appetite"] = len(doc["by_pattern_appetite"])
    doc["stats"]["lineage_appetite"] = len(doc["by_group_lineage_appetite"])
    doc["stats"]["tag_appetite"] = len(doc["by_tag_appetite"])

    if out_path:
        out_path = out_path.expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return doc


def _lookup_table_row(table: dict[str, Any], key: str) -> Optional[dict[str, Any]]:
    if not key:
        return None
    row = table.get(key)
    return row if isinstance(row, dict) else None


def _appetite_light_mult(value: float) -> float:
    """Gentle appetite nudge for the Replay pass (appetite dominates the Derive pass, not here)."""
    return max(0.5, min(1.8, 1.0 + (float(value) - 2.5) * 0.28))


def score_recipe(
    recipe: dict[str, Any],
    *,
    shape: dict[str, Any],
    ratings_doc: Optional[dict[str, Any]] = None,
    heuristics_doc: Optional[dict[str, Any]] = None,
    appetite_doc: Optional[dict[str, Any]] = None,
    explore_floor: Optional[float] = None,
) -> Tuple[float, dict[str, Any]]:
    """
    Composite selection score for a replay/generate candidate (quality-first).

    Quality signals (first strong match wins for rating_effective, weights combine):
      output_explicit → lineage_ancestor → pattern → source_inferred → graph_inferred
    Appetite ("do more WITH") is applied as a light multiplier on top; the Derive pass
    (plan_hourly_derive) is where appetite dominates.
    """
    import os

    floor = float(explore_floor if explore_floor is not None else os.environ.get("HOURLY_RATING_EXPLORE_FLOOR", "0.35"))
    meta: dict[str, Any] = {"rating_effective": None, "evidence": [], "signals": {}}

    if not ratings_doc and not heuristics_doc:
        return floor, meta

    rating_value: Optional[float] = None
    best_weight = floor

    def consider(value: Optional[float], evidence: str, *, signal: str) -> None:
        nonlocal rating_value, best_weight
        if not is_usable_quality_rating(value):
            return
        numeric = float(value)
        meta["evidence"].append(evidence)
        meta["signals"][signal] = numeric
        w = _rating_to_weight(numeric, explore_floor=floor)
        if rating_value is None or w > best_weight:
            rating_value = numeric
            best_weight = w

    # Explicit output rating (ratings index).
    # ``explicit: 0`` = omit from consideration (hard exclude), not explore-floor.
    if ratings_doc:
        from shape_factory_ratings import lookup_output_rating

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
            consider(float(out_row["explicit"]), "output_explicit", signal="output_explicit")
            meta["explicit"] = int(out_row["explicit"])

    picks = recipe.get("picks") if isinstance(recipe.get("picks"), dict) else {}
    family = str(recipe.get("family") or shape.get("family") or shape.get("id") or "").strip()
    prompt_raw = picks.get("prompt_profile") or picks.get("gex2_prompt")
    recipe_key = _shape_recipe_key(family, str(prompt_raw or ""))

    # Lineage ancestor credit (feeds highly rated descendants).
    if heuristics_doc:
        by_lineage = heuristics_doc.get("by_group_lineage") or {}
        lineage_candidates: List[str] = []
        for slot in ("source_video", "source_still", "source_video_ref"):
            raw = picks.get(slot)
            if raw:
                lineage_candidates.extend(_source_group_ids(Path(str(raw)).name))
        if ratings_doc:
            out_row = lookup_output_rating(str(recipe.get("output_path") or ""), ratings_doc)
        else:
            out_row = None
        lineage_candidates.extend(_output_group_ids(str(recipe.get("output_path") or ""), out_row))

        best_lineage: Optional[float] = None
        best_gid: Optional[str] = None
        for gid in lineage_candidates:
            row = _lookup_table_row(by_lineage, gid) if isinstance(by_lineage, dict) else None
            if row and row.get("inferred") is not None:
                val = float(row["inferred"])
                if best_lineage is None or val > best_lineage:
                    best_lineage = val
                    best_gid = gid
        if best_lineage is not None:
            consider(best_lineage, f"lineage_ancestor:{best_gid}", signal="lineage_ancestor")
            meta["lineage_group_id"] = best_gid

        # Workflow + prompt pattern.
        by_pattern = heuristics_doc.get("by_pattern") or {}
        pattern_row = None
        if recipe_key and isinstance(by_pattern, dict):
            pattern_row = _lookup_table_row(by_pattern, recipe_key)
        if pattern_row is None and isinstance(by_pattern, dict):
            gh = str(shape.get("graph_hash") or "")
            if gh:
                pattern_row = _lookup_table_row(by_pattern, f"graph:{gh[:16]}")
        if pattern_row and pattern_row.get("inferred") is not None:
            consider(float(pattern_row["inferred"]), f"pattern:{recipe_key or 'graph'}", signal="pattern")

    # Source basename aggregate.
    if ratings_doc:
        try:
            from correlate_output_ratings import normalize_source_basename
        except ImportError:
            normalize_source_basename = lambda s: Path(str(s)).name  # type: ignore

        by_source = ratings_doc.get("by_source_basename") or {}
        for slot in ("source_video", "source_still", "source_video_ref"):
            raw = picks.get(slot)
            if not raw:
                continue
            bn = normalize_source_basename(Path(str(raw)).name)
            row = by_source.get(bn) if isinstance(by_source, dict) else None
            if isinstance(row, dict) and row.get("inferred") is not None:
                consider(float(row["inferred"]), f"source_inferred:{bn}", signal="source_inferred")
                meta["source_inferred"] = float(row["inferred"])
                meta["source_n"] = row.get("n")
                break

    # Graph hash fallback.
    if ratings_doc and rating_value is None:
        gh = str(shape.get("graph_hash") or "")
        by_graph = ratings_doc.get("by_graph_hash") or {}
        row = by_graph.get(gh) if isinstance(by_graph, dict) else None
        if isinstance(row, dict) and row.get("inferred") is not None:
            consider(float(row["inferred"]), "graph_inferred", signal="graph_inferred")
            meta["graph_inferred"] = float(row["inferred"])
            meta["graph_n"] = row.get("n")

    # --- Appetite ("do more WITH") as a light multiplier ---
    appetite_value: Optional[float] = None
    appetite_state: Optional[str] = None
    appetite_facet: Optional[str] = None

    if appetite_doc:
        from shape_factory_ratings import lookup_output_appetite

        app_row = lookup_output_appetite(str(recipe.get("output_path") or ""), appetite_doc)
        if isinstance(app_row, dict) and app_row.get("appetite"):
            appetite_state = str(app_row.get("appetite"))
            appetite_facet = str(app_row.get("facet") or "both")
            appetite_value = float(app_row.get("score") or APPETITE_SCORE.get(appetite_state, 0.0))
            meta["appetite_evidence"] = f"output_appetite:{appetite_state}"

    if appetite_value is None and heuristics_doc:
        by_pat_app = heuristics_doc.get("by_pattern_appetite") or {}
        pat_row = None
        if recipe_key and isinstance(by_pat_app, dict):
            pat_row = _lookup_table_row(by_pat_app, recipe_key)
        if pat_row is None and isinstance(by_pat_app, dict):
            gh = str(shape.get("graph_hash") or "")
            if gh:
                pat_row = _lookup_table_row(by_pat_app, f"graph:{gh[:16]}")
        if pat_row and pat_row.get("inferred") is not None:
            appetite_value = float(pat_row["inferred"])
            meta["appetite_evidence"] = f"pattern_appetite:{recipe_key or 'graph'}"

    if appetite_value is None and heuristics_doc:
        by_lin_app = heuristics_doc.get("by_group_lineage_appetite") or {}
        if isinstance(by_lin_app, dict):
            lin_candidates: List[str] = []
            for slot in ("source_video", "source_still", "source_video_ref"):
                raw = picks.get(slot)
                if raw:
                    lin_candidates.extend(_source_group_ids(Path(str(raw)).name))
            lin_candidates.extend(_output_group_ids(str(recipe.get("output_path") or ""), None))
            best_lin_app: Optional[float] = None
            for gid in lin_candidates:
                row = _lookup_table_row(by_lin_app, gid)
                if row and row.get("inferred") is not None:
                    val = float(row["inferred"])
                    if best_lin_app is None or val > best_lin_app:
                        best_lin_app = val
            if best_lin_app is not None:
                appetite_value = best_lin_app
                meta["appetite_evidence"] = "lineage_appetite"

    if appetite_value is not None:
        meta["appetite"] = appetite_state
        meta["appetite_facet"] = appetite_facet
        meta["appetite_value"] = round(appetite_value, 3)
        if appetite_state == "fast_track":
            meta["fast_track"] = True
        best_weight = best_weight * _appetite_light_mult(appetite_value)

    if rating_value is None and appetite_value is None:
        return floor, meta

    meta["rating_effective"] = rating_value
    if meta.get("signals", {}).get("output_explicit") is not None:
        meta["rating_kind"] = "explicit"
    elif rating_value is not None:
        meta["rating_kind"] = "predicted"
    else:
        meta["rating_kind"] = "none"
    return best_weight, meta


def _default_asset_tags_path(og_root: Path) -> Path:
    return og_root.resolve().parent / "_status" / "asset_tags.json"


def _load_json_doc(path: Path) -> Optional[dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def cmd_heuristics_build(args: argparse.Namespace) -> int:
    og_root = Path(args.root).expanduser().resolve()
    ratings_path = Path(args.ratings_index or default_ratings_index_path(og_root)).expanduser().resolve()
    lineage_path = Path(args.lineage_edges or default_lineage_edges_path(og_root)).expanduser().resolve()
    jobs_root = Path(args.jobs_root).expanduser().resolve()
    appetite_path = Path(args.appetite_index or default_appetite_index_path(og_root)).expanduser().resolve()
    tags_path = Path(args.asset_tags or _default_asset_tags_path(og_root)).expanduser().resolve()
    data_root = Path(args.data_root).expanduser().resolve() if getattr(args, "data_root", None) else None
    out_path = Path(args.out or default_heuristics_index_path(og_root)).expanduser().resolve()

    from shape_factory_ratings import load_appetite_doc, load_ratings_doc, ratings_db_path_for_index

    ratings_db = ratings_db_path_for_index(ratings_path)
    if not ratings_path.is_file() and not ratings_db.is_file():
        print(f"error: ratings index not found: {ratings_path}", file=__import__("sys").stderr)
        return 1

    ratings_doc = load_ratings_doc(ratings_path)
    graph = LineageGraph.load(lineage_path)
    appetite_doc = (
        load_appetite_doc(appetite_path)
        if (appetite_path.is_file() or ratings_db_path_for_index(appetite_path).is_file())
        else _load_json_doc(appetite_path)
    )
    tags_doc = _load_json_doc(tags_path)
    doc = build_heuristics_index(
        ratings_doc=ratings_doc,
        lineage_graph=graph,
        jobs_root=jobs_root,
        appetite_doc=appetite_doc,
        tags_doc=tags_doc,
        data_root=data_root,
        out_path=out_path,
        ancestor_decay=float(args.ancestor_decay),
        max_lineage_hops=int(args.max_lineage_hops),
    )
    stats = doc.get("stats") or {}
    print(f"Wrote {out_path}")
    print(
        f"patterns={stats.get('patterns')} lineage_groups={stats.get('lineage_groups')} "
        f"rated_outputs_used={stats.get('rated_outputs_used')} lineage_edges={stats.get('lineage_edges')}"
    )
    print(
        f"appetite_outputs={stats.get('appetite_outputs_used')} "
        f"pattern_appetite={stats.get('pattern_appetite')} lineage_appetite={stats.get('lineage_appetite')} "
        f"tag_appetite={stats.get('tag_appetite')}"
    )
    return 0


def cmd_heuristics_show(args: argparse.Namespace) -> int:
    og_root = Path(args.root).expanduser().resolve()
    path = Path(args.index or default_heuristics_index_path(og_root)).expanduser().resolve()
    if not path.is_file():
        print(f"error: heuristics index not found: {path}", file=__import__("sys").stderr)
        return 1
    doc = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, Any] = {"ok": True, "path": str(path), "matches": {}}

    if args.pattern:
        table = doc.get("by_pattern") or {}
        if isinstance(table, dict) and args.pattern in table:
            out["matches"]["pattern"] = {args.pattern: table[args.pattern]}

    if args.group_id:
        table = doc.get("by_group_lineage") or {}
        if isinstance(table, dict) and args.group_id in table:
            out["matches"]["group_lineage"] = {args.group_id: table[args.group_id]}

    if not out["matches"]:
        out["ok"] = False
        out["error"] = "no matches"
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1


def add_heuristics_subparser(sub: argparse._SubParsersAction) -> None:
    heuristics = sub.add_parser(
        "heuristics",
        help="Build/query graph heuristics index (patterns + lineage uplift)",
    )
    heuristics_sub = heuristics.add_subparsers(dest="heuristics_cmd", required=True)

    build = heuristics_sub.add_parser("build", help="Build heuristics_index.json from ratings + lineage")
    build.add_argument("--root", default="/home/yuji/comfyui-runpod-data/output/og")
    build.add_argument("--ratings-index", default=None)
    build.add_argument("--lineage-edges", default=None)
    build.add_argument("--jobs-root", default="/home/yuji/src/comfyui-runpod/.data/shape_factory/jobs")
    build.add_argument("--appetite-index", default=None, help="appetite_index.json (default: <og>/../_status/)")
    build.add_argument("--asset-tags", default=None, help="asset_tags.json for tag appetite (default: <og>/../_status/)")
    build.add_argument("--data-root", default="/home/yuji/comfyui-runpod-data", help="For job output index join (appetite -> pattern)")
    build.add_argument("--out", default=None)
    build.add_argument("--ancestor-decay", type=float, default=0.85)
    build.add_argument("--max-lineage-hops", type=int, default=6)
    build.set_defaults(func=cmd_heuristics_build)

    show = heuristics_sub.add_parser("show", help="Look up pattern or lineage group scores")
    show.add_argument("--root", default="/home/yuji/comfyui-runpod-data/output/og")
    show.add_argument("--index", default=None)
    show.add_argument("--pattern", default=None, help="shape_recipe key e.g. FB9_GEX2+catalog-default")
    show.add_argument("--group-id", dest="group_id", default=None, help="lineage group_id e.g. og:stem:...")
    show.set_defaults(func=cmd_heuristics_show)
