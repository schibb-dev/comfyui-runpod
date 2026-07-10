#!/usr/bin/env python3
"""Tests for apply_api_slot_bindings prompt_bundle node targeting."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from shape_factory import apply_api_slot_bindings


class ApplyApiSlotBindingsPromptTests(unittest.TestCase):
    def test_prompt_bundle_targets_shape_node_ids_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            profile = root / "prompt.json"
            profile.write_text(
                json.dumps({"positive": "NEW_POS", "negative": "NEW_NEG"}),
                encoding="utf-8",
            )
            shape = {
                "requires": [
                    {
                        "slot": "prompt_profile",
                        "binding": {
                            "type": "prompt_bundle",
                            "positive": {"node_id": 380, "widget_index": 0},
                            "negative": {"node_id": 17, "widget_index": 0},
                        },
                    }
                ]
            }
            prompt = {
                "380": {"class_type": "Text Multiline", "inputs": {"text": "old_pos"}},
                "17": {"class_type": "CLIPTextEncode", "inputs": {"text": "old_neg"}},
                "99": {"class_type": "CLIPTextEncode", "inputs": {"text": "other_neg"}},
                "100": {"class_type": "Text Multiline", "inputs": {"text": "other_pos"}},
            }
            job = {"bindings": {"prompt_profile": {"path": str(profile)}}}
            warnings = apply_api_slot_bindings(prompt, shape, job, root)
            self.assertEqual(prompt["380"]["inputs"]["text"], "NEW_POS")
            self.assertEqual(prompt["17"]["inputs"]["text"], "NEW_NEG")
            self.assertEqual(prompt["99"]["inputs"]["text"], "other_neg")
            self.assertEqual(prompt["100"]["inputs"]["text"], "other_pos")
            self.assertFalse(any("falling back" in w for w in warnings))

    def test_prompt_bundle_fallback_without_node_ids(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            profile = root / "prompt.json"
            profile.write_text(
                json.dumps({"positive": "P", "negative": "N"}),
                encoding="utf-8",
            )
            shape = {
                "requires": [
                    {
                        "slot": "prompt_profile",
                        "binding": {"type": "prompt_bundle"},
                    }
                ]
            }
            prompt = {
                "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "x"}},
                "2": {"class_type": "Text Multiline", "inputs": {"text": "y"}},
            }
            job = {"bindings": {"prompt_profile": {"path": str(profile)}}}
            warnings = apply_api_slot_bindings(prompt, shape, job, root)
            self.assertEqual(prompt["1"]["inputs"]["text"], "N")
            self.assertEqual(prompt["2"]["inputs"]["text"], "P")
            self.assertTrue(any("falling back" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
