#!/usr/bin/env python3
"""Preview/debug/raw VHS combines must never be stored to disk."""

from __future__ import annotations

import unittest

from pathlib import Path

from shape_factory import (
    apply_api_slot_bindings,
    enforce_no_stored_preview_outputs,
    select_final_output_paths,
)
from snowflake_factory import strip_video_previews_and_redirect_outputs


class NoStoredPreviewOutputsTests(unittest.TestCase):
    def test_strip_mutes_preview_keeps_final(self) -> None:
        wf = {
            "nodes": [
                {
                    "id": 398,
                    "type": "VHS_VideoCombine",
                    "mode": 0,
                    "title": "OUTPUT: final MP4",
                    "widgets_values": {
                        "save_output": True,
                        "save_metadata": True,
                        "filename_prefix": "old",
                        "videopreview": {"ok": 1},
                    },
                },
                {
                    "id": 399,
                    "type": "VHS_VideoCombine",
                    "mode": 0,
                    "title": "OUTPUT: preview/debug MP4",
                    "widgets_values": {
                        "save_output": True,
                        "save_metadata": False,
                        "filename_prefix": "old",
                        "videopreview": {"ok": 1},
                    },
                },
            ]
        }
        changes = strip_video_previews_and_redirect_outputs(wf, "og/job", final_node_ids={398})
        self.assertGreaterEqual(int(changes.get("disabled_non_final_outputs") or 0), 1)
        final = wf["nodes"][0]["widgets_values"]
        preview = wf["nodes"][1]["widgets_values"]
        self.assertTrue(final.get("save_output"))
        self.assertEqual(final.get("filename_prefix"), "og/job")
        self.assertFalse(preview.get("save_output"))
        self.assertEqual(wf["nodes"][1].get("mode"), 2)
        self.assertNotIn("videopreview", final)
        self.assertNotIn("videopreview", preview)

    def test_api_bindings_and_enforce_mute_preview(self) -> None:
        workflow = {
            "nodes": [
                {"id": 398, "type": "VHS_VideoCombine", "mode": 0, "title": "OUTPUT: final MP4"},
                {
                    "id": 399,
                    "type": "VHS_VideoCombine",
                    "mode": 0,
                    "title": "OUTPUT: preview/debug MP4",
                    "widgets_values": {"save_output": True},
                },
            ]
        }
        prompt = {
            "398": {
                "class_type": "VHS_VideoCombine",
                "inputs": {"save_output": True, "save_metadata": True, "filename_prefix": "x"},
            },
            "399": {
                "class_type": "VHS_VideoCombine",
                "inputs": {"save_output": True, "save_metadata": False, "filename_prefix": "y"},
            },
        }
        shape = {
            "produces": [
                {"slot": "final_video", "binding": {"node_id": 398, "node_type": "VHS_VideoCombine"}}
            ]
        }
        job = {"output_prefix": "og/2026-08-17/hourly/demo"}
        apply_api_slot_bindings(prompt, shape, job, __import__("pathlib").Path("."))
        enforce_no_stored_preview_outputs(workflow, prompt, final_node_ids={398})
        self.assertTrue(prompt["398"]["inputs"]["save_output"])
        self.assertEqual(prompt["398"]["inputs"]["filename_prefix"], "og/2026-08-17/hourly/demo")
        self.assertFalse(prompt["399"]["inputs"]["save_output"])

    def test_select_final_prefers_produce_node_not_suffix(self) -> None:
        """Comfy suffix order is unreliable: preview can be _00001, final _00002."""
        preview = Path(
            "/data/output/og/job_00001.mp4"
        )
        final = Path(
            "/data/output/og/job_00002.mp4"
        )
        job = {
            "shape": {
                "produces": [
                    {
                        "slot": "final_video",
                        "media": "video",
                        "binding": {"node_id": 398, "node_type": "VHS_VideoCombine"},
                    }
                ]
            },
            "submit": {
                "outputs_by_node": {
                    "399": [str(preview)],
                    "398": [str(final)],
                },
                "outputs": [str(preview), str(final)],
            },
        }
        picked = select_final_output_paths([preview, final], job=job)
        self.assertEqual([p.name for p in picked], ["job_00002.mp4"])


if __name__ == "__main__":
    unittest.main()
