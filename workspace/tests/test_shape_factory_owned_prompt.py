#!/usr/bin/env python3
"""Tests for job-owned prompt fork / freeze (V1)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]


class OwnedPromptTests(unittest.TestCase):
    def test_fork_and_freeze(self) -> None:
        from shape_factory_owned_prompt import (
            OwnedPromptFrozenError,
            fork_owned_prompt,
            freeze_owned_prompt,
            is_owned_prompt_frozen,
            merge_owned_prompt,
            profile_dict_for_apply,
        )

        job = {
            "prompt": fork_owned_prompt(
                positive="hello {a|b}",
                negative="bad",
                label="catalog-default",
                source_profile="/pools/X/prompts/catalog-default.json",
            )
        }
        self.assertFalse(is_owned_prompt_frozen(job))
        self.assertTrue(freeze_owned_prompt(job))
        self.assertTrue(is_owned_prompt_frozen(job))
        self.assertFalse(freeze_owned_prompt(job))  # already frozen
        with self.assertRaises(OwnedPromptFrozenError):
            merge_owned_prompt(job, {"positive": "nope"})

        profile = profile_dict_for_apply(job)
        self.assertEqual(profile["positive"], "hello {a|b}")
        self.assertEqual(profile["negative"], "bad")

    def test_apply_prefers_owned_over_catalog_file(self) -> None:
        from shape_factory import apply_api_slot_bindings

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            catalog = root / "catalog-default.json"
            catalog.write_text(
                json.dumps({"label": "catalog-default", "positive": "FROM_FILE", "negative": ""}),
                encoding="utf-8",
            )
            shape = {
                "requires": [
                    {
                        "slot": "prompt_profile",
                        "binding": {
                            "type": "prompt_bundle",
                            "positive": {"node_id": 10, "input": "text"},
                            "negative": {"node_id": 11, "input": "text"},
                        },
                    }
                ]
            }
            job = {
                "bindings": {"prompt_profile": {"path": str(catalog), "binding_type": "prompt_bundle"}},
                "prompt": {
                    "positive": "FROM_OWNED",
                    "negative": "NEG_OWNED",
                    "label": "catalog-default",
                    "source_profile": str(catalog),
                    "frozen": False,
                },
                "output_prefix": "og/test",
            }
            prompt = {
                "10": {"class_type": "Text Multiline", "inputs": {"text": ""}},
                "11": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
            }
            apply_api_slot_bindings(prompt, shape, job, data_root=root)
            self.assertEqual(prompt["10"]["inputs"]["text"], "FROM_OWNED")
            self.assertEqual(prompt["11"]["inputs"]["text"], "NEG_OWNED")

            # Catalog mutation must not affect apply when owned exists.
            catalog.write_text(
                json.dumps({"label": "catalog-default", "positive": "MUTATED", "negative": ""}),
                encoding="utf-8",
            )
            prompt2 = {
                "10": {"class_type": "Text Multiline", "inputs": {"text": ""}},
                "11": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
            }
            apply_api_slot_bindings(prompt2, shape, job, data_root=root)
            self.assertEqual(prompt2["10"]["inputs"]["text"], "FROM_OWNED")

    def test_timings_running_freezes_owned_prompt(self) -> None:
        from shape_factory import update_job_timings_on_status
        from shape_factory_owned_prompt import fork_owned_prompt, is_owned_prompt_frozen

        job = {
            "prompt": fork_owned_prompt(positive="x", negative=""),
            "submit": {},
            "timings": {"schema_version": "comfyui-runpod.job-timings.v0"},
        }
        with mock.patch("shape_factory.capture_host_snapshot", return_value={"ts": 1.0}):
            update_job_timings_on_status(job, status="running", history=None, now=1.0, data_root=Path("."))
        self.assertTrue(is_owned_prompt_frozen(job))

    def test_owned_prompt_to_excerpt(self) -> None:
        from shape_factory_owned_prompt import fork_owned_prompt, owned_prompt_to_excerpt

        owned = fork_owned_prompt(positive="(hi:1.2)", negative="", label="catalog-default")
        ex = owned_prompt_to_excerpt(owned)
        self.assertTrue(ex.get("owned"))
        self.assertEqual(ex.get("label"), "catalog-default")
        self.assertIn("positive_rows", ex)


if __name__ == "__main__":
    unittest.main()
