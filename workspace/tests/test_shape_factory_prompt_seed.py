#!/usr/bin/env python3
"""Tests for prompt_seed_path_for_job fallbacks."""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class PromptSeedPathTests(unittest.TestCase):
    def test_same_dir_family_png_when_source_has_no_companion(self) -> None:
        from shape_factory import prompt_seed_path_for_job

        video = Path(
            "/home/yuji/comfyui-runpod-data/output/og/2026-04-03/"
            "X-Kneel-FB9-2026-04-03-142014_OG_00001.mp4"
        )
        if not video.is_file():
            self.skipTest("X-Kneel source video missing")
        if video.with_suffix(".png").is_file():
            self.skipTest("unexpected companion png present")
        family_png = Path(
            "/home/yuji/comfyui-runpod-data/output/og/2026-04-03/FB9_GEX2_2026-04-03_00001.png"
        )
        if not family_png.is_file():
            self.skipTest("family seed png missing")
        job = {
            "family_slug": "FB9_GEX2",
            "bindings": {"source_video": {"path": str(video)}},
        }
        seed = prompt_seed_path_for_job(job, data_root=REPO_ROOT / ".data")
        self.assertIsNotNone(seed)
        assert seed is not None
        self.assertTrue(seed.is_file())
        self.assertEqual(seed.suffix.lower(), ".png")


if __name__ == "__main__":
    unittest.main()
