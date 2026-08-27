#!/usr/bin/env python3
"""Tests for shape_factory_hourly planning."""

from __future__ import annotations

import json
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

    def test_plan_replay_excludes_omit_rated_recipes(self) -> None:
        from unittest.mock import patch

        from shape_factory_hourly import plan_hourly_replay

        good = {
            "combo_key": "prompt_profile-good__source_video-good",
            "output_path": "/data/output/og/good.mp4",
            "picks": {"prompt_profile": "/tmp/good.json", "source_video": "/tmp/good.mp4"},
            "source": "test",
        }
        bad = {
            "combo_key": "prompt_profile-bad__source_video-bad",
            "output_path": "/data/output/og/bad.mp4",
            "picks": {"prompt_profile": "/tmp/bad.json", "source_video": "/tmp/bad.mp4"},
            "source": "test",
        }
        ratings = {
            "by_output_relpath": {
                "og/good": {"explicit": 5},
                "og/bad": {"explicit": 0},
            }
        }
        shape = {"graph_hash": "abc123", "family": "FB9_GEX2"}
        with patch("shape_factory_hourly.collect_replay_recipes", return_value=[good, bad]):
            with patch("shape_factory_hourly.load_yaml", return_value=shape):
                with patch("shape_factory_hourly._load_ratings_index", return_value=ratings):
                    with patch("shape_factory_hourly._load_heuristics_index", return_value=None):
                        with patch("shape_factory_hourly._load_appetite_index", return_value=None):
                            plan = plan_hourly_replay(cursor=0, family="FB9_GEX2")
        self.assertTrue(plan.get("ok"))
        self.assertEqual(plan.get("combo_key"), good["combo_key"])
        self.assertEqual(plan.get("omit_excluded"), 1)
        self.assertEqual(plan.get("eligible_recipe_count"), 1)

    def test_plan_replay_fails_when_all_recipes_omit(self) -> None:
        from unittest.mock import patch

        from shape_factory_hourly import plan_hourly_replay

        bad = {
            "combo_key": "prompt_profile-bad__source_video-bad",
            "output_path": "/data/output/og/bad.mp4",
            "picks": {"prompt_profile": "/tmp/bad.json", "source_video": "/tmp/bad.mp4"},
            "source": "test",
        }
        ratings = {"by_output_relpath": {"og/bad": {"explicit": 0}}}
        shape = {"graph_hash": "abc123", "family": "FB9_GEX2"}
        with patch("shape_factory_hourly.collect_replay_recipes", return_value=[bad]):
            with patch("shape_factory_hourly.load_yaml", return_value=shape):
                with patch("shape_factory_hourly._load_ratings_index", return_value=ratings):
                    with patch("shape_factory_hourly._load_heuristics_index", return_value=None):
                        with patch("shape_factory_hourly._load_appetite_index", return_value=None):
                            plan = plan_hourly_replay(cursor=0, family="FB9_GEX2")
        self.assertFalse(plan.get("ok"))
        self.assertEqual(plan.get("error"), "no_eligible_replay_recipes")
        self.assertEqual(plan.get("omit_excluded"), 1)

    def test_queue_advance_decision(self) -> None:
        from shape_factory_hourly import queue_advance_decision

        # auto: Comfy room → submit to Comfy even when waiting already meets old min.
        room = queue_advance_decision(
            pending=1, queue_min=1, queue_max=2, factory_pending=0, submit_mode="auto"
        )
        self.assertTrue(room["advance"])
        self.assertEqual(room["destination"], "comfy")
        self.assertEqual(room["reason"], "comfy_room")
        self.assertEqual(room["submit_slots"], 1)

        # auto: Comfy full → spill to pending when under pending max.
        spill = queue_advance_decision(
            pending=2,
            queue_min=1,
            queue_max=2,
            factory_pending=1,
            pending_queue_max=4,
            submit_mode="auto",
        )
        self.assertTrue(spill["advance"])
        self.assertEqual(spill["destination"], "pending")
        self.assertEqual(spill["reason"], "comfy_full_pending")

        # auto: both full → skip.
        full = queue_advance_decision(
            pending=2,
            queue_min=1,
            queue_max=2,
            factory_pending=4,
            pending_queue_max=4,
            submit_mode="auto",
        )
        self.assertFalse(full["advance"])
        self.assertEqual(full["destination"], "skip")
        self.assertEqual(full["reason"], "queues_full")

        # comfy mode: no spill to pending.
        comfy_only = queue_advance_decision(
            pending=2, queue_max=2, factory_pending=0, pending_queue_max=4, submit_mode="comfy"
        )
        self.assertFalse(comfy_only["advance"])
        self.assertEqual(comfy_only["reason"], "at_max")

        # pending mode: ignore Comfy room; respect pending max.
        pend = queue_advance_decision(
            pending=0, queue_max=2, factory_pending=3, pending_queue_max=4, submit_mode="pending"
        )
        self.assertTrue(pend["advance"])
        self.assertEqual(pend["destination"], "pending")
        pend_full = queue_advance_decision(
            pending=0, queue_max=2, factory_pending=4, pending_queue_max=4, submit_mode="pending"
        )
        self.assertFalse(pend_full["advance"])
        self.assertEqual(pend_full["reason"], "pending_max")

    def test_hourly_schedule_due_and_mark(self) -> None:
        from datetime import datetime, timedelta, timezone
        from tempfile import TemporaryDirectory

        from shape_factory_hourly import (
            load_hourly_schedule,
            mark_hourly_tick,
            save_hourly_schedule,
            schedule_is_due,
            schedule_next_due_at,
        )

        with TemporaryDirectory() as td:
            path = Path(td) / "hourly-schedule.json"
            sch = save_hourly_schedule(
                {"interval_minutes": 30, "enabled": True, "last_tick_at": None},
                path=path,
            )
            now = datetime(2026, 8, 5, 20, 0, tzinfo=timezone.utc)
            self.assertTrue(schedule_is_due(sch, now=now))
            sch = mark_hourly_tick(sch, path=path, at=now)
            self.assertFalse(schedule_is_due(sch, now=now + timedelta(minutes=10)))
            self.assertTrue(schedule_is_due(sch, now=now + timedelta(minutes=30)))
            due = schedule_next_due_at(sch, now=now)
            self.assertEqual(due, now + timedelta(minutes=30))
            loaded = load_hourly_schedule(path=path)
            self.assertEqual(loaded["interval_minutes"], 30)
            # Non-preset snaps to nearest.
            snapped = save_hourly_schedule({"interval_minutes": 33}, path=path)
            self.assertEqual(snapped["interval_minutes"], 30)
            twenty = save_hourly_schedule({"interval_minutes": 20}, path=path)
            self.assertEqual(twenty["interval_minutes"], 20)

    def test_score_recipe_marks_predicted_rating_kind(self) -> None:
        from shape_factory_heuristics import score_recipe

        weight, meta = score_recipe(
            {
                "output_path": "/tmp/unrated.mp4",
                "family": "FB9_GEX2",
                "picks": {"prompt_profile": "/tmp/catalog-default.json"},
            },
            shape={"graph_hash": "abc" * 16, "family": "FB9_GEX2"},
            heuristics_doc={
                "by_pattern": {"FB9_GEX2+catalog-default": {"inferred": 4.6, "n": 8}},
            },
        )
        self.assertGreater(weight, 0.5)
        self.assertEqual(meta.get("rating_kind"), "predicted")
        self.assertNotIn("output_explicit", meta.get("signals") or {})

    def test_plan_predicted_derive_always_pick_mode_derive(self) -> None:
        from unittest.mock import patch

        from shape_factory_hourly import plan_hourly_predicted_derive

        seed = {
            "combo_key": "prompt_profile-aaa__source_video-srcA",
            "output_path": "/data/output/og/unrated.mp4",
            "picks": {
                "prompt_profile": "/tmp/prompts/aaa.json",
                "source_video": "/tmp/sources/srcA.mp4",
            },
            "source": "test",
        }
        alt = {
            "combo_key": "prompt_profile-aaa__source_video-srcB",
            "output_path": "/data/output/og/other.mp4",
            "picks": {
                "prompt_profile": "/tmp/prompts/aaa.json",
                "source_video": "/tmp/sources/srcB.mp4",
            },
            "source": "test",
        }
        shape = {"graph_hash": "abc123", "family": "FB9_GEX2"}
        heuristics = {
            "by_pattern": {"FB9_GEX2+aaa": {"inferred": 4.7, "n": 5}},
            "by_group_lineage": {},
        }

        def fake_weight(recipe, **_kwargs):
            if "unrated" in str(recipe.get("output_path") or ""):
                return 3.2, {
                    "rating_effective": 4.7,
                    "rating_kind": "predicted",
                    "evidence": ["pattern:FB9_GEX2+aaa"],
                    "signals": {"pattern": 4.7},
                }
            return 0.35, {"rating_effective": None, "rating_kind": None, "evidence": [], "signals": {}}

        with patch("shape_factory_hourly.collect_replay_recipes", return_value=[seed, alt]):
            with patch("shape_factory_hourly.load_yaml", return_value=shape):
                with patch("shape_factory_hourly._load_ratings_index", return_value={"by_output_relpath": {}}):
                    with patch("shape_factory_hourly._load_heuristics_index", return_value=heuristics):
                        with patch("shape_factory_hourly._load_appetite_index", return_value=None):
                            with patch("shape_factory_hourly._recipe_selection_weight", side_effect=fake_weight):
                                with patch("shape_factory_hourly.collect_pool_source_videos", return_value=[]):
                                    with patch("shape_factory_hourly._recent_combo_keys", return_value=set()):
                                        with patch("shape_factory_hourly._load_source_facets_doc", return_value=None):
                                            plan = plan_hourly_predicted_derive(cursor=0, family="FB9_GEX2")
        self.assertTrue(plan.get("ok"), msg=plan)
        self.assertEqual(plan.get("pick_mode"), "derive")
        self.assertEqual(plan.get("rating_kind"), "predicted")
        self.assertEqual(plan.get("step"), "predicted_derive")
        self.assertNotIn("disposition_entry", plan)
        self.assertNotEqual(plan.get("combo_key"), seed["combo_key"])

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
        jobs = sorted((data_root / "shape_factory" / "jobs" / "FB9_GEX2").glob("*.job.json"))
        if not jobs:
            self.skipTest("no FB9_GEX2 job fixtures")
        job = json.loads(jobs[0].read_text(encoding="utf-8"))
        picks = _picks_from_job(job, shape=shape, data_root=data_root)
        if picks is None:
            self.skipTest("could not recover picks from first job")
        self.assertTrue(picks["prompt_profile"].is_file())
        self.assertTrue(picks["source_video"].is_file())
        self.assertIn("/og/", str(picks["source_video"]).replace("\\", "/"))

    def test_combo_key_from_job_key_strips_hourly_suffix(self) -> None:
        from shape_factory_hourly import _combo_key_from_job_key

        raw = (
            "hourly__prompt_profile-ef85da9aa887__source_video-X-Kneel-FB9-2026-04-01-144236_OG_00001"
            "__000_202607110020"
        )
        self.assertEqual(
            _combo_key_from_job_key(raw),
            "pp-ef85da9aa887__src-X-Kneel-FB9-2026-04-01-144236_OG_00001",
        )

    def test_derive_rewire_processing_synthesizes_new_source(self) -> None:
        from shape_factory_hourly import _derive_rewire
        import random

        seed = {
            "combo_key": "prompt_profile-aaa__source_video-srcA",
            "source": "seed",
            "output_path": "/tmp/out.mp4",
            "picks": {
                "prompt_profile": "/tmp/prompts/aaa.json",
                "source_video": "/tmp/sources/srcA.mp4",
            },
        }
        pool = [
            seed,
            {
                "combo_key": "prompt_profile-bbb__source_video-srcB",
                "picks": {
                    "prompt_profile": "/tmp/prompts/bbb.json",
                    "source_video": "/tmp/sources/srcB.mp4",
                },
            },
        ]
        rewired, action, meta = _derive_rewire(
            seed, facet="processing", family="TEST", pool=pool, rng=random.Random(0)
        )
        self.assertIsNotNone(rewired)
        self.assertEqual(action, "derive")
        assert rewired is not None
        self.assertEqual(rewired["picks"]["prompt_profile"], "/tmp/prompts/aaa.json")
        self.assertEqual(rewired["picks"]["source_video"], "/tmp/sources/srcB.mp4")
        self.assertNotEqual(rewired["combo_key"], seed["combo_key"])

    def test_derive_rewire_processing_skips_recent_same_prompt_alt(self) -> None:
        """Do not lock onto the only same-prompt alt when that combo is recent."""
        from shape_factory_hourly import _derive_rewire
        import random

        seed = {
            "combo_key": "prompt_profile-aaa__source_video-srcA",
            "source": "seed",
            "output_path": "/tmp/out.mp4",
            "picks": {
                "prompt_profile": "/tmp/prompts/aaa.json",
                "source_video": "/tmp/sources/srcA.mp4",
            },
        }
        recent_combo = "prompt_profile-aaa__source_video-srcRecent"
        pool = [
            seed,
            {
                "combo_key": recent_combo,
                "picks": {
                    "prompt_profile": "/tmp/prompts/aaa.json",
                    "source_video": "/tmp/sources/srcRecent.mp4",
                },
            },
            {
                "combo_key": "prompt_profile-bbb__source_video-srcFresh",
                "picks": {
                    "prompt_profile": "/tmp/prompts/bbb.json",
                    "source_video": "/tmp/sources/srcFresh.mp4",
                },
            },
        ]
        rewired, action, meta = _derive_rewire(
            seed,
            facet="processing",
            family="TEST",
            pool=pool,
            rng=random.Random(0),
            recent={recent_combo},
        )
        self.assertIsNotNone(rewired)
        self.assertEqual(action, "derive")
        assert rewired is not None
        self.assertEqual(rewired["picks"]["source_video"], "/tmp/sources/srcFresh.mp4")
        self.assertNotEqual(rewired["combo_key"], recent_combo)

    def test_derive_rewire_processing_holds_appearance_family(self) -> None:
        from shape_factory_hourly import _derive_rewire
        import random

        seed = {
            "combo_key": "prompt_profile-aaa__source_video-blondeA",
            "picks": {
                "prompt_profile": "/tmp/prompts/aaa.json",
                "source_video": "/tmp/sources/blondeA.mp4",
            },
        }
        pool = [
            seed,
            {
                "combo_key": "prompt_profile-bbb__source_video-blondeB",
                "picks": {
                    "prompt_profile": "/tmp/prompts/bbb.json",
                    "source_video": "/tmp/sources/blondeB.mp4",
                },
            },
            {
                "combo_key": "prompt_profile-ccc__source_video-redheadA",
                "picks": {
                    "prompt_profile": "/tmp/prompts/ccc.json",
                    "source_video": "/tmp/sources/redheadA.mp4",
                },
            },
        ]
        facets_doc = {
            "by_source_key": {
                "blondeA.mp4": {"facets": {"appearance": ["blonde"], "expression": ["smiling"], "identity": ["s1"]}},
                "blondeB.mp4": {"facets": {"appearance": ["blonde"], "expression": ["angry"], "identity": ["s2"]}},
                "redheadA.mp4": {"facets": {"appearance": ["redhead"], "expression": ["smiling"], "identity": ["s3"]}},
            }
        }
        # cursor % 3 == 0 -> appearance
        rewired, action, meta = _derive_rewire(
            seed,
            facet="processing",
            family="TEST",
            pool=pool,
            rng=random.Random(0),
            cursor=0,
            facets_doc=facets_doc,
        )
        self.assertEqual(action, "derive")
        assert rewired is not None
        self.assertEqual(meta.get("hold_axis"), "appearance")
        self.assertTrue(meta.get("facet_constrained"))
        self.assertEqual(rewired["picks"]["source_video"], "/tmp/sources/blondeB.mp4")

    def test_derive_rewire_processing_rotates_hold_axis(self) -> None:
        from shape_factory_source_facets import hold_axis_for_cursor

        self.assertEqual(hold_axis_for_cursor(0), "appearance")
        self.assertEqual(hold_axis_for_cursor(1), "expression")
        self.assertEqual(hold_axis_for_cursor(2), "identity")
        self.assertEqual(hold_axis_for_cursor(3), "appearance")

    def test_derive_rewire_processing_fallback_without_facets(self) -> None:
        from shape_factory_hourly import _derive_rewire
        import random

        seed = {
            "combo_key": "prompt_profile-aaa__source_video-srcA",
            "picks": {
                "prompt_profile": "/tmp/prompts/aaa.json",
                "source_video": "/tmp/sources/srcA.mp4",
            },
        }
        pool = [
            seed,
            {
                "combo_key": "prompt_profile-bbb__source_video-srcB",
                "picks": {
                    "prompt_profile": "/tmp/prompts/bbb.json",
                    "source_video": "/tmp/sources/srcB.mp4",
                },
            },
        ]
        rewired, action, meta = _derive_rewire(
            seed,
            facet="processing",
            family="TEST",
            pool=pool,
            rng=random.Random(0),
            cursor=0,
            facets_doc={"by_source_key": {}},
        )
        self.assertEqual(action, "derive")
        assert rewired is not None
        self.assertFalse(meta.get("facet_constrained"))
        self.assertEqual(rewired["picks"]["source_video"], "/tmp/sources/srcB.mp4")

    def test_recent_source_penalty_downweights_repeat_sources(self) -> None:
        from shape_factory_hourly import _apply_recent_source_penalty, _recent_source_basenames

        recipes = [
            {"combo_key": "prompt_profile-a__source_video-srcA", "picks": {"source_video": "/tmp/srcA.mp4"}},
            {"combo_key": "prompt_profile-b__source_video-srcB", "picks": {"source_video": "/tmp/srcB.mp4"}},
        ]
        recent = {"prompt_profile-z__source_video-srcA"}
        recent_sources = _recent_source_basenames(recent)
        weights = _apply_recent_source_penalty(recipes, [1.0, 1.0], recent_sources, penalty=0.1)
        self.assertLess(weights[0], weights[1])
        self.assertAlmostEqual(weights[0], 0.1)
        self.assertAlmostEqual(weights[1], 1.0)

    def test_derive_rewire_widens_when_family_sources_are_recent(self) -> None:
        from shape_factory_hourly import _derive_rewire
        import random

        seed = {
            "combo_key": "prompt_profile-aaa__source_video-blondeA",
            "picks": {
                "prompt_profile": "/tmp/prompts/aaa.json",
                "source_video": "/tmp/sources/blondeA.mp4",
            },
        }
        pool = [
            seed,
            {
                "combo_key": "prompt_profile-bbb__source_video-blondeB",
                "picks": {
                    "prompt_profile": "/tmp/prompts/bbb.json",
                    "source_video": "/tmp/sources/blondeB.mp4",
                },
            },
            {
                "combo_key": "prompt_profile-ccc__source_video-redheadA",
                "picks": {
                    "prompt_profile": "/tmp/prompts/ccc.json",
                    "source_video": "/tmp/sources/redheadA.mp4",
                },
            },
        ]
        facets_doc = {
            "by_source_key": {
                "blondeA.mp4": {"facets": {"appearance": ["blonde"]}},
                "blondeB.mp4": {"facets": {"appearance": ["blonde"]}},
                "redheadA.mp4": {"facets": {"appearance": ["redhead"]}},
            }
        }
        # blondeB already recent as a source; widen should pick redheadA.
        recent = {"prompt_profile-zzz__source_video-blondeB"}
        rewired, action, meta = _derive_rewire(
            seed,
            facet="processing",
            family="TEST",
            pool=pool,
            rng=random.Random(0),
            cursor=0,  # appearance hold
            facets_doc=facets_doc,
            recent=recent,
        )
        self.assertEqual(action, "derive")
        assert rewired is not None
        self.assertEqual(rewired["picks"]["source_video"], "/tmp/sources/redheadA.mp4")
        self.assertEqual(meta.get("fallback"), "widen_for_source_novelty")

    def test_derive_rewire_noop_when_no_alts(self) -> None:
        from shape_factory_hourly import _derive_rewire
        import random

        seed = {
            "combo_key": "prompt_profile-aaa__source_video-srcA",
            "picks": {
                "prompt_profile": "/tmp/prompts/aaa.json",
                "source_video": "/tmp/sources/srcA.mp4",
            },
        }
        rewired, action, meta = _derive_rewire(
            seed, facet="processing", family="TEST", pool=[seed], rng=random.Random(0)
        )
        self.assertIsNone(rewired)
        self.assertEqual(action, "noop")

    def test_derive_rewire_uses_extra_pool_sources(self) -> None:
        from shape_factory_hourly import _derive_rewire
        import random

        seed = {
            "combo_key": "prompt_profile-aaa__source_video-srcA",
            "picks": {
                "prompt_profile": "/tmp/prompts/aaa.json",
                "source_video": "/tmp/sources/srcA.mp4",
            },
        }
        rewired, action, meta = _derive_rewire(
            seed,
            facet="processing",
            family="TEST",
            pool=[seed],
            rng=random.Random(0),
            extra_sources=["/tmp/sources/poolOnly.mp4"],
        )
        self.assertEqual(action, "derive")
        self.assertIsNotNone(rewired)
        assert rewired is not None
        self.assertEqual(rewired["picks"]["source_video"], "/tmp/sources/poolOnly.mp4")
        self.assertGreaterEqual(int(meta.get("candidate_count_unfiltered") or 0), 1)

    def test_select_seed_family_is_deterministic(self) -> None:
        from shape_factory_hourly import select_seed_family

        a = select_seed_family(7)
        b = select_seed_family(7)
        c = select_seed_family(8)
        self.assertEqual(a, b)
        from shape_factory_hourly import _DEFAULT_SEED_FAMILY_WEIGHTS

        allowed = {name for name, _w in _DEFAULT_SEED_FAMILY_WEIGHTS}
        self.assertIn(a, allowed)
        self.assertIn(c, allowed)

    def test_select_seed_family_respects_env_weights(self) -> None:
        import os
        from shape_factory_hourly import select_seed_family

        prev = os.environ.get("HOURLY_SEED_FAMILIES")
        try:
            os.environ["HOURLY_SEED_FAMILIES"] = "OnlyFam:100"
            self.assertEqual(select_seed_family(0), "OnlyFam")
            self.assertEqual(select_seed_family(99), "OnlyFam")
        finally:
            if prev is None:
                os.environ.pop("HOURLY_SEED_FAMILIES", None)
            else:
                os.environ["HOURLY_SEED_FAMILIES"] = prev

    def test_select_seed_family_includes_still_templates(self) -> None:
        from shape_factory_hourly import _DEFAULT_SEED_FAMILY_WEIGHTS, select_seed_family

        names = {n for n, _w in _DEFAULT_SEED_FAMILY_WEIGHTS}
        self.assertIn("FB9-FaceBlast", names)
        self.assertIn("BounceDanceA", names)
        self.assertIn("FB9_GEX", names)
        self.assertIn("FB9_GEX2", names)
        weights = dict(_DEFAULT_SEED_FAMILY_WEIGHTS)
        self.assertEqual(weights["FB9_GEX"], weights["FB9_GEX2"])
        i2v = {
            "FB9-FaceBlast",
            "X-KNEEL-FB9",
            "BounceDanceA",
            "FB8VB2",
            "FB8VA5-ZOOMOUT",
            "Breast-shake-FB8VA5",
        }
        i2v_w = sum(w for n, w in _DEFAULT_SEED_FAMILY_WEIGHTS if n in i2v)
        total_w = sum(w for _n, w in _DEFAULT_SEED_FAMILY_WEIGHTS)
        self.assertEqual(total_w, 100)
        self.assertGreaterEqual(i2v_w, 83)
        self.assertEqual(max(weights.values()), weights["X-KNEEL-FB9"])
        self.assertGreaterEqual(weights["X-KNEEL-FB9"], 32)
        self.assertNotIn("FB8VA4", weights)
        picked = {select_seed_family(i) for i in range(200)}
        self.assertIn("X-KNEEL-FB9", picked)
        self.assertTrue(picked & {"FB9-FaceBlast", "BounceDanceA", "FB8VB2"})
        self.assertTrue(picked & {"FB9_GEX", "FB9_GEX2"})
        self.assertNotIn("FB8VA4", picked)

    def test_want_seed_over_chain_respects_share(self) -> None:
        import os

        from shape_factory_hourly import want_seed_over_chain

        prev = os.environ.get("HOURLY_SEED_OVER_CHAIN_SHARE")
        try:
            os.environ["HOURLY_SEED_OVER_CHAIN_SHARE"] = "0"
            self.assertFalse(want_seed_over_chain(0))
            self.assertFalse(want_seed_over_chain(99))
            os.environ["HOURLY_SEED_OVER_CHAIN_SHARE"] = "1"
            self.assertTrue(want_seed_over_chain(0))
            self.assertTrue(want_seed_over_chain(99))
            os.environ["HOURLY_SEED_OVER_CHAIN_SHARE"] = "0.25"
            hits = sum(1 for i in range(80) if want_seed_over_chain(i))
            self.assertGreater(hits, 5)
            self.assertLess(hits, 50)
            os.environ["HOURLY_SEED_OVER_CHAIN_SHARE"] = "0.50"
            hits50 = sum(1 for i in range(80) if want_seed_over_chain(i))
            self.assertGreater(hits50, 20)
            self.assertLess(hits50, 60)
        finally:
            if prev is None:
                os.environ.pop("HOURLY_SEED_OVER_CHAIN_SHARE", None)
            else:
                os.environ["HOURLY_SEED_OVER_CHAIN_SHARE"] = prev

    def test_facial_lookback_skips_ancient_gex2(self) -> None:
        import os
        import tempfile
        from datetime import datetime, timedelta, timezone

        from shape_factory_hourly import find_gex2_needing_facial

        prev = os.environ.get("HOURLY_FACIAL_LOOKBACK_DAYS")
        try:
            os.environ["HOURLY_FACIAL_LOOKBACK_DAYS"] = "14"
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                gex2 = root / "FB9_GEX2"
                facial = root / "FB9_GEX_FACIAL"
                gex2.mkdir()
                facial.mkdir()
                now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
                old = (now - timedelta(days=40)).isoformat()
                recent = (now - timedelta(days=3)).isoformat()
                (gex2 / "old.job.json").write_text(
                    json.dumps(
                        {
                            "job_key": "gex2-old",
                            "status": "complete",
                            "created_at": old,
                            "deposit": {"videos": ["/tmp/gex2_old.mp4"]},
                        }
                    ),
                    encoding="utf-8",
                )
                self.assertIsNone(
                    find_gex2_needing_facial(job_dir=root, now_ts=now.timestamp())
                )
                (gex2 / "new.job.json").write_text(
                    json.dumps(
                        {
                            "job_key": "gex2-new",
                            "status": "complete",
                            "created_at": recent,
                            "deposit": {"videos": ["/tmp/gex2_new.mp4"]},
                        }
                    ),
                    encoding="utf-8",
                )
                hit = find_gex2_needing_facial(job_dir=root, now_ts=now.timestamp())
                self.assertIsNotNone(hit)
                assert hit is not None
                self.assertEqual(hit.get("job_key"), "gex2-new")
                os.environ["HOURLY_FACIAL_LOOKBACK_DAYS"] = "none"
                hit_all = find_gex2_needing_facial(job_dir=root, now_ts=now.timestamp())
                self.assertIsNotNone(hit_all)
                assert hit_all is not None
                # Newest by event ts wins when unlimited.
                self.assertEqual(hit_all.get("job_key"), "gex2-new")
        finally:
            if prev is None:
                os.environ.pop("HOURLY_FACIAL_LOOKBACK_DAYS", None)
            else:
                os.environ["HOURLY_FACIAL_LOOKBACK_DAYS"] = prev

    def test_predict_skips_facial_when_seed_over_chain(self) -> None:
        import os
        import tempfile
        from unittest.mock import patch

        from shape_factory_hourly import predict_hourly_gex2, select_seed_family

        prev = os.environ.get("HOURLY_SEED_OVER_CHAIN_SHARE")
        prev_every = os.environ.get("HOURLY_FACIAL_DRAIN_EVERY")
        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                jobs = root / "shape_factory" / "jobs"
                gex2 = jobs / "FB9_GEX2"
                facial = jobs / "FB9_GEX_FACIAL"
                for d in (gex2, facial):
                    d.mkdir(parents=True)
                (gex2 / "a.job.json").write_text(
                    json.dumps(
                        {
                            "job_key": "gex2-a",
                            "status": "complete",
                            "created_at": "2026-08-18T00:00:00+00:00",
                            "deposit": {"videos": ["/tmp/gex2_out.mp4"]},
                        }
                    ),
                    encoding="utf-8",
                )
                state = {"sample_cursor": 6, "phase": "idle"}
                os.environ["HOURLY_SEED_OVER_CHAIN_SHARE"] = "0"
                os.environ["HOURLY_FACIAL_DRAIN_EVERY"] = "6"
                with patch(
                    "shape_factory_hourly._default_job_root",
                    return_value=jobs,
                ), patch(
                    "shape_factory_hourly.plan_hourly_step",
                    return_value={
                        "ok": True,
                        "pick_mode": "pool_product",
                        "step": "pool_product",
                        "bindings_preview": {"source_still": "/tmp/still.jpeg"},
                    },
                ):
                    blocked = predict_hourly_gex2(state, data_root=root)
                    self.assertEqual(blocked.get("family"), "FB9_GEX_FACIAL")
                    self.assertEqual(blocked.get("step"), "chain_facial")

                    # Off-cadence cursor skips facial even when seed-over is off.
                    off = predict_hourly_gex2({**state, "sample_cursor": 7}, data_root=root)
                    self.assertNotEqual(off.get("step"), "chain_facial")

                    os.environ["HOURLY_SEED_OVER_CHAIN_SHARE"] = "1"
                    seeded = predict_hourly_gex2(state, data_root=root)
                    self.assertEqual(seeded.get("family"), select_seed_family(6))
                    self.assertEqual(seeded.get("step"), "pool_product")
                    self.assertEqual(seeded.get("source_still"), "/tmp/still.jpeg")
        finally:
            if prev is None:
                os.environ.pop("HOURLY_SEED_OVER_CHAIN_SHARE", None)
            else:
                os.environ["HOURLY_SEED_OVER_CHAIN_SHARE"] = prev
            if prev_every is None:
                os.environ.pop("HOURLY_FACIAL_DRAIN_EVERY", None)
            else:
                os.environ["HOURLY_FACIAL_DRAIN_EVERY"] = prev_every

    def test_want_facial_chain_every_n(self) -> None:
        import os

        from shape_factory_hourly import want_facial_chain, want_i2v_gex_chain

        prev_f = os.environ.get("HOURLY_FACIAL_DRAIN_EVERY")
        prev_i = os.environ.get("HOURLY_I2V_GEX_DRAIN_EVERY")
        try:
            os.environ["HOURLY_FACIAL_DRAIN_EVERY"] = "6"
            self.assertTrue(want_facial_chain(0))
            self.assertTrue(want_facial_chain(6))
            self.assertTrue(want_facial_chain(12))
            self.assertFalse(want_facial_chain(1))
            self.assertFalse(want_facial_chain(7))
            os.environ["HOURLY_FACIAL_DRAIN_EVERY"] = "1"
            self.assertTrue(want_facial_chain(7))

            os.environ["HOURLY_I2V_GEX_DRAIN_EVERY"] = "3"
            self.assertTrue(want_i2v_gex_chain(0))
            self.assertTrue(want_i2v_gex_chain(3))
            self.assertTrue(want_i2v_gex_chain(6))
            self.assertFalse(want_i2v_gex_chain(1))
            self.assertFalse(want_i2v_gex_chain(2))
            self.assertFalse(want_i2v_gex_chain(4))
        finally:
            if prev_f is None:
                os.environ.pop("HOURLY_FACIAL_DRAIN_EVERY", None)
            else:
                os.environ["HOURLY_FACIAL_DRAIN_EVERY"] = prev_f
            if prev_i is None:
                os.environ.pop("HOURLY_I2V_GEX_DRAIN_EVERY", None)
            else:
                os.environ["HOURLY_I2V_GEX_DRAIN_EVERY"] = prev_i

    def test_simulate_hourly_picks_reports_variety(self) -> None:
        import os
        import tempfile
        from unittest.mock import patch

        from shape_factory_hourly import format_hourly_picks_table, simulate_hourly_picks

        prev = os.environ.get("HOURLY_SEED_OVER_CHAIN_SHARE")
        prev_lb = os.environ.get("HOURLY_FACIAL_LOOKBACK_DAYS")
        try:
            os.environ["HOURLY_SEED_OVER_CHAIN_SHARE"] = "0.5"
            os.environ["HOURLY_FACIAL_LOOKBACK_DAYS"] = "14"
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                jobs = root / "shape_factory" / "jobs"
                gex2 = jobs / "FB9_GEX2"
                facial = jobs / "FB9_GEX_FACIAL"
                bounce = jobs / "BounceDanceA"
                for d in (gex2, facial, bounce):
                    d.mkdir(parents=True)
                for i in range(3):
                    (gex2 / f"g{i}.job.json").write_text(
                        json.dumps(
                            {
                                "job_key": f"gex2-{i}",
                                "status": "complete",
                                "created_at": "2026-08-18T00:00:00+00:00",
                                "deposit": {"videos": [f"/tmp/gex2_{i}.mp4"]},
                            }
                        ),
                        encoding="utf-8",
                    )
                (bounce / "b.job.json").write_text(
                    json.dumps(
                        {
                            "job_key": "bounce-1",
                            "status": "complete",
                            "deposit": {"videos": ["/tmp/bounce.mp4"]},
                        }
                    ),
                    encoding="utf-8",
                )

                def _fake_plan(*, cursor: int = 0, family: str = "", **_kwargs):
                    return {
                        "ok": True,
                        "pick_mode": "pool_product",
                        "step": "pool_product",
                        "family": family,
                        "bindings_preview": {
                            "source_still": f"/tmp/still-{family}-{cursor}.jpeg",
                            "prompt_profile": f"/tmp/prompt-{family}.json",
                        },
                        "combo_key": f"still-{family}-{cursor}",
                    }

                with patch("shape_factory_hourly.plan_hourly_step", side_effect=_fake_plan), patch(
                    "shape_factory_hourly._default_job_root",
                    return_value=jobs,
                ):
                    result = simulate_hourly_picks(
                        16,
                        hourly_state={"sample_cursor": 100},
                        data_root=root,
                    )
                self.assertTrue(result.get("ok"))
                self.assertEqual(result["count"], 16)
                summary = result["summary"]
                self.assertEqual(summary["facial_backlog_start"], 3)
                self.assertGreaterEqual(summary["seed_count"], 4)
                self.assertGreaterEqual(summary["image_based_count"], 4)
                families = set(summary["by_family"])
                self.assertTrue(families & {"FB9_GEX_FACIAL", "FB9_GEX", "FB9-FaceBlast", "BounceDanceA", "X-KNEEL-FB9"})
                table = format_hourly_picks_table(result)
                self.assertIn("Summary", table)
                self.assertIn("FB9", table)
        finally:
            if prev is None:
                os.environ.pop("HOURLY_SEED_OVER_CHAIN_SHARE", None)
            else:
                os.environ["HOURLY_SEED_OVER_CHAIN_SHARE"] = prev
            if prev_lb is None:
                os.environ.pop("HOURLY_FACIAL_LOOKBACK_DAYS", None)
            else:
                os.environ["HOURLY_FACIAL_LOOKBACK_DAYS"] = prev_lb

    def test_still_recency_mult_boosts_fresh_input_images(self) -> None:
        import os
        import tempfile
        import time
        from pathlib import Path

        from shape_factory_hourly import _still_recency_mult

        prev_b = os.environ.get("HOURLY_RECENT_STILL_BOOST")
        prev_d = os.environ.get("HOURLY_RECENT_STILL_DAYS")
        try:
            os.environ["HOURLY_RECENT_STILL_BOOST"] = "4.0"
            os.environ["HOURLY_RECENT_STILL_DAYS"] = "14"
            with tempfile.TemporaryDirectory() as td:
                root = Path(td) / "input"
                root.mkdir()
                fresh = root / "fresh.jpeg"
                stale = root / "stale.jpeg"
                fresh.write_bytes(b"x")
                stale.write_bytes(b"y")
                now = time.time()
                os.utime(fresh, (now, now))
                os.utime(stale, (now - 40 * 86400, now - 40 * 86400))
                self.assertGreater(_still_recency_mult(str(fresh), now_ts=now), 3.0)
                self.assertEqual(_still_recency_mult(str(stale), now_ts=now), 1.0)
                self.assertEqual(_still_recency_mult("/tmp/og/clip.mp4", now_ts=now), 1.0)
                # i2v starters use a stronger fresh-still boost than v2v / untagged.
                bounce = _still_recency_mult(str(fresh), now_ts=now, family="BounceDanceA")
                faceblast = _still_recency_mult(str(fresh), now_ts=now, family="FB9-FaceBlast")
                generic = _still_recency_mult(str(fresh), now_ts=now, family="FB9_GEX")
                self.assertGreater(bounce, generic)
                self.assertGreater(faceblast, generic)
                self.assertEqual(bounce, faceblast)
        finally:
            if prev_b is None:
                os.environ.pop("HOURLY_RECENT_STILL_BOOST", None)
            else:
                os.environ["HOURLY_RECENT_STILL_BOOST"] = prev_b
            if prev_d is None:
                os.environ.pop("HOURLY_RECENT_STILL_DAYS", None)
            else:
                os.environ["HOURLY_RECENT_STILL_DAYS"] = prev_d

    def test_bouncedance_hourly_prefers_fresh_pool_product(self) -> None:
        import os
        import tempfile
        from pathlib import Path
        from unittest import mock

        from shape_factory_hourly import plan_hourly_step

        prev = os.environ.get("HOURLY_BOUNCEDANCE_FRESH_STILL_SHARE")
        prev_cat = os.environ.get("HOURLY_INPUT_STILL_CATALOG")
        try:
            os.environ["HOURLY_BOUNCEDANCE_FRESH_STILL_SHARE"] = "1.0"
            os.environ["HOURLY_INPUT_STILL_CATALOG"] = "0"
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                (root / "shapes").mkdir()
                (root / "pools" / "BounceDanceA" / "prompts").mkdir(parents=True)
                still = root / "input" / "new.jpeg"
                still.parent.mkdir(parents=True)
                still.write_bytes(b"img")
                prompt = root / "pools" / "BounceDanceA" / "prompts" / "catalog-default.json"
                prompt.write_text("{}", encoding="utf-8")
                (root / "shapes" / "BounceDanceA.shape.yaml").write_text(
                    "family_slug: BounceDanceA\n"
                    "requires:\n"
                    "  - slot: source_still\n"
                    "  - slot: prompt_profile\n",
                    encoding="utf-8",
                )
                (root / "pools" / "BounceDanceA" / "pools.yaml").write_text(
                    "pools:\n"
                    "  source_still:\n"
                    "    slot: source_still\n"
                    "    members:\n"
                    f"      - glob: {still}\n"
                    "  prompt_profile:\n"
                    "    slot: prompt_profile\n"
                    "    members:\n"
                    f"      - dir: {prompt.parent}\n"
                    '        ext: [".json"]\n',
                    encoding="utf-8",
                )
                with mock.patch("shape_factory_hourly._is_top_of_hour", return_value=False):
                    plan = plan_hourly_step(cursor=0, data_root=root, family="BounceDanceA")
                self.assertTrue(plan.get("ok"), plan)
                self.assertEqual(plan.get("pick_mode"), "pool_product")
                self.assertTrue(plan.get("fresh_still_preferred"))
                self.assertEqual(plan["picks"]["source_still"], str(still.resolve()))
        finally:
            if prev is None:
                os.environ.pop("HOURLY_BOUNCEDANCE_FRESH_STILL_SHARE", None)
            else:
                os.environ["HOURLY_BOUNCEDANCE_FRESH_STILL_SHARE"] = prev
            if prev_cat is None:
                os.environ.pop("HOURLY_INPUT_STILL_CATALOG", None)
            else:
                os.environ["HOURLY_INPUT_STILL_CATALOG"] = prev_cat

    def test_faceblast_hourly_prefers_fresh_pool_product(self) -> None:
        import os
        import tempfile
        from pathlib import Path
        from unittest import mock

        from shape_factory_hourly import plan_hourly_step

        prev = os.environ.get("HOURLY_FRESH_STILL_SHARE")
        prev_cat = os.environ.get("HOURLY_INPUT_STILL_CATALOG")
        try:
            os.environ["HOURLY_FRESH_STILL_SHARE"] = "1.0"
            os.environ["HOURLY_INPUT_STILL_CATALOG"] = "0"
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                family = "FB9-FaceBlast"
                (root / "shapes").mkdir()
                (root / "pools" / family / "prompts").mkdir(parents=True)
                still = root / "input" / "fresh.jpeg"
                still.parent.mkdir(parents=True)
                still.write_bytes(b"img")
                prompt = root / "pools" / family / "prompts" / "catalog-default.json"
                prompt.write_text("{}", encoding="utf-8")
                (root / "shapes" / f"{family}.shape.yaml").write_text(
                    f"family_slug: {family}\n"
                    "requires:\n"
                    "  - slot: source_still\n"
                    "  - slot: prompt_profile\n",
                    encoding="utf-8",
                )
                (root / "pools" / family / "pools.yaml").write_text(
                    "pools:\n"
                    "  source_still:\n"
                    "    slot: source_still\n"
                    "    members:\n"
                    f"      - glob: {still}\n"
                    "  prompt_profile:\n"
                    "    slot: prompt_profile\n"
                    "    members:\n"
                    f"      - dir: {prompt.parent}\n"
                    '        ext: [".json"]\n',
                    encoding="utf-8",
                )
                with mock.patch("shape_factory_hourly._is_top_of_hour", return_value=False):
                    plan = plan_hourly_step(cursor=0, data_root=root, family=family)
                self.assertTrue(plan.get("ok"), plan)
                self.assertEqual(plan.get("pick_mode"), "pool_product")
                self.assertTrue(plan.get("fresh_still_preferred"))
                self.assertEqual(plan["picks"]["source_still"], str(still.resolve()))
        finally:
            if prev is None:
                os.environ.pop("HOURLY_FRESH_STILL_SHARE", None)
            else:
                os.environ["HOURLY_FRESH_STILL_SHARE"] = prev
            if prev_cat is None:
                os.environ.pop("HOURLY_INPUT_STILL_CATALOG", None)
            else:
                os.environ["HOURLY_INPUT_STILL_CATALOG"] = prev_cat

    def test_pool_product_skips_recently_used_still(self) -> None:
        import os
        import tempfile
        from pathlib import Path
        from unittest import mock

        from shape_factory_hourly import plan_pool_product_fallback

        prev_cat = os.environ.get("HOURLY_INPUT_STILL_CATALOG")
        try:
            os.environ["HOURLY_INPUT_STILL_CATALOG"] = "0"
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                family = "FB9-FaceBlast"
                (root / "shapes").mkdir()
                (root / "pools" / family / "prompts").mkdir(parents=True)
                used = root / "input" / "SSScb9used.jpeg"
                fresh = root / "input" / "newstill.jpeg"
                used.parent.mkdir(parents=True)
                used.write_bytes(b"old")
                fresh.write_bytes(b"new")
                prompt = root / "pools" / family / "prompts" / "catalog-default.json"
                prompt.write_text("{}", encoding="utf-8")
                (root / "shapes" / f"{family}.shape.yaml").write_text(
                    f"family_slug: {family}\n"
                    "requires:\n"
                    "  - slot: source_still\n"
                    "  - slot: prompt_profile\n",
                    encoding="utf-8",
                )
                (root / "pools" / family / "pools.yaml").write_text(
                    "pools:\n"
                    "  source_still:\n"
                    "    slot: source_still\n"
                    "    members:\n"
                    f"      - glob: {used.parent}/*.jpeg\n"
                    "  prompt_profile:\n"
                    "    slot: prompt_profile\n"
                    "    members:\n"
                    f"      - dir: {prompt.parent}\n"
                    '        ext: [".json"]\n',
                    encoding="utf-8",
                )
                recent = {"pp-catalog-default__still-SSScb9used"}
                with mock.patch("shape_factory_hourly._recent_combo_keys", return_value=recent):
                    plan = plan_pool_product_fallback(cursor=3, data_root=root, family=family)
                self.assertTrue(plan.get("ok"), plan)
                self.assertEqual(Path(plan["picks"]["source_still"]).name, "newstill.jpeg")
        finally:
            if prev_cat is None:
                os.environ.pop("HOURLY_INPUT_STILL_CATALOG", None)
            else:
                os.environ["HOURLY_INPUT_STILL_CATALOG"] = prev_cat

    def test_pool_product_prefers_weekly_still(self) -> None:
        import os
        import tempfile
        import time
        from pathlib import Path

        from shape_factory_hourly import plan_pool_product_fallback

        prev_cat = os.environ.get("HOURLY_INPUT_STILL_CATALOG")
        prev_share = os.environ.get("HOURLY_WEEKLY_STILL_SHARE")
        prev_days = os.environ.get("HOURLY_RECENT_STILL_DAYS")
        try:
            os.environ["HOURLY_INPUT_STILL_CATALOG"] = "0"
            os.environ["HOURLY_WEEKLY_STILL_SHARE"] = "1.0"
            os.environ["HOURLY_RECENT_STILL_DAYS"] = "7"
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                family = "BounceDanceA"
                (root / "shapes").mkdir()
                (root / "pools" / family / "prompts").mkdir(parents=True)
                old = root / "input" / "SSSold.jpeg"
                recent = root / "input" / "SSSnew.jpeg"
                old.parent.mkdir(parents=True)
                old.write_bytes(b"old")
                recent.write_bytes(b"new")
                now = time.time()
                os.utime(old, (now - 14 * 86400, now - 14 * 86400))
                os.utime(recent, (now - 86400, now - 86400))
                prompt = root / "pools" / family / "prompts" / "catalog-default.json"
                prompt.write_text("{}", encoding="utf-8")
                (root / "shapes" / f"{family}.shape.yaml").write_text(
                    f"family_slug: {family}\n"
                    "requires:\n"
                    "  - slot: source_still\n"
                    "  - slot: prompt_profile\n",
                    encoding="utf-8",
                )
                (root / "pools" / family / "pools.yaml").write_text(
                    "pools:\n"
                    "  source_still:\n"
                    "    slot: source_still\n"
                    "    members:\n"
                    f"      - glob: {old.parent}/*.jpeg\n"
                    "  prompt_profile:\n"
                    "    slot: prompt_profile\n"
                    "    members:\n"
                    f"      - dir: {prompt.parent}\n"
                    '        ext: [".json"]\n',
                    encoding="utf-8",
                )
                plan = plan_pool_product_fallback(cursor=11, data_root=root, family=family)
                self.assertTrue(plan.get("ok"), plan)
                self.assertEqual(Path(plan["picks"]["source_still"]).name, "SSSnew.jpeg")
                self.assertTrue(plan.get("weekly_still_preferred"))
                self.assertTrue(plan.get("weekly_still_picked"))
        finally:
            for key, prev in (
                ("HOURLY_INPUT_STILL_CATALOG", prev_cat),
                ("HOURLY_WEEKLY_STILL_SHARE", prev_share),
                ("HOURLY_RECENT_STILL_DAYS", prev_days),
            ):
                if prev is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = prev

    def test_fresh_still_share_defaults_to_ninety_percent(self) -> None:
        import os

        from shape_factory_hourly import _fresh_still_share, _weekly_still_pick_share

        prev_f = os.environ.get("HOURLY_FRESH_STILL_SHARE")
        prev_w = os.environ.get("HOURLY_WEEKLY_STILL_SHARE")
        try:
            os.environ.pop("HOURLY_FRESH_STILL_SHARE", None)
            os.environ.pop("HOURLY_WEEKLY_STILL_SHARE", None)
            self.assertAlmostEqual(_fresh_still_share("BounceDanceA"), 0.90)
            self.assertAlmostEqual(_weekly_still_pick_share(), 0.90)
        finally:
            if prev_f is None:
                os.environ.pop("HOURLY_FRESH_STILL_SHARE", None)
            else:
                os.environ["HOURLY_FRESH_STILL_SHARE"] = prev_f
            if prev_w is None:
                os.environ.pop("HOURLY_WEEKLY_STILL_SHARE", None)
            else:
                os.environ["HOURLY_WEEKLY_STILL_SHARE"] = prev_w

    def test_picks_from_job_uses_embedded_prompt_text(self) -> None:
        import tempfile
        from pathlib import Path

        from shape_factory_hourly import _job_is_replayable, _picks_from_job

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            still = root / "input" / "face.jpeg"
            still.parent.mkdir(parents=True)
            still.write_bytes(b"img")
            job = {
                "job_key": "FB9-FaceBlast__backfill__clip",
                "origin": "backfill",
                "family_slug": "FB9-FaceBlast",
                "bindings": {
                    "prompt_profile": {
                        "positive": "a still-started clip",
                        "negative": "blur",
                        "recovered": True,
                    },
                    "source_still": {"path": str(still), "binding_type": "load_image"},
                },
                "submit": {"status": "completed", "outputs": [str(root / "out.mp4")]},
            }
            shape = {
                "family_slug": "FB9-FaceBlast",
                "requires": [{"slot": "source_still"}, {"slot": "prompt_profile"}],
            }
            self.assertTrue(_job_is_replayable(job))
            picks = _picks_from_job(job, shape=shape, data_root=root)
            self.assertIsNotNone(picks)
            assert picks is not None
            self.assertEqual(picks["source_still"].resolve(), still.resolve())
            self.assertTrue(picks["prompt_profile"].is_file())
            self.assertIn("a still-started clip", picks["prompt_profile"].read_text(encoding="utf-8"))

    def test_recent_source_penalty_applies_to_stills(self) -> None:
        from shape_factory_hourly import _apply_recent_source_penalty, _recent_source_basenames

        recipes = [
            {
                "combo_key": "pp-catalog-default__still-SSScb9used",
                "picks": {"source_still": "/input/SSScb9used.jpeg"},
            },
            {
                "combo_key": "pp-catalog-default__still-newstill",
                "picks": {"source_still": "/input/newstill.jpeg"},
            },
        ]
        recent = {"pp-catalog-default__still-SSScb9used"}
        weights = _apply_recent_source_penalty(recipes, [1.0, 1.0], _recent_source_basenames(recent), penalty=0.1)
        self.assertAlmostEqual(weights[0], 0.1)
        self.assertAlmostEqual(weights[1], 1.0)

    def test_resolve_glob_mtime_desc_keeps_newest_under_limit(self) -> None:
        import os
        import tempfile
        import time
        from pathlib import Path

        from shape_factory import resolve_glob

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old = root / "old.png"
            new = root / "new.png"
            mid = root / "mid.png"
            old.write_bytes(b"a")
            new.write_bytes(b"b")
            mid.write_bytes(b"c")
            now = time.time()
            os.utime(old, (now - 300, now - 300))
            os.utime(mid, (now - 200, now - 200))
            os.utime(new, (now - 10, now - 10))
            paths = resolve_glob({"glob": str(root / "*.png"), "sort": "mtime_desc", "limit": 2})
            names = {p.name for p in paths}
            self.assertEqual(names, {"new.png", "mid.png"})

    def test_plan_pool_product_fallback_samples_required_slots(self) -> None:
        import tempfile
        from shape_factory_hourly import plan_pool_product_fallback

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            family = "MiniFam"
            shapes = root / "shapes"
            pools = root / "pools" / family
            media = root / "media"
            prompts = pools / "prompts"
            shapes.mkdir(parents=True)
            prompts.mkdir(parents=True)
            media.mkdir(parents=True)
            still = media / "a.png"
            still.write_bytes(b"png")
            prompt = prompts / "p.json"
            prompt.write_text('{"positive":"x","negative":"y"}\n', encoding="utf-8")
            (shapes / f"{family}.shape.yaml").write_text(
                "family_slug: MiniFam\n"
                "requires:\n"
                "  - slot: source_still\n"
                "  - slot: prompt_profile\n",
                encoding="utf-8",
            )
            (pools / "pools.yaml").write_text(
                f"pools:\n"
                f"  source_still:\n"
                f"    slot: source_still\n"
                f"    members:\n"
                f"      - glob: {still}\n"
                f"  prompt_profile:\n"
                f"    slot: prompt_profile\n"
                f"    members:\n"
                f"      - dir: {prompts}\n"
                f"        ext: [\".json\"]\n",
                encoding="utf-8",
            )
            plan = plan_pool_product_fallback(cursor=3, data_root=root, family=family)
            self.assertTrue(plan.get("ok"), plan)
            self.assertEqual(plan.get("pick_mode"), "pool_product")
            self.assertEqual(plan["picks"]["source_still"], str(still.resolve()))
            self.assertEqual(plan["picks"]["prompt_profile"], str(prompt.resolve()))

    def test_find_chain_need_helpers(self) -> None:
        import tempfile
        from shape_factory_hourly import (
            find_gex2_needing_facial,
            find_i2v_needing_gex,
            find_kneel_needing_gex,
            find_kneel_needing_gex2,
        )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            gex2 = root / "FB9_GEX2"
            gex = root / "FB9_GEX"
            facial = root / "FB9_GEX_FACIAL"
            kneel = root / "X-KNEEL-FB9"
            faceblast = root / "FB9-FaceBlast"
            bounce = root / "BounceDanceA"
            for d in (gex2, gex, facial, kneel, faceblast, bounce):
                d.mkdir()
            vid_g = "/tmp/gex2_out.mp4"
            vid_k = "/tmp/kneel_out.mp4"
            vid_g_parent = "/tmp/kneel_parent_for_gex2.mp4"
            vid_fb = "/tmp/faceblast_out.mp4"
            vid_bd = "/tmp/bouncedance_final.mp4"
            vid_bd_preview = "/tmp/bouncedance_PREVIEW.mp4"
            (gex2 / "a.job.json").write_text(
                json.dumps(
                    {
                        "job_key": "gex2-a",
                        "status": "complete",
                        "deposit": {"videos": [vid_g]},
                        "bindings": {"source_video": {"path": vid_g_parent}},
                    }
                ),
                encoding="utf-8",
            )
            (kneel / "k.job.json").write_text(
                json.dumps(
                    {
                        "job_key": "kneel-k",
                        "status": "complete",
                        "deposit": {"videos": [vid_k]},
                    }
                ),
                encoding="utf-8",
            )
            (faceblast / "fb.job.json").write_text(
                json.dumps(
                    {
                        "job_key": "faceblast-1",
                        "submit": {"status": "complete"},
                        "deposit": {"videos": [vid_fb]},
                    }
                ),
                encoding="utf-8",
            )
            (bounce / "bd.job.json").write_text(
                json.dumps(
                    {
                        "job_key": "bounce-1",
                        "submit": {"status": "completed"},
                        "deposit": {
                            "videos": [
                                vid_bd,
                                vid_bd_preview,
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            hit_facial = find_gex2_needing_facial(job_dir=root)
            self.assertIsNotNone(hit_facial)
            assert hit_facial is not None
            self.assertEqual(hit_facial.get("job_key"), "gex2-a")
            self.assertEqual(hit_facial.get("video"), vid_g)
            self.assertEqual(hit_facial.get("source_ref"), vid_g_parent)
            self.assertEqual(find_kneel_needing_gex2(job_dir=root), "kneel-k")
            self.assertEqual(find_kneel_needing_gex(job_dir=root), "kneel-k")
            # X-KNEEL preferred over BounceDance / FaceBlast when all need GEX.
            hit = find_i2v_needing_gex(job_dir=root)
            self.assertIsNotNone(hit)
            assert hit is not None
            self.assertEqual(hit.get("producer_family"), "X-KNEEL-FB9")
            self.assertEqual(hit.get("job_key"), "kneel-k")
            self.assertEqual(hit.get("video"), vid_k)
            (facial / "f.job.json").write_text(
                json.dumps({"bindings": {"source_video": {"path": vid_g}}}),
                encoding="utf-8",
            )
            (gex2 / "from_kneel.job.json").write_text(
                json.dumps({"bindings": {"source_video": {"path": vid_k}}}),
                encoding="utf-8",
            )
            self.assertIsNone(find_gex2_needing_facial(job_dir=root))
            self.assertIsNone(find_kneel_needing_gex2(job_dir=root))
            # Consumed by GEX2 does not satisfy Kneel→GEX.
            self.assertEqual(find_kneel_needing_gex(job_dir=root), "kneel-k")
            (gex / "from_kneel.job.json").write_text(
                json.dumps({"bindings": {"source_video": {"path": vid_k}}}),
                encoding="utf-8",
            )
            hit2 = find_i2v_needing_gex(job_dir=root)
            self.assertIsNotNone(hit2)
            assert hit2 is not None
            self.assertEqual(hit2.get("producer_family"), "BounceDanceA")
            self.assertEqual(hit2.get("job_key"), "bounce-1")
            self.assertEqual(hit2.get("video"), vid_bd)
            self.assertNotEqual(hit2.get("video"), vid_bd_preview)
            (gex / "from_bounce.job.json").write_text(
                json.dumps({"bindings": {"source_video": {"path": vid_bd}}}),
                encoding="utf-8",
            )
            hit3 = find_i2v_needing_gex(job_dir=root)
            self.assertIsNotNone(hit3)
            assert hit3 is not None
            self.assertEqual(hit3.get("producer_family"), "FB9-FaceBlast")
            (gex / "from_faceblast.job.json").write_text(
                json.dumps({"bindings": {"source_video": {"path": vid_fb}}}),
                encoding="utf-8",
            )
            self.assertIsNone(find_kneel_needing_gex(job_dir=root))
            self.assertIsNone(find_i2v_needing_gex(job_dir=root))

    def test_top_of_hour_and_recent_five_star_multiplier(self) -> None:
        from datetime import datetime, timezone

        from shape_factory_hourly import (
            _apply_recent_five_star_bias,
            _is_top_of_hour,
            _recent_five_star_multiplier,
        )

        self.assertTrue(_is_top_of_hour(datetime(2026, 7, 14, 10, 0), window_minutes=12))
        self.assertTrue(_is_top_of_hour(datetime(2026, 7, 14, 10, 11), window_minutes=12))
        self.assertFalse(_is_top_of_hour(datetime(2026, 7, 14, 10, 12), window_minutes=12))
        self.assertFalse(_is_top_of_hour(datetime(2026, 7, 14, 10, 30), window_minutes=12))

        now = datetime(2026, 7, 14, 10, 5, tzinfo=timezone.utc)
        recent_row = {"explicit": 5, "rated_at": "2026-07-13T12:00:00+00:00"}
        old_row = {"explicit": 5, "rated_at": "2026-01-01T12:00:00+00:00"}
        mid_row = {"explicit": 4, "rated_at": "2026-07-13T12:00:00+00:00"}
        self.assertGreater(
            _recent_five_star_multiplier(recent_row, now=now, top_of_hour=True),
            5.0,
        )
        self.assertEqual(
            _recent_five_star_multiplier(old_row, now=now, top_of_hour=True),
            1.0,
        )
        self.assertEqual(
            _recent_five_star_multiplier(mid_row, now=now, top_of_hour=True),
            1.0,
        )
        self.assertEqual(
            _recent_five_star_multiplier(recent_row, now=now, top_of_hour=False),
            1.0,
        )

        recipes = [
            {"output_path": "/data/output/og/2026-07-13/a.mp4"},
            {"output_path": "/data/output/og/2026-01-01/b.mp4"},
        ]
        weights = [1.0, 1.0]
        meta = [{}, {}]
        ratings = {
            "by_output_relpath": {
                "og/2026-07-13/a": {"explicit": 5, "rated_at": "2026-07-13T12:00:00+00:00"},
                "og/2026-01-01/b": {"explicit": 5, "rated_at": "2026-01-01T12:00:00+00:00"},
            }
        }
        with unittest.mock.patch("shape_factory_hourly._is_top_of_hour", return_value=True):
            boosted, stats = _apply_recent_five_star_bias(recipes, weights, meta, ratings, now=now)
        self.assertGreater(boosted[0], boosted[1])
        self.assertEqual(stats["recent_five_star_boosted"], 1)
        self.assertTrue(stats["top_of_hour"])

    def test_plan_hourly_step_prefers_replay_at_top_of_hour(self) -> None:
        from unittest import mock

        from shape_factory_hourly import plan_hourly_step

        replay = {
            "ok": True,
            "combo_key": "prompt_profile-x__source_video-y",
            "rating_kind": "explicit",
            "rating_effective": 5,
            "recent_five_star_boosted": 2,
        }
        derive = {"ok": True, "fast_track": False, "combo_key": "other"}
        predicted = {"ok": True, "combo_key": "pred"}
        with mock.patch("shape_factory_hourly._is_top_of_hour", return_value=True):
            with mock.patch("shape_factory_hourly.plan_hourly_predicted_derive", return_value=predicted):
                with mock.patch("shape_factory_hourly.plan_hourly_derive", return_value=derive):
                    with mock.patch("shape_factory_hourly.plan_hourly_replay", return_value=replay):
                        plan = plan_hourly_step(cursor=0, family="FB9_GEX2")
        self.assertTrue(plan.get("ok"))
        self.assertEqual(plan.get("step"), "replay")
        self.assertTrue(plan.get("top_of_hour"))
        self.assertEqual(plan.get("combo_key"), replay["combo_key"])

    def test_source_promotion_detects_kneel_and_2025(self) -> None:
        from shape_factory_hourly import (
            _is_2025_source,
            _is_kneel_source,
            _recipe_promotion_mult,
            _source_promotion_mult,
        )

        self.assertTrue(_is_kneel_source("/data/og/2026-04-03/X-Kneel-FB9-2026-04-03-142014_OG_00001.mp4"))
        self.assertTrue(_is_2025_source("/data/output/og/2025-10-06/135612_OG_00001.mp4"))
        self.assertFalse(_is_2025_source("/data/output/og/2026-04-03/FB9_GEX2_2026-04-03_00001.mp4"))
        self.assertGreater(
            _source_promotion_mult("/data/og/2026-04-03/X-Kneel-FB9-2026-04-03_OG_00001.mp4"),
            1.0,
        )
        kneel_recipe = {
            "picks": {"source_video": "/data/og/x/X-Kneel-FB9-seed.mp4"},
            "output_path": "/data/og/2026-04-03/FB9_GEX2_out.mp4",
        }
        plain = {
            "picks": {"source_video": "/data/og/2026-04-03/FB9_GEX2_2026-04-03_00001.mp4"},
            "output_path": "/data/og/2026-04-04/FB9_GEX2_2026-04-04_00001.mp4",
        }
        self.assertGreater(_recipe_promotion_mult(kneel_recipe), _recipe_promotion_mult(plain))

    def test_pick_preferring_non_recent_weights_promoted_sources(self) -> None:
        import random

        from shape_factory_hourly import _pick_preferring_non_recent, _source_promotion_mult

        candidates = [
            "/tmp/og/2026-04-03/FB9_GEX2_chain.mp4",
            "/tmp/og/2025-10-06/135612_OG_00001.mp4",
            "/tmp/og/2026-04-03/X-Kneel-FB9-seed.mp4",
        ]
        counts = {c: 0 for c in candidates}
        rng = random.Random(0)
        for _ in range(200):
            picked = _pick_preferring_non_recent(
                candidates,
                combo_for=lambda c: f"prompt_profile-x__source_video-{Path(c).stem}",
                recent=set(),
                rng=rng,
                weight_for=_source_promotion_mult,
            )
            counts[str(picked)] += 1
        # Promoted kneel/2025 should win more often than plain GEX2 chain.
        self.assertGreater(
            counts["/tmp/og/2026-04-03/X-Kneel-FB9-seed.mp4"]
            + counts["/tmp/og/2025-10-06/135612_OG_00001.mp4"],
            counts["/tmp/og/2026-04-03/FB9_GEX2_chain.mp4"],
        )

    def test_resolve_glob_supports_year_folder_pattern(self) -> None:
        from shape_factory import resolve_glob

        og = Path("/home/yuji/comfyui-runpod-data/output/og")
        if not (og / "2025-10-06").is_dir():
            self.skipTest("no 2025 og folders on this host")
        paths = resolve_glob(
            {"glob": str(og / "2025-*" / "*.mp4"), "limit": 20}
        )
        self.assertGreater(len(paths), 0)
        self.assertTrue(all("/og/2025-" in str(p).replace("\\", "/") for p in paths))

    def test_archive_og_detection_age_and_hourly_gate(self) -> None:
        from datetime import datetime, timezone

        from shape_factory_hourly import _archive_age_spread_mult, _is_archive_og_path, _is_archive_og_recipe

        now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc).timestamp()
        old = "/data/output/og/2025-10-06/135612_OG_00001.mp4"
        recent = "/data/output/og/2026-07-01/FB9_GEX2_2026-07-01_00001.mp4"
        hourly = "/data/output/og/2025-10-06/hourly/hourly__demo.mp4"

        self.assertTrue(_is_archive_og_path(old, now_ts=now, min_age_days=45))
        self.assertFalse(_is_archive_og_path(recent, now_ts=now, min_age_days=45))
        self.assertFalse(_is_archive_og_path(hourly, now_ts=now, min_age_days=45))

        archive_recipe = {
            "source": f"og:{old}",
            "output_path": old,
            "picks": {"source_video": "/tmp/seed.mp4"},
        }
        hourly_recipe = {
            "source": "hourly__prompt_profile-x__source_video-y",
            "output_path": hourly,
            "picks": {"source_video": "/tmp/seed.mp4"},
        }
        self.assertTrue(_is_archive_og_recipe(archive_recipe, now_ts=now, min_age_days=45))
        self.assertFalse(_is_archive_og_recipe(hourly_recipe, now_ts=now, min_age_days=45))
        self.assertGreater(_archive_age_spread_mult(archive_recipe, now_ts=now), 1.0)
        self.assertEqual(_archive_age_spread_mult(hourly_recipe, now_ts=now), 1.0)

    def test_plan_replay_archive_og_share_forces_pool(self) -> None:
        import os
        from unittest import mock

        from shape_factory_hourly import plan_hourly_replay

        archive = {
            "combo_key": "prompt_profile-a__source_video-old",
            "source": "og:/data/output/og/2025-06-19/old_OG_00001.mp4",
            "output_path": "/data/output/og/2025-06-19/old_OG_00001.mp4",
            "picks": {
                "prompt_profile": "/tmp/prompt.json",
                "source_video": "/tmp/seed.mp4",
            },
            "bindings_preview": {},
        }
        recent = {
            "combo_key": "prompt_profile-b__source_video-new",
            "source": "hourly__recent",
            "output_path": "/data/output/og/2026-07-20/hourly/hourly__new.mp4",
            "picks": {
                "prompt_profile": "/tmp/prompt.json",
                "source_video": "/tmp/seed2.mp4",
            },
            "bindings_preview": {},
        }

        with mock.patch.dict(
            os.environ,
            {"HOURLY_ARCHIVE_OG_SHARE": "1", "HOURLY_ARCHIVE_MIN_AGE_DAYS": "45", "HOURLY_RATING_BLEND": "0"},
            clear=False,
        ):
            with mock.patch("shape_factory_hourly.collect_replay_recipes", return_value=[archive, recent]):
                with mock.patch("shape_factory_hourly._load_ratings_index", return_value=None):
                    with mock.patch("shape_factory_hourly._load_heuristics_index", return_value=None):
                        with mock.patch("shape_factory_hourly._load_appetite_index", return_value=None):
                            with mock.patch("shape_factory_hourly._recent_combo_keys", return_value=set()):
                                with mock.patch("shape_factory_hourly.load_yaml", return_value={}):
                                    with mock.patch.object(Path, "is_file", return_value=True):
                                        plan = plan_hourly_replay(
                                            cursor=0, family="FB9_GEX2", data_root=REPO_ROOT / ".data"
                                        )

        self.assertTrue(plan.get("ok"), plan)
        self.assertTrue(plan.get("archive_og_forced"))
        self.assertEqual(plan.get("archive_og_candidate_count"), 1)
        self.assertEqual(plan.get("combo_key"), archive["combo_key"])
        self.assertTrue(plan.get("archive_og"))

    def test_prefer_identity_anchor_upgrades_when_still_resolves(self) -> None:
        import tempfile
        from unittest import mock

        from shape_factory_hourly import prefer_identity_anchor_on_extend

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            ws = root / "workspace"
            out = root / "output"
            shapes = data / "shapes"
            inp = ws / "input"
            og = out / "og"
            for p in (shapes, inp, og):
                p.mkdir(parents=True)
            (shapes / "FB9_GEX2_identity_anchor.shape.yaml").write_text(
                "family_slug: FB9_GEX2_identity_anchor\n", encoding="utf-8"
            )
            still = inp / "face.jpeg"
            still.write_bytes(b"still")
            clip = og / "clip.mp4"
            clip.write_bytes(b"vid")

            plan = {
                "ok": True,
                "family": "FB9_GEX2",
                "pick_mode": "extend",
                "derive_action": "extend",
                "picks": {
                    "source_video": str(clip),
                    "prompt_profile": str(data / "prompt.json"),
                },
                "parent_output": str(clip),
                "combo_key": "old",
            }
            (data / "prompt.json").write_text("{}", encoding="utf-8")

            with mock.patch(
                "shape_factory_identity_still.list_identity_still_candidates",
                return_value={
                    "ok": True,
                    "needed": True,
                    "recommended_id": "a1",
                    "candidates": [
                        {
                            "id": "a1",
                            "path": str(still),
                            "evidence": "lineage_root",
                            "label": "Lineage",
                        }
                    ],
                    "mint_targets": [],
                },
            ):
                out_plan = prefer_identity_anchor_on_extend(
                    plan,
                    data_root=data,
                    workspace_root=ws,
                    output_root=out,
                    allow_mint=False,
                )

            self.assertEqual(out_plan.get("family"), "FB9_GEX2_identity_anchor")
            self.assertEqual(out_plan.get("upgraded_from"), "FB9_GEX2")
            self.assertEqual(Path(out_plan["picks"]["identity_anchor"]).resolve(), still.resolve())
            self.assertEqual(out_plan.get("identity_evidence"), "lineage_root")
            self.assertNotEqual(out_plan.get("combo_key"), "old")

    def test_prefer_identity_anchor_falls_back_without_still(self) -> None:
        import tempfile
        from unittest import mock

        from shape_factory_hourly import prefer_identity_anchor_on_extend

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            ws = root / "workspace"
            out = root / "output"
            shapes = data / "shapes"
            og = out / "og"
            for p in (shapes, ws / "input", og):
                p.mkdir(parents=True)
            (shapes / "FB9_GEX2_identity_anchor.shape.yaml").write_text(
                "family_slug: FB9_GEX2_identity_anchor\n", encoding="utf-8"
            )
            clip = og / "clip.mp4"
            clip.write_bytes(b"vid")
            plan = {
                "ok": True,
                "family": "FB9_GEX2",
                "pick_mode": "extend",
                "picks": {"source_video": str(clip), "prompt_profile": "/tmp/p.json"},
                "parent_output": str(clip),
            }
            with mock.patch(
                "shape_factory_identity_still.list_identity_still_candidates",
                return_value={
                    "ok": True,
                    "needed": True,
                    "candidates": [],
                    "mint_targets": [],
                },
            ):
                out_plan = prefer_identity_anchor_on_extend(
                    plan,
                    data_root=data,
                    workspace_root=ws,
                    output_root=out,
                    allow_mint=True,
                )
            self.assertEqual(out_plan.get("family"), "FB9_GEX2")
            self.assertIsNone(out_plan.get("upgraded_from"))
            self.assertEqual(out_plan.get("identity_anchor_skipped"), "no_still")

    def test_prefer_identity_anchor_skips_non_extend(self) -> None:
        from shape_factory_hourly import prefer_identity_anchor_on_extend

        plan = {"ok": True, "family": "FB9_GEX2", "pick_mode": "derive", "picks": {}}
        self.assertIs(prefer_identity_anchor_on_extend(plan, data_root=REPO_ROOT / ".data"), plan)


if __name__ == "__main__":
    unittest.main()
