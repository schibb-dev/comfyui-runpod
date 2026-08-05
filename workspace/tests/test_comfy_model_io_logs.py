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

from comfy_model_io_logs import (  # noqa: E402
    ModelIoFollower,
    parse_model_io_from_comfy_logs,
    slice_new_log_entries,
)


def _iso(epoch_s: float) -> str:
    return (
        _dt.datetime.fromtimestamp(epoch_s, tz=_dt.timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


class ComfyModelIoLogsTests(unittest.TestCase):
    def test_slice_skips_history_on_first_cursor(self) -> None:
        entries = [
            {"t": _iso(100.0), "m": "Requested to load WanVAE"},
            {"t": _iso(104.0), "m": "loaded completely; 1 MB usable, 1 MB loaded, full load: True"},
        ]
        new, key, ts = slice_new_log_entries(entries, cursor_key=None)
        self.assertEqual(new, [])
        self.assertTrue(key)
        new2, key2, _ts2 = slice_new_log_entries(
            entries
            + [
                {
                    "t": _iso(110.0),
                    "m": "Requested to load WAN21",
                }
            ],
            cursor_key=key,
            cursor_ts=ts,
        )
        self.assertEqual(len(new2), 1)
        self.assertIn("WAN21", new2[0]["m"])
        self.assertNotEqual(key2, key)

    def test_follower_attributes_loads_and_switch(self) -> None:
        t0 = 1_800_000_000.0
        f = ModelIoFollower()
        events = f.note_running_prompt("prompt-a", now_ts=t0)
        self.assertTrue(any(e["type"] == "model_switch" for e in events))

        # First feed: seed cursor (skip history).
        seed = [
            {"t": _iso(t0 - 50), "m": "noise"},
        ]
        self.assertEqual(f.feed_entries(seed), [])

        batch = [
            {"t": _iso(t0 + 1.0), "m": "Requested to load WanVAE"},
            {
                "t": _iso(t0 + 5.0),
                "m": "loaded completely; 10484.02 MB usable, 242.03 MB loaded, full load: True",
            },
            {"t": _iso(t0 + 6.0), "m": "Requested to load WAN21"},
            {"t": _iso(t0 + 14.0), "m": "[MultiGPU DisTorch V2] DisTorch loading completed."},
        ]
        # Need to include seed + batch so cursor advances from seed key.
        ev = f.feed_entries(seed + batch)
        loads = [e for e in ev if e["type"] == "model_load"]
        self.assertEqual(len(loads), 2)
        self.assertEqual(loads[0]["name"], "WanVAE")
        self.assertAlmostEqual(float(loads[0]["sec"]), 4.0, places=2)
        self.assertEqual(loads[0]["prompt_id"], "prompt-a")
        self.assertEqual(loads[1]["method"], "distorch")
        rollup = f.current_rollup() or {}
        self.assertAlmostEqual(float(rollup["load_sec"]), 12.0, places=2)
        self.assertEqual(rollup["models"], ["WanVAE", "WAN21"])

        switch = f.note_running_prompt("prompt-b", now_ts=t0 + 20.0)
        self.assertTrue(any(e["type"] == "model_prompt_closed" for e in switch))
        self.assertTrue(any(e.get("to_prompt_id") == "prompt-b" for e in switch if e["type"] == "model_switch"))
        closed = next(e for e in switch if e["type"] == "model_prompt_closed")
        self.assertAlmostEqual(float(closed["rollup"]["load_sec"]), 12.0, places=2)

        # Warm switch: no loads under prompt-b yet.
        warm = f.current_rollup() or {}
        self.assertEqual(warm.get("load_sec"), 0.0)
        self.assertEqual(warm.get("prompt_id"), "prompt-b")

    def test_batch_window_parser_still_works(self) -> None:
        t0 = 1_700_100_000.0
        entries = [
            {"t": _iso(t0 + 0.0), "m": "Requested to load WanVAE"},
            {
                "t": _iso(t0 + 4.5),
                "m": "loaded completely; 10484.02 MB usable, 242.03 MB loaded, full load: True",
            },
            {"t": _iso(t0 + 900.0), "m": "Got an OOM, unloading all loaded models."},
            {"t": _iso(t0 + 912.0), "m": "Requested to load CLIPVisionModelProjection"},
        ]
        out = parse_model_io_from_comfy_logs(entries, window_start_ts=t0, window_end_ts=t0 + 900.0)
        self.assertEqual(out["totals"]["load_count"], 1)
        self.assertEqual(out["unloads"][0]["kind"], "oom_all")
        self.assertAlmostEqual(out["unloads"][0]["to_next_load_sec"], 12.0, places=2)


if __name__ == "__main__":
    unittest.main()
