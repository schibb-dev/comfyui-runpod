#!/usr/bin/env python3
"""Tests for asset_job_lib + stub worker drain."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class AssetJobLibTests(unittest.TestCase):
    def test_catalog_loads_stub_types(self) -> None:
        import asset_job_lib as aj

        cat = aj.load_catalog(aj.default_catalog_path(REPO_ROOT))
        types = aj.active_job_types(cat, include_stub=True)
        self.assertIn("lineage_reindex", types)
        self.assertIn("vision_florence_tag", types)
        self.assertIn("vision_slice_caption", types)
        self.assertNotIn("input_index", types)  # deferred

    def test_enqueue_read_commit_idempotent(self) -> None:
        import asset_job_lib as aj

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = root / "asset_job_queue.jsonl"
            state_path = root / "asset_job_worker_state.json"
            status = root

            r1 = aj.enqueue_job(
                queue,
                {
                    "job_type": "vision_florence_tag",
                    "reason": "test",
                    "asset": {"group_id": "og:stem:foo", "relpath": "og/x.mp4", "sha256": "abc123"},
                },
            )
            r2 = aj.enqueue_job(
                queue,
                {
                    "job_type": "vision_florence_tag",
                    "reason": "test",
                    "asset": {"group_id": "og:stem:foo", "relpath": "og/x.mp4", "sha256": "abc123"},
                },
            )
            self.assertEqual(r1["idempotency_key"], r2["idempotency_key"])

            state = aj.load_worker_state(state_path)
            batch = aj.read_batch(queue, state, job_types=["vision_florence_tag"], limit=10)
            # Both lines unread; first wins, second same key skipped within batch? 
            # read_batch skips already-seen; within one pass both are new until committed.
            # After first batch commit, re-read should skip both keys.
            self.assertGreaterEqual(len(batch), 1)
            stub = aj.run_stub_handler("vision_florence_tag", batch, status_dir=status)
            self.assertTrue(stub.get("would_run"))
            state = aj.commit_cursor(state, batch)
            aj.save_worker_state(state_path, state)

            state2 = aj.load_worker_state(state_path)
            batch2 = aj.read_batch(queue, state2, job_types=["vision_florence_tag"], limit=10)
            self.assertEqual(batch2, [])


if __name__ == "__main__":
    unittest.main()
