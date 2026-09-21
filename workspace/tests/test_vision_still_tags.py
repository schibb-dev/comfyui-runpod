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

    def test_list_recent_still_tag_runs_can_omit_scope(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            status = root / "status"
            status.mkdir()
            (data / "shape_factory").mkdir(parents=True)
            inp = root / "input"
            inp.mkdir()
            cid = "b" * 64
            (inp / f"SSS{cid}.jpeg").write_bytes(b"fakejpeg")
            import sqlite3
            import os

            cat = data / "shape_factory" / "input_still_catalog.sqlite"
            con = sqlite3.connect(str(cat))
            con.execute(
                "CREATE TABLE stills (path TEXT PRIMARY KEY, size INT, mtime REAL, first_seen REAL, last_seen REAL)"
            )
            con.execute("INSERT INTO stills VALUES (?,?,?,?,?)", (f"input/SSS{cid}.jpeg", 8, 1.0, 1.0, 1.0))
            con.commit()
            con.close()
            os.environ["COMFYUI_BIND_INPUT_DIR"] = str(inp)
            os.environ["SHAPE_FACTORY_DATA_ROOT"] = str(data)
            enq = enqueue_run(
                data_root=data,
                content_ids=[cid],
                only_missing=False,
                force=True,
                limit=12,
                dry_run=True,
                status_dir=status,
            )
            self.assertTrue(enq.get("ok"), enq)
            from vision_still_tags import list_recent_still_tag_runs

            stubs = list_recent_still_tag_runs(data_root=data, limit=10, include_scope=False)
            self.assertTrue(stubs)
            self.assertNotIn("scope", stubs[0])
            self.assertEqual(stubs[0]["run_id"], enq["run_id"])
            full = list_recent_still_tag_runs(data_root=data, limit=10, include_scope=True)
            self.assertIn("scope", full[0])

    def test_enqueue_reserves_stills_for_second_batch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            status = root / "status"
            status.mkdir()
            (data / "shape_factory").mkdir(parents=True)

            inp = root / "input"
            inp.mkdir()
            cids = [f"{i:064x}" for i in range(3)]
            stills = []
            for cid in cids:
                p = inp / f"SSS{cid}.jpeg"
                p.write_bytes(b"fakejpeg")
                stills.append(p)

            import sqlite3
            import os

            cat = data / "shape_factory" / "input_still_catalog.sqlite"
            con = sqlite3.connect(str(cat))
            con.execute(
                "CREATE TABLE stills (path TEXT PRIMARY KEY, size INT, mtime REAL, first_seen REAL, last_seen REAL)"
            )
            for p in stills:
                con.execute(
                    "INSERT INTO stills VALUES (?,?,?,?,?)",
                    (str(p), p.stat().st_size, float(stills.index(p)), float(stills.index(p)), float(stills.index(p))),
                )
            con.commit()
            con.close()

            os.environ["COMFYUI_BIND_INPUT_DIR"] = str(inp)
            os.environ["SHAPE_FACTORY_DATA_ROOT"] = str(data)

            first = enqueue_run(data_root=data, only_missing=True, limit=2, dry_run=True, status_dir=status)
            second = enqueue_run(data_root=data, only_missing=True, limit=2, dry_run=True, status_dir=status)
            self.assertEqual(first["enqueued"], 2)
            self.assertEqual(second["enqueued"], 1)

            db = default_db_path(data_root=data)
            con2 = connect(db)
            try:
                run_a = get_run(con2, first["run_id"])
                run_b = get_run(con2, second["run_id"])
                assert run_a and run_b
                scope_a = set(run_a["scope"]["content_ids"])
                scope_b = set(run_b["scope"]["content_ids"])
                self.assertEqual(len(scope_a), 2)
                self.assertEqual(len(scope_b), 1)
                self.assertFalse(scope_a & scope_b)
                rows = con2.execute(
                    "SELECT content_id, queue_run_id FROM still_tag_items WHERE queue_run_id IS NOT NULL"
                ).fetchall()
                self.assertEqual(len(rows), 3)
            finally:
                con2.close()

    def test_rekey_filename_hex_to_byte_hash(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            from still_content_account import account
            from vision_still_tags import rekey_items_to_byte_hash, upsert_provisional

            root = Path(td)
            data = root / "data"
            (data / "shape_factory").mkdir(parents=True)
            inp = root / "input"
            inp.mkdir()
            blob = b"\xff\xd8" + b"tagged-bytes" * 20
            fake_hex = "b" * 64
            still = inp / f"qqqfx-{fake_hex}.jpg"
            still.write_bytes(blob)
            import hashlib

            real = hashlib.sha256(blob).hexdigest()
            db_acct = data / "shape_factory" / "still_content_accounting.sqlite"
            account(input_root=inp, data_root=data, db_path=db_acct, workers=1)
            tag_db = default_db_path(data_root=data)
            ensure_db(tag_db)
            con = connect(tag_db)
            try:
                upsert_provisional(
                    con,
                    content_id=fake_hex,
                    tags=["from_name"],
                    model_pin="test",
                    pin_policy="test",
                    run_id="r1",
                )
                con.commit()
            finally:
                con.close()
            out = rekey_items_to_byte_hash(data_root=data)
            self.assertEqual(out["moved"], 1)
            con = connect(tag_db)
            try:
                self.assertIsNone(get_item(con, fake_hex))
                item = get_item(con, real)
                self.assertIsNotNone(item)
                assert item is not None
                self.assertIn("from_name", item["provisional_tags"])
            finally:
                con.close()

    def test_enqueue_unnamed_still_via_accounting(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            from still_content_account import account
            import hashlib

            root = Path(td)
            data = root / "data"
            status = root / "status"
            status.mkdir()
            (data / "shape_factory").mkdir(parents=True)
            inp = root / "input"
            inp.mkdir()
            blob = b"\xff\xd8" + b"no-hash-name" * 16
            still = inp / "camille-rowe.jpg"
            still.write_bytes(blob)
            real = hashlib.sha256(blob).hexdigest()
            account(
                input_root=inp,
                data_root=data,
                db_path=data / "shape_factory" / "still_content_accounting.sqlite",
                workers=1,
            )
            import os

            os.environ["COMFYUI_BIND_INPUT_DIR"] = str(inp)
            os.environ["SHAPE_FACTORY_DATA_ROOT"] = str(data)
            enq = enqueue_run(data_root=data, only_missing=True, limit=12, dry_run=True, status_dir=status)
            self.assertEqual(enq["enqueued"], 1)
            out = process_run(data_root=data, run_id=enq["run_id"], status_dir=status)
            self.assertTrue(out["ok"])
            con = connect(default_db_path(data_root=data))
            try:
                item = get_item(con, real)
                self.assertIsNotNone(item)
                assert item is not None
                self.assertTrue(item["provisional_tags"])
            finally:
                con.close()


if __name__ == "__main__":
    unittest.main()
