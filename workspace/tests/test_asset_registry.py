#!/usr/bin/env python3
"""Tests for asset_registry (content-addressed SQLite asset registry)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import asset_registry as areg


class AssetRegistryTests(unittest.TestCase):
    def test_kind_for_ext(self) -> None:
        self.assertEqual(areg.kind_for_ext(".png"), "image")
        self.assertEqual(areg.kind_for_ext("jpg"), "image")
        self.assertEqual(areg.kind_for_ext(".MP4"), "video")
        self.assertEqual(areg.kind_for_ext(".txt"), "other")

    def test_hash_and_register_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            asset = root / "a.png"
            asset.write_bytes(b"hello-bytes")
            con = areg.connect(root / "reg.sqlite")

            cid = areg.register(con, asset, relpath="input/a.png")
            self.assertIsNotNone(cid)
            # Deterministic content hash.
            self.assertEqual(cid, areg.hash_file(asset))

            got = areg.by_content_id(con, cid)
            self.assertEqual(got["current_relpath"], "input/a.png")
            self.assertEqual(got["kind"], "image")
            self.assertEqual(got["status"], "present")

            self.assertEqual(areg.by_relpath(con, "input/a.png")["content_id"], cid)
            names = areg.by_basename(con, "a.png")
            self.assertEqual([n["content_id"] for n in names], [cid])
            con.close()

    def test_moved_history_tracks_relocation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            asset = root / "same.bin"
            asset.write_bytes(b"identical-content")
            con = areg.connect(root / "reg.sqlite")

            cid = areg.register(con, asset, relpath="input/old.bin")
            cid2 = areg.register(con, asset, relpath="input/new.bin")
            self.assertEqual(cid, cid2)  # identity unchanged by move

            row = areg.by_content_id(con, cid)
            self.assertEqual(row["current_relpath"], "input/new.bin")
            self.assertIn("input/old.bin", row["moved_history"])
            con.close()

    def test_refs_merge(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            asset = root / "r.bin"
            asset.write_bytes(b"refme")
            con = areg.connect(root / "reg.sqlite")

            cid = areg.register(con, asset, relpath="r.bin", refs=["job:one"])
            areg.register(con, asset, relpath="r.bin", refs=["job:two"])
            areg.add_ref(con, cid, "job:three")

            row = areg.by_content_id(con, cid)
            self.assertEqual(set(row["refs"]), {"job:one", "job:two", "job:three"})
            con.close()

    def test_unchanged_file_skips_rehash(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            asset = root / "c.png"
            asset.write_bytes(b"cache-me")
            con = areg.connect(root / "reg.sqlite")

            cid = areg.register(con, asset, relpath="input/c.png")

            calls = {"n": 0}
            real = areg.hash_file

            def counting(p, **k):
                calls["n"] += 1
                return real(p, **k)

            areg.hash_file = counting
            try:
                # Unchanged (same size+mtime) -> cache hit, no rehash.
                cid2 = areg.register(con, asset, relpath="input/c.png", refs=["job:x"])
                self.assertEqual(cid2, cid)
                self.assertEqual(calls["n"], 0)
                self.assertIn("job:x", areg.by_content_id(con, cid)["refs"])

                # Content change bumps mtime -> rehash happens.
                import os, time

                asset.write_bytes(b"cache-me-different")
                os.utime(asset, (time.time() + 5, time.time() + 5))
                areg.register(con, asset, relpath="input/c.png", force_rehash=False)
                self.assertEqual(calls["n"], 1)
            finally:
                areg.hash_file = real
            con.close()

    def test_register_missing_file_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            con = areg.connect(root / "reg.sqlite")
            self.assertIsNone(areg.register(con, root / "nope.png", relpath="input/nope.png"))
            con.close()


if __name__ == "__main__":
    unittest.main()
