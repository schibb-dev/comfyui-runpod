#!/usr/bin/env python3
"""Tests for shape_factory_seed_sources (seed output -> source still recovery)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import shape_factory_seed_sources as sss


def _write_png_with_prompt(path: Path, prompt: dict) -> None:
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    info.add_text("prompt", json.dumps(prompt))
    Image.new("RGB", (4, 4), (10, 20, 30)).save(path, pnginfo=info)


class SeedSourcesTests(unittest.TestCase):
    def test_source_still_relpath(self) -> None:
        self.assertEqual(sss.source_still_relpath("Foo.jpg"), "input/Foo.jpg")
        self.assertEqual(sss.source_still_relpath("a/b/Bar.avif"), "input/Bar.avif")
        self.assertEqual(sss.source_still_relpath(""), "")

    def test_persistence_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "factory_seed_sources.json"
            table = {"output/og/x.mp4": {"source_still_relpath": "input/y.jpg"}}
            sss.save_seed_sources(p, table)
            self.assertEqual(sss.load_seed_sources(p), table)
        # Missing file -> empty
        self.assertEqual(sss.load_seed_sources(Path(td) / "gone.json"), {})

    def test_infer_source_still_from_png_load_image(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            mp4 = Path(td) / "clip.mp4"
            mp4.write_bytes(b"not-a-real-mp4")
            png = mp4.with_suffix(".png")
            _write_png_with_prompt(
                png,
                {
                    "88": {"class_type": "LoadImage", "inputs": {"image": "Portrait-13X.avif"}},
                    "9": {"class_type": "KSampler", "inputs": {"seed": 1}},
                },
            )
            info = sss.infer_source_still(mp4)
            self.assertIsNotNone(info)
            assert info is not None
            self.assertEqual(info["source_basename"], "Portrait-13X.avif")
            self.assertEqual(info["evidence"], "png_load_image")

    def test_infer_returns_none_without_image_source(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            mp4 = Path(td) / "clip.mp4"
            mp4.write_bytes(b"x")
            png = mp4.with_suffix(".png")
            _write_png_with_prompt(png, {"1": {"class_type": "KSampler", "inputs": {"seed": 2}}})
            self.assertIsNone(sss.infer_source_still(mp4))

    def test_build_seed_sources_persists_positive_and_negative(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out_root = root / "output"
            og = out_root / "output" / "og" / "2026-03-18"
            og.mkdir(parents=True)

            good = og / "good.mp4"
            good.write_bytes(b"m")
            _write_png_with_prompt(
                good.with_suffix(".png"),
                {"88": {"class_type": "LoadImage", "inputs": {"image": "src.jpg"}}},
            )
            bad = og / "bad.mp4"
            bad.write_bytes(b"m")
            _write_png_with_prompt(bad.with_suffix(".png"), {"1": {"class_type": "KSampler", "inputs": {}}})

            seed_path = out_root / "output" / "_status" / "factory_seed_sources.json"
            result = sss.build_seed_sources(
                output_relpaths=[
                    "output/output/og/2026-03-18/good.mp4",
                    "output/output/og/2026-03-18/bad.mp4",
                ],
                output_root=out_root,
                seed_sources_path=seed_path,
                workspace_input_exists=lambda rel: True,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["resolved"], 1)
            self.assertEqual(result["negative"], 1)

            table = sss.load_seed_sources(seed_path)
            self.assertEqual(
                table["output/output/og/2026-03-18/good.mp4"]["source_still_relpath"],
                "input/src.jpg",
            )
            self.assertIsNone(
                table["output/output/og/2026-03-18/bad.mp4"]["source_still_relpath"]
            )

    def test_build_skips_source_still_when_input_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out_root = root / "output"
            og = out_root / "output" / "og"
            og.mkdir(parents=True)
            mp4 = og / "clip.mp4"
            mp4.write_bytes(b"m")
            _write_png_with_prompt(
                mp4.with_suffix(".png"),
                {"88": {"class_type": "LoadImage", "inputs": {"image": "missing.jpg"}}},
            )
            seed_path = out_root / "output" / "_status" / "factory_seed_sources.json"
            result = sss.build_seed_sources(
                output_relpaths=["output/output/og/clip.mp4"],
                output_root=out_root,
                seed_sources_path=seed_path,
                workspace_input_exists=lambda rel: False,
            )
            self.assertEqual(result["negative"], 1)
            table = sss.load_seed_sources(seed_path)
            row = table["output/output/og/clip.mp4"]
            self.assertIsNone(row["source_still_relpath"])
            self.assertTrue(row["missing_input"])


if __name__ == "__main__":
    unittest.main()
