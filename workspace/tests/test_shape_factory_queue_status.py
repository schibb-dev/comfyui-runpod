#!/usr/bin/env python3
"""Tests for Comfy queue ↔ factory job status reconciliation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "workspace" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import shape_factory as sf


class QueueStatusTests(unittest.TestCase):
    def test_buckets_split_running_and_pending(self) -> None:
        with mock.patch.object(
            sf,
            "fetch_comfy_queue",
            return_value={
                "queue_running": [[0, "run-1", {}]],
                "queue_pending": [[1, "pend-1", {}], [2, "pend-2", {}]],
            },
        ):
            running, pending = sf.queue_prompt_id_buckets("http://x")
            combined = sf.queue_prompt_ids("http://x")
        self.assertEqual(running, {"run-1"})
        self.assertEqual(pending, {"pend-1", "pend-2"})
        self.assertEqual(combined, {"run-1", "pend-1", "pend-2"})

    def test_update_distinguishes_queued_from_running(self) -> None:
        job = {"submit": {"prompt_id": "pend-1", "status": "running"}}
        st = sf.update_job_status_from_comfy(
            job,
            server="http://x",
            data_root=Path("."),
            running_ids={"run-1"},
            pending_ids={"pend-1"},
        )
        self.assertEqual(st, "queued")
        self.assertEqual(job["submit"]["status"], "queued")

    def test_missing_queue_and_history_becomes_interrupted(self) -> None:
        job = {"submit": {"prompt_id": "gone", "status": "running"}, "output_prefix": "og/nope"}
        with mock.patch.object(sf, "fetch_comfy_history", return_value=None), mock.patch.object(
            sf, "discover_job_outputs", return_value=[]
        ):
            st = sf.update_job_status_from_comfy(
                job,
                server="http://x",
                data_root=Path("."),
                running_ids=set(),
                pending_ids=set(),
            )
        self.assertEqual(st, "interrupted")
        self.assertEqual(job["submit"]["status"], "interrupted")

    def test_history_error_persists_exception_message(self) -> None:
        history = {
            "status": {
                "status_str": "error",
                "completed": False,
                "messages": [
                    [
                        "execution_error",
                        {
                            "node_id": "12",
                            "node_type": "SamplerCustomAdvanced",
                            "exception_type": "RuntimeError",
                            "exception_message": (
                                "Allocation on device 0 would exceed allowed memory. (out of memory)\n"
                                "Currently allocated: 13.66 GiB"
                            ),
                        },
                    ]
                ],
            },
            "outputs": {},
        }
        job = {"submit": {"prompt_id": "err-1", "status": "running"}, "output_prefix": "og/x"}
        with mock.patch.object(sf, "fetch_comfy_history", return_value=history), mock.patch.object(
            sf, "extract_history_output_paths", return_value=[]
        ), mock.patch.object(sf, "discover_job_outputs", return_value=[]), mock.patch.object(
            sf, "update_job_timings_on_status", return_value=None
        ):
            st = sf.update_job_status_from_comfy(
                job,
                server="http://x",
                data_root=Path("."),
                running_ids=set(),
                pending_ids=set(),
            )
        self.assertEqual(st, "error")
        self.assertEqual(job["submit"]["status"], "error")
        self.assertIn("out of memory", job["submit"]["error"])
        self.assertEqual(job["submit"]["error_node"], "SamplerCustomAdvanced")
        self.assertEqual(job["submit"]["comfy_error"]["node_id"], "12")


if __name__ == "__main__":
    unittest.main()
