#!/usr/bin/env python3
"""Tests for job-owned prompt fork / freeze / promote (V1)."""

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
            prompt_content_hash,
        )

        job = {
            "prompt": fork_owned_prompt(
                positive="hello {a|b}",
                negative="bad",
                label="catalog-default",
                source_profile="/pools/X/prompts/catalog-default.json",
            )
        }
        self.assertEqual(
            job["prompt"]["content_hash"],
            prompt_content_hash("hello {a|b}", "bad"),
        )
        self.assertFalse(is_owned_prompt_frozen(job))
        self.assertTrue(freeze_owned_prompt(job))
        self.assertTrue(is_owned_prompt_frozen(job))
        self.assertFalse(freeze_owned_prompt(job))  # already frozen
        with self.assertRaises(OwnedPromptFrozenError):
            merge_owned_prompt(job, {"positive": "nope"})

        profile = profile_dict_for_apply(job)
        self.assertEqual(profile["positive"], "hello {a|b}")
        self.assertEqual(profile["negative"], "bad")

    def test_merge_refreshes_content_hash(self) -> None:
        from shape_factory_owned_prompt import fork_owned_prompt, merge_owned_prompt, prompt_content_hash

        job = {"prompt": fork_owned_prompt(positive="a", negative="b")}
        h1 = job["prompt"]["content_hash"]
        merge_owned_prompt(job, {"positive": "aa"})
        self.assertNotEqual(h1, job["prompt"]["content_hash"])
        self.assertEqual(job["prompt"]["content_hash"], prompt_content_hash("aa", "b"))

    def test_promote_fork_and_overwrite(self) -> None:
        from shape_factory_owned_prompt import promote_prompt_to_library

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prompts = root / "pools" / "DEMO" / "prompts"
            prompts.mkdir(parents=True)
            (prompts / "catalog-default.json").write_text(
                json.dumps({"label": "catalog-default", "positive": "OLD", "negative": ""}),
                encoding="utf-8",
            )
            fork = promote_prompt_to_library(
                data_root=root,
                family_slug="DEMO",
                positive="NEW",
                negative="neg",
                mode="fork",
                label="evening-crane",
                note="from test",
                promoted_from_job="demo__1",
                parent_path=str(prompts / "catalog-default.json"),
            )
            self.assertTrue(fork["ok"])
            self.assertEqual(fork["mode"], "fork")
            fork_path = Path(fork["path"])
            self.assertTrue(fork_path.is_file())
            doc = json.loads(fork_path.read_text(encoding="utf-8"))
            self.assertEqual(doc["positive"], "NEW")
            self.assertEqual(doc["promoted_from_job"], "demo__1")
            self.assertIn("variant_id", doc)
            self.assertIn("content_hash", doc)

            over = promote_prompt_to_library(
                data_root=root,
                family_slug="DEMO",
                positive="DEFAULT2",
                negative="",
                mode="overwrite",
                promoted_from_job="demo__2",
            )
            self.assertTrue(over["ok"])
            self.assertEqual(over["mode"], "overwrite")
            self.assertTrue(Path(over["bak_path"]).is_file())
            default = json.loads((prompts / "catalog-default.json").read_text(encoding="utf-8"))
            self.assertEqual(default["positive"], "DEFAULT2")
            # Fork file unchanged
            self.assertEqual(json.loads(fork_path.read_text(encoding="utf-8"))["positive"], "NEW")

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
        self.assertTrue(ex.get("content_hash"))
        self.assertFalse(ex.get("snowflake"))

    def test_encode_prompt_markup_roundtrip(self) -> None:
        from shape_factory_work_products import decode_prompt_markup, encode_prompt_markup

        rows = [
            {"text": "plain clause", "weight": 1.0},
            {"text": "heavy", "weight": 1.25},
            {"text": "soft", "weight": 0.8},
        ]
        encoded = encode_prompt_markup(rows)
        self.assertEqual(
            encoded,
            "plain clause\n(heavy:1.25)\n(soft:0.8)",
        )
        again = encode_prompt_markup(decode_prompt_markup(encoded))
        self.assertEqual(encoded, again)

    def test_snowflake_when_diverged_from_seed(self) -> None:
        from shape_factory_owned_prompt import fork_owned_prompt, merge_owned_prompt, owned_prompt_to_excerpt

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            seed_path = root / "catalog-default.json"
            seed_path.write_text(
                json.dumps({"label": "catalog-default", "positive": "SEED", "negative": "N"}),
                encoding="utf-8",
            )
            job = {
                "prompt": fork_owned_prompt(
                    positive="SEED",
                    negative="N",
                    label="catalog-default",
                    source_profile=str(seed_path),
                )
            }
            same = owned_prompt_to_excerpt(job["prompt"], data_root=root)
            self.assertFalse(same.get("snowflake"))
            self.assertEqual(same.get("seed", {}).get("positive"), "SEED")

            merge_owned_prompt(job, {"positive": "SNOW"})
            diverged = owned_prompt_to_excerpt(job["prompt"], data_root=root)
            self.assertTrue(diverged.get("snowflake"))
            self.assertEqual(diverged.get("positive"), "SNOW")
            self.assertEqual(diverged.get("seed", {}).get("positive"), "SEED")

    def test_update_pending_via_rows(self) -> None:
        from shape_factory import update_pending_job_owned_prompt
        from shape_factory_owned_prompt import fork_owned_prompt

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            jobs = root / "jobs"
            jobs.mkdir(parents=True)
            job = {
                "job_key": "demo__rows",
                "family_slug": "DEMO",
                "prompt": fork_owned_prompt(positive="old", negative="n", label="catalog-default"),
                "submit": {"status": "pending"},
            }
            path = jobs / "demo__rows.job.json"
            path.write_text(json.dumps(job), encoding="utf-8")
            with mock.patch("shape_factory.hostify_job_paths", return_value=False):
                res = update_pending_job_owned_prompt(
                    data_root=root,
                    job_key="demo__rows",
                    job_path=path,
                    positive_rows=[{"text": "new clause", "weight": 1.3}],
                    negative_rows=[{"text": "neg", "weight": 1.0}],
                )
            self.assertTrue(res.get("ok"), res)
            self.assertEqual(res.get("prompt", {}).get("positive"), "(new clause:1.3)")
            self.assertEqual(res.get("prompt", {}).get("negative"), "neg")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["prompt"]["positive"], "(new clause:1.3)")
            self.assertEqual(saved["prompt"]["negative"], "neg")


if __name__ == "__main__":
    unittest.main()
