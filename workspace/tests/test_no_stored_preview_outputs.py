#!/usr/bin/env python3
"""Preview/debug/raw VHS combines must never be stored to disk."""

from __future__ import annotations

import unittest

from pathlib import Path

import support  # noqa: F401  — injects workspace/scripts onto sys.path
from shape_factory import (
    apply_api_slot_bindings,
    enforce_no_stored_preview_outputs,
    select_final_output_paths,
    upsert_pool_index_members,
)
from snowflake_factory import strip_video_previews_and_redirect_outputs
from snowflake_inventory import apply_review_workflow_edits


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

    def test_select_final_prefers_final_token_over_preview(self) -> None:
        preview = Path("/data/output/og/job_PREVIEW_00001.mp4")
        final = Path("/data/output/og/job_FINAL_00001.mp4")
        picked = select_final_output_paths([preview, final])
        self.assertEqual([p.name for p in picked], ["job_FINAL_00001.mp4"])

    def test_api_bindings_mute_preview_prefix_when_save_metadata_true(self) -> None:
        prompt = {
            "398": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "save_output": True,
                    "save_metadata": True,
                    "filename_prefix": "og/job_FINAL",
                },
            },
            "399": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "save_output": True,
                    "save_metadata": True,
                    "filename_prefix": "og/job_PREVIEW",
                },
            },
        }
        apply_api_slot_bindings(prompt, {"produces": []}, {"output_prefix": "og/job"}, Path("."))
        self.assertTrue(prompt["398"]["inputs"]["save_output"])
        self.assertFalse(prompt["399"]["inputs"]["save_output"])

    def test_enforce_mutes_preview_filename_prefix(self) -> None:
        workflow = {"nodes": []}
        prompt = {
            "399": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "save_output": True,
                    "filename_prefix": "og/job_PREVIEW",
                },
            }
        }
        enforce_no_stored_preview_outputs(workflow, prompt)
        self.assertFalse(prompt["399"]["inputs"]["save_output"])

    def test_inventory_review_mutes_preview_keeps_final(self) -> None:
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
                        "save_metadata": True,
                        "filename_prefix": "old",
                        "videopreview": {"ok": 1},
                    },
                },
            ],
            "links": [],
        }
        result = apply_review_workflow_edits(wf, "BounceDanceA", "og/day")
        changes = result.get("changes") or {}
        self.assertGreaterEqual(int(changes.get("disabled_non_final_outputs") or 0), 1)
        nodes = {int(n["id"]): n for n in result["workflow"]["nodes"]}
        final_w = nodes[398]["widgets_values"]
        preview_w = nodes[399]["widgets_values"]
        self.assertTrue(final_w.get("save_output"))
        self.assertIn("_FINAL", str(final_w.get("filename_prefix") or ""))
        self.assertFalse(preview_w.get("save_output"))
        self.assertEqual(nodes[399].get("mode"), 2)
        self.assertNotIn("_PREVIEW", str(preview_w.get("filename_prefix") or ""))

    def test_deposit_replaces_preview_member_for_same_job(self) -> None:
        index_doc: dict = {
            "pools": {
                "X": {
                    "pool_id": "X",
                    "members": [
                        {"path": "/data/preview.mp4", "job_key": "job-a"},
                        {"path": "/data/other.mp4", "job_key": "job-b"},
                    ],
                }
            }
        }
        added = upsert_pool_index_members(
            index_doc,
            "X",
            [{"path": "/data/final.mp4", "job_key": "job-a"}],
            replace_job_keys={"job-a"},
        )
        self.assertEqual(added, 1)
        paths = [m["path"] for m in index_doc["pools"]["X"]["members"]]
        self.assertEqual(paths, ["/data/other.mp4", "/data/final.mp4"])


if __name__ == "__main__":
    unittest.main()
