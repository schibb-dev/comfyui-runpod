#!/usr/bin/env python3
"""Tests for the thin input-still catalog used by hourly / pool globs."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path


class InputStillCatalogTests(unittest.TestCase):
    def test_bootstrap_uses_mtime_then_new_files_use_wall_clock(self) -> None:
        from input_still_catalog import list_recent_stills, load_first_seen_map, scan_input_stills

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "input"
            root.mkdir()
            old = root / "old.png"
            old.write_bytes(b"old")
            now = time.time()
            os.utime(old, (now - 30 * 86400, now - 30 * 86400))
            cat = Path(td) / "cat.sqlite"

            first = scan_input_stills(input_root=root, catalog_path=cat, now_ts=now)
            self.assertTrue(first["ok"])
            self.assertEqual(first["inserted"], 1)
            seen = load_first_seen_map(cat)
            self.assertAlmostEqual(seen[str(old.resolve())], now - 30 * 86400, delta=2)

            new = root / "new.png"
            new.write_bytes(b"new")
            later = now + 10
            os.utime(new, (later - 90 * 86400, later - 90 * 86400))  # preserved ancient mtime
            # Parent dir mtime must change so the incremental scan notices the new file.
            os.utime(root, (later, later))
            second = scan_input_stills(input_root=root, catalog_path=cat, now_ts=later)
            self.assertEqual(second["inserted"], 1)
            seen = load_first_seen_map(cat)
            self.assertAlmostEqual(seen[str(new.resolve())], later, delta=1)
            recent = list_recent_stills(catalog_path=cat, exts=[".png"], limit=10)
            self.assertEqual(recent[0].name, "new.png")

    def test_skips_factory_and_unchanged_dir_keeps_rows(self) -> None:
        from input_still_catalog import load_first_seen_map, scan_input_stills

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "input"
            factory = root / "_factory"
            factory.mkdir(parents=True)
            keep = root / "keep.jpg"
            keep.write_bytes(b"keep")
            (factory / "staged.png").write_bytes(b"staged")
            cat = Path(td) / "cat.sqlite"
            now = time.time()
            scan_input_stills(input_root=root, catalog_path=cat, now_ts=now)
            seen = load_first_seen_map(cat)
            self.assertEqual(len(seen), 1)
            self.assertIn(str(keep.resolve()), seen)

            again = scan_input_stills(input_root=root, catalog_path=cat, now_ts=now + 5)
            self.assertGreaterEqual(again["dirs_skipped"], 1)
            self.assertEqual(again["inserted"], 0)
            self.assertEqual(len(load_first_seen_map(cat)), 1)

    def test_resolve_glob_uses_catalog_for_input_stills(self) -> None:
        from input_still_catalog import scan_input_stills
        from shape_factory import resolve_glob

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "input"
            root.mkdir()
            a = root / "a.png"
            b = root / "b.png"
            a.write_bytes(b"a")
            b.write_bytes(b"b")
            now = time.time()
            os.utime(a, (now - 100, now - 100))
            os.utime(b, (now - 10, now - 10))
            cat = Path(td) / "cat.sqlite"
            scan_input_stills(input_root=root, catalog_path=cat, now_ts=now)
            prev = os.environ.get("HOURLY_INPUT_STILL_CATALOG_PATH")
            try:
                os.environ["HOURLY_INPUT_STILL_CATALOG_PATH"] = str(cat)
                paths = resolve_glob({"glob": str(root / "**" / "*.png"), "sort": "mtime_desc", "limit": 10})
                self.assertEqual([p.name for p in paths], ["b.png", "a.png"])
            finally:
                if prev is None:
                    os.environ.pop("HOURLY_INPUT_STILL_CATALOG_PATH", None)
                else:
                    os.environ["HOURLY_INPUT_STILL_CATALOG_PATH"] = prev

    def test_hourly_recency_prefers_catalog_first_seen(self) -> None:
        from input_still_catalog import scan_input_stills
        from shape_factory_hourly import _still_recency_mult

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "input"
            root.mkdir()
            still = root / "fresh.png"
            still.write_bytes(b"x")
            now = time.time()
            os.utime(still, (now - 40 * 86400, now - 40 * 86400))
            cat = Path(td) / "cat.sqlite"
            # Pretend catalog already bootstrapped so first_seen=now despite old mtime.
            scan_input_stills(input_root=root, catalog_path=cat, now_ts=now - 100)
            # Force a second insert path: mark bootstrapped then add another file.
            later_file = root / "later.png"
            later_file.write_bytes(b"y")
            os.utime(later_file, (now - 40 * 86400, now - 40 * 86400))
            os.utime(root, (now, now))
            scan_input_stills(input_root=root, catalog_path=cat, now_ts=now)

            prev = os.environ.get("HOURLY_INPUT_STILL_CATALOG_PATH")
            prev_b = os.environ.get("HOURLY_RECENT_STILL_BOOST")
            prev_d = os.environ.get("HOURLY_RECENT_STILL_DAYS")
            try:
                os.environ["HOURLY_INPUT_STILL_CATALOG_PATH"] = str(cat)
                os.environ["HOURLY_RECENT_STILL_BOOST"] = "4.0"
                os.environ["HOURLY_RECENT_STILL_DAYS"] = "14"
                import shape_factory_hourly as hourly

                hourly._FIRST_SEEN_CACHE = None
                self.assertGreater(_still_recency_mult(str(later_file.resolve()), now_ts=now), 3.0)
                # Bootstrap row keeps mtime-based first_seen → no recency boost.
                self.assertEqual(_still_recency_mult(str(still.resolve()), now_ts=now), 1.0)
            finally:
                if prev is None:
                    os.environ.pop("HOURLY_INPUT_STILL_CATALOG_PATH", None)
                else:
                    os.environ["HOURLY_INPUT_STILL_CATALOG_PATH"] = prev
                if prev_b is None:
                    os.environ.pop("HOURLY_RECENT_STILL_BOOST", None)
                else:
                    os.environ["HOURLY_RECENT_STILL_BOOST"] = prev_b
                if prev_d is None:
                    os.environ.pop("HOURLY_RECENT_STILL_DAYS", None)
                else:
                    os.environ["HOURLY_RECENT_STILL_DAYS"] = prev_d
                import shape_factory_hourly as hourly

                hourly._FIRST_SEEN_CACHE = None


class DownloadCopySuffixTests(unittest.TestCase):
    def test_strip_download_copy_suffix(self) -> None:
        from input_still_catalog import download_copy_name_candidates, strip_download_copy_suffix

        h = "eada631b1c1a6328a1d4f37fa26b8d1f38f79954022851d0c512908d44374272"
        self.assertEqual(strip_download_copy_suffix(f"{h} (1).jpeg"), f"{h}.jpeg")
        self.assertEqual(strip_download_copy_suffix(f"{h} (2).JPEG"), f"{h}.JPEG")
        self.assertEqual(strip_download_copy_suffix(f"input/{h} (1).png"), f"input/{h}.png")
        self.assertEqual(strip_download_copy_suffix(f"{h}.jpeg"), f"{h}.jpeg")
        self.assertEqual(download_copy_name_candidates(f"{h} (1).jpeg"), [f"{h} (1).jpeg", f"{h}.jpeg"])

    def test_resolve_prefers_canonical_when_copy_missing(self) -> None:
        from input_still_catalog import resolve_catalog_still_path

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "input"
            root.mkdir()
            h = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            real = root / f"{h}.jpeg"
            real.write_bytes(b"real")
            got = resolve_catalog_still_path(str(root / f"{h} (1).jpeg"), input_root=root)
            self.assertIsNotNone(got)
            self.assertEqual(got.resolve(), real.resolve())

    def test_resolve_prefers_canonical_when_both_exist(self) -> None:
        from input_still_catalog import resolve_catalog_still_path

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "input"
            root.mkdir()
            h = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
            real = root / f"{h}.jpeg"
            copy = root / f"{h} (1).jpeg"
            real.write_bytes(b"real")
            copy.write_bytes(b"copy")
            got = resolve_catalog_still_path(str(copy), input_root=root)
            self.assertIsNotNone(got)
            self.assertEqual(got.resolve(), real.resolve())


if __name__ == "__main__":
    unittest.main()
