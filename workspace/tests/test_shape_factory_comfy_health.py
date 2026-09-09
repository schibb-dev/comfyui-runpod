#!/usr/bin/env python3
"""Drain-owned Comfy health circuit (backoff until the next successful probe)."""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "workspace" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from shape_factory_comfy_health import (  # noqa: E402
    _cli,
    comfy_health_in_backoff,
    default_comfy_health_path,
    observe_comfy_queue_result,
    snapshot_comfy_health,
)
import shape_factory as sf  # noqa: E402


class ComfyHealthTests(unittest.TestCase):
    def test_observe_opens_and_holds_then_clears(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            t0 = 1_000_000.0
            state = observe_comfy_queue_result(root, ok=False, error="reset", now=t0)
            self.assertEqual(state["status"], "backoff")
            self.assertEqual(state["consecutive_failures"], 1)
            self.assertEqual(state["backoff_sec"], 60)
            self.assertTrue(comfy_health_in_backoff(state, now=t0 + 10))
            # UI-style repeats during the window must not climb the ladder.
            again = observe_comfy_queue_result(root, ok=False, error="reset again", now=t0 + 10)
            self.assertEqual(again["consecutive_failures"], 1)
            self.assertTrue(comfy_health_in_backoff(again, now=t0 + 10))
            # Window elapsed: next failure climbs.
            later = observe_comfy_queue_result(root, ok=False, error="still down", now=t0 + 61)
            self.assertEqual(later["consecutive_failures"], 2)
            self.assertEqual(later["backoff_sec"], 180)
            pub = snapshot_comfy_health(root, now=t0 + 61)
            self.assertEqual(pub["status"], "backoff")
            self.assertGreater(pub["retry_in_sec"], 100)
            cleared = observe_comfy_queue_result(root, ok=True, running=1, pending=0, now=t0 + 250)
            self.assertEqual(cleared["status"], "ok")
            self.assertFalse(comfy_health_in_backoff(cleared, now=t0 + 250))
            self.assertEqual(snapshot_comfy_health(root, now=t0 + 250)["ok"], True)

    def test_ensure_skips_probe_while_backoff_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            t0 = 2_000_000.0
            observe_comfy_queue_result(root, ok=False, error="down", now=t0)
            with mock.patch.object(sf, "comfy_waiting_queue_empty") as probe:
                gate = sf.ensure_comfy_submit_ready(
                    "http://x",
                    data_root=root,
                    now=t0 + 5,
                )
                probe.assert_not_called()
            self.assertFalse(gate.get("ready"))
            self.assertEqual(gate.get("reason"), "comfy_backoff")
            self.assertGreater(int(gate.get("retry_in_sec") or 0), 0)

    def test_ensure_probe_fail_trips_and_success_clears(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            t0 = 3_000_000.0
            with mock.patch.object(
                sf,
                "comfy_waiting_queue_empty",
                side_effect=ConnectionResetError(104, "Connection reset by peer"),
            ):
                gate = sf.ensure_comfy_submit_ready("http://x", data_root=root, now=t0)
            self.assertEqual(gate.get("reason"), "comfy_not_ready")
            self.assertTrue(default_comfy_health_path(root).is_file())
            with mock.patch.object(sf, "comfy_waiting_queue_empty", return_value=(True, 0, 0)):
                ready = sf.ensure_comfy_submit_ready("http://x", data_root=root, now=t0 + 61)
            self.assertTrue(ready.get("ready"))
            self.assertEqual(snapshot_comfy_health(root, now=t0 + 61)["status"], "ok")

    def test_submit_job_file_respects_health_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            observe_comfy_queue_result(root, ok=False, error="down", now=time.time())
            job_path = root / "job.job.json"
            job_path.write_text(
                json.dumps({"job_key": "t1", "submit": {"status": "pending"}}),
                encoding="utf-8",
            )
            with mock.patch.object(sf, "comfy_waiting_queue_empty") as probe:
                result = sf.submit_job_file(
                    job_path,
                    server="http://x",
                    data_root=root,
                    pending_only=True,
                )
                probe.assert_not_called()
            self.assertTrue(result.get("skipped"))
            self.assertEqual(result.get("reason"), "comfy_backoff")
            saved = json.loads(job_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["submit"]["status"], "pending")

    def test_cli_gate_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch("sys.stdout"):
                self.assertEqual(_cli(["show", "--data-root", str(root)]), 0)
                self.assertEqual(_cli(["gate", "--data-root", str(root)]), 0)
                self.assertEqual(_cli(["fail", "--data-root", str(root), "--error", "x"]), 0)
                self.assertEqual(_cli(["gate", "--data-root", str(root)]), 3)
                self.assertEqual(_cli(["ok", "--data-root", str(root), "--running", "0", "--pending", "0"]), 0)
                self.assertEqual(_cli(["gate", "--data-root", str(root)]), 0)


if __name__ == "__main__":
    unittest.main()
