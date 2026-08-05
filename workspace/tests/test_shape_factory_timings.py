#!/usr/bin/env python3
from __future__ import annotations

import datetime as _dt
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "workspace" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import shape_factory as sf  # noqa: E402


def _ms(epoch_s: float) -> int:
    return int(epoch_s * 1000)


def _iso(epoch_s: float) -> str:
    return (
        _dt.datetime.fromtimestamp(epoch_s, tz=_dt.timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


class ShapeFactoryTimingsTests(unittest.TestCase):
    def test_parse_history_uses_error_not_cached(self) -> None:
        """OOM runs emit execution_cached early; true duration is start→execution_error."""
        t0 = 1_700_000_000.0
        history = {
            "status": {
                "status_str": "error",
                "completed": False,
                "messages": [
                    ["execution_start", {"prompt_id": "x", "timestamp": _ms(t0)}],
                    [
                        "execution_cached",
                        {"nodes": ["18", "82"], "prompt_id": "x", "timestamp": _ms(t0 + 0.024)},
                    ],
                    [
                        "execution_error",
                        {
                            "prompt_id": "x",
                            "node_id": "136",
                            "node_type": "SamplerCustomAdvanced",
                            "timestamp": _ms(t0 + 1127.371),
                        },
                    ],
                ],
            }
        }
        out = sf.parse_history_execution_timings(history)
        self.assertAlmostEqual(out["sec"], 1127.371, places=2)
        self.assertEqual(out["terminal"], "error")
        self.assertTrue(out.get("error"))
        self.assertEqual(out["source"], "history.messages")
        # Must not collapse to the cached blip.
        self.assertGreater(out["sec"], 60.0)

    def test_parse_history_success_ignores_intermediate_cached(self) -> None:
        t0 = 1_700_000_100.0
        history = {
            "status": {
                "status_str": "success",
                "completed": True,
                "messages": [
                    ["execution_start", {"timestamp": _ms(t0)}],
                    ["execution_cached", {"nodes": ["18"], "timestamp": _ms(t0 + 0.01)}],
                    ["execution_success", {"timestamp": _ms(t0 + 942.5)}],
                ],
            }
        }
        out = sf.parse_history_execution_timings(history)
        self.assertAlmostEqual(out["sec"], 942.5, places=2)
        self.assertEqual(out["terminal"], "success")
        self.assertNotIn("error", out)

    def test_parse_history_interrupted(self) -> None:
        t0 = 1_700_000_200.0
        history = {
            "status": {
                "messages": [
                    ["execution_start", {"timestamp": _ms(t0)}],
                    ["execution_interrupted", {"timestamp": _ms(t0 + 12.5)}],
                ]
            }
        }
        out = sf.parse_history_execution_timings(history)
        self.assertAlmostEqual(out["sec"], 12.5, places=2)
        self.assertEqual(out["terminal"], "interrupted")
        self.assertTrue(out.get("error"))

    def test_efficiency_skipped_for_error_runs(self) -> None:
        job = {
            "timings": {
                "workload": {"frames": 112, "steps": 20},
                "execution": {"sec": 1127.0, "error": True, "terminal": "error"},
                "totals": {"submit_to_complete_sec": 2000.0},
                "efficiency": {"frames_per_min_exec": 999999.0},
            }
        }
        sf.recompute_efficiency_metrics(job)
        eff = job["timings"]["efficiency"]
        self.assertNotIn("frames_per_min_exec", eff)
        self.assertNotIn("exec_sec_per_frame", eff)

    def test_parse_model_io_loads_and_oom_unload(self) -> None:
        t0 = 1_700_100_000.0
        entries = [
            {"t": _iso(t0 + 0.0), "m": "Requested to load WanVAE"},
            {
                "t": _iso(t0 + 4.5),
                "m": "loaded completely; 10484.02 MB usable, 242.03 MB loaded, full load: True",
            },
            {"t": _iso(t0 + 10.0), "m": "Requested to load WAN21"},
            {"t": _iso(t0 + 18.2), "m": "[MultiGPU DisTorch V2] DisTorch loading completed."},
            {"t": _iso(t0 + 900.0), "m": "Got an OOM, unloading all loaded models."},
            {"t": _iso(t0 + 912.0), "m": "Requested to load CLIPVisionModelProjection"},
            {
                "t": _iso(t0 + 912.5),
                "m": "loaded completely; 13817.66 MB usable, 1208.10 MB loaded, full load: True",
            },
        ]
        out = sf.parse_model_io_from_comfy_logs(
            entries,
            window_start_ts=t0,
            window_end_ts=t0 + 900.0,
        )
        self.assertEqual(out.get("source"), "comfy.internal.logs")
        loads = out.get("loads") or []
        self.assertEqual(len(loads), 2)
        self.assertEqual(loads[0]["name"], "WanVAE")
        self.assertAlmostEqual(loads[0]["sec"], 4.5, places=2)
        self.assertEqual(loads[0]["method"], "full")
        self.assertEqual(loads[1]["name"], "WAN21")
        self.assertAlmostEqual(loads[1]["sec"], 8.2, places=2)
        self.assertEqual(loads[1]["method"], "distorch")
        # Post-OOM CLIP load must not be attributed to this job.
        self.assertTrue(all(x["name"] != "CLIPVisionModelProjection" for x in loads))
        unloads = out.get("unloads") or []
        self.assertEqual(len(unloads), 1)
        self.assertEqual(unloads[0]["kind"], "oom_all")
        self.assertAlmostEqual(unloads[0]["to_next_load_sec"], 12.0, places=2)
        totals = out.get("totals") or {}
        self.assertAlmostEqual(totals["load_sec"], 12.7, places=2)
        self.assertEqual(totals["load_count"], 2)
        self.assertEqual(totals["unload_event_count"], 1)

    def test_attach_model_io_timings_uses_execution_window(self) -> None:
        t0 = 1_700_200_000.0
        job = {
            "timings": {
                "execution": {"started_ts": t0, "finished_ts": t0 + 60.0, "sec": 60.0},
            }
        }
        entries = [
            {"t": _iso(t0 + 1.0), "m": "Requested to load CLIPVisionModelProjection"},
            {
                "t": _iso(t0 + 2.0),
                "m": "loaded completely; 13575.63 MB usable, 1208.10 MB loaded, full load: True",
            },
        ]
        models = sf.attach_model_io_timings(job, server="http://unused", log_entries=entries)
        self.assertIsNotNone(models)
        self.assertEqual(job["timings"]["models"]["totals"]["load_count"], 1)
        self.assertAlmostEqual(job["timings"]["models"]["totals"]["load_sec"], 1.0, places=2)


if __name__ == "__main__":
    unittest.main()
