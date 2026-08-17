#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class IngestWindowsInputInboxTests(unittest.TestCase):
    def test_dry_run_counts_then_apply_copies_and_archives(self) -> None:
        from ingest_windows_input_inbox import ingest_windows_input_inbox

        with tempfile.TemporaryDirectory() as td:
            inbox = Path(td) / "inbox"
            dest = Path(td) / "input"
            inbox.mkdir()
            dest.mkdir()
            (inbox / "a.png").write_bytes(b"png")
            (inbox / "notes.txt").write_text("nope", encoding="utf-8")
            dry = ingest_windows_input_inbox(inbox=inbox, dest=dest, apply=False)
            self.assertTrue(dry["ok"])
            self.assertEqual(dry["copied"], 1)
            self.assertFalse((dest / "a.png").exists())

            applied = ingest_windows_input_inbox(inbox=inbox, dest=dest, apply=True)
            self.assertEqual(applied["copied"], 1)
            self.assertTrue((dest / "a.png").is_file())
            self.assertFalse((inbox / "a.png").exists())
            ingested = list((inbox / "_ingested").rglob("a.png"))
            self.assertEqual(len(ingested), 1)

    def test_existing_dest_is_skipped_and_source_archived(self) -> None:
        from ingest_windows_input_inbox import ingest_windows_input_inbox

        with tempfile.TemporaryDirectory() as td:
            inbox = Path(td) / "inbox"
            dest = Path(td) / "input"
            inbox.mkdir()
            dest.mkdir()
            (inbox / "a.png").write_bytes(b"new")
            (dest / "a.png").write_bytes(b"old")
            out = ingest_windows_input_inbox(inbox=inbox, dest=dest, apply=True)
            self.assertEqual(out["skipped_exists"], 1)
            self.assertEqual((dest / "a.png").read_bytes(), b"old")
            self.assertFalse((inbox / "a.png").exists())


if __name__ == "__main__":
    unittest.main()
