#!/usr/bin/env python3
"""Tests for split ledger payload store."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from comfy_queue_ledger import _default_state
from comfy_queue_ledger_store import LedgerPayloadStore, LedgerStatePersister, build_disk_meta


class PayloadStoreTests(unittest.TestCase):
    def test_upsert_is_idempotent_by_hash(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = LedgerPayloadStore(Path(td) / "p.sqlite")
            prompt = {"1": {"class_type": "LoadImage", "inputs": {}}}
            self.assertTrue(store.upsert("a", prompt=prompt, extra_data={"client_id": "ui"}))
            self.assertFalse(store.upsert("a", prompt=prompt, extra_data={"client_id": "ui"}))
            self.assertTrue(store.upsert("a", prompt=prompt, extra_data={"client_id": "other"}))
            got = store.get("a")
            self.assertEqual(got["extra_data"]["client_id"], "other")
            store.close()


class PersisterMigrationTests(unittest.TestCase):
    def test_migrates_legacy_embedded_state_to_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state_path = root / "comfy_queue_ledger_state.json"
            legacy = _default_state()
            legacy["known"] = {
                "pid-1": {
                    "first_seen_ts": 1.0,
                    "last_phase": "pending",
                    "prompt": {"1": {"class_type": "LoadImage", "inputs": {"image": "x.png"}}},
                    "extra_data": {"client_id": "ui", "big": "x" * 1000},
                    "outputs_to_execute": ["1"],
                }
            }
            legacy["backlog"] = [
                {
                    "prompt_id": "pid-2",
                    "source": "park",
                    "prompt": {"2": {"class_type": "SaveImage", "inputs": {}}},
                    "extra_data": {},
                }
            ]
            # Fat legacy write (indent) to mimic production.
            state_path.write_text(json.dumps(legacy, indent=2) + "\n", encoding="utf-8")
            fat = state_path.stat().st_size

            persister = LedgerStatePersister(state_path)
            state, info = persister.load(_default_state)
            self.assertTrue(info["legacy_embedded"])
            self.assertGreaterEqual(info["migrated_payloads"], 2)
            self.assertIn("prompt", state["known"]["pid-1"])
            self.assertEqual(state["known"]["pid-1"]["prompt"]["1"]["class_type"], "LoadImage")
            self.assertIn("prompt", state["backlog"][0])

            slim = state_path.stat().st_size
            self.assertLess(slim, fat)
            self.assertLess(slim, 50_000)
            disk = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(disk.get("payload_store"), "sqlite")
            self.assertNotIn("prompt", disk["known"]["pid-1"])
            self.assertTrue(persister.payload_db_path.exists())

            # Second persist with unchanged payloads should skip payload writes.
            stats = persister.persist(state)
            self.assertEqual(stats["payload_writes"], 0)
            persister.close()

    def test_dirty_meta_skips_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state_path = Path(td) / "state.json"
            persister = LedgerStatePersister(state_path)
            state = _default_state()
            state["known"] = {"a": {"first_seen_ts": 1.0, "last_phase": "pending", "prompt": {"1": {}}}}
            s1 = persister.persist(state, force=True)
            self.assertTrue(s1["wrote_meta"])
            # Touch only in-memory last_seen (not on disk meta).
            state["known"]["a"]["last_seen_ts"] = 99.0
            s2 = persister.persist(state)
            self.assertFalse(s2["wrote_meta"])
            self.assertEqual(s2["payload_writes"], 0)
            # Structural change forces meta write.
            state["mode"] = "churn"
            s3 = persister.persist(state)
            self.assertTrue(s3["wrote_meta"])
            persister.close()

    def test_build_disk_meta_strips_payloads(self) -> None:
        state = _default_state()
        state["known"] = {"a": {"last_phase": "running", "prompt": {"1": {"class_type": "X"}}}}
        meta = build_disk_meta(state)
        self.assertNotIn("prompt", meta["known"]["a"])
        self.assertEqual(meta["known"]["a"]["last_phase"], "running")


if __name__ == "__main__":
    unittest.main()
