#!/usr/bin/env python3
"""Tests for multi-axis quality ratings (SQLite live store)."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from shape_factory_ratings import (
    QUALITY_AXES,
    aggregate_explicit_from_axes,
    axes_complete,
    build_asset_ratings_explorer,
    is_omit_quality_rating,
    is_usable_quality_rating,
    load_appetite_doc,
    load_ratings_doc,
    lookup_output_appetite,
    lookup_output_rating,
    normalize_axes_map,
    open_ratings_db,
    ratings_db_path_for_index,
    set_output_appetite,
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

            # Click path must not rewrite JSON; live store is SQLite.
            self.assertFalse(idx.is_file())
            self.assertTrue(ratings_db_path_for_index(idx).is_file())

            doc = load_ratings_doc(idx)
            row = lookup_output_rating("og/clip.mp4", doc)
            assert row is not None
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
            doc = load_ratings_doc(idx)
            row = lookup_output_rating("og/clip.mp4", doc)
            assert row is not None
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
            doc = load_ratings_doc(idx)
            row = lookup_output_rating("og/clip.mp4", doc)
            assert row is not None
            self.assertNotIn("action_quality", row["axes"])
            self.assertFalse(axes_complete(row["axes"]))

            set_output_rating(
                media_abs=media,
                media_relpath="og/clip.mp4",
                stars=0,
                og_root=og,
                ratings_index_path=idx,
            )
            doc = load_ratings_doc(idx)
            self.assertFalse(doc.get("by_output_relpath"))

    def test_set_axis_does_not_rewrite_json_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            og = root / "og"
            og.mkdir()
            media = og / "clip.mp4"
            media.write_bytes(b"fake")
            idx = root / "ratings_index.json"
            idx.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "updated_at": "2020-01-01T00:00:00Z",
                        "by_output_relpath": {},
                        "by_graph_hash": {},
                        "by_shape_recipe": {},
                        "by_source_basename": {},
                        "stats": {},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            mtime_before = idx.stat().st_mtime
            time.sleep(0.02)
            set_output_quality_axis(
                media_abs=media,
                media_relpath="og/clip.mp4",
                axis="subject_beauty",
                stars=5,
                og_root=og,
                ratings_index_path=idx,
            )
            self.assertEqual(idx.stat().st_mtime, mtime_before)
            doc = load_ratings_doc(idx)
            row = lookup_output_rating("og/clip.mp4", doc)
            assert row is not None
            self.assertEqual(row["axes"]["subject_beauty"], 5)

    def test_second_set_is_upsert_not_duplicate(self) -> None:
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
                stars=3,
                og_root=og,
                ratings_index_path=idx,
            )
            set_output_quality_axis(
                media_abs=media,
                media_relpath="og/clip.mp4",
                axis="subject_beauty",
                stars=5,
                og_root=og,
                ratings_index_path=idx,
            )
            db = ratings_db_path_for_index(idx)
            con = open_ratings_db(db, ratings_json=idx)
            try:
                n = con.execute("SELECT COUNT(*) AS n FROM rating_row").fetchone()["n"]
                self.assertEqual(n, 1)
                row = con.execute("SELECT subject_beauty, explicit FROM rating_row").fetchone()
                self.assertEqual(row["subject_beauty"], 5)
            finally:
                con.close()

    def test_lookup_by_short_and_discovery_keys(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            og = root / "og"
            og.mkdir()
            media = og / "clip.mp4"
            media.write_bytes(b"fake")
            idx = root / "ratings_index.json"
            saved = set_output_quality_axis(
                media_abs=media,
                media_relpath="og/clip.mp4",
                axis="render_quality",
                stars=4,
                og_root=og,
                ratings_index_path=idx,
            )
            doc = load_ratings_doc(idx)
            discovery = str(saved.get("discovery_key") or "og/clip.mp4")
            short = str(saved.get("short_key") or "")
            self.assertIsNotNone(lookup_output_rating(discovery, doc))
            if short:
                self.assertIsNotNone(lookup_output_rating(short, doc))
            self.assertIsNotNone(lookup_output_rating("og/clip.mp4", doc))

    def test_migrate_from_json_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            idx = root / "ratings_index.json"
            appetite = root / "appetite_index.json"
            discovery = "output/og/clip.mp4"
            short = "og/clip.mp4"
            row = {
                "explicit": 4,
                "axes": {"subject_beauty": 5, "render_quality": 3, "action_quality": 4},
                "short_key": short,
                "rated_at": "2026-01-02T03:04:05Z",
                "sources": ["src.jpg"],
                "source_paths": ["input/src.jpg"],
            }
            idx.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "updated_at": "2026-01-02T03:04:05Z",
                        "stats": {},
                        "by_graph_hash": {"abc": {"mean": 4.0}},
                        "by_shape_recipe": {},
                        "by_source_basename": {},
                        "by_output_relpath": {discovery: row, short: row},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            appetite.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "updated_at": "2026-01-02T03:04:05Z",
                        "by_output_relpath": {
                            discovery: {
                                "appetite": "more",
                                "facet": "source",
                                "score": 1.0,
                                "short_key": short,
                                "updated_at": "2026-01-02T03:04:05Z",
                            },
                            short: {
                                "appetite": "more",
                                "facet": "source",
                                "score": 1.0,
                                "short_key": short,
                                "updated_at": "2026-01-02T03:04:05Z",
                            },
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            rdoc = load_ratings_doc(idx)
            adoc = load_appetite_doc(appetite)
            self.assertTrue(ratings_db_path_for_index(idx).is_file())
            got = lookup_output_rating(discovery, rdoc)
            assert got is not None
            self.assertEqual(got["axes"]["subject_beauty"], 5)
            self.assertEqual(got["explicit"], 4)
            self.assertEqual(got["rated_at"], "2026-01-02T03:04:05Z")
            self.assertIsNotNone(lookup_output_rating(short, rdoc))
            # Aggregates preserved from JSON export overlay.
            self.assertIn("abc", rdoc.get("by_graph_hash") or {})

            app = lookup_output_appetite(discovery, adoc)
            assert app is not None
            self.assertEqual(app["appetite"], "more")
            self.assertEqual(app["facet"], "source")

            db = ratings_db_path_for_index(idx)
            con = open_ratings_db(db, ratings_json=idx, appetite_json=appetite)
            try:
                self.assertEqual(con.execute("SELECT COUNT(*) AS n FROM rating_row").fetchone()["n"], 1)
                self.assertEqual(con.execute("SELECT COUNT(*) AS n FROM appetite_row").fetchone()["n"], 1)
                self.assertEqual(
                    con.execute("SELECT value FROM meta WHERE key='migrated_from_json'").fetchone()["value"],
                    "1",
                )
            finally:
                con.close()

    def test_set_appetite_sqlite_no_json_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            og = root / "og"
            og.mkdir()
            media = og / "clip.mp4"
            media.write_bytes(b"fake")
            appetite_path = root / "appetite_index.json"
            appetite_path.write_text(
                json.dumps({"version": 1, "updated_at": "2020-01-01T00:00:00Z", "by_output_relpath": {}}),
                encoding="utf-8",
            )
            mtime_before = appetite_path.stat().st_mtime
            time.sleep(0.02)
            set_output_appetite(
                media_abs=media,
                media_relpath="og/clip.mp4",
                appetite="more",
                facet="both",
                og_root=og,
                appetite_index_path=appetite_path,
            )
            self.assertEqual(appetite_path.stat().st_mtime, mtime_before)
            doc = load_appetite_doc(appetite_path)
            row = lookup_output_appetite("og/clip.mp4", doc)
            assert row is not None
            self.assertEqual(row["appetite"], "more")


if __name__ == "__main__":
    unittest.main()
