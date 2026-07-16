#!/usr/bin/env python3
"""Tests for vision slice review packaging."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401

from vision_slice_review import list_vision_slice_review, register_variant, variant_ndjson_name


class VisionSliceReviewTests(unittest.TestCase):
    def test_group_assets_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            status = Path(td)
            rows = [
                {
                    "asset_relpath": "og/a.mp4",
                    "t0": 0.0,
                    "t1": 2.0,
                    "frame_t": 1.0,
                    "slice": "window",
                    "caption": "first",
                },
                {
                    "asset_relpath": "og/a.mp4",
                    "t0": 0.0,
                    "t1": 4.0,
                    "frame_t": 2.0,
                    "slice": "whole",
                    "caption": "whole",
                },
                {
                    "asset_relpath": "og/b.mp4",
                    "t0": 0.0,
                    "t1": 2.0,
                    "frame_t": 1.0,
                    "slice": "window",
                    "caption": "other",
                },
            ]
            (status / "vision_slice_captions.ndjson").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
            )
            (status / "vision_slice_manifest.json").write_text(
                json.dumps({"run_id": "t1", "caption_count": 3, "asset_count": 2}),
                encoding="utf-8",
            )
            doc = list_vision_slice_review(status_dir=status)
            self.assertTrue(doc["ok"])
            self.assertEqual(doc["asset_count"], 2)
            self.assertEqual(doc["caption_count"], 3)
            self.assertEqual(len(doc["variants"]), 1)
            self.assertEqual(doc["variants"][0]["id"], "base_caption")
            a0 = doc["assets"][0]
            self.assertEqual(a0["asset_relpath"], "og/a.mp4")
            self.assertEqual(a0["video_url"], "/files/og/a.mp4")
            self.assertTrue(a0["has_whole"])
            self.assertEqual(a0["slices"][0]["caption"], "first")
            self.assertEqual(a0["slices"][0]["captions"]["base_caption"]["caption"], "first")

    def test_compare_variants(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            status = Path(td)
            base = [
                {
                    "asset_relpath": "og/a.mp4",
                    "t0": 0.0,
                    "t1": 2.0,
                    "frame_t": 1.0,
                    "slice": "window",
                    "caption": "short",
                    "task": "caption",
                }
            ]
            detailed = [
                {
                    "asset_relpath": "og/a.mp4",
                    "t0": 0.0,
                    "t1": 2.0,
                    "frame_t": 1.0,
                    "slice": "window",
                    "caption": "a longer detailed caption",
                    "task": "detailed_caption",
                }
            ]
            (status / variant_ndjson_name("base_caption")).write_text(
                json.dumps(base[0]) + "\n", encoding="utf-8"
            )
            (status / variant_ndjson_name("florence_detailed")).write_text(
                json.dumps(detailed[0]) + "\n", encoding="utf-8"
            )
            register_variant(
                status,
                variant_id="base_caption",
                label="base",
                model_pin="microsoft/Florence-2-base",
                task="caption",
                provider="comfy_florence2",
                run_id="r1",
                ndjson_name=variant_ndjson_name("base_caption"),
                caption_count=1,
            )
            register_variant(
                status,
                variant_id="florence_detailed",
                label="detailed",
                model_pin="microsoft/Florence-2-base",
                task="detailed_caption",
                provider="comfy_florence2",
                run_id="r2",
                ndjson_name=variant_ndjson_name("florence_detailed"),
                caption_count=1,
            )
            doc = list_vision_slice_review(status_dir=status)
            self.assertEqual(len(doc["variants"]), 2)
            caps = doc["assets"][0]["slices"][0]["captions"]
            self.assertEqual(caps["base_caption"]["caption"], "short")
            self.assertEqual(caps["florence_detailed"]["caption"], "a longer detailed caption")
            base_stats = next(v for v in doc["variants"] if v["id"] == "base_caption")
            det_stats = next(v for v in doc["variants"] if v["id"] == "florence_detailed")
            self.assertEqual(base_stats["quality"]["mean_chars"], 5.0)
            self.assertEqual(det_stats["quality"]["mean_chars"], 25.0)
            self.assertIn("stats", doc)
            self.assertEqual(doc["stats"]["variant_count"], 2)

    def test_run_progress_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            status = Path(td)
            row = {
                "asset_relpath": "og/a.mp4",
                "t0": 0.0,
                "t1": 2.0,
                "frame_t": 1.0,
                "slice": "window",
                "caption": "hello world",
            }
            (status / variant_ndjson_name("cohort_x2_pg_tags")).write_text(
                json.dumps(row) + "\n", encoding="utf-8"
            )
            (status / "vision_slice_manifest__cohort_x2_pg_tags.json").write_text(
                json.dumps(
                    {
                        "variant": "cohort_x2_pg_tags",
                        "caption_count": 1,
                        "error_count": 2,
                        "frame_count": 10,
                        "started_utc": "2026-07-16T00:00:00Z",
                        "finished_utc": "2026-07-16T00:01:00Z",
                        "timing": {"wall_s": 60.0, "captions_per_min_steady": 12.5},
                    }
                ),
                encoding="utf-8",
            )
            doc = list_vision_slice_review(status_dir=status)
            v = doc["variants"][0]
            self.assertEqual(v["status"], "running")  # 1 < 10 frames
            self.assertEqual(v["caption_count"], 1)
            self.assertEqual(v["frame_count"], 10)
            self.assertEqual(v["error_count"], 2)
            self.assertEqual(v["progress_pct"], 10.0)
            self.assertTrue(doc["stats"]["any_running"])
            self.assertEqual(doc["stats"]["poll_suggested_ms"], 8000)

    def test_quality_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            status = Path(td)
            row = {
                "asset_relpath": "og/a.mp4",
                "t0": 0.0,
                "t1": 2.0,
                "frame_t": 1.0,
                "slice": "window",
                "caption": "hello",
            }
            (status / variant_ndjson_name("base_caption")).write_text(
                json.dumps(row) + "\n", encoding="utf-8"
            )
            qrow = {
                "asset_relpath": "og/a.mp4",
                "t0": 0.0,
                "t1": 2.0,
                "frame_t": 1.0,
                "slice": "window",
                "quality": {
                    "sharpness": 0.8,
                    "convergence": None,
                    "artifacting": 0.2,
                    "exposure": 0.5,
                    "contrast": 0.4,
                },
            }
            (status / "vision_slice_quality.ndjson").write_text(
                json.dumps(qrow) + "\n", encoding="utf-8"
            )
            doc = list_vision_slice_review(status_dir=status)
            sl = doc["assets"][0]["slices"][0]
            self.assertEqual(sl["quality"]["sharpness"], 0.8)
            aq = doc["assets"][0]["quality"]
            self.assertAlmostEqual(aq["sharpness"]["mean"], 0.8)
            self.assertIn("video_quality", doc["stats"])
            self.assertAlmostEqual(doc["stats"]["video_quality"]["sharpness"], 0.8)


if __name__ == "__main__":
    unittest.main()
