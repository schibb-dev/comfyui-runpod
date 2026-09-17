#!/usr/bin/env python3
"""Byte-hash still accounting: aliases + duplicate inode groups."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401

from still_content_account import account, apply_hardlinks, extract_name_hex


class StillContentAccountTests(unittest.TestCase):
    def test_extract_name_hex_is_hint_only(self) -> None:
        self.assertEqual(
            extract_name_hex("SSS" + ("a" * 64) + ".jpeg"),
            "a" * 64,
        )
        self.assertIsNone(extract_name_hex("camille-rowe.jpg"))
        self.assertIsNone(extract_name_hex("0574e2c1b151d3ca3d31dbf063b22f09.jpg"))

    def test_scan_groups_byte_duplicates_and_keeps_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "input"
            data = Path(td) / "data"
            (data / "shape_factory").mkdir(parents=True)
            root.mkdir()
            blob = b"\xff\xd8" + b"same-bytes" * 40
            (root / "alice.jpg").write_bytes(blob)
            (root / "bob.jpg").write_bytes(blob)
            (root / "unique.webp").write_bytes(b"RIFF" + b"other" * 20)
            named = "a" * 64 + ".jpg"
            (root / named).write_bytes(b"not-that-hash")
            db = data / "shape_factory" / "still_content_accounting.sqlite"
            out = account(input_root=root, data_root=data, db_path=db, workers=2)
            self.assertEqual(out["files"], 4)
            self.assertEqual(out["unique_content_ids"], 3)
            self.assertEqual(out["duplicate_content_n_inodes_gt1"], 1)
            self.assertEqual(out["extra_inodes_to_collapse"], 1)
            con = sqlite3.connect(str(db))
            try:
                n_paths = con.execute(
                    "SELECT n_paths FROM contents WHERE n_inodes > 1"
                ).fetchone()[0]
                self.assertEqual(n_paths, 2)
                statuses = {
                    Path(r[0]).name: r[1]
                    for r in con.execute("SELECT path, name_hex_status FROM paths")
                }
            finally:
                con.close()
            self.assertEqual(statuses[named], "mismatch")
            self.assertEqual(statuses["alice.jpg"], "absent")
            dry = apply_hardlinks(db_path=db, dry_run=True)
            self.assertEqual(dry["would_link"], 1)
            applied = apply_hardlinks(db_path=db, dry_run=False)
            self.assertEqual(applied["linked"], 1)
            self.assertTrue((root / "alice.jpg").is_file())
            self.assertTrue((root / "bob.jpg").is_file())
            self.assertEqual((root / "alice.jpg").read_bytes(), (root / "bob.jpg").read_bytes())
            sa = (root / "alice.jpg").stat()
            sb = (root / "bob.jpg").stat()
            self.assertEqual((sa.st_dev, sa.st_ino), (sb.st_dev, sb.st_ino))

    def test_hardlink_replace_keeps_dest_if_canonical_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "keep.jpg"
            dest.write_bytes(b"payload")
            from still_content_account import replace_path_with_hardlink

            with self.assertRaises(OSError):
                replace_path_with_hardlink(Path(td) / "missing.jpg", dest)
            self.assertEqual(dest.read_bytes(), b"payload")


if __name__ == "__main__":
    unittest.main()
