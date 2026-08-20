#!/usr/bin/env python3
"""Tests for begin/finish job edit (editing status + drain skip)."""

from __future__ import annotations

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


class JobEditTests(unittest.TestCase):
    def _write_job(self, root: Path, key: str, submit: dict) -> Path:
        fam = root / "shape_factory" / "jobs" / "TestFam"
        fam.mkdir(parents=True)
        path = fam / f"{key}.job.json"
        job = {
            "job_key": key,
            "family_slug": "TestFam",
            "bindings": {
                "source_video": {"path": str(root / "clip.mp4"), "role": "A"},
            },
            "submit": submit,
        }
        path.write_text(json.dumps(job), encoding="utf-8")
        return path

    def test_job_pending_submit_skips_editing(self) -> None:
        self.assertFalse(sf.job_pending_submit({"submit": {"status": "editing"}}))
        self.assertTrue(sf.job_pending_submit({"submit": {"status": "pending"}}))
        self.assertTrue(sf.job_pending_submit({"submit": {}}))

    def test_begin_edit_from_pending(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_job(root, "job-a", {"status": "pending"})
            res = sf.begin_job_edit(data_root=root, server="", job_key="job-a")
            self.assertTrue(res.get("ok"), res)
            self.assertEqual(res.get("status"), "editing")
            _path, job = sf.find_job_by_key(root, "job-a")
            self.assertIsNotNone(job)
            assert job is not None
            self.assertEqual(job["submit"]["status"], "editing")
            self.assertEqual(job["submit"].get("editing_from_status"), "pending")
            self.assertFalse(sf.job_pending_submit(job))

    def test_begin_edit_unqueues_waiting(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_job(root, "job-q", {"status": "queued", "prompt_id": "pid-1"})
            with mock.patch.object(
                sf, "queue_prompt_id_buckets", return_value=(set(), {"pid-1"})
            ), mock.patch.object(sf, "_http_json", return_value={}) as http:
                res = sf.begin_job_edit(
                    data_root=root,
                    server="http://comfy.test",
                    job_key="job-q",
                )
            self.assertTrue(res.get("ok"), res)
            self.assertTrue(res.get("comfy_deleted"))
            http.assert_called()
            _path, job = sf.find_job_by_key(root, "job-q")
            assert job is not None
            self.assertEqual(job["submit"]["status"], "editing")
            self.assertNotIn("prompt_id", job["submit"])
            self.assertEqual(job["submit"].get("previous_prompt_id"), "pid-1")

    def test_begin_edit_refuses_status_running(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_job(root, "job-r", {"status": "running", "prompt_id": "pid-r"})
            res = sf.begin_job_edit(
                data_root=root,
                server="http://comfy.test",
                job_key="job-r",
            )
            self.assertFalse(res.get("ok"))
            self.assertEqual(res.get("error"), "not_editable")

    def test_begin_edit_refuses_comfy_running(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_job(root, "job-r2", {"status": "queued", "prompt_id": "pid-r"})
            with mock.patch.object(
                sf, "queue_prompt_id_buckets", return_value=({"pid-r"}, set())
            ):
                res = sf.begin_job_edit(
                    data_root=root,
                    server="http://comfy.test",
                    job_key="job-r2",
                )
            self.assertFalse(res.get("ok"))
            self.assertEqual(res.get("error"), "still_running")

    def test_finish_edit_later_and_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_job(root, "job-e", {"status": "editing", "editing_from_status": "queued"})
            later = sf.finish_job_edit(data_root=root, action="later", job_key="job-e")
            self.assertTrue(later.get("ok"), later)
            self.assertEqual(later.get("status"), "pending")
            _p, job = sf.find_job_by_key(root, "job-e")
            assert job is not None
            self.assertEqual(job["submit"]["status"], "pending")
            self.assertTrue(sf.job_pending_submit(job))

            # Re-enter editing then cancel
            sf.begin_job_edit(data_root=root, server="", job_key="job-e")
            cancel = sf.finish_job_edit(data_root=root, action="cancel", job_key="job-e")
            self.assertTrue(cancel.get("ok"), cancel)
            self.assertEqual(cancel.get("status"), "pending")

    def test_finish_edit_now_calls_submit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_job(root, "job-n", {"status": "editing"})
            with mock.patch.object(
                sf,
                "submit_job_file",
                return_value={"ok": True, "prompt_id": "new-pid", "job_key": "job-n"},
            ) as submit:
                res = sf.finish_job_edit(
                    data_root=root,
                    action="now",
                    server="http://comfy.test",
                    job_key="job-n",
                )
            self.assertTrue(res.get("ok"), res)
            self.assertEqual(res.get("prompt_id"), "new-pid")
            submit.assert_called_once()

    def test_job_edit_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_job(
                root,
                "job-s",
                {"status": "editing", "editing_from_status": "pending"},
            )
            snap = sf.job_edit_snapshot(data_root=root, job_key="job-s")
            self.assertTrue(snap.get("ok"), snap)
            self.assertEqual(snap.get("job_key"), "job-s")
            self.assertEqual(snap.get("status"), "editing")
            self.assertEqual(snap.get("family_slug"), "TestFam")

    def test_submit_job_file_pending_only_skips_editing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = self._write_job(root, "job-skip", {"status": "editing"})
            result = sf.submit_job_file(
                path,
                server="http://127.0.0.1:8188",
                data_root=root,
                pending_only=True,
            )
            self.assertTrue(result.get("skipped"))
            self.assertEqual(result.get("reason"), "editing")


if __name__ == "__main__":
    unittest.main()
