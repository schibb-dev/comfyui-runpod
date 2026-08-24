#!/usr/bin/env python3
"""Tests for shared creation-control façade."""

from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401
import shape_factory_creation_control as cc


class TestShapeFactoryCreationControl(unittest.TestCase):
    def test_mutate_begin_edit_delegates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch("shape_factory.begin_job_edit", return_value={"ok": True, "status": "editing"}) as beg:
                out = cc.mutate_job(
                    action="begin_edit",
                    data_root=root,
                    server="http://comfy.test",
                    job_key="job-1",
                )
        self.assertTrue(out.get("ok"))
        self.assertEqual((out.get("control") or {}).get("actor"), "operator")
        beg.assert_called_once()

    def test_mutate_unknown_action(self) -> None:
        out = cc.mutate_job(action="nope", data_root=Path("/tmp"))
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("error"), "unknown_action")

    def test_mutate_finish_edit_passes_action(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch("shape_factory.finish_job_edit", return_value={"ok": True, "action": "cancel"}) as fin:
                out = cc.mutate_job(
                    action="finish_edit",
                    data_root=root,
                    finish_action="cancel",
                    server="http://comfy.test",
                    job_key="job-1",
                )
        self.assertTrue(out.get("ok"))
        fin.assert_called_once()
        kwargs = fin.call_args.kwargs
        self.assertEqual(kwargs.get("action"), "cancel")

    def test_mutate_update_trim_delegates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch(
                "shape_factory.update_pending_job_vhs_window",
                return_value={"ok": True, "job_key": "job-1"},
            ) as trim:
                out = cc.mutate_job(
                    action="update_trim",
                    data_root=root,
                    job_key="job-1",
                    skip_first_frames=4,
                    frame_load_cap=44,
                    mark_in=0.3,
                    mark_out=1.2,
                )
        self.assertTrue(out.get("ok"))
        trim.assert_called_once()
        kwargs = trim.call_args.kwargs
        self.assertEqual(kwargs.get("skip_first_frames"), 4)
        self.assertEqual(kwargs.get("frame_load_cap"), 44)

    def test_mutate_discard_delegates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch("shape_factory.discard_pending_job", return_value={"ok": True}) as disc:
                out = cc.mutate_job(
                    action="discard",
                    data_root=root,
                    job_key="job-1",
                    expunge=True,
                    reason="user_removed",
                )
        self.assertTrue(out.get("ok"))
        disc.assert_called_once()
        kwargs = disc.call_args.kwargs
        self.assertTrue(kwargs.get("expunge"))
        self.assertEqual(kwargs.get("reason"), "user_removed")

    def test_mutate_finish_edit_rejects_bad_action(self) -> None:
        out = cc.mutate_job(action="finish_edit", data_root=Path("/tmp"), finish_action="oops")
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("error"), "bad_action")

    def test_mutate_unqueue_requires_prompt_or_job(self) -> None:
        out = cc.mutate_job(action="unqueue_to_pending", data_root=Path("/tmp"), server="http://comfy.test")
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("error"), "missing_prompt_id")

    def test_mutate_unqueue_with_prompt_delegates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch("shape_factory.unqueue_to_pending", return_value={"ok": True, "job_key": "job-1"}) as unq:
                out = cc.mutate_job(
                    action="unqueue_to_pending",
                    data_root=root,
                    server="http://comfy.test",
                    job_key="job-1",
                    prompt_id="abc123",
                    actor="hourly",
                    source_surface="hourly",
                    reason="retry",
                )
        self.assertTrue(out.get("ok"))
        self.assertEqual((out.get("control") or {}).get("actor"), "hourly")
        kwargs = unq.call_args.kwargs
        self.assertEqual(kwargs.get("prompt_id"), "abc123")
        self.assertEqual(kwargs.get("job_key"), "job-1")

    def test_create_generate_calls_cmd_generate(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            shape = root / "shape.yaml"
            pools = root / "pools.yaml"
            wf = root / "wf"
            jobs = root / "jobs"
            for p in (shape, pools):
                p.write_text("{}", encoding="utf-8")
            wf.mkdir()
            jobs.mkdir()
            with mock.patch("shape_factory.cmd_generate", return_value=0) as gen:
                out = cc.create_generate_job(
                    shape=shape,
                    pools=pools,
                    data_root=root,
                    workflow_dir=wf,
                    job_dir=jobs,
                    pick="replay",
                    limit=1,
                )
        self.assertTrue(out.get("ok"))
        gen.assert_called_once()
        args = gen.call_args.args[0]
        self.assertIsInstance(args, argparse.Namespace)
        self.assertEqual(args.pick, "replay")


if __name__ == "__main__":
    unittest.main()
