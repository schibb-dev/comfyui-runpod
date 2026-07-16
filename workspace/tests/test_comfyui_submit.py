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


if __name__ == "__main__":
    unittest.main()
