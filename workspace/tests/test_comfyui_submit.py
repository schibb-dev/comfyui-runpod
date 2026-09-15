#!/usr/bin/env python3
"""Tests for Comfy submit metadata enrichment."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "workspace" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import comfyui_submit


class ComfyUiSubmitTests(unittest.TestCase):
    def test_submit_prompt_includes_named_workflow_metadata(self) -> None:
        prompt = {"1": {"class_type": "SaveImage", "inputs": {"filename_prefix": "og/test"}}}
        workflow_ui = {"nodes": [], "links": [], "version": 0.4}

        captured = {}

        def fake_http_json(method: str, url: str, payload=None, timeout_s: int = 30):
            captured["method"] = method
            captured["url"] = url
            captured["payload"] = payload
            return {"prompt_id": "pid-123"}

        with mock.patch.object(comfyui_submit, "_http_json", side_effect=fake_http_json):
            out = comfyui_submit.submit_prompt_to_comfyui(
                "http://127.0.0.1:8188",
                prompt,
                workflow_ui=workflow_ui,
                workflow_name="FB9 GEX FACIAL / extend ui178422",
                client_id="factory-map-ui",
                preview_method="auto",
            )

        self.assertEqual(out["prompt_id"], "pid-123")
        payload = captured["payload"]
        self.assertEqual(payload["client_id"], "factory-map-ui")
        extra = payload.get("extra_data") or {}
        self.assertEqual(extra.get("workflow_name"), "FB9_GEX_FACIAL_extend_ui178422")
        self.assertEqual(extra.get("name"), "FB9_GEX_FACIAL_extend_ui178422")
        self.assertEqual(extra.get("filename"), "FB9_GEX_FACIAL_extend_ui178422.json")
        workflow = ((extra.get("extra_pnginfo") or {}).get("workflow") or {})
        self.assertEqual(workflow.get("name"), "FB9_GEX_FACIAL_extend_ui178422")
        self.assertEqual(workflow.get("version"), 0.4)

    def test_submit_prompt_appends_run_spec_suffix_on_new_posts(self) -> None:
        prompt = {
            "458": {
                "class_type": "UnetLoaderGGUFDisTorchMultiGPU",
                "inputs": {
                    "unet_name": "WAN/wan2.1-i2v-14b-720p-Q5_K_M.gguf",
                    "virtual_vram_gb": 4.0,
                },
                "_meta": {"title": "Model"},
            },
            "83": {
                "class_type": "mxSlider2D",
                "inputs": {"Xi": 432, "Xf": 432, "Yi": 768, "Yf": 768, "isfloatX": 0, "isfloatY": 0},
                "_meta": {"title": "Size"},
            },
            "82": {
                "class_type": "mxSlider",
                "inputs": {"Xi": 20, "Xf": 20, "isfloatX": 0},
                "_meta": {"title": "Steps"},
            },
            "398": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "filename_prefix": "og/2026-09-15/X-Kneel-FB9_shape/X-KNEEL-FB9__pp-catalog-default__still-abc__000_adhoc_ui1",
                    "save_output": True,
                },
            },
        }
        captured = {}

        def fake_http_json(method: str, url: str, payload=None, timeout_s: int = 30):
            captured["payload"] = payload
            return {"prompt_id": "pid-spec"}

        with mock.patch.object(comfyui_submit, "_http_json", side_effect=fake_http_json):
            comfyui_submit.submit_prompt_to_comfyui("http://127.0.0.1:8188", prompt)

        posted = captured["payload"]["prompt"]
        prefix = posted["398"]["inputs"]["filename_prefix"]
        self.assertIn("__rs-720p_Q5_432x768_20st_vv4", prefix)
        # Caller graph is not mutated.
        self.assertNotIn("__rs-", prompt["398"]["inputs"]["filename_prefix"])


if __name__ == "__main__":
    unittest.main()
