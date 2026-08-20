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

    def test_sanitize_linked_text_widget_defaults_clears_stale_idle(self) -> None:
        from shape_factory import sanitize_linked_text_widget_defaults

        workflow = {
            "nodes": [
                {
                    "id": 215,
                    "type": "CLIPTextEncode",
                    "inputs": [
                        {"name": "clip", "link": 1},
                        {"name": "text", "link": 2},
                    ],
                    "widgets_values": ["Slow and small Movements. Idle Animation"],
                },
                {
                    "id": 380,
                    "type": "Text Multiline",
                    "inputs": [],
                    "widgets_values": ["real scene prompt"],
                },
            ]
        }
        cleared = sanitize_linked_text_widget_defaults(workflow)
        self.assertEqual(cleared, 1)
        self.assertEqual(workflow["nodes"][0]["widgets_values"], [""])
        self.assertEqual(workflow["nodes"][1]["widgets_values"], ["real scene prompt"])

    def test_apply_prompt_bundle_writes_upstream_not_linked_default(self) -> None:
        from shape_factory import apply_prompt_bundle

        with tempfile.TemporaryDirectory() as td:
            profile = Path(td) / "prompt.json"
            profile.write_text(
                json.dumps({"positive": "assembled scene", "negative": ""}),
                encoding="utf-8",
            )
            workflow = {
                "links": [[839, 380, 0, 215, 1, "STRING"]],
                "nodes": [
                    {
                        "id": 215,
                        "type": "CLIPTextEncode",
                        "inputs": [
                            {"name": "clip", "link": 1},
                            {"name": "text", "link": 839},
                        ],
                        "widgets_values": ["Slow and small Movements. Idle Animation"],
                    },
                    {
                        "id": 380,
                        "type": "Text Multiline",
                        "inputs": [],
                        "widgets_values": ["old scene"],
                    },
                ],
            }
            # Mis-aimed binding: encode node whose text is linked.
            binding = {"positive": {"node_id": 215, "widget_index": 0}}
            warnings = apply_prompt_bundle(workflow, binding, profile)
            self.assertFalse(warnings)
            self.assertEqual(workflow["nodes"][0]["widgets_values"], ["Slow and small Movements. Idle Animation"])
            self.assertEqual(workflow["nodes"][1]["widgets_values"], ["assembled scene"])

    def test_companion_png_missing_vhs_is_fatal(self) -> None:
        from shape_factory import _binding_patch_failures

        shape = {
            "requires": [
                {
                    "slot": "source_video",
                    "binding": {"type": "vhs_load_video_path", "node_id": 377},
                }
            ]
        }
        job = {"bindings": {"source_video": {"path": "/tmp/x.mp4"}}}
        warnings = ["no VHS_LoadVideoPath node 377 in API prompt"]
        fatal = _binding_patch_failures(warnings, shape, job)
        self.assertTrue(any("companion_png_missing_video_slot:source_video" in f for f in fatal))

    def test_optional_missing_vhs_does_not_clobber_primary(self) -> None:
        """FACIAL-style: convert drops unlinked ref node 386; must not overwrite 377."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            primary = root / "primary.mp4"
            ref = root / "ref.mp4"
            primary.write_bytes(b"primary")
            ref.write_bytes(b"ref")
            shape = {
                "requires": [
                    {
                        "slot": "source_video",
                        "binding": {"type": "vhs_load_video_path", "node_id": 377},
                    },
                    {
                        "slot": "source_video_ref",
                        "optional": True,
                        "binding": {"type": "vhs_load_video_path", "node_id": 386},
                    },
                ]
            }
            # Bindings listed ref-first to prove sort/required-first still protects 377.
            job = {
                "bindings": {
                    "source_video_ref": {"path": str(ref)},
                    "source_video": {"path": str(primary)},
                }
            }
            prompt = {
                "377": {
                    "class_type": "VHS_LoadVideoPath",
                    "inputs": {"video": "old.mp4"},
                }
            }
            warnings = apply_api_slot_bindings(prompt, shape, job, root)
            self.assertTrue(
                any("skipped (optional slot 'source_video_ref')" in w for w in warnings),
                warnings,
            )
            self.assertFalse(any("patched unbound" in w for w in warnings), warnings)
            video = prompt["377"]["inputs"]["video"]
            self.assertTrue(video.endswith("primary.mp4") or "primary.mp4" in video, video)

    def test_shape_ui_defaults_sets_duration_slider(self) -> None:
        from shape_factory import apply_shape_ui_defaults_api, apply_shape_ui_defaults_ui

        shape = {
            "ui_defaults": {
                "ui_nodes": {
                    426: {"type": "mxSlider", "widgets_values": [6, 6, 1]},
                },
                "api_nodes": {"426": {"inputs": {"Xi": 6, "Xf": 6}}},
            }
        }
        workflow = {
            "nodes": [
                {
                    "id": 426,
                    "type": "mxSlider",
                    "title": "Duration",
                    "widgets_values": [5, 5, 1],
                }
            ]
        }
        prompt = {"426": {"class_type": "mxSlider", "inputs": {"Xi": 5, "Xf": 5, "isfloatX": 1}}}
        apply_shape_ui_defaults_ui(workflow, shape)
        apply_shape_ui_defaults_api(prompt, shape)
        self.assertEqual(workflow["nodes"][0]["widgets_values"], [6, 6, 1])
        self.assertEqual(prompt["426"]["inputs"]["Xi"], 6)
        self.assertEqual(prompt["426"]["inputs"]["Xf"], 6)


if __name__ == "__main__":
    unittest.main()
