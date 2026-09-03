"""Tests for strip_delivery_postprocess.py"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

KNEEL = Path(
    "/home/yuji/comfyui-runpod-data/comfyui_user/default/workflows/generated/catalog/X-KNEEL-FB9-readable.json"
)
KNEEL_BAK = KNEEL.with_suffix(KNEEL.suffix + ".pre-delivery-strip.bak")
GEX2 = Path(
    "/home/yuji/comfyui-runpod-data/comfyui_user/default/workflows/generated/catalog/FB9_GEX2-readable.json"
)
GEX2_BAK = GEX2.with_suffix(GEX2.suffix + ".pre-delivery-strip.bak")


class StripDeliveryPostprocessTests(unittest.TestCase):
    def test_origin_strip_removes_delivery_nodes(self) -> None:
        if not KNEEL_BAK.is_file():
            self.skipTest("pre-strip backup missing")
        from strip_delivery_postprocess import strip_delivery_postprocess

        wf = json.loads(KNEEL_BAK.read_text(encoding="utf-8"))
        stripped, removed = strip_delivery_postprocess(wf, mode="origin")
        types = {n.get("type") for n in stripped.get("nodes") or []}
        self.assertGreaterEqual(len(removed), 10)
        self.assertNotIn("ImageUpscaleWithModel", types)
        self.assertNotIn("RIFE VFI", types)
        self.assertNotIn("UpscaleModelLoader", types)
        self.assertIn("ColorMatch", types)

    def test_stripped_kneel_keeps_active_combine(self) -> None:
        if not KNEEL.is_file():
            self.skipTest("catalog missing")
        wf = json.loads(KNEEL.read_text(encoding="utf-8"))
        active = [
            n
            for n in wf.get("nodes") or []
            if n.get("type") == "VHS_VideoCombine" and int(n.get("mode") or 0) == 0
        ]
        self.assertTrue(active)
        images_link = None
        for inp in active[0].get("inputs") or []:
            if inp.get("name") == "images":
                images_link = inp.get("link")
        self.assertIsNotNone(images_link)

    def test_extend_strip_removes_bypassers_only(self) -> None:
        if not GEX2_BAK.is_file():
            self.skipTest("pre-strip backup missing")
        from strip_delivery_postprocess import strip_delivery_postprocess

        wf = json.loads(GEX2_BAK.read_text(encoding="utf-8"))
        stripped, removed = strip_delivery_postprocess(wf, mode="extend")
        self.assertEqual(len(removed), 2)
        for n in stripped.get("nodes") or []:
            if n.get("type") == "Fast Groups Bypasser (rgthree)":
                mt = (n.get("properties") or {}).get("matchTitle") or ""
                self.assertNotIn(mt, {"Upscaler", "Interpolation"})
        self.assertIn("VHS_MergeImages", {n.get("type") for n in stripped.get("nodes") or []})


if __name__ == "__main__":
    unittest.main()
