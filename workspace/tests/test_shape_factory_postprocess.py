"""Tests for shape-level postprocess policy application."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

KNEEL_CATALOG = Path(
    "/home/yuji/comfyui-runpod-data/comfyui_user/default/workflows/generated/catalog/X-KNEEL-FB9-readable.json"
)
KNEEL_BAK = KNEEL_CATALOG.with_suffix(KNEEL_CATALOG.suffix + ".pre-delivery-strip.bak")
GEX2_CATALOG = Path(
    "/home/yuji/comfyui-runpod-data/comfyui_user/default/workflows/generated/catalog/FB9_GEX2-readable.json"
)
ZOOMOUT_CATALOG = Path(
    "/home/yuji/comfyui-runpod-data/comfyui_user/default/workflows/generated/catalog/FB8VA5-ZOOMOUT-readable.json"
)


def _nodes_by_type(workflow: dict, ntype: str) -> list[dict]:
    return [n for n in workflow.get("nodes") or [] if n.get("type") == ntype]


def _modes(workflow: dict, ntype: str) -> list[int]:
    return [int(n.get("mode") or 0) for n in _nodes_by_type(workflow, ntype)]


class PostprocessApplyTests(unittest.TestCase):
    def test_no_block_is_noop(self) -> None:
        from shape_factory_postprocess import apply_shape_postprocess_ui

        wf = {"nodes": [{"id": 1, "type": "RIFE VFI", "mode": 0}]}
        changes = apply_shape_postprocess_ui(wf, {})
        self.assertEqual(changes, {})
        self.assertEqual(wf["nodes"][0]["mode"], 0)

    def test_origin_defaults_bypass_upscale_and_rife(self) -> None:
        if not KNEEL_BAK.is_file():
            self.skipTest("pre-strip backup missing")
        from shape_factory_postprocess import apply_shape_postprocess_ui

        wf = json.loads(KNEEL_BAK.read_text(encoding="utf-8"))
        shape = {
            "postprocess": {
                "profile_id": "origin-default",
                "upscale": False,
                "interpolate": False,
                "color_match": True,
                "merge_frames": False,
            }
        }
        apply_shape_postprocess_ui(wf, shape)
        self.assertTrue(all(int(n.get("mode") or 0) == 2 for n in wf["nodes"] if n.get("type") == "ImageUpscaleWithModel"))
        self.assertTrue(all(int(n.get("mode") or 0) == 0 for n in wf["nodes"] if n.get("type") == "ColorMatch"))

    def test_stripped_catalog_has_no_upscale_nodes(self) -> None:
        if not KNEEL_CATALOG.is_file():
            self.skipTest("catalog missing")
        wf = json.loads(KNEEL_CATALOG.read_text(encoding="utf-8"))
        types = {n.get("type") for n in wf.get("nodes") or []}
        self.assertNotIn("ImageUpscaleWithModel", types)
        self.assertNotIn("RIFE VFI", types)
        self.assertIn("ColorMatch", types)

    def test_gex_extend_keeps_color_match_and_merge(self) -> None:
        if not GEX2_CATALOG.is_file():
            self.skipTest("catalog template not on disk")
        from shape_factory_postprocess import apply_shape_postprocess_ui

        wf = json.loads(GEX2_CATALOG.read_text(encoding="utf-8"))
        shape = {
            "postprocess": {
                "profile_id": "gex-extend-default",
                "upscale": False,
                "interpolate": False,
                "color_match": True,
                "merge_frames": True,
            }
        }
        apply_shape_postprocess_ui(wf, shape)
        self.assertEqual(_modes(wf, "ColorMatch"), [0])
        self.assertEqual(_modes(wf, "VHS_MergeImages"), [0])
        self.assertEqual(_modes(wf, "ImageUpscaleWithModel"), [])
        self.assertEqual(_modes(wf, "RIFE VFI"), [])

    def test_zoomout_pre_strip_outlier_forced_off(self) -> None:
        bak = ZOOMOUT_CATALOG.with_suffix(ZOOMOUT_CATALOG.suffix + ".pre-delivery-strip.bak")
        if not bak.is_file():
            self.skipTest("zoomout backup missing")
        from shape_factory_postprocess import apply_shape_postprocess_ui

        wf = json.loads(bak.read_text(encoding="utf-8"))
        self.assertTrue(any(m == 0 for m in _modes(wf, "ImageUpscaleWithModel")))
        shape = {"postprocess": {"upscale": False, "interpolate": False, "color_match": True}}
        apply_shape_postprocess_ui(wf, shape)
        self.assertTrue(all(m == 2 for m in _modes(wf, "ImageUpscaleWithModel")))

    def test_job_adhoc_override_enables_interpolate(self) -> None:
        if not KNEEL_BAK.is_file():
            self.skipTest("pre-strip backup missing")
        from shape_factory_postprocess import apply_shape_postprocess_ui

        wf = json.loads(KNEEL_BAK.read_text(encoding="utf-8"))
        shape = {"postprocess": {"upscale": False, "interpolate": False, "color_match": True}}
        job = {"adhoc_overrides": {"postprocess": {"interpolate": True}}}
        apply_shape_postprocess_ui(wf, shape, job)
        self.assertTrue(all(m == 0 for m in _modes(wf, "RIFE VFI")))
        self.assertTrue(all(m == 2 for m in _modes(wf, "ImageUpscaleWithModel")))

    def test_infer_postprocess_from_workflow(self) -> None:
        from shape_factory_postprocess import infer_postprocess_from_workflow

        wf = {
            "nodes": [
                {"type": "ImageUpscaleWithModel", "mode": 2},
                {"type": "RIFE VFI", "mode": 0},
                {"type": "ColorMatch", "mode": 0},
            ]
        }
        got = infer_postprocess_from_workflow(wf)
        self.assertFalse(got["upscale"])
        self.assertTrue(got["interpolate"])
        self.assertTrue(got["color_match"])


class ShapeFactoryWrapperTests(unittest.TestCase):
    def test_shape_factory_wrapper_delegates(self) -> None:
        from shape_factory import apply_shape_postprocess_ui

        wf = {"nodes": [{"id": 9, "type": "RIFE VFI", "mode": 0}]}
        shape = {"postprocess": {"interpolate": False}}
        changes = apply_shape_postprocess_ui(wf, shape)
        self.assertEqual(wf["nodes"][0]["mode"], 2)
        self.assertIn("applied", changes)


if __name__ == "__main__":
    unittest.main()
