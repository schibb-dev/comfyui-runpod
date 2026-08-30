#!/usr/bin/env python3
"""Tests for Comfy queue ↔ factory job status reconciliation."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
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

    def test_waiting_queue_empty_allows_running(self) -> None:
        with mock.patch.object(
            sf,
            "queue_prompt_id_buckets",
            return_value=({"run-1"}, set()),
        ):
            empty, run_n, pend_n = sf.comfy_waiting_queue_empty("http://x")
        self.assertTrue(empty)
        self.assertEqual(run_n, 1)
        self.assertEqual(pend_n, 0)

    def test_waiting_queue_busy_when_pending(self) -> None:
        with mock.patch.object(
            sf,
            "queue_prompt_id_buckets",
            return_value=(set(), {"pend-1"}),
        ):
            empty, run_n, pend_n = sf.comfy_waiting_queue_empty("http://x")
        self.assertFalse(empty)
        self.assertEqual(run_n, 0)
        self.assertEqual(pend_n, 1)

    def test_pending_only_skips_when_comfy_waiting_busy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_path = Path(tmp) / "job.job.json"
            job_path.write_text(
                json.dumps({"job_key": "t1", "submit": {"status": "pending"}}),
                encoding="utf-8",
            )
            with mock.patch.object(
                sf,
                "comfy_waiting_queue_empty",
                return_value=(False, 1, 2),
            ):
                result = sf.submit_job_file(
                    job_path,
                    server="http://x",
                    data_root=Path(tmp),
                    dry_run=False,
                    force=False,
                    pending_only=True,
                )
        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("reason"), "comfy_pending_busy")
        self.assertEqual(result.get("comfy_running"), 1)
        self.assertEqual(result.get("comfy_pending"), 2)

    def test_unqueue_demotes_factory_job_by_prompt_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            jobs_dir = data_root / "shape_factory" / "jobs" / "SomeFamily"
            jobs_dir.mkdir(parents=True)
            job_path = jobs_dir / "hourly__abc.job.json"
            sidecar = jobs_dir / "hourly__abc.submit.json"
            sidecar.write_text(
                json.dumps({"prompt_id": "pid-q1", "submitted_at": "t0"}),
                encoding="utf-8",
            )
            job_path.write_text(
                json.dumps(
                    {
                        "job_key": "hourly__abc",
                        "family_slug": "SomeFamily",
                        "submit": {
                            "status": "queued",
                            "prompt_id": "pid-q1",
                            "submit_path": str(sidecar),
                        },
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(
                sf,
                "queue_prompt_id_buckets",
                return_value=(set(), {"pid-q1"}),
            ), mock.patch.object(sf, "_http_json", return_value={}) as http:
                result = sf.unqueue_to_pending(
                    prompt_id="pid-q1",
                    server="http://x",
                    data_root=data_root,
                )
            http.assert_called()
            self.assertTrue(result.get("ok"))
            self.assertTrue(result.get("factory_job"))
            self.assertEqual(result.get("status"), "pending")
            self.assertEqual(result.get("previous_prompt_id"), "pid-q1")
            job = json.loads(job_path.read_text(encoding="utf-8"))
            self.assertEqual(job["submit"]["status"], "pending")
            self.assertNotIn("prompt_id", job["submit"])
            self.assertEqual(job["submit"]["previous_prompt_id"], "pid-q1")
            sid = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertIsNone(sid.get("prompt_id"))

    def test_unqueue_refuses_running(self) -> None:
        with mock.patch.object(
            sf,
            "queue_prompt_id_buckets",
            return_value=({"pid-run"}, set()),
        ):
            result = sf.unqueue_to_pending(
                prompt_id="pid-run",
                server="http://x",
                data_root=Path("/tmp"),
            )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "still_running")

    def test_unqueue_already_gone_still_demotes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            jobs_dir = data_root / "shape_factory" / "jobs" / "F"
            jobs_dir.mkdir(parents=True)
            job_path = jobs_dir / "j1.job.json"
            job_path.write_text(
                json.dumps(
                    {
                        "job_key": "j1",
                        "submit": {"status": "queued", "prompt_id": "pid-gone"},
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(
                sf,
                "queue_prompt_id_buckets",
                return_value=(set(), set()),
            ), mock.patch.object(sf, "_http_json") as http:
                result = sf.unqueue_to_pending(
                    prompt_id="pid-gone",
                    server="http://x",
                    data_root=data_root,
                )
            http.assert_not_called()
            self.assertTrue(result.get("ok"))
            self.assertTrue(result.get("factory_job"))
            job = json.loads(job_path.read_text(encoding="utf-8"))
            self.assertEqual(job["submit"]["status"], "pending")
            self.assertNotIn("prompt_id", job["submit"])

    def test_unqueue_no_factory_job_comfy_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            (data_root / "shape_factory" / "jobs").mkdir(parents=True)
            with mock.patch.object(
                sf,
                "queue_prompt_id_buckets",
                return_value=(set(), {"pid-ext"}),
            ), mock.patch.object(sf, "_http_json", return_value={}) as http:
                result = sf.unqueue_to_pending(
                    prompt_id="pid-ext",
                    server="http://x",
                    data_root=data_root,
                )
            http.assert_called()
            self.assertTrue(result.get("ok"))
            self.assertFalse(result.get("factory_job"))
            self.assertTrue(result.get("comfy_deleted"))

    def test_discard_pending_renames_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            jobs_dir = data_root / "shape_factory" / "jobs" / "F"
            jobs_dir.mkdir(parents=True)
            job_path = jobs_dir / "pend1.job.json"
            prompt_path = jobs_dir / "pend1.prompt.json"
            prompt_path.write_text("{}", encoding="utf-8")
            job_path.write_text(
                json.dumps(
                    {
                        "job_key": "pend1",
                        "submit": {"status": "pending"},
                    }
                ),
                encoding="utf-8",
            )
            result = sf.discard_pending_job(data_root=data_root, job_key="pend1", expunge=False)
            self.assertTrue(result.get("ok"))
            self.assertTrue(result.get("discarded"))
            self.assertFalse(result.get("expunged"))
            self.assertFalse(job_path.is_file())
            self.assertTrue(Path(str(job_path) + ".discarded").is_file())
            self.assertTrue(Path(str(prompt_path) + ".discarded").is_file())
            discarded = json.loads(Path(str(job_path) + ".discarded").read_text(encoding="utf-8"))
            self.assertEqual(discarded["submit"]["status"], "abandoned")

    def test_expunge_pending_deletes_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            jobs_dir = data_root / "shape_factory" / "jobs" / "F"
            jobs_dir.mkdir(parents=True)
            job_path = jobs_dir / "pend2.job.json"
            prompt_path = jobs_dir / "pend2.prompt.json"
            prompt_path.write_text("{}", encoding="utf-8")
            job_path.write_text(
                json.dumps({"job_key": "pend2", "submit": {"status": "pending"}}),
                encoding="utf-8",
            )
            result = sf.discard_pending_job(data_root=data_root, job_key="pend2", expunge=True)
            self.assertTrue(result.get("ok"))
            self.assertTrue(result.get("expunged"))
            self.assertFalse(job_path.is_file())
            self.assertFalse(prompt_path.is_file())
            self.assertFalse(Path(str(job_path) + ".discarded").is_file())
            self.assertIn(str(job_path), result.get("deleted") or [])

    def test_discard_refuses_queued(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            jobs_dir = data_root / "shape_factory" / "jobs" / "F"
            jobs_dir.mkdir(parents=True)
            job_path = jobs_dir / "q1.job.json"
            job_path.write_text(
                json.dumps(
                    {
                        "job_key": "q1",
                        "submit": {"status": "queued", "prompt_id": "pid-1"},
                    }
                ),
                encoding="utf-8",
            )
            result = sf.discard_pending_job(data_root=data_root, job_key="q1")
            self.assertFalse(result.get("ok"))
            self.assertEqual(result.get("error"), "not_pending")
            self.assertTrue(job_path.is_file())

    def test_archive_error_preserves_submit_forensics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            jobs_dir = data_root / "shape_factory" / "jobs" / "F"
            jobs_dir.mkdir(parents=True)
            job_path = jobs_dir / "err1.job.json"
            job_path.write_text(
                json.dumps(
                    {
                        "job_key": "err1",
                        "submit": {
                            "status": "error",
                            "prompt_id": "pid-err",
                            "error": "CUDA out of memory",
                            "exception_type": "torch.cuda.OutOfMemoryError",
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = sf.discard_pending_job(
                data_root=data_root, job_key="err1", expunge=False, reason="user_archived_failure"
            )
            self.assertTrue(result.get("ok"))
            self.assertTrue(result.get("discarded"))
            self.assertFalse(result.get("expunged"))
            self.assertEqual(result.get("status"), "error")
            self.assertFalse(job_path.is_file())
            archived = Path(str(job_path) + ".discarded")
            self.assertTrue(archived.is_file())
            body = json.loads(archived.read_text(encoding="utf-8"))
            submit = body["submit"]
            self.assertEqual(submit["status"], "error")
            self.assertEqual(submit["error"], "CUDA out of memory")
            self.assertTrue(submit.get("discarded"))
            self.assertEqual(submit.get("discard_reason"), "user_archived_failure")
            self.assertEqual(submit.get("previous_prompt_id"), "pid-err")
            self.assertNotIn("prompt_id", submit)

    def test_pending_only_limit_skips_already_submitted(self) -> None:
        """--limit must apply after pending filter, not to alphabetical all-jobs."""
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Path(tmp) / "jobs" / "Fam"
            jobs.mkdir(parents=True)
            old = jobs / "aaa_old.job.json"
            old.write_text(
                json.dumps(
                    {
                        "job_key": "aaa_old",
                        "submit": {"status": "complete", "prompt_id": "pid-old"},
                    }
                ),
                encoding="utf-8",
            )
            pend = jobs / "zzz_pending.job.json"
            pend.write_text(
                json.dumps({"job_key": "zzz_pending", "submit": {"status": "pending"}}),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                job=None,
                jobs_dir=None,
                family="Fam",
                job_dir=str(Path(tmp) / "jobs"),
                limit=1,
            )
            # Broken behavior would return only aaa_old (alphabetically first).
            paths = sf.iter_pending_submit_job_paths(args)
            self.assertEqual([p.name for p in paths], ["zzz_pending.job.json"])

    def test_rebind_job_after_prompt_move_updates_submit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            jobs = data / "shape_factory" / "jobs" / "Fam"
            jobs.mkdir(parents=True)
            key = "Fam__pp-x__src-y__000_ui1"
            path = jobs / f"{key}.job.json"
            path.write_text(
                json.dumps(
                    {
                        "job_key": key,
                        "submit": {
                            "prompt_id": "old-pid",
                            "status": "interrupted",
                            "interrupted_reason": "missing_from_comfy_queue_and_history",
                            "interrupted_at": "2026-01-01T00:00:00Z",
                        },
                    }
                ),
                encoding="utf-8",
            )
            out = sf.rebind_job_after_prompt_move(
                data_root=data,
                old_prompt_id="old-pid",
                new_prompt_id="new-pid",
                status="queued",
            )
            self.assertTrue(out.get("ok"))
            self.assertTrue(out.get("factory_job"))
            self.assertEqual(out.get("job_key"), key)
            saved = json.loads(path.read_text(encoding="utf-8"))
            submit = saved["submit"]
            self.assertEqual(submit["prompt_id"], "new-pid")
            self.assertEqual(submit["previous_prompt_id"], "old-pid")
            self.assertEqual(submit["status"], "queued")
            self.assertEqual(submit.get("prompt_id_rebound_reason"), "queue_move_reorder")
            self.assertNotIn("interrupted_reason", submit)
            self.assertNotIn("interrupted_at", submit)


if __name__ == "__main__":
    unittest.main()
