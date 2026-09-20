#!/usr/bin/env python3
"""Appetite on an ancestor should demote/remove descendant pool members."""

from __future__ import annotations

import unittest

from shape_factory_heuristics import score_recipe
from shape_factory_ratings import (
    attach_ancestry_appetite_blocks,
    expand_appetite_blocks,
    factory_appetite_for_path,
    factory_appetite_for_paths,
    path_blocks_factory,
)


class AppetiteAncestryTests(unittest.TestCase):
    def test_expand_marks_children_of_removed_parent(self) -> None:
        doc = {
            "by_output_relpath": {
                "og/2026-09-01/parent.mp4": {"appetite": "remove"},
            }
        }
        blocks = expand_appetite_blocks(
            doc,
            parent_child_edges=[
                ("og/2026-09-01/parent.mp4", "og/2026-09-02/child.mp4"),
                ("og/2026-09-02/child.mp4", "og/2026-09-03/grandchild.mp4"),
            ],
        )
        self.assertEqual(blocks["og/2026-09-02/child.mp4"]["appetite"], "remove")
        self.assertEqual(blocks["og/2026-09-02/child.mp4"]["via"], "ancestor")
        self.assertEqual(blocks["og/2026-09-03/grandchild.mp4"]["appetite"], "remove")
        self.assertNotIn("grandchild.mp4", blocks)

    def test_less_inherits_unless_child_is_remove(self) -> None:
        doc = {
            "by_output_relpath": {
                "og/parent.mp4": {"appetite": "less"},
                "og/child.mp4": {"appetite": "remove"},
            }
        }
        attached = attach_ancestry_appetite_blocks(
            doc,
            parent_child_edges=[("og/parent.mp4", "og/cousin.mp4")],
        )
        self.assertEqual(factory_appetite_for_path("og/cousin.mp4", attached), "less")
        self.assertEqual(factory_appetite_for_path("og/child.mp4", attached), "remove")
        self.assertTrue(path_blocks_factory("og/cousin.mp4", attached) is False)
        self.assertTrue(path_blocks_factory("og/child.mp4", attached))

    def test_score_recipe_omits_descendant_of_removed_source(self) -> None:
        appetite = attach_ancestry_appetite_blocks(
            {"by_output_relpath": {"input/bad.jpeg": {"appetite": "remove"}}},
            parent_child_edges=[("input/bad.jpeg", "og/from-bad.mp4")],
        )
        weight, meta = score_recipe(
            {
                "output_path": "og/from-bad.mp4",
                "picks": {"source_still": "input/other.png"},
            },
            shape={"graph_hash": "x"},
            ratings_doc={"by_output_relpath": {}},
            appetite_doc=appetite,
        )
        self.assertEqual(weight, 0.0)
        self.assertTrue(meta.get("omit"))
        self.assertEqual(meta.get("appetite"), "remove")

    def test_recipe_paths_include_source_binding(self) -> None:
        appetite = attach_ancestry_appetite_blocks(
            {"by_output_relpath": {"input/bad.jpeg": {"appetite": "remove"}}},
        )
        self.assertEqual(
            factory_appetite_for_paths(
                ["og/unrelated.mp4", "input/bad.jpeg"],
                appetite,
            ),
            "remove",
        )


if __name__ == "__main__":
    unittest.main()
