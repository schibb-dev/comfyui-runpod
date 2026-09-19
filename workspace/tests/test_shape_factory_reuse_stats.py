#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from shape_factory_reuse_stats import (
    EXPERIMENT_WEIGHT,
    classify_job_reuse,
    collect_reuse_stats,
    content_id_from_name,
    guide_hourly_from_operator,
    job_guide_weight,
    param_override_signature,
    prompt_reuse_ids,
    rank_reuse_units,
    summarize_counts,
)

STILL_A = "a" * 64
STILL_B = "b" * 64
ASSET = "c" * 64


def _job(**kwargs):
    row = {"family_slug": "X-KNEEL-FB9", "stack_id": "i2v-720p-Q5"}
    row.update(kwargs)
    return row


class TestShapeFactoryReuseStats(unittest.TestCase):
    def test_content_id_from_prefixed_still(self) -> None:
        self.assertEqual(content_id_from_name(f"input/SSS{STILL_A}.jpeg"), STILL_A)
        self.assertIsNone(content_id_from_name("input/plain.jpeg"))

    def test_classify_still_and_clip_layers(self) -> None:
        still_job = classify_job_reuse(
            _job(bindings={"source_still": f"/workspace/input/SSS{STILL_A}.jpeg"})
        )
        self.assertEqual(still_job["still_ids"], [STILL_A])
        self.assertEqual(still_job["clip_id"], "")

        clip_job = classify_job_reuse(
            _job(
                source_clip_id="clip_abc",
                bindings={"source_video": f"og/{ASSET}.mp4"},
            )
        )
        self.assertEqual(clip_job["clip_id"], "clip_abc")
        self.assertEqual(clip_job["asset_id"], ASSET)
        self.assertEqual(clip_job["adhoc_use"], "")

        adhoc = classify_job_reuse(
            _job(
                bindings={"source_video": f"og/{ASSET}.mp4"},
                vhs_window={"mark_in": 1.25, "mark_out": 4.0},
            )
        )
        self.assertEqual(adhoc["clip_id"], "")
        self.assertTrue(adhoc["adhoc_use"].startswith(f"og/{ASSET}.mp4|1.250|4.000"))

    def test_rank_prefers_reused_seed_layer(self) -> None:
        layers = {
            "still_content_id": summarize_counts({STILL_A: 8, STILL_B: 3, "d" * 64: 1, "e" * 64: 1, "f" * 64: 1}),
            "clip_id": summarize_counts({f"clip_{i}": 1 for i in range(20)}),
            "video_asset": summarize_counts({ASSET: 2}),
            "adhoc_use": summarize_counts({}),
        }
        ranked = rank_reuse_units(layers)
        self.assertEqual(ranked["recommended"], "still_content_id")
        self.assertGreater(layers["still_content_id"]["reuse_rate"], layers["clip_id"]["reuse_rate"])

    def test_collect_from_job_tree(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fam = root / "X-KNEEL-FB9"
            fam.mkdir()
            (fam / "one.job.json").write_text(
                json.dumps(_job(bindings={"source_still": f"SSS{STILL_A}.jpeg"})),
                encoding="utf-8",
            )
            (fam / "two.job.json").write_text(
                json.dumps(_job(bindings={"source_still": f"SSS{STILL_A}.jpeg"})),
                encoding="utf-8",
            )
            (fam / "three.job.json").write_text(
                json.dumps(
                    _job(
                        family_slug="FB9_GEX",
                        source_clip_id="clip_1",
                        bindings={"source_video": f"og/{ASSET}.mp4"},
                    )
                ),
                encoding="utf-8",
            )
            payload = collect_reuse_stats(root, ratings_doc={}, appetite_doc={})
            self.assertEqual(payload["jobs_scanned"], 3)
            self.assertEqual(payload["layers"]["still_content_id"]["unique"], 1)
            self.assertEqual(payload["layers"]["still_content_id"]["uses"], 2)
            self.assertEqual(payload["layers"]["clip_id"]["uses"], 1)
            self.assertEqual(payload["layers"]["video_asset"]["uses"], 1)
            self.assertEqual(payload["recipe_layers"]["family"]["unique"], 2)
            self.assertIn("family_prompt", payload["recipe_layers"])
            self.assertEqual(payload["jobs_hourly"], 0)
            self.assertEqual(payload["jobs_operator"], 3)

    def test_hourly_jobs_split_from_operator(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "hourly__pp-catalog-default__000.job.json").write_text(
                json.dumps(
                    {
                        "job_key": "hourly__pp-catalog-default__000",
                        "family_slug": "FB9_GEX",
                        "bindings": {"source_still": f"SSS{STILL_A}.jpeg"},
                    }
                ),
                encoding="utf-8",
            )
            (root / "X-KNEEL-FB9__000.job.json").write_text(
                json.dumps(_job(bindings={"source_still": f"SSS{STILL_B}.jpeg"})),
                encoding="utf-8",
            )
            payload = collect_reuse_stats(root, ratings_doc={}, appetite_doc={})
            self.assertEqual(payload["jobs_hourly"], 1)
            self.assertEqual(payload["jobs_operator"], 1)
            hourly_stills = payload["by_origin"]["hourly"]["seed_layers"]["still_content_id"]
            op_stills = payload["by_origin"]["operator"]["seed_layers"]["still_content_id"]
            self.assertEqual(hourly_stills["top"][0]["id"], STILL_A)
            self.assertEqual(op_stills["top"][0]["id"], STILL_B)
            self.assertEqual(
                payload["optimal_unit"]["recommended"],
                payload["by_origin"]["operator"]["optimal_unit"]["recommended"],
            )
            self.assertEqual(payload["guide_hourly"]["source"], "operator")

    def test_guide_hourly_uses_operator_family_mix(self) -> None:
        operator = {
            "optimal_unit": {"recommended": "still_content_id"},
            "optimal_recipe_unit": {"recommended": "family_prompt"},
            "recipe_layers": {
                "family": {
                    "uses": 10,
                    "top": [{"id": "X-KNEEL-FB9", "uses": 7}, {"id": "FB9_GEX", "uses": 3}],
                },
                "family_prompt": {
                    "uses": 10,
                    "top": [{"id": "X-KNEEL-FB9|default", "uses": 7}],
                },
            },
        }
        hourly = {
            "recipe_layers": {
                "family": {
                    "uses": 10,
                    "top": [{"id": "FB9_GEX", "uses": 8}, {"id": "X-KNEEL-FB9", "uses": 2}],
                },
                "family_prompt": {"uses": 10, "top": []},
            },
        }
        guide = guide_hourly_from_operator(operator, hourly)
        weights = {row["family"]: row["weight"] for row in guide["suggested_seed_family_weights"]}
        self.assertGreater(weights["X-KNEEL-FB9"], weights["FB9_GEX"])
        drift = {row["family"]: row["direction"] for row in guide["family_drift"]}
        self.assertEqual(drift["FB9_GEX"], "hourly_over")
        self.assertEqual(drift["X-KNEEL-FB9"], "hourly_under")

    def test_prompt_and_param_recipe_ids(self) -> None:
        job = _job(
            stack_id="",
            job_key="FB9_GEX__pp-catalog-faceblast-extend__000",
            prompt={"label": "catalog-faceblast-extend", "content_hash": "abc123"},
            bindings={
                "prompt_profile": {
                    "path": "/pools/FB9_GEX/prompts/catalog-faceblast-extend.json",
                }
            },
            adhoc_overrides={"parameters": {"frames": 81, "overlap": 2, "seed": 99}, "stack": "i2v-480p-Q8"},
        )
        variant, digest = prompt_reuse_ids(job)
        self.assertEqual(variant, "faceblast-extend")
        self.assertEqual(digest, "abc123")
        self.assertEqual(param_override_signature(job["adhoc_overrides"]), "frames=81,overlap=2")
        row = classify_job_reuse(job)
        self.assertEqual(row["family_prompt"], "X-KNEEL-FB9|faceblast-extend")
        self.assertEqual(row["stack"], "i2v-480p-Q8")
        self.assertIn("parameters.frames", row["override_keys"])
        self.assertIn("parameters.seed", row["override_keys"])
        self.assertNotIn("seed", row["param_override"])

    def test_experiment_floor_below_appetite(self) -> None:
        experiment = job_guide_weight({"appetite": "", "blocked": False}, hourly=False)
        hourly_exp = job_guide_weight({"appetite": "", "blocked": False}, hourly=True)
        op_more = job_guide_weight({"appetite": "more", "favored": True}, hourly=False)
        hourly_more = job_guide_weight({"appetite": "more", "favored": True}, hourly=True)
        self.assertEqual(experiment, EXPERIMENT_WEIGHT)
        self.assertEqual(hourly_exp, EXPERIMENT_WEIGHT)
        self.assertGreater(hourly_more, experiment)
        self.assertGreater(op_more, hourly_more)

    def test_appetite_outranks_unrated_hourly_volume(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for i in range(8):
                (root / f"hourly__gex__{i:03d}.job.json").write_text(
                    json.dumps(
                        {
                            "job_key": f"hourly__gex__{i:03d}",
                            "family_slug": "FB9_GEX",
                            "submit": {"outputs": [f"og/hourly-{i}.mp4"]},
                        }
                    ),
                    encoding="utf-8",
                )
            (root / "op-faceblast.job.json").write_text(
                json.dumps(
                    _job(
                        family_slug="FB9-FaceBlast",
                        submit={"outputs": ["og/favored-op.mp4"]},
                    )
                ),
                encoding="utf-8",
            )
            payload = collect_reuse_stats(
                root,
                ratings_doc={},
                appetite_doc={
                    "by_output_relpath": {
                        "og/favored-op.mp4": {"appetite": "more"},
                    }
                },
            )
            self.assertEqual(payload["jobs_operator_favored"], 1)
            self.assertEqual(payload["jobs_hourly_favored"], 0)
            self.assertEqual(payload["guide_hourly"]["source"], "appetite+experiment_floor")
            top = payload["guide_hourly"]["suggested_seed_family_weights"][0]["family"]
            self.assertEqual(top, "FB9-FaceBlast")


if __name__ == "__main__":
    unittest.main()
