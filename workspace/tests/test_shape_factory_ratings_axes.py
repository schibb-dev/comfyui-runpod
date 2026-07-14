#!/usr/bin/env python3
"""Tests for multi-axis quality ratings."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from shape_factory_ratings import (
    QUALITY_AXES,
    aggregate_explicit_from_axes,
    axes_complete,
    build_asset_ratings_explorer,
    is_omit_quality_rating,
    is_usable_quality_rating,
    normalize_axes_map,
    set_output_quality_axis,
    set_output_rating,
)


class QualityAxesHelpersTests(unittest.TestCase):
    def test_usable_vs_omit(self) -> None:
        self.assertTrue(is_usable_quality_rating(1))
        self.assertTrue(is_usable_quality_rating(5))
        self.assertFalse(is_usable_quality_rating(None))
        self.assertFalse(is_usable_quality_rating(0))
        self.assertFalse(is_usable_quality_rating(-1))
        self.assertFalse(is_usable_quality_rating(0.5))
        self.assertTrue(is_omit_quality_rating(0))
        self.assertTrue(is_omit_quality_rating(-2))
        self.assertFalse(is_omit_quality_rating(None))
        self.assertFalse(is_omit_quality_rating(3))

    def test_aggregate_and_complete(self) -> None:
        partial = {"subject_beauty": 5, "render_quality": 3}
        self.assertFalse(axes_complete(partial))
        self.assertEqual(aggregate_explicit_from_axes(partial), 4)
        full = {"subject_beauty": 5, "render_quality": 3, "action_quality": 4}
        self.assertTrue(axes_complete(full))
        self.assertEqual(aggregate_explicit_from_axes(full), 4)

    def test_normalize_axes_map_filters_junk(self) -> None:
        raw = {"subject_beauty": 5, "render_quality": 0, "action_quality": "x", "extra": 2}
        self.assertEqual(normalize_axes_map(raw), {"subject_beauty": 5})


class QualityAxesWriteTests(unittest.TestCase):
    def test_set_axis_derives_explicit_and_xmp(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            og = root / "og"
            og.mkdir()
            media = og / "clip.mp4"
            media.write_bytes(b"fake")
            idx = root / "ratings_index.json"

            set_output_quality_axis(
                media_abs=media,
                media_relpath="og/clip.mp4",
                axis="subject_beauty",
                stars=5,
                og_root=og,
                ratings_index_path=idx,
            )
            set_output_quality_axis(
                media_abs=media,
                media_relpath="og/clip.mp4",
                axis="render_quality",
                stars=3,
                og_root=og,
                ratings_index_path=idx,
            )
            saved = set_output_quality_axis(
                media_abs=media,
                media_relpath="og/clip.mp4",
                axis="action_quality",
                stars=4,
                og_root=og,
                ratings_index_path=idx,
            )
            self.assertEqual(saved["explicit"], 4)
            self.assertTrue(axes_complete(saved["axes"]))
            self.assertTrue((og / "clip.XMP").is_file())
            xmp = (og / "clip.XMP").read_text(encoding="utf-8")
            self.assertIn('xmp:Rating="4"', xmp)

            doc = json.loads(idx.read_text(encoding="utf-8"))
            row = next(iter(doc["by_output_relpath"].values()))
            self.assertEqual(row["axes"]["subject_beauty"], 5)
            self.assertEqual(row["explicit"], 4)
            self.assertTrue(str(row.get("rated_at") or "").startswith("20"))

            explorer = build_asset_ratings_explorer(relpath="og/clip.mp4", ratings_doc=doc)
            self.assertEqual(explorer["axes"]["render_quality"], 3)
            self.assertTrue(explorer["explicit"]["axes_complete"])

    def test_clear_axis_and_all(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            og = root / "og"
            og.mkdir()
            media = og / "clip.mp4"
            media.write_bytes(b"fake")
            idx = root / "ratings_index.json"
            set_output_rating(
                media_abs=media,
                media_relpath="og/clip.mp4",
                stars=4,
                og_root=og,
                ratings_index_path=idx,
            )
            doc = json.loads(idx.read_text(encoding="utf-8"))
            row = next(iter(doc["by_output_relpath"].values()))
            self.assertTrue(axes_complete(row["axes"]))
            self.assertEqual(set(row["axes"]), set(QUALITY_AXES))

            set_output_quality_axis(
                media_abs=media,
                media_relpath="og/clip.mp4",
                axis="action_quality",
                stars=0,
                og_root=og,
                ratings_index_path=idx,
            )
            doc = json.loads(idx.read_text(encoding="utf-8"))
            row = next(iter(doc["by_output_relpath"].values()))
            self.assertNotIn("action_quality", row["axes"])
            self.assertFalse(axes_complete(row["axes"]))

            set_output_rating(
                media_abs=media,
                media_relpath="og/clip.mp4",
                stars=0,
                og_root=og,
                ratings_index_path=idx,
            )
            doc = json.loads(idx.read_text(encoding="utf-8"))
            self.assertFalse(doc.get("by_output_relpath"))


if __name__ == "__main__":
    unittest.main()
