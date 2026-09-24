#!/usr/bin/env python3
"""Durable hourly GPU pause lock used by still-tag occupy/release."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import suspend_comfy_queue as scq


def _write_schedule(root: Path, *, enabled: bool) -> None:
    sf = root / "shape_factory"
    sf.mkdir(parents=True, exist_ok=True)
    (sf / "hourly-schedule.json").write_text(
        json.dumps({"interval_minutes": 15, "enabled": enabled}, indent=2) + "\n",
        encoding="utf-8",
    )


class HourlyGpuPauseTests(unittest.TestCase):
    def test_acquire_release_restores_prior_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_schedule(root, enabled=True)
            acq = scq.acquire_hourly_gpu_pause(data_root=root, paused_by="still_tag")
            self.assertTrue(acq["ok"])
            self.assertTrue(acq["restore_enabled"])
            self.assertTrue(scq.hourly_gpu_pause_status(data_root=root)["active"])
            sch = json.loads((root / "shape_factory" / "hourly-schedule.json").read_text())
            self.assertFalse(sch["enabled"])

            rel = scq.release_hourly_gpu_pause(data_root=root)
            self.assertTrue(rel["released"])
            sch2 = json.loads((root / "shape_factory" / "hourly-schedule.json").read_text())
            self.assertTrue(sch2["enabled"])
            self.assertFalse(scq.hourly_gpu_pause_status(data_root=root)["active"])

    def test_nested_acquire_preserves_original_restore_intent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_schedule(root, enabled=True)
            scq.acquire_hourly_gpu_pause(data_root=root)
            # Second session sees enabled=false but must keep restore_enabled=true.
            acq2 = scq.acquire_hourly_gpu_pause(data_root=root)
            self.assertTrue(acq2["nested"])
            self.assertTrue(acq2["restore_enabled"])
            self.assertEqual(acq2["depth"], 2)

            rel1 = scq.release_hourly_gpu_pause(data_root=root)
            self.assertFalse(rel1["released"])
            self.assertEqual(rel1.get("reason"), "nested_hold")
            sch = json.loads((root / "shape_factory" / "hourly-schedule.json").read_text())
            self.assertFalse(sch["enabled"])

            rel2 = scq.release_hourly_gpu_pause(data_root=root)
            self.assertTrue(rel2["released"])
            sch2 = json.loads((root / "shape_factory" / "hourly-schedule.json").read_text())
            self.assertTrue(sch2["enabled"])

    def test_operator_enable_clears_pause_lock(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_schedule(root, enabled=True)
            scq.acquire_hourly_gpu_pause(data_root=root)
            out = scq.set_hourlies_enabled(enabled=True, data_root=root)
            self.assertTrue(out.get("cleared_gpu_pause"))
            self.assertFalse(scq.hourly_gpu_pause_status(data_root=root)["active"])
            sch = json.loads((root / "shape_factory" / "hourly-schedule.json").read_text())
            self.assertTrue(sch["enabled"])

    def test_collect_ops_status_exposes_suspend(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output = root / "output"
            control = output / "experiments" / "_status" / "comfy_queue_ledger_control.json"
            control.parent.mkdir(parents=True)
            control.write_text(json.dumps({"paused": True}), encoding="utf-8")
            _write_schedule(root, enabled=True)
            scq.acquire_hourly_gpu_pause(data_root=root, paused_by="still_tag")

            def fake_run(cmd, *, check=True):
                joined = " ".join(cmd)
                if "is-active" in joined:
                    return mock.Mock(returncode=0, stdout="inactive\n", stderr="")
                if "is-enabled" in joined:
                    return mock.Mock(returncode=1, stdout="disabled\n", stderr="")
                if "docker" in joined and "inspect" in joined:
                    return mock.Mock(returncode=0, stdout="exited\n", stderr="")
                return mock.Mock(returncode=1, stdout="", stderr="no")

            with mock.patch.object(scq, "_run", side_effect=fake_run):
                with mock.patch.object(scq, "_queue_counts", return_value=(0, 0)):
                    out = scq.collect_ops_status(
                        server="http://127.0.0.1:8188",
                        output_root=output,
                        data_root=root,
                    )
            self.assertTrue(out["suspend"]["active"])
            self.assertIn("ledger", out["suspend"]["reasons"])
            self.assertIn("still_tag", out["suspend"]["reasons"])
            self.assertTrue(out["gpu_pause"]["active"])
            self.assertFalse(out["hourly"]["enabled"])


if __name__ == "__main__":
    unittest.main()
