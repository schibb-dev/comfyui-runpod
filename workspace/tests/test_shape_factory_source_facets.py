#!/usr/bin/env python3
"""Tests for source similarity facets."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class SourceFacetsTests(unittest.TestCase):
    def test_build_from_catalog_yaml(self) -> None:
        from shape_factory_source_facets import (
            build_source_facets_from_catalog,
            default_catalog_path,
            lookup_source_facets,
            _load_yaml,
        )

        catalog_path = default_catalog_path(REPO_ROOT / "workspace")
        if not catalog_path.is_file():
            catalog_path = REPO_ROOT / "workspace" / "source_facet_catalog.yaml"
        if not catalog_path.is_file():
            self.skipTest("source_facet_catalog.yaml missing")
        catalog = _load_yaml(catalog_path)
        doc = build_source_facets_from_catalog(catalog)
        self.assertGreaterEqual(doc["stats"]["sources"], 10)
        facets = lookup_source_facets(
            "X-Kneel-FB9-2026-03-31-031315_OG_00001.mp4", doc
        )
        self.assertIn("appearance", facets)
        self.assertIn("expression", facets)
        self.assertIn("identity", facets)

    def test_filter_sources_by_hold_axis(self) -> None:
        from shape_factory_source_facets import filter_sources_by_hold_axis

        facets_doc = {
            "by_source_key": {
                "a.mp4": {"facets": {"appearance": ["blonde"], "identity": ["s1"]}},
                "b.mp4": {"facets": {"appearance": ["blonde"], "identity": ["s2"]}},
                "c.mp4": {"facets": {"appearance": ["redhead"], "identity": ["s1"]}},
            }
        }
        matched, meta = filter_sources_by_hold_axis(
            ["b.mp4", "c.mp4"],
            seed_source="a.mp4",
            hold_axis="appearance",
            facets_doc=facets_doc,
        )
        self.assertEqual(matched, ["b.mp4"])
        self.assertTrue(meta["facet_constrained"])
        self.assertEqual(meta["hold_values"], ["blonde"])

        by_id, meta_id = filter_sources_by_hold_axis(
            ["b.mp4", "c.mp4"],
            seed_source="a.mp4",
            hold_axis="identity",
            facets_doc=facets_doc,
        )
        self.assertEqual(by_id, ["c.mp4"])
        self.assertTrue(meta_id["facet_constrained"])

    def test_filter_fallback_when_seed_unfaceted(self) -> None:
        from shape_factory_source_facets import filter_sources_by_hold_axis

        cands = ["b.mp4", "c.mp4"]
        matched, meta = filter_sources_by_hold_axis(
            cands,
            seed_source="unknown.mp4",
            hold_axis="appearance",
            facets_doc={"by_source_key": {}},
        )
        self.assertEqual(matched, cands)
        self.assertFalse(meta["facet_constrained"])
        self.assertEqual(meta["fallback"], "no_seed_facet")

    def test_save_roundtrip(self) -> None:
        from shape_factory_source_facets import (
            build_source_facets_from_catalog,
            load_source_facets,
            lookup_source_facets,
            save_source_facets,
        )

        doc = build_source_facets_from_catalog(
            {
                "sources": {
                    "clip.mp4": {
                        "facets": {
                            "appearance": ["Blonde"],
                            "expression": ["Smiling"],
                            "identity": ["Subj_A"],
                        }
                    }
                }
            }
        )
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "source_facets.json"
            save_source_facets(path, doc)
            loaded = load_source_facets(path)
            facets = lookup_source_facets("path/to/clip.mp4", loaded)
            self.assertEqual(facets["appearance"], ["blonde"])
            self.assertEqual(facets["identity"], ["subj_a"])


if __name__ == "__main__":
    unittest.main()
