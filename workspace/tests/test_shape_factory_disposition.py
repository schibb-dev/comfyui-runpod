#!/usr/bin/env python3
"""Tests for shape_factory_disposition."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from shape_factory_disposition import (
    compute_disposition_promotions,
    is_retired_disposition,
    load_seed_catalog,
    merge_catalog,
    stamp_output_disposition,
    toggle_output_disposition,
    trash_output_media,
)


class DispositionCatalogTests(unittest.TestCase):
    def test_seed_catalog_has_entries(self) -> None:
        cat = load_seed_catalog()
        entries = [m for m in cat.get("markers") or [] if m.get("kind") == "entry"]
        ids = {m["id"] for m in entries}
        self.assertIn("refine", ids)
        self.assertIn("advance", ids)
        self.assertIn("retire", ids)
        self.assertNotIn("derived", ids)

    def test_promotion_explicit_quality_missing_no_auto_promote(self) -> None:
        cat = load_seed_catalog()
        promos = compute_disposition_promotions(
            cat, quality=None, appetite=None, facet="both", explicit_quality_missing=True
        )
        self.assertNotIn("derived", promos["promote"])
        self.assertEqual(promos["promote"], [])

    def test_promotion_high_quality_high_appetite(self) -> None:
        cat = load_seed_catalog()
        promos = compute_disposition_promotions(cat, quality=5.0, appetite="more", facet="both")
        self.assertIn("advance", promos["promote"])

    def test_promotion_low_quality_high_appetite(self) -> None:
        cat = load_seed_catalog()
        promos = compute_disposition_promotions(cat, quality=2.0, appetite="fast_track", facet="source")
        self.assertTrue({"refine", "investigate"}.intersection(promos["promote"]))
        entry_ids = {m["id"] for m in cat.get("markers") or [] if m.get("kind") == "entry"}
        self.assertNotIn("extract", entry_ids)
        step_ids = {m["id"] for m in cat.get("markers") or [] if m.get("kind") == "step"}
        reason_ids = {m["id"] for m in cat.get("markers") or [] if m.get("kind") == "reason"}
        self.assertIn("advance.extend", step_ids)
        self.assertIn("retire.archive", step_ids)
        self.assertIn("retire.trash", step_ids)
        self.assertNotIn("extract.frame", step_ids)
        self.assertNotIn("refine.aspect", step_ids)
        self.assertIn("refine.identity", reason_ids)
        self.assertIn("refine.lighting", reason_ids)
        self.assertIn("refine.other", reason_ids)
        lighting = next(m for m in cat["markers"] if m["id"] == "refine.lighting")
        self.assertEqual(lighting.get("modifier_mode"), "multi")
        self.assertTrue(any(x.get("id") == "bad_shadows" for x in lighting.get("modifiers") or []))


class DispositionIndexTests(unittest.TestCase):
    def test_toggle_entry_replaces_other_entries(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            og = root / "og"
            og.mkdir()
            media = og / "clip.mp4"
            media.write_bytes(b"fake")
            idx = root / "_status" / "disposition_index.json"
            cat = load_seed_catalog()
            toggle_output_disposition(
                media_abs=media,
                media_relpath="og/clip.mp4",
                marker_id="refine",
                on=True,
                og_root=og,
                disposition_index_path=idx,
                catalog=cat,
            )
            saved = toggle_output_disposition(
                media_abs=media,
                media_relpath="og/clip.mp4",
                marker_id="investigate",
                on=True,
                og_root=og,
                disposition_index_path=idx,
                catalog=cat,
            )
            self.assertEqual(saved["markers"], ["investigate"])

    def test_reason_auto_sets_refine_and_stores_modifiers(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            og = root / "og"
            og.mkdir()
            media = og / "clip.mp4"
            media.write_bytes(b"fake")
            idx = root / "_status" / "disposition_index.json"
            cat = load_seed_catalog()
            saved = toggle_output_disposition(
                media_abs=media,
                media_relpath="og/clip.mp4",
                marker_id="refine.activity",
                on=True,
                modifiers=["too_busy", "too_still"],
                og_root=og,
                disposition_index_path=idx,
                catalog=cat,
            )
            self.assertIn("refine", saved["markers"])
            self.assertIn("refine.activity", saved["markers"])
            # exclusive → last wins
            self.assertEqual(saved["reason_detail"]["refine.activity"]["modifiers"], ["too_still"])

            lighting = toggle_output_disposition(
                media_abs=media,
                media_relpath="og/clip.mp4",
                marker_id="refine.lighting",
                on=True,
                modifiers=["dark", "bad_shadows"],
                og_root=og,
                disposition_index_path=idx,
                catalog=cat,
            )
            self.assertEqual(
                set(lighting["reason_detail"]["refine.lighting"]["modifiers"]),
                {"dark", "bad_shadows"},
            )

    def test_other_requires_note(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            og = root / "og"
            og.mkdir()
            media = og / "clip.mp4"
            media.write_bytes(b"fake")
            idx = root / "_status" / "disposition_index.json"
            cat = load_seed_catalog()
            with self.assertRaises(ValueError):
                toggle_output_disposition(
                    media_abs=media,
                    media_relpath="og/clip.mp4",
                    marker_id="refine.other",
                    on=True,
                    og_root=og,
                    disposition_index_path=idx,
                    catalog=cat,
                )
            saved = toggle_output_disposition(
                media_abs=media,
                media_relpath="og/clip.mp4",
                marker_id="refine.other",
                on=True,
                note="odd parallax",
                og_root=og,
                disposition_index_path=idx,
                catalog=cat,
            )
            self.assertEqual(saved["reason_detail"]["refine.other"]["note"], "odd parallax")

    def test_clear_refine_clears_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            og = root / "og"
            og.mkdir()
            media = og / "clip.mp4"
            media.write_bytes(b"fake")
            idx = root / "_status" / "disposition_index.json"
            cat = load_seed_catalog()
            toggle_output_disposition(
                media_abs=media,
                media_relpath="og/clip.mp4",
                marker_id="refine.artifacts",
                on=True,
                og_root=og,
                disposition_index_path=idx,
                catalog=cat,
            )
            cleared = toggle_output_disposition(
                media_abs=media,
                media_relpath="og/clip.mp4",
                marker_id="refine",
                on=False,
                og_root=og,
                disposition_index_path=idx,
                catalog=cat,
            )
            self.assertNotIn("refine", cleared["markers"])
            self.assertNotIn("refine.artifacts", cleared["markers"])
            self.assertEqual(cleared.get("reason_detail") or {}, {})

    def test_stamp_disposition_entry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            og = root / "og"
            og.mkdir()
            media = og / "pred.mp4"
            media.write_bytes(b"fake")
            idx = root / "_status" / "disposition_index.json"
            cat = load_seed_catalog()
            saved = stamp_output_disposition(
                media_abs=media,
                marker_id="investigate",
                note="needs a look",
                og_root=og,
                disposition_index_path=idx,
                catalog=cat,
                media_relpath="og/pred.mp4",
            )
            self.assertIn("investigate", saved["markers"])
            doc = json.loads(idx.read_text(encoding="utf-8"))
            row = next(iter(doc["by_output_relpath"].values()))
            self.assertEqual(row["markers"], ["investigate"])
            self.assertEqual(row["notes"].get("investigate"), "needs a look")

    def test_trash_moves_companion(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            og = root / "og"
            og.mkdir()
            media = og / "clip.mp4"
            media.write_bytes(b"fake")
            xmp = og / "clip.XMP"
            xmp.write_text("xmp", encoding="utf-8")
            out = trash_output_media(media, og_root=og)
            self.assertTrue(out["ok"])
            self.assertFalse(media.exists())
            self.assertFalse(xmp.exists())


class DispositionRetireTests(unittest.TestCase):
    def test_is_retired(self) -> None:
        self.assertTrue(is_retired_disposition(["retire", "park"]))
        self.assertFalse(is_retired_disposition(["refine"]))


if __name__ == "__main__":
    unittest.main()
