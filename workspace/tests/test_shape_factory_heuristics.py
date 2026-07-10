#!/usr/bin/env python3
"""Tests for shape_factory_heuristics graph scoring."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class ShapeFactoryHeuristicsTests(unittest.TestCase):
    def test_lineage_ancestor_credit_propagates(self) -> None:
        from shape_factory_heuristics import LineageGraph, build_heuristics_index

        edges = [
            {
                "child_group_id": "og:stem:child_output",
                "parent_group_id": "og:stem:parent_source",
                "evidence": "png_prompt_source_path",
            }
        ]
        graph = LineageGraph.from_edges(edges)
        ratings_doc = {
            "by_output_relpath": {
                "og/stem/child_output": {
                    "explicit": 5,
                    "short_key": "og/stem/child_output",
                }
            }
        }
        doc = build_heuristics_index(ratings_doc=ratings_doc, lineage_graph=graph)
        parent = doc["by_group_lineage"].get("og:stem:parent_source")
        self.assertIsNotNone(parent)
        self.assertGreaterEqual(float(parent["inferred"]), 3.0)
        self.assertGreaterEqual(int(parent["descendant_rated_outputs"]), 1)

    def test_score_recipe_prefers_pattern_and_lineage(self) -> None:
        from shape_factory_heuristics import score_recipe

        shape = {"graph_hash": "abc" * 16, "family": "FB9_GEX2"}
        recipe = {
            "family": "FB9_GEX2",
            "picks": {
                "prompt_profile": "/tmp/catalog-default.json",
                "source_video": "/tmp/X-Kneel-FB9-2026-04-02-163059_OG_00001.mp4",
            },
            "output_path": "/tmp/unrated.mp4",
        }
        heuristics_doc = {
            "by_pattern": {"FB9_GEX2+catalog-default": {"inferred": 4.8, "n": 10}},
            "by_group_lineage": {
                "input:X-Kneel-FB9-2026-04-02-163059_OG_00001.mp4": {"inferred": 4.5, "n": 3},
            },
        }
        weight, meta = score_recipe(recipe, shape=shape, heuristics_doc=heuristics_doc)
        self.assertGreater(weight, 1.0)
        self.assertIn("pattern", str(meta.get("evidence")))

    def test_build_writes_index_file(self) -> None:
        from shape_factory_heuristics import build_heuristics_index, LineageGraph

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "heuristics_index.json"
            doc = build_heuristics_index(
                ratings_doc={"by_output_relpath": {}},
                lineage_graph=LineageGraph.from_edges([]),
                out_path=out,
            )
            self.assertTrue(out.is_file())
            loaded = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(loaded.get("version"), doc.get("version"))


if __name__ == "__main__":
    unittest.main()
