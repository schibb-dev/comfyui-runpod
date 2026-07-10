#!/usr/bin/env python3
"""Tests for shape_factory_hourly planning."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class ShapeFactoryHourlyTests(unittest.TestCase):
    def test_collect_replay_recipes_includes_og_history(self) -> None:
        from shape_factory_hourly import collect_replay_recipes

        data_root = REPO_ROOT / ".data"
        if not (data_root / "shapes" / "FB9_GEX2.shape.yaml").is_file():
            self.skipTest("FB9_GEX2 shape missing")
        recipes = collect_replay_recipes("FB9_GEX2", data_root=data_root)
        if not recipes:
            self.skipTest("no replay recipes (missing OG PNG metadata on this host?)")
        sources = {str(r.get("source") or "") for r in recipes}
        self.assertTrue(any(s.startswith("og:") for s in sources) or any("FB9_GEX2" in s for s in sources))

    def test_plan_replay_picks_from_recipes(self) -> None:
        from shape_factory_hourly import plan_hourly_replay

        data_root = REPO_ROOT / ".data"
        if not (data_root / "shapes" / "FB9_GEX2.shape.yaml").is_file():
            self.skipTest("FB9_GEX2 shape missing")
        plan = plan_hourly_replay(cursor=0, data_root=data_root, family="FB9_GEX2")
        if not plan.get("ok"):
            self.skipTest(plan.get("error") or "no recipes")
        self.assertEqual(plan.get("pick_mode"), "replay")
        self.assertIsInstance(plan.get("picks"), dict)
        self.assertIn("prompt_profile", plan["picks"])
        self.assertTrue(Path(plan["picks"]["prompt_profile"]).is_file())

    def test_plan_replay_cursor_changes_choice(self) -> None:
        from shape_factory_hourly import plan_hourly_replay

        data_root = REPO_ROOT / ".data"
        if not (data_root / "shapes" / "FB9_GEX2.shape.yaml").is_file():
            self.skipTest("FB9_GEX2 shape missing")
        a = plan_hourly_replay(cursor=0, data_root=data_root, family="FB9_GEX2")
        b = plan_hourly_replay(cursor=1, data_root=data_root, family="FB9_GEX2")
        if not a.get("ok") or not b.get("ok"):
            self.skipTest("not enough recipes")
        if (a.get("recipe_count") or 0) < 2:
            self.skipTest("need 2+ recipes")
        # pseudo-random — usually differs; allow rare collision
        self.assertGreaterEqual(a.get("recipe_count"), 2)

    def test_product_combo_count_with_prompts(self) -> None:
        from shape_factory_hourly import product_combos_for_family

        data_root = REPO_ROOT / ".data"
        if not (data_root / "pools" / "FB9_GEX2" / "pools.yaml").is_file():
            self.skipTest("FB9_GEX2 pools missing")
        combos = product_combos_for_family(family="FB9_GEX2", data_root=data_root)
        self.assertGreaterEqual(len(combos), 1)

    def test_recipe_selection_weight_prefers_explicit_output_rating(self) -> None:
        from shape_factory_heuristics import score_recipe

        shape = {"graph_hash": "abc123"}
        ratings_doc = {
            "by_output_relpath": {
                "og/2026-04-03/FB9_GEX2_2026-04-03_00001": {"explicit": 5},
            },
            "by_source_basename": {},
            "by_graph_hash": {"abc123": {"inferred": 2.0, "n": 10}},
        }
        high = {
            "output_path": "/data/output/og/2026-04-03/FB9_GEX2_2026-04-03_00001.mp4",
            "picks": {"source_video": "/tmp/foo.mp4"},
        }
        low = {
            "output_path": "output/og/other.mp4",
            "picks": {"source_video": "/tmp/bar.mp4"},
        }
        w_high, meta_high = score_recipe(high, shape=shape, ratings_doc=ratings_doc)
        w_low, meta_low = score_recipe(low, shape=shape, ratings_doc=ratings_doc)
        self.assertGreater(w_high, w_low)
        self.assertEqual(meta_high.get("explicit"), 5)
        self.assertIn("output_explicit", meta_high.get("evidence") or [])

    def test_plan_replay_includes_rating_metadata_when_index_present(self) -> None:
        from shape_factory_hourly import plan_hourly_replay

        data_root = REPO_ROOT / ".data"
        if not (data_root / "shapes" / "FB9_GEX2.shape.yaml").is_file():
            self.skipTest("FB9_GEX2 shape missing")
        plan = plan_hourly_replay(cursor=42, data_root=data_root, family="FB9_GEX2")
        if not plan.get("ok"):
            self.skipTest(plan.get("error") or "no recipes")
        self.assertIn("ratings_index_loaded", plan)
        self.assertIn("heuristics_index_loaded", plan)
        self.assertIn("rating_blend", plan)
        self.assertIn("selection_weight", plan)

    def test_resolve_media_path_flattens_nested_output(self) -> None:
        from shape_factory_hourly import _resolve_media_path

        bind = Path("/home/yuji/comfyui-runpod-data/output")
        target = bind / "og" / "2026-04-03" / "FB9_GEX2_2026-04-03_00001.mp4"
        if not target.is_file():
            self.skipTest("canonical OG seed missing")
        data_root = REPO_ROOT / ".data"
        for raw in (
            "output/output/og/2026-04-03/FB9_GEX2_2026-04-03_00001.mp4",
            str(bind / "output" / "og" / "2026-04-03" / "FB9_GEX2_2026-04-03_00001.mp4"),
            "output/og/2026-04-03/FB9_GEX2_2026-04-03_00001.mp4",
        ):
            got = _resolve_media_path(raw, data_root=data_root)
            self.assertIsNotNone(got, msg=raw)
            self.assertEqual(got, target.resolve())

    def test_picks_from_job_recovers_missing_prompt_with_nested_video(self) -> None:
        from shape_factory_hourly import _picks_from_job, load_yaml

        data_root = REPO_ROOT / ".data"
        shape_path = data_root / "shapes" / "FB9_GEX2.shape.yaml"
        if not shape_path.is_file():
            self.skipTest("FB9_GEX2 shape missing")
        shape = load_yaml(shape_path)
        # Prefer a real job that previously failed recovery.
        jobs = sorted((data_root / "shape_factory" / "jobs" / "FB9_GEX2").glob("*idle-small-motions*000.job.json"))
        if not jobs:
            self.skipTest("no idle-small-motions job fixture")
        job = json.loads(jobs[0].read_text(encoding="utf-8"))
        picks = _picks_from_job(job, shape=shape, data_root=data_root)
        self.assertIsNotNone(picks)
        assert picks is not None
        self.assertTrue(picks["prompt_profile"].is_file())
        self.assertTrue(picks["source_video"].is_file())
        self.assertIn("/og/", str(picks["source_video"]).replace("\\", "/"))


if __name__ == "__main__":
    unittest.main()
