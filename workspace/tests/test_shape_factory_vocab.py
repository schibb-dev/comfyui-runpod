#!/usr/bin/env python3
"""Tests for shape_factory_vocab (IO tags, stems, start_image integrity)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from shape_factory_vocab import (
    format_catalog_stem,
    guess_io_from_workflow,
    parse_catalog_stem,
    stamp_job_vocab,
    validate_shape_vocab,
    validate_start_image_vs_primary_input,
    wan_start_image_roots,
)


def _link(lid, oid, oslot, tid, tslot, typ="IMAGE"):
    return [lid, oid, oslot, tid, tslot, typ]


class CatalogStemTests(unittest.TestCase):
    def test_parse_i2v(self) -> None:
        p = parse_catalog_stem("FB8VA4_2026-01-11_224827_I2V_00001-readable.json")
        self.assertTrue(p["ok"])
        self.assertEqual(p["io_class"], "I2V")
        self.assertEqual(p["brand"], "FB8VA4")
        self.assertEqual(p["seq"], "00001")
        self.assertFalse(p.get("legacy_ext"))

    def test_parse_legacy_ext(self) -> None:
        p = parse_catalog_stem("FB8VB2_2026-01-09_225941_EXT_00001")
        self.assertTrue(p["ok"])
        self.assertEqual(p["io_class"], "V2V")
        self.assertTrue(p["legacy_ext"])
        self.assertEqual(p["chain_role_hint"], "extend")

    def test_format_stem(self) -> None:
        s = format_catalog_stem("FB9_GEX2", date="2026-08-27", time="120000", io_class="V2V", seq=3)
        self.assertEqual(s, "FB9_GEX2_2026-08-27_120000_V2V_00003")


class VocabFieldTests(unittest.TestCase):
    def test_validate_profile_slots(self) -> None:
        shape = {
            "primary_input": "still",
            "input_profile": "still_prompt",
            "chain_role": "origin",
            "requires": [
                {"slot": "source_still"},
                {"slot": "prompt_profile"},
            ],
        }
        self.assertEqual(validate_shape_vocab(shape), [])

    def test_validate_missing_identity_slot(self) -> None:
        shape = {
            "primary_input": "video",
            "input_profile": "video_identity_still_prompt",
            "chain_role": "extend",
            "requires": [
                {"slot": "source_video"},
                {"slot": "prompt_profile"},
            ],
        }
        errs = validate_shape_vocab(shape)
        self.assertTrue(any("identity_anchor" in e for e in errs))

    def test_stamp_job(self) -> None:
        job: dict = {}
        stamp_job_vocab(
            job,
            {
                "primary_input": "video",
                "input_profile": "video_prompt",
                "chain_role": "extend",
            },
        )
        self.assertEqual(job["io_class"], "V2V")
        self.assertEqual(job["chain_role"], "extend")


class StartImageTests(unittest.TestCase):
    def _still_i2v_workflow(self) -> dict:
        # 88 LoadImage -> 401 Resize -> 55 Wan start_image
        return {
            "nodes": [
                {"id": 88, "type": "LoadImage", "inputs": []},
                {
                    "id": 401,
                    "type": "ImageResize",
                    "inputs": [{"name": "image", "link": 1}],
                },
                {
                    "id": 55,
                    "type": "WanImageToVideo",
                    "inputs": [{"name": "start_image", "link": 2}],
                },
            ],
            "links": [
                _link(1, 88, 0, 401, 0),
                _link(2, 401, 0, 55, 0),
            ],
        }

    def _video_only_bug_workflow(self) -> dict:
        # VHS -> Wan start_image (the FB8VB2 failure mode when labeled I2V)
        return {
            "nodes": [
                {"id": 463, "type": "VHS_LoadVideo", "inputs": []},
                {
                    "id": 55,
                    "type": "WanImageToVideo",
                    "inputs": [{"name": "start_image", "link": 9}],
                },
            ],
            "links": [_link(9, 463, 0, 55, 0)],
        }

    def test_still_primary_ok(self) -> None:
        errs = validate_start_image_vs_primary_input(
            {"primary_input": "still"},
            self._still_i2v_workflow(),
        )
        self.assertEqual(errs, [])

    def test_still_primary_rejects_video_only(self) -> None:
        errs = validate_start_image_vs_primary_input(
            {"primary_input": "still"},
            self._video_only_bug_workflow(),
        )
        self.assertTrue(errs)
        self.assertTrue(any("video-only" in e or "no LoadImage" in e for e in errs))

    def test_wan_roots_detect_still(self) -> None:
        roots = wan_start_image_roots(self._still_i2v_workflow())
        self.assertEqual(len(roots), 1)
        self.assertIn("still", roots[0]["media_roots"])


class GuessIoTests(unittest.TestCase):
    def test_guess_i2v(self) -> None:
        wf = {
            "nodes": [
                {"id": 1, "type": "LoadImage"},
                {
                    "id": 2,
                    "type": "WanImageToVideo",
                    "inputs": [{"name": "start_image", "link": 1}],
                },
            ],
            "links": [_link(1, 1, 0, 2, 0)],
        }
        g = guess_io_from_workflow(wf)
        self.assertEqual(g["io_class"], "I2V")


class EnrolledShapesSmoke(unittest.TestCase):
    def test_all_enrolled_shapes_vocab(self) -> None:
        root = Path(__file__).resolve().parents[2] / ".data" / "shapes"
        if not root.is_dir():
            self.skipTest("no .data/shapes")
        import yaml

        for path in sorted(root.glob("*.shape.yaml")):
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            errs = validate_shape_vocab(doc)
            self.assertEqual(errs, [], msg=f"{path.name}: {errs}")


if __name__ == "__main__":
    unittest.main()
