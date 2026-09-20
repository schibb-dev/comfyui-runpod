#!/usr/bin/env python3
"""Tests for still-tag index-hour schedule + drain policy."""

from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class StillTagIndexHourTests(unittest.TestCase):
    def test_schedule_roundtrip_and_window(self) -> None:
        from vision_still_tags import index_window_status, load_schedule, save_schedule

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shape_factory").mkdir(parents=True)
            sch = save_schedule(
                {
                    "enabled": True,
                    "mode": "clock",
                    "timezone": "UTC",
                    "window_start": "02:00",
                    "window_duration_min": 120,
                    "front": True,
                    "auto_drain_on_enqueue": False,
                },
                data_root=root,
            )
            loaded = load_schedule(data_root=root)
            self.assertTrue(loaded["enabled"])
            self.assertEqual(loaded["window_start"], "02:00")

            inside = index_window_status(
                sch, now=dt.datetime(2026, 8, 28, 2, 30, tzinfo=dt.timezone.utc)
            )
            self.assertTrue(inside["in_window"])
            self.assertEqual(inside["reason"], "ok")

            outside = index_window_status(
                sch, now=dt.datetime(2026, 8, 28, 12, 0, tzinfo=dt.timezone.utc)
            )
            self.assertFalse(outside["in_window"])
            self.assertEqual(outside["reason"], "outside_window")

            disabled = dict(sch)
            disabled["enabled"] = False
            off = index_window_status(
                disabled, now=dt.datetime(2026, 8, 28, 2, 30, tzinfo=dt.timezone.utc)
            )
            self.assertEqual(off["reason"], "disabled")

    def test_should_auto_drain_defaults_off(self) -> None:
        from vision_still_tags import should_auto_drain_on_enqueue

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shape_factory").mkdir(parents=True)
            self.assertFalse(should_auto_drain_on_enqueue(data_root=root))
            self.assertTrue(should_auto_drain_on_enqueue(data_root=root, drain_now=True))

    def test_drain_respects_disabled_schedule(self) -> None:
        from vision_still_tags import drain_backlog, ensure_db, default_db_path, save_schedule

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shape_factory").mkdir(parents=True)
            ensure_db(default_db_path(data_root=root))
            save_schedule({"enabled": False}, data_root=root)
            out = drain_backlog(data_root=root, respect_schedule=True, force=False)
            self.assertTrue(out.get("ok"))
            self.assertTrue(out.get("skipped"))
            self.assertEqual(out.get("reason"), "schedule_disabled")

    def test_comfy_front_payload(self) -> None:
        from vision_slice_runner import CaptionRequest, ComfyCaptionRunner, ComfyRunnerConfig

        cfg = ComfyRunnerConfig(server="http://127.0.0.1:8188", front=True)
        runner = ComfyCaptionRunner(cfg)
        captured: dict = {}

        def fake_http(method, url, payload=None, timeout_s=30.0):
            captured["payload"] = payload
            return {"prompt_id": "test-pid"}

        with tempfile.TemporaryDirectory() as td:
            img = Path(td) / "x.jpg"
            img.write_bytes(b"fakejpeg-bytes")
            with mock.patch("vision_slice_runner._http_json", side_effect=fake_http), mock.patch.object(
                runner, "_image_ref_for_load_image", return_value="vision_v1/x.jpg"
            ), mock.patch.object(
                runner,
                "_wait_history",
                return_value={"outputs": {"4": {"text": ["1girl, solo"]}}},
            ):
                runner.caption(CaptionRequest(image_path=img, asset_relpath="x.jpg"))

        self.assertIs(captured.get("payload", {}).get("front"), True)
        self.assertEqual(captured["payload"]["prompt"]["1"]["class_type"], "LoadImage")

    def test_caption_many_builds_imagebatch_graph(self) -> None:
        from vision_slice_runner import CaptionRequest, ComfyCaptionRunner, ComfyRunnerConfig

        cfg = ComfyRunnerConfig(server="http://127.0.0.1:8188", front=True, image_mode="input_ref")
        runner = ComfyCaptionRunner(cfg)
        captured: dict = {}

        def fake_http(method, url, payload=None, timeout_s=30.0):
            captured["payload"] = payload
            return {"prompt_id": "batch-pid"}

        with tempfile.TemporaryDirectory() as td:
            imgs = []
            for name in ("a.jpg", "b.jpg", "c.jpg"):
                p = Path(td) / name
                p.write_bytes(b"fakejpeg-bytes")
                imgs.append(p)
            with mock.patch.dict("os.environ", {"COMFYUI_BIND_INPUT_DIR": td}), mock.patch(
                "vision_slice_runner._http_json", side_effect=fake_http
            ), mock.patch.object(
                runner,
                "_wait_history",
                return_value={"outputs": {"3": {"string": ["one", "two", "three"]}}},
            ):
                out = runner.caption_many(
                    [CaptionRequest(image_path=p, asset_relpath=p.name) for p in imgs]
                )

        self.assertEqual([r.caption for r in out], ["one", "two", "three"])
        prompt = captured["payload"]["prompt"]
        self.assertEqual(prompt["71"]["class_type"], "ImageBatch")
        self.assertEqual(prompt["3"]["inputs"]["image"], ["72", 0])
        self.assertIs(captured["payload"].get("front"), True)

    def test_enqueue_then_force_dry_run_drain(self) -> None:
        """Demo path: backlog enqueue without kick, then force drain with dry-run."""
        import hashlib

        from vision_still_tags import (
            backlog_stats,
            drain_backlog,
            enqueue_run,
            should_auto_drain_on_enqueue,
        )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shape_factory").mkdir(parents=True)
            cid = hashlib.sha256(b"index-hour-smoke").hexdigest()
            # Fake still on disk so resolve_targets can find it via content_ids path search.
            # Prefer explicit content_ids so we don't need a catalog.
            with mock.patch(
                "vision_still_tags.resolve_targets",
                return_value=[
                    {
                        "content_id": cid,
                        "path": str(root / f"SSS{cid}.jpeg"),
                        "relpath": f"input/SSS{cid}.jpeg",
                        "missing": False,
                    }
                ],
            ):
                self.assertFalse(should_auto_drain_on_enqueue(data_root=root))
                enq = enqueue_run(
                    data_root=root,
                    content_ids=[cid],
                    only_missing=False,
                    force=True,
                    limit=1,
                    dry_run=True,
                )
                self.assertTrue(enq.get("ok"))
                self.assertEqual(enq.get("enqueued"), 1)
                mid = backlog_stats(data_root=root)
                self.assertGreaterEqual(int(mid.get("queued_runs") or 0), 1)

                out = drain_backlog(
                    data_root=root,
                    force=True,
                    respect_schedule=False,
                    front=True,
                    max_items=1,
                    provider_override="dry-run",
                )
            self.assertTrue(out.get("ok"))
            self.assertFalse(out.get("skipped"))
            self.assertGreaterEqual(int(out.get("done_items") or 0), 1)
            after = backlog_stats(data_root=root)
            self.assertEqual(int(after.get("queued_runs") or 0), 0)

    def test_scale_batch_fits_session_window(self) -> None:
        from vision_still_tags import scale_batch_for_session, session_item_budget

        self.assertEqual(scale_batch_for_session(session_minutes=15, sec_per_still=12), 32)
        self.assertEqual(scale_batch_for_session(session_minutes=15, sec_per_still=30), 30)
        self.assertEqual(
            scale_batch_for_session(session_minutes=15, sec_per_still=12, pending_count=5),
            5,
        )
        self.assertEqual(session_item_budget(session_minutes=15, sec_per_still=12), 75)
        self.assertEqual(session_item_budget(session_minutes=15, sec_per_still=12, cap=48), 48)

    def test_sla_due_after_max_wait(self) -> None:
        from vision_still_tags import index_window_status, sla_due_status

        sch = {
            "enabled": True,
            "mode": "sla",
            "max_wait_hours": 6,
            "resume_gap_min": 20,
            "session_minutes": 15,
        }
        now = dt.datetime(2026, 9, 19, 18, 0, tzinfo=dt.timezone.utc)
        waiting = sla_due_status(
            schedule=sch,
            backlog={"queued_runs": 1, "queued_targets": 8, "oldest_queued_at": "2026-09-19T16:00:00Z"},
            session={"status": "idle"},
            now=now,
        )
        self.assertFalse(waiting["due"])
        self.assertEqual(waiting["reason"], "waiting_sla")

        due = sla_due_status(
            schedule=sch,
            backlog={"queued_runs": 1, "queued_targets": 8, "oldest_queued_at": "2026-09-19T12:00:00Z"},
            session={"status": "idle"},
            now=now,
        )
        self.assertTrue(due["due"])
        self.assertEqual(due["reason"], "sla_due")

        gap = sla_due_status(
            schedule=sch,
            backlog={"queued_runs": 1, "queued_targets": 8, "oldest_queued_at": "2026-09-19T12:00:00Z"},
            session={"status": "idle", "ended_at": "2026-09-19T17:50:00Z"},
            now=now,
        )
        self.assertFalse(gap["due"])
        self.assertEqual(gap["reason"], "resume_gap")

        win = index_window_status(
            sch,
            now=now,
            backlog={"queued_runs": 1, "queued_targets": 8, "oldest_queued_at": "2026-09-19T12:00:00Z"},
            session={"status": "idle"},
        )
        self.assertTrue(win["in_window"])
        self.assertEqual(win["mode"], "sla")
        self.assertEqual(win["reason"], "sla_due")
        self.assertEqual(win["scaled_batch"], 8)

    def test_manual_enqueue_has_one_hour_sla(self) -> None:
        import hashlib

        from vision_still_tags import enqueue_run, infer_manual_request, sla_due_status

        self.assertTrue(infer_manual_request(content_ids=["abc"]))
        self.assertFalse(infer_manual_request(content_ids=None, collection_id=None))

        sch = {
            "enabled": True,
            "mode": "sla",
            "max_wait_hours": 6,
            "manual_max_wait_hours": 1,
            "resume_gap_min": 20,
        }
        now = dt.datetime(2026, 9, 19, 18, 0, tzinfo=dt.timezone.utc)
        backlog_only = sla_due_status(
            schedule=sch,
            backlog={
                "queued_runs": 1,
                "queued_targets": 8,
                "oldest_queued_at": "2026-09-19T16:00:00Z",
                "oldest_backlog_queued_at": "2026-09-19T16:00:00Z",
            },
            session={"status": "idle"},
            now=now,
        )
        self.assertFalse(backlog_only["due"])
        self.assertEqual(backlog_only["sla_class"], "backlog")
        self.assertEqual(backlog_only["max_wait_hours"], 6)

        manual_due = sla_due_status(
            schedule=sch,
            backlog={
                "queued_runs": 1,
                "queued_targets": 2,
                "oldest_queued_at": "2026-09-19T16:00:00Z",
                "oldest_manual_queued_at": "2026-09-19T16:00:00Z",
            },
            session={"status": "idle"},
            now=now,
        )
        self.assertTrue(manual_due["due"])
        self.assertEqual(manual_due["reason"], "sla_due")
        self.assertEqual(manual_due["sla_class"], "manual")
        self.assertEqual(manual_due["max_wait_hours"], 1)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shape_factory").mkdir(parents=True)
            cid = hashlib.sha256(b"manual-sla").hexdigest()
            with mock.patch(
                "vision_still_tags.resolve_targets",
                return_value=[
                    {
                        "content_id": cid,
                        "path": str(root / f"SSS{cid}.jpeg"),
                        "relpath": f"input/SSS{cid}.jpeg",
                        "missing": False,
                    }
                ],
            ):
                enq = enqueue_run(
                    data_root=root,
                    content_ids=[cid],
                    only_missing=False,
                    force=True,
                    limit=1,
                    dry_run=True,
                )
                bulk = enqueue_run(
                    data_root=root,
                    only_missing=True,
                    force=True,
                    limit=1,
                    dry_run=True,
                    manual=False,
                )
            self.assertTrue(enq.get("manual"))
            self.assertEqual(enq.get("sla_hours"), 1)
            self.assertFalse(bulk.get("manual"))
            self.assertEqual(bulk.get("sla_hours"), 3)

    def test_drain_skips_sla_until_due(self) -> None:
        import hashlib

        from vision_still_tags import drain_backlog, enqueue_run, ensure_db, default_db_path, save_schedule

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shape_factory").mkdir(parents=True)
            ensure_db(default_db_path(data_root=root))
            save_schedule({"enabled": True, "mode": "sla", "max_wait_hours": 6}, data_root=root)
            out = drain_backlog(data_root=root, respect_schedule=True, force=False)
            self.assertTrue(out.get("ok"))
            self.assertTrue(out.get("skipped"))
            self.assertEqual(out.get("reason"), "no_backlog")

            cid = hashlib.sha256(b"sla-wait").hexdigest()
            with mock.patch(
                "vision_still_tags.resolve_targets",
                return_value=[
                    {
                        "content_id": cid,
                        "path": str(root / f"SSS{cid}.jpeg"),
                        "relpath": f"input/SSS{cid}.jpeg",
                        "missing": False,
                    }
                ],
            ):
                enqueue_run(
                    data_root=root,
                    content_ids=[cid],
                    only_missing=False,
                    force=True,
                    limit=1,
                    dry_run=True,
                )
            waiting = drain_backlog(data_root=root, respect_schedule=True, force=False)
            self.assertTrue(waiting.get("skipped"))
            self.assertEqual(waiting.get("reason"), "waiting_sla")

    def test_force_dry_run_skips_gpu_occupy(self) -> None:
        import hashlib

        from vision_still_tags import drain_backlog, enqueue_run

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shape_factory").mkdir(parents=True)
            cid = hashlib.sha256(b"occupy-skip").hexdigest()
            occupy = mock.Mock(return_value={"ok": True, "hourly_was_enabled": False})
            with mock.patch(
                "vision_still_tags.resolve_targets",
                return_value=[
                    {
                        "content_id": cid,
                        "path": str(root / f"SSS{cid}.jpeg"),
                        "relpath": f"input/SSS{cid}.jpeg",
                        "missing": False,
                    }
                ],
            ), mock.patch("vision_still_tags.occupy_gpu_for_tagging", occupy), mock.patch(
                "vision_still_tags.release_gpu_after_tagging", mock.Mock(return_value={"ok": True})
            ):
                enqueue_run(
                    data_root=root,
                    content_ids=[cid],
                    only_missing=False,
                    force=True,
                    limit=1,
                    dry_run=True,
                )
                out = drain_backlog(
                    data_root=root,
                    force=True,
                    respect_schedule=False,
                    provider_override="dry-run",
                    max_items=1,
                )
            self.assertTrue(out.get("ok"))
            self.assertFalse(out.get("occupied"))
            occupy.assert_not_called()

    def test_interval_elapsed_and_scheduled_tick(self) -> None:
        from vision_still_tags import interval_elapsed, run_scheduled_tick, save_schedule, save_tag_tick

        now = dt.datetime(2026, 9, 19, 18, 0, tzinfo=dt.timezone.utc)
        self.assertTrue(interval_elapsed(None, 15, now=now))
        self.assertFalse(interval_elapsed("2026-09-19T17:50:00Z", 15, now=now))
        self.assertTrue(interval_elapsed("2026-09-19T17:40:00Z", 15, now=now))

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shape_factory").mkdir(parents=True)
            save_schedule(
                {
                    "enabled": True,
                    "mode": "sla",
                    "scan_interval_min": 15,
                    "evaluate_interval_min": 15,
                    "auto_enqueue_untagged": False,
                },
                data_root=root,
            )
            save_tag_tick(
                {"last_scan_at": "2026-09-19T17:50:00Z", "last_evaluate_at": "2026-09-19T17:50:00Z"},
                data_root=root,
            )
            waiting = run_scheduled_tick(data_root=root, now=now)
            self.assertTrue(waiting.get("skipped"))
            self.assertEqual(waiting.get("reason"), "tick_wait")

            with mock.patch(
                "vision_still_tags.scan_new_stills",
                return_value={"ok": True, "inserted": 0, "updated": 0},
            ):
                due = run_scheduled_tick(data_root=root, now=now, force_scan=True, force_evaluate=True)
            self.assertFalse(due.get("skipped"))
            self.assertTrue(due.get("scanned"))
            self.assertTrue(due.get("evaluated"))
            self.assertIsNone(due.get("enqueue"))

    def test_schedule_knobs_roundtrip_and_window(self) -> None:
        from vision_still_tags import DEFAULT_SCHEDULE, index_window_status, load_schedule, save_schedule

        self.assertEqual(DEFAULT_SCHEDULE["kill_after_min"], 60)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shape_factory").mkdir(parents=True)
            save_schedule(
                {
                    "enabled": True,
                    "mode": "sla",
                    "scan_interval_min": 7,
                    "evaluate_interval_min": 11,
                    "max_wait_hours": 2.5,
                    "manual_max_wait_hours": 0.5,
                    "session_minutes": 8,
                    "kill_after_min": 25,
                    "resume_gap_min": 4,
                },
                data_root=root,
            )
            loaded = load_schedule(data_root=root)
            self.assertEqual(loaded["scan_interval_min"], 7)
            self.assertEqual(loaded["evaluate_interval_min"], 11)
            self.assertEqual(loaded["max_wait_hours"], 2.5)
            self.assertEqual(loaded["manual_max_wait_hours"], 0.5)
            self.assertEqual(loaded["session_minutes"], 8)
            self.assertEqual(loaded["kill_after_min"], 25)
            self.assertEqual(loaded["resume_gap_min"], 4)
            win = index_window_status(loaded, now=dt.datetime(2026, 9, 19, 18, 0, tzinfo=dt.timezone.utc))
            self.assertEqual(win["scan_interval_min"], 7)
            self.assertEqual(win["evaluate_interval_min"], 11)
            self.assertEqual(win["session_minutes"], 8)
            self.assertEqual(win["kill_after_min"], 25)
            self.assertEqual(win["manual_max_wait_hours"], 0.5)

    def test_process_run_requeues_after_hard_deadline(self) -> None:
        import hashlib
        import time

        from vision_still_tags import connect, default_db_path, enqueue_run, get_run, process_run

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shape_factory").mkdir(parents=True)
            cid = hashlib.sha256(b"kill-deadline").hexdigest()
            with mock.patch(
                "vision_still_tags.resolve_targets",
                return_value=[
                    {
                        "content_id": cid,
                        "path": str(root / f"SSS{cid}.jpeg"),
                        "relpath": f"input/SSS{cid}.jpeg",
                        "missing": False,
                    }
                ],
            ):
                enq = enqueue_run(
                    data_root=root,
                    content_ids=[cid],
                    only_missing=False,
                    force=True,
                    limit=1,
                    dry_run=True,
                )
            run_id = str(enq["run_id"])
            out = process_run(
                data_root=root,
                run_id=run_id,
                batch_n=1,
                hard_deadline=time.time() - 1,
            )
            self.assertTrue(out.get("ok"))
            self.assertTrue(out.get("killed"))
            self.assertEqual(out.get("reason"), "kill_after_min")
            con = connect(default_db_path(data_root=root))
            try:
                run = get_run(con, run_id)
            finally:
                con.close()
            self.assertEqual(run["status"], "queued")
            self.assertEqual(int(run["done_count"] or 0), 0)

    def test_force_drain_empty_queue_skips_occupy(self) -> None:
        from vision_still_tags import drain_backlog, ensure_db, default_db_path

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shape_factory").mkdir(parents=True)
            ensure_db(default_db_path(data_root=root))
            occupy = mock.Mock(return_value={"ok": True, "hourly_was_enabled": False})
            with mock.patch("vision_still_tags.occupy_gpu_for_tagging", occupy):
                out = drain_backlog(
                    data_root=root,
                    force=True,
                    respect_schedule=False,
                    provider_override="dry-run",
                )
            self.assertTrue(out.get("ok"))
            self.assertTrue(out.get("skipped"))
            self.assertEqual(out.get("reason"), "no_backlog")
            occupy.assert_not_called()

    def test_wait_history_raises_on_execution_error(self) -> None:
        from vision_slice_runner import ComfyCaptionRunner, ComfyRunnerConfig, history_execution_error

        entry = {
            "status": {
                "status_str": "error",
                "completed": False,
                "messages": [
                    [
                        "execution_error",
                        {
                            "node_id": "17",
                            "node_type": "LoadImage",
                            "exception_message": "Truncated File Read",
                        },
                    ]
                ],
            }
        }
        self.assertIn("Truncated File Read", history_execution_error(entry) or "")

        cfg = ComfyRunnerConfig(server="http://127.0.0.1:8188", timeout_s=2, poll_interval_s=0.01)
        runner = ComfyCaptionRunner(cfg)
        with mock.patch("vision_slice_runner._http_json", return_value={"pid-1": entry}):
            with self.assertRaises(RuntimeError) as ctx:
                runner._wait_history("pid-1")
        self.assertIn("Truncated File Read", str(ctx.exception))

    def test_output_still_is_staged_under_input_factory(self) -> None:
        from vision_still_tags import _ensure_still_under_comfy_input

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_root = root / "data"
            input_root = data_root / "input"
            og = data_root / "output" / "og" / "2026-09-07"
            input_root.mkdir(parents=True)
            og.mkdir(parents=True)
            src = og / "FB9_GEX_shape_00001.png"
            src.write_bytes(b"\x89PNG\r\n\x1a\n" + (b"y" * 80))
            staged = _ensure_still_under_comfy_input(
                src, data_root=data_root, input_root=input_root
            )
            self.assertTrue(str(staged).startswith(str(input_root)))
            self.assertIn("_factory", staged.parts)
            self.assertTrue(staged.is_file())
            self.assertEqual(staged.read_bytes(), src.read_bytes())

    def test_truncated_png_is_skipped(self) -> None:
        from vision_still_tags import _still_unreadable_reason

        with tempfile.TemporaryDirectory() as td:
            tiny = Path(td) / "x.png"
            tiny.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 19)
            reason = _still_unreadable_reason(tiny)
            self.assertIsNotNone(reason)
            self.assertIn("truncated", str(reason).lower())

    def test_input_ref_uploads_when_file_outside_comfy_input(self) -> None:
        from vision_slice_runner import ComfyCaptionRunner, ComfyRunnerConfig

        cfg = ComfyRunnerConfig(server="http://127.0.0.1:8188", image_mode="input_ref")
        runner = ComfyCaptionRunner(cfg)
        upload = mock.Mock(return_value={"name": "out.png", "subfolder": "vision_v1"})
        with tempfile.TemporaryDirectory() as td:
            img = Path(td) / "out.png"
            img.write_bytes(b"png-bytes")
            with mock.patch("vision_slice_runner._http_upload_image", upload):
                ref = runner._image_ref_for_load_image(img, relpath="input/out.png")
        self.assertEqual(ref, "vision_v1/out.png")
        upload.assert_called_once()

    def test_input_ref_keeps_local_name_inside_comfy_input(self) -> None:
        from vision_slice_runner import ComfyCaptionRunner, ComfyRunnerConfig

        cfg = ComfyRunnerConfig(server="http://127.0.0.1:8188", image_mode="input_ref")
        runner = ComfyCaptionRunner(cfg)
        upload = mock.Mock(return_value={"name": "x.jpg", "subfolder": "vision_v1"})
        with tempfile.TemporaryDirectory() as td:
            img = Path(td) / "x.jpg"
            img.write_bytes(b"jpeg-bytes")
            with mock.patch.dict("os.environ", {"COMFYUI_BIND_INPUT_DIR": td}), mock.patch(
                "vision_slice_runner._http_upload_image", upload
            ):
                ref = runner._image_ref_for_load_image(img, relpath="input/x.jpg")
        self.assertEqual(ref, "x.jpg")
        upload.assert_not_called()

    def test_empty_queued_run_is_cancelled_and_skips_occupy(self) -> None:
        from vision_still_tags import (
            cancel_empty_queued_runs,
            connect,
            default_db_path,
            drain_backlog,
            ensure_db,
            sla_due_status,
        )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shape_factory").mkdir(parents=True)
            db = default_db_path(data_root=root)
            ensure_db(db)
            con = connect(db)
            try:
                con.execute(
                    """
                    INSERT INTO still_tag_runs(
                      run_id, status, scope_json, enqueued_at, total, done_count, error_count, skipped_count
                    ) VALUES (?, 'queued', ?, '2026-09-19T17:48:29Z', 0, 0, 0, 0)
                    """,
                    ("still_tag_empty", '{"targets":[],"manual":false}'),
                )
                con.commit()
            finally:
                con.close()
            due = sla_due_status(
                schedule={"enabled": True, "mode": "sla", "max_wait_hours": 1},
                backlog={"queued_runs": 1, "queued_targets": 0, "oldest_queued_at": "2026-09-19T17:48:29Z"},
                session={"status": "idle"},
            )
            self.assertFalse(due["due"])
            self.assertEqual(due["reason"], "no_backlog")
            occupy = mock.Mock(return_value={"ok": True, "hourly_was_enabled": False})
            with mock.patch("vision_still_tags.occupy_gpu_for_tagging", occupy):
                out = drain_backlog(
                    data_root=root,
                    force=True,
                    respect_schedule=False,
                    provider_override="dry-run",
                )
            self.assertEqual(out.get("reason"), "no_backlog")
            occupy.assert_not_called()
            self.assertEqual(cancel_empty_queued_runs(data_root=root), 0)


if __name__ == "__main__":
    unittest.main()
