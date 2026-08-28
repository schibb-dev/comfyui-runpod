#!/usr/bin/env python3
"""Tests for still auto-tagger SQLite + dry-run batch."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401

from vision_still_tags import (
    connect,
    default_db_path,
    enqueue_run,
    enrich_still_items,
    ensure_db,
    get_item,
    get_run,
    list_events,
    process_run,
    upsert_editorial,
)


class VisionStillTagsTests(unittest.TestCase):
    def test_enqueue_and_dry_run_process(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            status = root / "status"
            status.mkdir()
            (data / "shape_factory").mkdir(parents=True)

            # Fake input still with content hash in name
            inp = root / "input"
            inp.mkdir()
            cid = "a" * 64
            still = inp / f"SSS{cid}.jpeg"
            still.write_bytes(b"fakejpeg")

            # Minimal catalog sqlite
            import sqlite3

            cat = data / "shape_factory" / "input_still_catalog.sqlite"
            con = sqlite3.connect(str(cat))
            con.execute(
                "CREATE TABLE stills (path TEXT PRIMARY KEY, size INT, mtime REAL, first_seen REAL, last_seen REAL)"
            )
            con.execute(
                "INSERT INTO stills VALUES (?,?,?,?,?)",
                (str(still), still.stat().st_size, 1.0, 1.0, 1.0),
            )
            con.commit()
            con.close()

            import os

            os.environ["COMFYUI_BIND_INPUT_DIR"] = str(inp)
            os.environ["SHAPE_FACTORY_DATA_ROOT"] = str(data)

            enq = enqueue_run(
                data_root=data,
                content_ids=[cid],
                only_missing=True,
                limit=12,
                dry_run=True,
                status_dir=status,
            )
            self.assertTrue(enq["ok"])
            self.assertEqual(enq["enqueued"], 1)
            run_id = enq["run_id"]

            out = process_run(data_root=data, run_id=run_id, status_dir=status)
            self.assertTrue(out["ok"])
            run = out["run"]
            self.assertEqual(run["status"], "done")
            self.assertEqual(run["done_count"], 1)

            db = default_db_path(data_root=data)
            con2 = connect(db)
            try:
                item = get_item(con2, cid)
                self.assertIsNotNone(item)
                assert item is not None
                self.assertGreaterEqual(len(item["provisional_tags"]), 3)
                self.assertIn("1girl", item["effective_tags"])
                ev = list_events(con2, run_id=run_id)
                kinds = [e["kind"] for e in ev]
                self.assertIn("enqueued", kinds)
                self.assertIn("item_done", kinds)
                self.assertIn("finished", kinds)
            finally:
                con2.close()

            nd = status / "vision_still_tags.ndjson"
            self.assertTrue(nd.is_file())
            row = json.loads(nd.read_text(encoding="utf-8").strip().splitlines()[-1])
            self.assertEqual(row["content_id"], cid)

            items = [{"content_id": cid, "tags": []}]
            enrich_still_items(items, data_root=data)
            self.assertTrue(items[0]["provisional_tags"])
            self.assertTrue(items[0]["effective_tags"])

            # Editorial wins / merges
            con3 = connect(db)
            try:
                upsert_editorial(con3, content_id=cid, tags=["manual_mark"])
            finally:
                con3.close()
            items2 = [{"content_id": cid}]
            enrich_still_items(items2, data_root=data)
            self.assertIn("manual_mark", items2[0]["effective_tags"])
            self.assertIn("1girl", items2[0]["effective_tags"])


if __name__ == "__main__":
    unittest.main()
