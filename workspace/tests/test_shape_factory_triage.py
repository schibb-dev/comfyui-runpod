#!/usr/bin/env python3
"""Tests for shape_factory_triage."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from shape_factory_triage import (
    default_triage_index_path,
    needs_triage_item,
    record_triage_pass,
)


class TriageTests(unittest.TestCase):
    def test_never_triaged_needs_triage(self) -> None:
        item = {"relpath": "output/og/2026-04-03/foo.mp4"}
        self.assertTrue(needs_triage_item(item, triage_doc={"by_output_relpath": {}}))

    def test_triaged_without_disposition_out_of_pool(self) -> None:
        item = {"relpath": "output/og/2026-04-03/foo.mp4"}
        triage_doc = {
            "by_output_relpath": {
                "output/og/2026-04-03/foo.mp4": {
                    "last_triaged_at": "2026-07-08T20:00:00+00:00",
                    "pass_count": 1,
                }
            }
        }
        self.assertFalse(needs_triage_item(item, triage_doc=triage_doc, disposition_doc={"by_output_relpath": {}}))

    def test_disposition_change_reopens_triage(self) -> None:
        item = {"relpath": "output/og/2026-04-03/foo.mp4"}
        triage_doc = {
            "by_output_relpath": {
                "output/og/2026-04-03/foo.mp4": {
                    "last_triaged_at": "2026-07-08T20:00:00+00:00",
                    "pass_count": 1,
                }
            }
        }
        disposition_doc = {
            "by_output_relpath": {
                "output/og/2026-04-03/foo.mp4": {
                    "markers": ["refine"],
                    "updated_at": "2026-07-08T21:00:00+00:00",
                }
            }
        }
        self.assertTrue(needs_triage_item(item, triage_doc=triage_doc, disposition_doc=disposition_doc))

    def test_retire_never_needs_triage(self) -> None:
        item = {"relpath": "output/og/2026-04-03/foo.mp4"}
        disposition_doc = {
            "by_output_relpath": {
                "output/og/2026-04-03/foo.mp4": {
                    "markers": ["retire"],
                    "updated_at": "2026-07-08T21:00:00+00:00",
                }
            }
        }
        self.assertFalse(needs_triage_item(item, triage_doc={}, disposition_doc=disposition_doc))

    def test_record_triage_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            og_root = root / "og"
            og_root.mkdir()
            media = og_root / "clip.mp4"
            media.write_bytes(b"fake")
            idx_path = default_triage_index_path(og_root)
            saved = record_triage_pass(
                media_abs=media,
                media_relpath="og/clip.mp4",
                og_root=og_root,
                triage_index_path=idx_path,
                disposition_doc={"by_output_relpath": {}},
            )
            self.assertEqual(saved["pass_count"], 1)
            import json

            triage_doc = json.loads(idx_path.read_text(encoding="utf-8"))
            self.assertFalse(
                needs_triage_item(
                    {"relpath": "og/clip.mp4"},
                    triage_doc=triage_doc,
                    disposition_doc={"by_output_relpath": {}},
                )
            )


if __name__ == "__main__":
    unittest.main()
