#!/usr/bin/env python3
"""Tests for Phase 2 delivery postprocess shape and catalog."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

REPO = Path(__file__).resolve().parents[2]
CATALOG = Path(
    "/home/yuji/comfyui-runpod-data/comfyui_user/default/workflows/generated/catalog"
)
DELIVERY_STEM = "wan-delivery-postprocess"


class DeliveryCatalogTests(unittest.TestCase):
    def test_combined_topology(self) -> None:
        from build_delivery_catalogs import build_delivery_catalog

        wf = build_delivery_catalog(prefix="test/FINAL")
        types = {n["type"] for n in wf["nodes"]}
        self.assertEqual(
            types,
            {
                "VHS_LoadVideoPath",
                "ColorMatch",
                "UpscaleModelLoader",
                "ImageUpscaleWithModel",
                "RIFE VFI",
                "VHS_VideoCombine",
            },
        )
        self.assertEqual(len(wf["links"]), 6)

    def test_optional_nodes_default_bypass(self) -> None:
        from build_delivery_catalogs import build_delivery_catalog

        wf = build_delivery_catalog(prefix="test/FINAL")
        optional = {
            n["type"]: int(n.get("mode") or 0)
            for n in wf["nodes"]
            if n["type"]
            in ("ColorMatch", "UpscaleModelLoader", "ImageUpscaleWithModel", "RIFE VFI")
        }
        self.assertEqual(optional, dict.fromkeys(optional, 2))

    def test_enrolled_graph_hash_matches_catalog(self) -> None:
        import yaml
        from shape_factory_vocab import graph_fingerprint_topology

        shape_path = REPO / ".data" / "shapes" / "delivery" / f"{DELIVERY_STEM}.shape.yaml"
        catalog_path = CATALOG / f"{DELIVERY_STEM}-readable.json"
        if not shape_path.is_file() or not catalog_path.is_file():
            self.skipTest("missing delivery artifacts")
        shape = yaml.safe_load(shape_path.read_text(encoding="utf-8"))
        wf = json.loads(catalog_path.read_text(encoding="utf-8"))
        got = graph_fingerprint_topology(wf, aliases=False)
        self.assertEqual(got, shape["graph_hash"])


class DeliveryApplyTests(unittest.TestCase):
    def test_apply_toggles_modes(self) -> None:
        from build_delivery_catalogs import build_delivery_catalog
        from shape_factory_delivery_postprocess import apply_shape_delivery_ui

        wf = build_delivery_catalog(prefix="test/FINAL")
        shape = {
            "chain_role": "denouement",
            "delivery": {
                "color_match": True,
                "upscale": False,
                "interpolate": True,
            },
        }
        changes = apply_shape_delivery_ui(wf, shape)
        self.assertIn("applied", changes)
        modes = {n["type"]: int(n.get("mode") or 0) for n in wf["nodes"]}
        self.assertEqual(modes["ColorMatch"], 0)
        self.assertEqual(modes["ImageUpscaleWithModel"], 2)
        self.assertEqual(modes["UpscaleModelLoader"], 2)
        self.assertEqual(modes["RIFE VFI"], 0)

    def test_shape_factory_routes_denouement_to_delivery(self) -> None:
        from shape_factory import apply_shape_postprocess_ui

        wf = {
            "nodes": [
                {"id": 15, "type": "ColorMatch", "mode": 2},
                {"id": 30, "type": "RIFE VFI", "mode": 2},
            ]
        }
        shape = {
            "chain_role": "denouement",
            "delivery": {"interpolate": True},
        }
        apply_shape_postprocess_ui(wf, shape)
        self.assertEqual(wf["nodes"][1]["mode"], 0)

    def test_generation_shape_still_uses_editorial(self) -> None:
        from shape_factory import apply_shape_postprocess_ui

        wf = {"nodes": [{"id": 9, "type": "VHS_MergeImages", "mode": 0}]}
        shape = {
            "chain_role": "extend",
            "postprocess": {"merge_frames": False},
        }
        apply_shape_postprocess_ui(wf, shape)
        self.assertEqual(wf["nodes"][0]["mode"], 2)


class DeliveryShapeEnrollmentTests(unittest.TestCase):
    def test_delivery_shape_vocab(self) -> None:
        import yaml
        from shape_factory_vocab import validate_shape_vocab

        path = REPO / ".data" / "shapes" / "delivery" / f"{DELIVERY_STEM}.shape.yaml"
        if not path.is_file():
            self.skipTest("no delivery shape")
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        errs = validate_shape_vocab(doc)
        self.assertEqual(errs, [], msg=errs)
        self.assertEqual(doc.get("chain_role"), "denouement")
        self.assertIsInstance(doc.get("delivery"), dict)


if __name__ == "__main__":
    unittest.main()
