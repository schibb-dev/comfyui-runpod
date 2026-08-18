#!/usr/bin/env python3
"""Tests for Comfy ops status helpers used by the ledger UI."""

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


class CollectOpsStatusTests(unittest.TestCase):
    def test_maps_feeders_and_control_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output_root = Path(td)
            control = output_root / "experiments" / "_status" / "comfy_queue_ledger_control.json"
            control.parent.mkdir(parents=True)
            control.write_text(
                json.dumps(
                    {
                        "paused": True,
                        "last_park_at": "2026-08-17T18:43:41Z",
                        "last_park": {"added": 15, "skipped": 0, "no_prompt": 0},
                    }
                ),
                encoding="utf-8",
            )

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
                    with mock.patch(
                        "shape_factory_hourly.load_hourly_schedule",
                        return_value={"enabled": False},
                    ):
                        out = scq.collect_ops_status(
                            server="http://127.0.0.1:8188",
                            output_root=output_root,
                        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["comfy"]["running"], 0)
        self.assertEqual(out["drain"]["active"], False)
        self.assertFalse(out["watch_queue"]["running"])
        self.assertEqual(out["hourly"]["enabled"], False)
        self.assertTrue(out["ledger"]["paused"])
        self.assertEqual(out["ledger"]["last_park"]["added"], 15)


if __name__ == "__main__":
    unittest.main()
