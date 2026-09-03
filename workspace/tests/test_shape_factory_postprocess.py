"""Tests for generation editorial policy (ColorMatch, VHS_MergeImages)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

GEX2_CATALOG = Path(
    "/home/yuji/comfyui-runpod-data/comfyui_user/default/workflows/generated/catalog/FB9_GEX2-readable.json"
)
KNEEL_CATALOG = Path(
    "/home/yuji/comfyui-runpod-data/comfyui_user/default/workflows/generated/catalog/X-KNEEL-FB9-readable.json"
)


def _modes(workflow: dict, ntype: str) -> list[int]:
    return [int(n.get("mode") or 0) for n in workflow.get("nodes") or [] if n.get("type") == ntype]


class EditorialApplyTests(unittest.TestCase):
    def test_no_block_is_noop(self) -> None:
        from shape_factory_generation_editorial import apply_shape_editorial_ui

        wf = {"nodes": [{"id": 1, "type": "ColorMatch", "mode": 0}]}
        self.assertEqual(apply_shape_editorial_ui(wf, {}), {})
        self.assertEqual(wf["nodes"][0]["mode"], 0)

    def test_stripped_catalog_has_no_delivery_nodes(self) -> None:
        if not KNEEL_CATALOG.is_file():
            self.skipTest("catalog missing")
        wf = json.loads(KNEEL_CATALOG.read_text(encoding="utf-8"))
        types = {n.get("type") for n in wf.get("nodes") or []}
        self.assertNotIn("ImageUpscaleWithModel", types)
        self.assertNotIn("RIFE VFI", types)
        self.assertIn("ColorMatch", types)

    def test_gex_extend_keeps_color_match_and_merge(self) -> None:
        if not GEX2_CATALOG.is_file():
            self.skipTest("catalog missing")
        from shape_factory_generation_editorial import apply_shape_editorial_ui

        wf = json.loads(GEX2_CATALOG.read_text(encoding="utf-8"))
        shape = {
            "postprocess": {
                "profile_id": "gex-extend-default",
                "color_match": True,
                "merge_frames": True,
            }
        }
        apply_shape_editorial_ui(wf, shape)
        self.assertEqual(_modes(wf, "ColorMatch"), [0])
        self.assertEqual(_modes(wf, "VHS_MergeImages"), [0])

    def test_infer_editorial_from_workflow(self) -> None:
        from shape_factory_generation_editorial import infer_editorial_from_workflow

        wf = {
            "nodes": [
                {"type": "ColorMatch", "mode": 0},
                {"type": "VHS_MergeImages", "mode": 2},
            ]
        }
        got = infer_editorial_from_workflow(wf)
        self.assertTrue(got["color_match"])
        self.assertFalse(got["merge_frames"])

    def test_shape_factory_wrapper_delegates(self) -> None:
        from shape_factory import apply_shape_postprocess_ui

        wf = {"nodes": [{"id": 9, "type": "VHS_MergeImages", "mode": 0}]}
        shape = {"postprocess": {"merge_frames": False}}
        changes = apply_shape_postprocess_ui(wf, shape)
        self.assertEqual(wf["nodes"][0]["mode"], 2)
        self.assertIn("applied", changes)


if __name__ == "__main__":
    unittest.main()
