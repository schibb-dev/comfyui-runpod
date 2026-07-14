#!/usr/bin/env python3
"""Tests for shape_factory_disposition."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from shape_factory_disposition import (
    compute_disposition_promotions,
    is_retired_disposition,
    load_seed_catalog,
    merge_catalog,
    stamp_output_disposition,
    toggle_output_disposition,
    trash_output_media,
)


class DispositionCatalogTests(unittest.TestCase):
    def test_seed_catalog_has_entries(self) -> None:
        cat = load_seed_catalog()
        entries = [m for m in cat.get("markers") or [] if m.get("kind") == "entry"]
        ids = {m["id"] for m in entries}
        self.assertIn("refine", ids)
        self.assertIn("advance", ids)
        self.assertIn("retire", ids)
        self.assertIn("derived", ids)

    def test_promotion_derived_only(self) -> None:
        cat = load_seed_catalog()
        promos = compute_disposition_promotions(
            cat, quality=None, appetite=None, facet="both", explicit_quality_missing=True
        )
        self.assertIn("derived", promos["promote"])

    def test_promotion_high_quality_high_appetite(self) -> None:
        cat = load_seed_catalog()
        promos = compute_disposition_promotions(cat, quality=5.0, appetite="more", facet="both")
        self.assertIn("advance", promos["promote"])

    def test_promotion_low_quality_high_appetite(self) -> None:
        cat = load_seed_catalog()
        promos = compute_disposition_promotions(cat, quality=2.0, appetite="fast_track", facet="source")
        self.assertTrue({"refine", "investigate", "extract"}.intersection(promos["promote"]))


class DispositionIndexTests(unittest.TestCase):
    def test_toggle_entry_replaces_other_entries(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            og = root / "og"
            og.mkdir()
            media = og / "clip.mp4"
            media.write_bytes(b"fake")
            idx = root / "_status" / "disposition_index.json"
            cat = load_seed_catalog()
            toggle_output_disposition(
                media_abs=media,
                media_relpath="og/clip.mp4",
                marker_id="refine",
                on=True,
                og_root=og,
                disposition_index_path=idx,
                catalog=cat,
            )
            saved = toggle_output_disposition(
                media_abs=media,
                media_relpath="og/clip.mp4",
                marker_id="investigate",
                on=True,
                og_root=og,
                disposition_index_path=idx,
                catalog=cat,
            )
            self.assertEqual(saved["markers"], ["investigate"])

    def test_stamp_derived_disposition(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            og = root / "og"
            og.mkdir()
            media = og / "pred.mp4"
            media.write_bytes(b"fake")
            idx = root / "_status" / "disposition_index.json"
            cat = load_seed_catalog()
            saved = stamp_output_disposition(
                media_abs=media,
                marker_id="derived",
                note="predicted hourly",
                og_root=og,
                disposition_index_path=idx,
                catalog=cat,
                media_relpath="og/pred.mp4",
            )
            self.assertIn("derived", saved["markers"])
            doc = json.loads(idx.read_text(encoding="utf-8"))
            row = next(iter(doc["by_output_relpath"].values()))
            self.assertEqual(row["markers"], ["derived"])
            self.assertEqual(row["notes"].get("derived"), "predicted hourly")

    def test_trash_moves_companion(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            og = root / "og"
            og.mkdir()
            media = og / "clip.mp4"
            media.write_bytes(b"fake")
            xmp = og / "clip.XMP"
            xmp.write_text("xmp", encoding="utf-8")
            out = trash_output_media(media, og_root=og)
            self.assertTrue(out["ok"])
            self.assertFalse(media.exists())
            self.assertFalse(xmp.exists())


class DispositionRetireTests(unittest.TestCase):
    def test_is_retired(self) -> None:
        self.assertTrue(is_retired_disposition(["retire", "park"]))
        self.assertFalse(is_retired_disposition(["refine"]))


if __name__ == "__main__":
    unittest.main()
