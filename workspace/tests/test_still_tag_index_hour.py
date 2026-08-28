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


if __name__ == "__main__":
    unittest.main()
