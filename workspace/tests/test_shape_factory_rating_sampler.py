#!/usr/bin/env python3
"""Tests for shape_factory_rating_sampler."""

from __future__ import annotations

import unittest

from shape_factory_heuristics import LineageGraph
from shape_factory_rating_sampler import (
    _sibling_keeper_boost,
    analyze_vision_gaps,
    is_rating_complete,
    needs_rating_item,
    score_unrated_candidate,
)


class RatingSamplerTests(unittest.TestCase):
    def test_sibling_keeper_boost(self) -> None:
        edges = [
            {"child_group_id": "og:stem:rated_child", "parent_group_id": "og:stem:shared_parent"},
            {"child_group_id": "og:stem:unrated_target", "parent_group_id": "og:stem:shared_parent"},
        ]
        graph = LineageGraph.from_edges(edges)
        rated = {"og:stem:rated_child": 5}
        boost, ev = _sibling_keeper_boost("og:stem:unrated_target", graph, rated)
        self.assertGreaterEqual(boost, 4.0)
        self.assertTrue(any("rated_sibling" in e for e in ev))

    def test_stratified_session_mix(self) -> None:
        from shape_factory_rating_sampler import RatingCandidate, _stratified_session_pick

        pool = [
            RatingCandidate(relpath=f"o/{i}.mp4", group_id=f"g{i}", predicted_score=float(i) / 10, heuristic_confidence=0.5)
            for i in range(100)
        ]
        picked = _stratified_session_pick(pool, limit=20, seed=1)
        self.assertEqual(len(picked), 20)
        buckets = {c.session_bucket for c in picked}
        self.assertIn("easy_down", buckets)
        self.assertIn("middle", buckets)
        downs = sum(1 for c in picked if c.session_bucket == "easy_down")
        ups = sum(1 for c in picked if c.session_bucket == "easy_up")
        self.assertGreaterEqual(downs, 6)
        self.assertGreaterEqual(ups, 1)

    def test_score_flags_vision_when_graph_weak_but_sibling_strong(self) -> None:
        edges = [
            {"child_group_id": "og:stem:keeper", "parent_group_id": "og:stem:parent_clip"},
            {"child_group_id": "og:stem:target", "parent_group_id": "og:stem:parent_clip"},
        ]
        graph = LineageGraph.from_edges(edges)
        item = {
            "relpath": "output/og/2026-04-03/target.mp4",
            "group_id": "og:stem:target",
            "has_embedded_prompt": True,
            "workflow_fingerprint": None,
        }
        cand = score_unrated_candidate(
            item,
            lineage=graph,
            heuristics_doc={"by_pattern": {}, "by_group_lineage": {}},
            ratings_doc=None,
            rated_by_gid={"og:stem:keeper": 5},
        )
        self.assertGreater(cand.predicted_score, 2.0)
        self.assertTrue(cand.vision_recommended)

    def test_analyze_vision_gaps(self) -> None:
        session = {
            "ok": True,
            "candidates": [
                {"vision_reasons": ["unclassified_workflow_with_embed"]},
                {"vision_reasons": ["unclassified_workflow_with_embed", "promising_pattern_needs_confirmation"]},
            ],
        }
        gaps = analyze_vision_gaps(session)
        self.assertEqual(gaps["vision_recommended_total"], 3)
        self.assertGreaterEqual(len(gaps["reasons"]), 1)

    def test_needs_rating_requires_quality_and_appetite(self) -> None:
        item = {"relpath": "output/og/2026-04-03/foo.mp4"}
        self.assertTrue(needs_rating_item(item, ratings_doc={}, appetite_doc={}))
        # Legacy lone explicit is NOT complete — axes required.
        ratings_legacy = {
            "by_output_relpath": {
                "output/og/2026-04-03/foo.mp4": {"explicit": 4},
            }
        }
        self.assertTrue(needs_rating_item(item, ratings_doc=ratings_legacy, appetite_doc={}))
        ratings = {
            "by_output_relpath": {
                "output/og/2026-04-03/foo.mp4": {
                    "explicit": 4,
                    "axes": {
                        "subject_beauty": 5,
                        "render_quality": 3,
                        "action_quality": 4,
                    },
                },
            }
        }
        self.assertTrue(needs_rating_item(item, ratings_doc=ratings, appetite_doc={}))
        appetite = {
            "by_output_relpath": {
                "output/og/2026-04-03/foo.mp4": {"appetite": "more"},
            }
        }
        self.assertFalse(needs_rating_item(item, ratings_doc=ratings, appetite_doc=appetite))
        self.assertTrue(is_rating_complete(item, ratings_doc=ratings, appetite_doc=appetite))

    def test_retired_never_needs_rating(self) -> None:
        item = {"relpath": "output/og/2026-04-03/foo.mp4"}
        disposition = {
            "by_output_relpath": {
                "output/og/2026-04-03/foo.mp4": {"markers": ["retire"]},
            }
        }
        self.assertFalse(needs_rating_item(item, ratings_doc={}, appetite_doc={}, disposition_doc=disposition))

    def test_pick_modes_random_latest_search(self) -> None:
        from shape_factory_rating_sampler import RatingCandidate, _pick_by_mode

        pool = [
            RatingCandidate(
                relpath=f"o/{i}.mp4",
                group_id=f"g{i}",
                predicted_score=float(i),
                heuristic_confidence=0.5,
                mtime=float(i),
            )
            for i in range(10)
        ]
        rnd = _pick_by_mode(pool, mode="random", limit=4, seed=7)
        self.assertEqual(len(rnd), 4)
        latest = _pick_by_mode(pool, mode="latest", limit=3, seed=0)
        self.assertEqual([c.group_id for c in latest], ["g9", "g8", "g7"])
        empty_search = _pick_by_mode(pool, mode="search", limit=3, query="")
        self.assertEqual(empty_search, [])
        search = _pick_by_mode(pool, mode="search", limit=2, query="x")
        self.assertEqual([c.group_id for c in search], ["g9", "g8"])

    def test_item_matches_query_tokens(self) -> None:
        from shape_factory_rating_sampler import _item_matches_query

        item = {"relpath": "output/og/2026-04-03/FB8VA5L_clip.mp4", "group_id": "og:stem:FB8VA5L"}
        self.assertTrue(_item_matches_query(item, "FB8VA5L clip"))
        self.assertFalse(_item_matches_query(item, "missingtoken"))

    def test_normalize_selection_mode_aliases(self) -> None:
        from shape_factory_rating_sampler import normalize_selection_mode

        self.assertEqual(normalize_selection_mode("heuristic"), "mixed")
        self.assertEqual(normalize_selection_mode("RANDOM"), "random")
        self.assertEqual(normalize_selection_mode("nope"), "mixed")


if __name__ == "__main__":
    unittest.main()
