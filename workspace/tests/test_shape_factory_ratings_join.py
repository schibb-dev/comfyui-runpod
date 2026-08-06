#!/usr/bin/env python3
"""Tests for ratings job-join recipe keys and lineage uplift."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from shape_factory_ratings import (
    AggBucket,
    apply_lineage_uplift,
    build_job_output_index,
    build_ratings_index,
    lineage_edge_weight,
    load_lineage_parent_index,
    _norm_path_key,
    _prompt_profile_name,
    _shape_recipe_key,
)


class RecipeJoinTests(unittest.TestCase):
    def test_prompt_profile_from_bindings_path(self) -> None:
        job = {
            "family_slug": "FB9_GEX2",
            "bindings": {"prompt_profile": {"path": "/data/pools/prompts/ef85da9aa887.json"}},
        }
        self.assertEqual(_prompt_profile_name(job), "ef85da9aa887")
        self.assertEqual(_shape_recipe_key(job), "FB9_GEX2+ef85da9aa887")

    def test_prompt_profile_from_job_key(self) -> None:
        job = {
            "family_slug": "FB9_GEX2",
            "job_key": "hourly__prompt_profile-catalog-default__draft_1__source_video-X__000",
            "bindings": {},
        }
        self.assertEqual(_prompt_profile_name(job), "catalog-default__draft_1")
        self.assertEqual(_shape_recipe_key(job), "FB9_GEX2+catalog-default__draft_1")

    def test_prompt_profile_from_short_job_key(self) -> None:
        job = {
            "family_slug": "FB9_GEX2",
            "job_key": "hourly__pp-catalog-default__draft_1__src-X__000",
            "bindings": {},
        }
        self.assertEqual(_prompt_profile_name(job), "catalog-default__draft_1")
        self.assertEqual(_shape_recipe_key(job), "FB9_GEX2+catalog-default__draft_1")

    def test_backfill_recipe_fallback(self) -> None:
        job = {
            "family_slug": "FB9_GEX2",
            "job_key": "FB9_GEX2__backfill__FB9_GEX2_2026-04-03_00001",
            "bindings": {
                "prompt_profile": {
                    "positive": "embedded text",
                    "prompt_text_source": "embedded",
                    "recovered": True,
                }
            },
            "submit": {"prompt_source": "backfill"},
        }
        self.assertEqual(_prompt_profile_name(job), "backfill")
        self.assertEqual(_shape_recipe_key(job), "FB9_GEX2+backfill")

    def test_norm_path_key_og_short_forms(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            data_root = Path(td)
            keys = _norm_path_key(
                str(data_root / "output/output/og/2026-04-03/FB9_GEX2_2026-04-03_00001.mp4"),
                data_root,
            )
            self.assertIn("og/2026-04-03/FB9_GEX2_2026-04-03_00001", keys)
            self.assertIn("output/og/2026-04-03/FB9_GEX2_2026-04-03_00001", keys)
            self.assertIn("FB9_GEX2_2026-04-03_00001", keys)

    def test_job_deposit_joins_rated_output_recipe(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_root = root / "data"
            og = data_root / "output" / "og" / "2026-04-03"
            og.mkdir(parents=True)
            media = og / "FB9_GEX2_2026-04-03_00001.mp4"
            media.write_bytes(b"fake-mp4")
            # Minimal XMP with rating 5 (correlate parser needs Rating)
            xmp = og / "FB9_GEX2_2026-04-03_00001.XMP"
            xmp.write_text(
                '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
                '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
                ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
                '  <rdf:Description xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Rating="5"/>\n'
                " </rdf:RDF>\n"
                "</x:xmpmeta>\n"
                '<?xpacket end="w"?>\n',
                encoding="utf-8",
            )

            jobs = root / "jobs" / "FB9_GEX2"
            jobs.mkdir(parents=True)
            job = {
                "job_key": "FB9_GEX2__backfill__FB9_GEX2_2026-04-03_00001",
                "family_slug": "FB9_GEX2",
                "shape_id": "wan-v2v-source+prompt",
                "graph_hash": "abc123deadbeef",
                "bindings": {
                    "prompt_profile": {
                        "positive": "text",
                        "prompt_text_source": "embedded",
                        "recovered": True,
                    }
                },
                "submit": {
                    "prompt_source": "backfill",
                    "status": "completed",
                    "outputs": [str(data_root / "output/output/og/2026-04-03/FB9_GEX2_2026-04-03_00001.mp4")],
                },
            }
            (jobs / "backfill.job.json").write_text(json.dumps(job), encoding="utf-8")

            job_index = build_job_output_index(jobs, data_root)
            meta = job_index.get("FB9_GEX2_2026-04-03_00001") or job_index.get(
                "og/2026-04-03/FB9_GEX2_2026-04-03_00001"
            )
            self.assertIsNotNone(meta)
            assert meta is not None
            self.assertEqual(meta.get("shape_recipe"), "FB9_GEX2+backfill")
            self.assertEqual(meta.get("graph_hash"), "abc123deadbeef")

            out = root / "ratings_index.json"
            doc = build_ratings_index(
                og_root=data_root / "output" / "og",
                jobs_root=jobs,
                data_root=data_root,
                out_path=out,
                join_lineage=False,
            )
            self.assertIn("FB9_GEX2+backfill", doc.get("by_shape_recipe") or {})
            row = (doc.get("by_output_relpath") or {}).get("og/2026-04-03/FB9_GEX2_2026-04-03_00001")
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row.get("explicit"), 5)
            self.assertEqual(row.get("shape_recipe"), "FB9_GEX2+backfill")


class LineageUpliftTests(unittest.TestCase):
    def test_edge_weights(self) -> None:
        self.assertEqual(lineage_edge_weight("shape_factory_deposit"), 1.0)
        self.assertEqual(lineage_edge_weight("png_prompt_source_path"), 0.9)
        self.assertEqual(lineage_edge_weight("basename"), 0.5)
        self.assertEqual(lineage_edge_weight("mystery_persisted"), 0.85)

    def test_two_hop_weighted_uplift(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            edges_path = Path(td) / "discovery_lineage_edges.json"
            edges_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "edges": [
                            {
                                "child_group_id": "og:stem:fb9_gex_facial_2026-04-23_00010",
                                "parent_group_id": "og:stem:fb9_gex2_2026-04-03_00001",
                                "resolved_parent_relpath": "og/2026-04-03/FB9_GEX2_2026-04-03_00001.mp4",
                                "evidence": "png_prompt_source_path",
                            },
                            {
                                "child_group_id": "og:stem:fb9_gex2_2026-04-03_00001",
                                "parent_group_id": "og:stem:x-kneel-fb9-2026-04-02-163059_og_00001",
                                "resolved_parent_relpath": "og/2026-04-02/X-Kneel-FB9-2026-04-02-163059_OG_00001.mp4",
                                "evidence": "png_prompt_source_path",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            parent_index = load_lineage_parent_index(edges_path)
            self.assertIn("fb9_gex_facial_2026-04-23_00010", parent_index)

            by_source: dict = {}
            from collections import defaultdict

            by_source_basename: dict = defaultdict(AggBucket)
            contributors: dict = defaultdict(list)
            rated = [
                {
                    "rating": 5,
                    "output_discovery_key": "output/og/2026-04-23/FB9_GEX_FACIAL_2026-04-23_00010",
                    "output_short_key": "og/2026-04-23/FB9_GEX_FACIAL_2026-04-23_00010",
                    "source_basenames": [],
                }
            ]
            stats = apply_lineage_uplift(
                rated_outputs=rated,
                parent_index=parent_index,
                by_source_basename=by_source_basename,
                source_contributors=contributors,
                max_hops=2,
            )
            self.assertGreaterEqual(stats["lineage_credits"], 2)
            gex2 = "FB9_GEX2_2026-04-03_00001.mp4"
            kneel = "X-Kneel-FB9-2026-04-02-163059_OG_00001.mp4"
            self.assertIn(gex2, by_source_basename)
            self.assertIn(kneel, by_source_basename)
            # hop1 weight 0.9; hop2 weight 0.9 * 0.9 * 0.75 = 0.6075
            self.assertAlmostEqual(by_source_basename[gex2].weights[0], 0.9, places=4)
            self.assertAlmostEqual(by_source_basename[kneel].weights[0], 0.9 * 0.9 * 0.75, places=4)
            self.assertEqual(by_source_basename[gex2].to_inferred()["inferred"], 5.0)
            ev = (contributors[gex2][0].get("evidence") or {})
            self.assertEqual(ev.get("source"), "lineage")
            self.assertEqual(ev.get("hop"), 1)

    def test_skips_already_listed_direct_sources(self) -> None:
        parent_index = {
            "child_a": [
                {
                    "parent_stem": "parent_a",
                    "parent_basename": "Parent_A.mp4",
                    "weight": 0.9,
                    "evidence": "png_prompt_source_path",
                }
            ]
        }
        from collections import defaultdict

        by_source_basename: dict = defaultdict(AggBucket)
        contributors: dict = defaultdict(list)
        rated = [
            {
                "rating": 4,
                "output_discovery_key": "output/og/2026-01-01/Child_A",
                "output_short_key": "og/2026-01-01/Child_A",
                "source_basenames": ["Parent_A.mp4"],
            }
        ]
        stats = apply_lineage_uplift(
            rated_outputs=rated,
            parent_index=parent_index,
            by_source_basename=by_source_basename,
            source_contributors=contributors,
        )
        self.assertEqual(stats["lineage_credits"], 0)
        self.assertNotIn("Parent_A.mp4", by_source_basename)


if __name__ == "__main__":
    unittest.main()
