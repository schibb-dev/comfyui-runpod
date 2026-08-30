#!/usr/bin/env python3
"""Tests for discovery_index_upsert tip-in / ensure."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401
from discovery_index_upsert import (
    ensure_discovery_relpath,
    tip_in_discovery_relpaths,
    upsert_discovery_relpath,
)


class DiscoveryIndexUpsertTests(unittest.TestCase):
    def test_upsert_creates_stem_group_and_ensure_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            media = root / "og" / "2026"
            media.mkdir(parents=True)
            mp4 = media / "clip_a.mp4"
            png = media / "clip_a.png"
            mp4.write_bytes(b"fake-mp4-bytes")
            png.write_bytes(b"fake-png-bytes")
            idx_path = root / "_status" / "discovery_og_wip_index.json"

            first = upsert_discovery_relpath(
                index_path=idx_path,
                output_root=root,
                relpath="og/2026/clip_a.mp4",
            )
            self.assertTrue(first.get("ok"), first)
            self.assertTrue(first.get("created"))
            item = first.get("item") or {}
            self.assertEqual(item.get("group_id"), "og:stem:clip_a")
            self.assertEqual(item.get("video_relpath"), "og/2026/clip_a.mp4")
            self.assertEqual(item.get("thumb_relpath"), "og/2026/clip_a.png")

            disk = json.loads(idx_path.read_text(encoding="utf-8"))
            self.assertEqual(disk.get("item_count"), 1)

            again = ensure_discovery_relpath(
                index_path=idx_path,
                output_root=root,
                relpath="og/2026/clip_a.png",
            )
            self.assertTrue(again.get("ok"), again)
            self.assertTrue(again.get("already_present"))
            self.assertFalse(again.get("created"))

    def test_rejects_non_og_wip_and_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            idx_path = root / "_status" / "discovery_og_wip_index.json"
            bad = upsert_discovery_relpath(
                index_path=idx_path,
                output_root=root,
                relpath="experiments/foo.mp4",
            )
            self.assertFalse(bad.get("ok"))
            self.assertEqual(bad.get("error"), "not_og_or_wip")

            miss = upsert_discovery_relpath(
                index_path=idx_path,
                output_root=root,
                relpath="og/nope.mp4",
            )
            self.assertFalse(miss.get("ok"))
            self.assertEqual(miss.get("error"), "file_missing")

    def test_tip_in_batch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "wip").mkdir()
            p = root / "wip" / "x.webm"
            p.write_bytes(b"webm")
            idx_path = root / "_status" / "discovery_og_wip_index.json"
            tip = tip_in_discovery_relpaths(
                index_path=idx_path,
                output_root=root,
                relpaths=["wip/x.webm", "og/missing.mp4"],
            )
            self.assertTrue(tip.get("ok"))
            self.assertEqual(tip.get("ok_count"), 1)
            self.assertEqual(tip.get("created_count"), 1)


if __name__ == "__main__":
    unittest.main()
