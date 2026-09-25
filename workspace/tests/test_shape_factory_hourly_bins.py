#!/usr/bin/env python3
"""Tests for hourly seed-stills curation bins (Phase 0/1)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from shape_factory_hourly_bins import (
    DEFAULT_BIN_ID,
    DEFAULT_POOL_FAMILY,
    bin_summary,
    ensure_steer_bins_for_source_stills,
    load_bins_doc,
    load_pipes_doc,
    resolve_bin_id_for_family,
    set_bin_item,
    still_bin_bias_mult,
)


class HourlyBinsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        (self.root / "shape_factory").mkdir()

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_default_docs_seeded(self) -> None:
        bins = load_bins_doc(self.root)
        self.assertIn(DEFAULT_BIN_ID, bins["bins"])
        pipes = load_pipes_doc(self.root)
        self.assertTrue(any(p.get("bin_id") == DEFAULT_BIN_ID for p in pipes["pipes"]))

    def test_set_item_and_bias(self) -> None:
        cid = "b" * 64
        rel = f"input/SSS{cid}deadbeef.jpeg"
        set_bin_item(
            bin_id=DEFAULT_BIN_ID,
            content_id=cid,
            status="keep",
            relpath=rel,
            surface="test",
            data_root=self.root,
        )
        self.assertGreaterEqual(still_bin_bias_mult(rel, family=DEFAULT_POOL_FAMILY, data_root=self.root), 8.0)
        self.assertEqual(still_bin_bias_mult(rel, family="Unrelated", data_root=self.root), 1.0)
        set_bin_item(
            bin_id=DEFAULT_BIN_ID,
            content_id=cid,
            status="pin",
            relpath=rel,
            surface="test",
            data_root=self.root,
        )
        self.assertGreaterEqual(still_bin_bias_mult(rel, family=DEFAULT_POOL_FAMILY, data_root=self.root), 16.0)
        set_bin_item(
            bin_id=DEFAULT_BIN_ID,
            content_id=cid,
            status="out",
            relpath=rel,
            surface="test",
            data_root=self.root,
        )
        self.assertEqual(still_bin_bias_mult(rel, family=DEFAULT_POOL_FAMILY, data_root=self.root), 0.0)
        ledger = (self.root / "shape_factory" / "hourly_guide" / "decisions.jsonl").read_text()
        self.assertEqual(ledger.count("\n"), 3)
        summary = bin_summary(data_root=self.root)
        self.assertEqual(summary["counts"]["out"], 1)

    def test_invalid_status(self) -> None:
        with self.assertRaises(ValueError):
            set_bin_item(
                bin_id=DEFAULT_BIN_ID,
                content_id="c" * 64,
                status="maybe",
                relpath="input/x.jpeg",
                data_root=self.root,
            )

    def test_ensure_steer_bin_per_source_still_family(self) -> None:
        pools = self.root / "pools"
        for fam in ("AlphaStill", "BetaStill"):
            d = pools / fam
            d.mkdir(parents=True)
            (d / "pools.yaml").write_text(
                "schema_version: comfyui-runpod.pools.v0\n"
                "pools:\n"
                "  source_still:\n"
                "    slot: source_still\n"
                "    members: []\n",
                encoding="utf-8",
            )
        # Video-only family should not get a still steer bin.
        v = pools / "VideoOnly"
        v.mkdir(parents=True)
        (v / "pools.yaml").write_text(
            "pools:\n  source_video:\n    slot: source_video\n    members: []\n",
            encoding="utf-8",
        )
        created = ensure_steer_bins_for_source_stills(data_root=self.root)
        self.assertIn("steer-still-AlphaStill", created)
        self.assertIn("steer-still-BetaStill", created)
        self.assertEqual(resolve_bin_id_for_family("AlphaStill", data_root=self.root), "steer-still-AlphaStill")
        self.assertIsNone(resolve_bin_id_for_family("VideoOnly", data_root=self.root))
        cid = "a" * 64
        rel = f"input/{cid}.jpeg"
        set_bin_item(
            bin_id="steer-still-AlphaStill",
            content_id=cid,
            status="pin",
            relpath=rel,
            surface="test",
            data_root=self.root,
        )
        self.assertGreaterEqual(still_bin_bias_mult(rel, family="AlphaStill", data_root=self.root), 16.0)
        self.assertEqual(still_bin_bias_mult(rel, family="BetaStill", data_root=self.root), 1.0)

    def test_split_legacy_shared_workflow_families(self) -> None:
        """Former multi-family bin becomes 1:1; orphans get empty bins (all New)."""
        doc = load_bins_doc(self.root)
        row = doc["bins"][DEFAULT_BIN_ID]
        row["workflow_families"] = [DEFAULT_POOL_FAMILY, "BounceDanceA", "FB9-FaceBlast"]
        row["items"] = [
            {
                "content_id": "d" * 64,
                "relpath": f"input/{'d'*64}.jpeg",
                "status": "keep",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ]
        from shape_factory_hourly_bins import bins_path, _atomic_write_json

        _atomic_write_json(bins_path(self.root), doc)
        # Fake pools so discover finds BounceDanceA / FaceBlast / default.
        for fam in (DEFAULT_POOL_FAMILY, "BounceDanceA", "FB9-FaceBlast"):
            d = self.root / "pools" / fam
            d.mkdir(parents=True)
            (d / "pools.yaml").write_text(
                "pools:\n  source_still:\n    slot: source_still\n    members: []\n",
                encoding="utf-8",
            )
        created = ensure_steer_bins_for_source_stills(data_root=self.root)
        self.assertIn("steer-still-BounceDanceA", created)
        self.assertIn("steer-still-FB9-FaceBlast", created)
        narrowed = load_bins_doc(self.root)["bins"][DEFAULT_BIN_ID]
        self.assertEqual(narrowed["workflow_families"], [DEFAULT_POOL_FAMILY])
        bounce = load_bins_doc(self.root)["bins"]["steer-still-BounceDanceA"]
        self.assertEqual(bounce["pool_family"], "BounceDanceA")
        self.assertEqual(bounce["workflow_families"], ["BounceDanceA"])
        # New family bins start empty — decisions do not inherit across pools.
        self.assertEqual(bounce.get("items") or [], [])
        self.assertEqual(resolve_bin_id_for_family("BounceDanceA", data_root=self.root), "steer-still-BounceDanceA")
        self.assertEqual(resolve_bin_id_for_family(DEFAULT_POOL_FAMILY, data_root=self.root), DEFAULT_BIN_ID)

    def test_clear_inherited_steer_clones(self) -> None:
        from shape_factory_hourly_bins import bins_path, clear_inherited_steer_clones, _atomic_write_json

        doc = load_bins_doc(self.root)
        cid_keep = "e" * 64
        cid_pin = "f" * 64
        shared_items = [
            {"content_id": cid_keep, "relpath": f"input/{cid_keep}.jpeg", "status": "keep", "updated_at": "t"},
            {"content_id": cid_pin, "relpath": f"input/{cid_pin}.jpeg", "status": "pin", "updated_at": "t"},
        ]
        doc["bins"][DEFAULT_BIN_ID]["items"] = [dict(x) for x in shared_items]
        doc["bins"]["steer-still-CloneMe"] = {
            "id": "steer-still-CloneMe",
            "pool_family": "CloneMe",
            "pool_slot": "source_still",
            "workflow_families": ["CloneMe"],
            "items": [dict(x) for x in shared_items],
            "updated_at": "t",
        }
        # Diverged bin: mostly different statuses — keep.
        doc["bins"]["steer-still-Diverged"] = {
            "id": "steer-still-Diverged",
            "pool_family": "Diverged",
            "pool_slot": "source_still",
            "workflow_families": ["Diverged"],
            "items": [
                {"content_id": cid_keep, "relpath": f"input/{cid_keep}.jpeg", "status": "out", "updated_at": "t"},
                {"content_id": cid_pin, "relpath": f"input/{cid_pin}.jpeg", "status": "later", "updated_at": "t"},
            ],
            "updated_at": "t",
        }
        _atomic_write_json(bins_path(self.root), doc)
        cleared = clear_inherited_steer_clones(data_root=self.root)
        self.assertIn("steer-still-CloneMe", cleared)
        self.assertNotIn("steer-still-Diverged", cleared)
        bins = load_bins_doc(self.root)["bins"]
        self.assertEqual(bins["steer-still-CloneMe"].get("items") or [], [])
        self.assertEqual(len(bins["steer-still-Diverged"].get("items") or []), 2)
        self.assertEqual(len(bins[DEFAULT_BIN_ID].get("items") or []), 2)

    def test_clear_bin_steering(self) -> None:
        from shape_factory_hourly_bins import clear_bin_steering

        cid = "c" * 64
        set_bin_item(
            bin_id=DEFAULT_BIN_ID,
            content_id=cid,
            status="keep",
            relpath=f"input/{cid}.jpeg",
            surface="test",
            data_root=self.root,
        )
        self.assertEqual(bin_summary(data_root=self.root)["item_count"], 1)
        out = clear_bin_steering(bin_id=DEFAULT_BIN_ID, surface="test", data_root=self.root)
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("cleared"), 1)
        self.assertEqual(bin_summary(data_root=self.root)["item_count"], 0)
        self.assertEqual(still_bin_bias_mult(f"input/{cid}.jpeg", family=DEFAULT_POOL_FAMILY, data_root=self.root), 1.0)

    def test_clear_single_item_back_to_new(self) -> None:
        cid = "1" * 64
        rel = f"input/{cid}.jpeg"
        set_bin_item(
            bin_id=DEFAULT_BIN_ID,
            content_id=cid,
            status="pin",
            relpath=rel,
            surface="test",
            data_root=self.root,
        )
        self.assertGreaterEqual(still_bin_bias_mult(rel, family=DEFAULT_POOL_FAMILY, data_root=self.root), 16.0)
        out = set_bin_item(
            bin_id=DEFAULT_BIN_ID,
            content_id=cid,
            status="clear",
            relpath=rel,
            surface="test",
            data_root=self.root,
        )
        self.assertTrue(out.get("ok"))
        self.assertEqual((out.get("decision") or {}).get("action"), "clear")
        self.assertEqual(bin_summary(data_root=self.root)["item_count"], 0)
        self.assertEqual(still_bin_bias_mult(rel, family=DEFAULT_POOL_FAMILY, data_root=self.root), 1.0)


if __name__ == "__main__":
    unittest.main()
