#!/usr/bin/env python3
"""Tests for shape_factory_map builder."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "workspace" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from shape_factory_map import (  # noqa: E402
    _build_edges,
    _combo_key_from_slot_paths,
    _job_summary,
    _member_preview,
    _path_media_row,
    _predict_next_hourly_sample,
    _projected_pairs_for_family,
    _relpath_guess_from_abs,
    build_shape_factory_map,
    classify_job_kind,
    job_key_slot_token,
    normalize_combo_key,
    resolve_output_relpath,
    resolve_shape_factory_data_root,
    select_job_summaries_for_map,
)


class ShapeFactoryMapTests(unittest.TestCase):
    def test_resolve_data_root_default(self) -> None:
        p = resolve_shape_factory_data_root(repo_root=ROOT)
        self.assertEqual(p, (ROOT / ".data").resolve())

    def test_classify_job_kind(self) -> None:
        self.assertEqual(classify_job_kind({"job_key": "hourly__pp-x__src-y"}), "hourly")
        self.assertEqual(classify_job_kind({"job_key": "Fam__pp-x__still-y__ui1787"}), "ui")
        self.assertEqual(classify_job_kind({"job_key": "Fam__x", "adhoc_overrides": {"parameters": {"frames": 8}}}), "ui")
        self.assertEqual(classify_job_kind({"job_key": "Fam__x", "pipeline_id": "fb9-gex2-to-facial"}), "pipeline")
        self.assertEqual(classify_job_kind({"job_key": "Fam__x", "pick_mode": "replay"}), "replay")
        self.assertEqual(classify_job_kind({"job_key": "Fam__x"}), "factory")

    def test_select_jobs_prefers_per_family_and_deposits(self) -> None:
        # Dominating family A would win a global top-N; quieter B must still appear
        # when its deposit preview references a job.
        summaries = []
        for i in range(30):
            summaries.append(
                {
                    "job_key": f"A__job-{i}",
                    "family_slug": "A",
                    "job_path": f"/tmp/A-{i}.job.json",
                }
            )
        summaries.append(
            {
                "job_key": "B__deposit-ref",
                "family_slug": "B",
                "job_path": "/tmp/B-dep.job.json",
            }
        )
        summaries.append(
            {
                "job_key": "B__other",
                "family_slug": "B",
                "job_path": "/tmp/B-other.job.json",
            }
        )
        families = [
            {
                "family_slug": "B",
                "deposit_pools": [
                    {
                        "pool_id": "B_X",
                        "members_preview": [{"job_key": "B__deposit-ref", "basename": "out.mp4"}],
                    }
                ],
            }
        ]
        # Tiny per-family + tiny global cap still keeps deposit-ref
        selected = select_job_summaries_for_map(
            summaries,
            families,
            jobs_per_family=2,
            jobs_limit=4,
        )
        keys = {r["job_key"] for r in selected}
        self.assertIn("B__deposit-ref", keys)
        self.assertTrue(any(k.startswith("A__") for k in keys))

    def test_combo_key_uses_short_slot_labels(self) -> None:
        key = _combo_key_from_slot_paths(
            {"prompt_profile": "/tmp/prompts/aaa.json", "source_video": "/tmp/src/clip.mp4"}
        )
        self.assertEqual(key, "pp-aaa__src-clip")
        self.assertEqual(job_key_slot_token("prompt_profile"), "pp")
        self.assertEqual(
            normalize_combo_key("prompt_profile-aaa__source_video-clip"),
            "pp-aaa__src-clip",
        )

    def test_build_edges_pipeline_link(self) -> None:
        families = [
            {
                "family_slug": "FB9_GEX2",
                "input_pools": [{"slot": "source_video", "name": "source_video"}],
                "deposit_pools": [{"pool_id": "FB9_GEX2_X_og", "slot": "final_video"}],
            },
            {
                "family_slug": "FB9_GEX_FACIAL",
                "input_pools": [
                    {
                        "slot": "source_video",
                        "name": "source_video",
                        "feeds_from": [{"pool_id": "FB9_GEX2_X_og"}],
                    }
                ],
                "deposit_pools": [{"pool_id": "FB9_GEX_FACIAL_X_og", "slot": "final_video"}],
            },
        ]
        pipelines = [
            {
                "pipeline_id": "fb9-gex2-to-facial",
                "steps": [
                    {
                        "id": "gex2-base",
                        "shape": "/x/FB9_GEX2.shape.yaml",
                        "deposits_to": "FB9_GEX2_X_og",
                    },
                    {
                        "id": "facial",
                        "shape": "/x/FB9_GEX_FACIAL.shape.yaml",
                        "binds_from_pool": "FB9_GEX2_X_og",
                        "binds_pick": "last",
                        "deposits_to": "FB9_GEX_FACIAL_X_og",
                    },
                ],
            }
        ]
        edges = _build_edges(families, pipelines)
        kinds = {e["kind"] for e in edges}
        self.assertIn("deposit", kinds)
        self.assertIn("pipeline_binds", kinds)
        self.assertIn("pipeline_step_link", kinds)

    def test_predict_next_hourly_sample(self) -> None:
        chain = {
            "samples": [
                {"id": "a", "pick_index": 0, "blocked": False},
                {"id": "b", "pick_index": 1, "blocked": False},
            ]
        }
        data_root = ROOT / ".data"
        nxt = _predict_next_hourly_sample({"sample_cursor": 3, "phase": "idle"}, chain, data_root=data_root)
        self.assertIsNotNone(nxt)
        assert nxt is not None
        if nxt.get("pick_mode") == "replay":
            self.assertIn("recipe_count", nxt)
            self.assertIn("source", nxt)
        elif nxt.get("pick_mode") == "product":
            self.assertIn("pending_combos", nxt)
            self.assertIn("combo_key", nxt)
        else:
            self.assertEqual(nxt["sample_id"], "b")
            self.assertEqual(nxt["cursor"], 3)

    def test_relpath_guess_from_host_index_path(self) -> None:
        host = (
            "/home/yuji/comfyui-runpod-data/output/output/output/og/"
            "2026-07-06/FB9_GEX2_shape/foo.mp4"
        )
        self.assertEqual(
            _relpath_guess_from_abs(host),
            "output/output/og/2026-07-06/FB9_GEX2_shape/foo.mp4",
        )

    def test_relpath_guess_from_host_input_path(self) -> None:
        # Host job files store absolute input paths under a bind dir that is not
        # under the container workspace_root; the guess must still map to input/.
        for host in (
            "/home/yuji/comfyui-runpod-data/input/001302_LF_00001_.png",
            "/home/yuji/src/comfyui-runpod/workspace/input/004359_OG_00001.png",
        ):
            self.assertEqual(
                _relpath_guess_from_abs(host),
                f"input/{host.rsplit('/input/', 1)[-1]}",
            )

    def test_member_preview_thumb_from_inferred_png(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out_root = root / "output"
            og = out_root / "output" / "output" / "og" / "2026-07-06" / "demo"
            og.mkdir(parents=True)
            mp4 = og / "clip.mp4"
            png = og / "clip.png"
            mp4.write_bytes(b"mp4")
            png.write_bytes(b"png")
            host_mp4 = (
                "/home/yuji/comfyui-runpod-data/output/output/output/og/"
                "2026-07-06/demo/clip.mp4"
            )
            row = _member_preview(
                {"path": host_mp4, "kind": "video", "source": "deposit"},
                output_root=out_root,
                file_exists=lambda rel: (out_root / rel).is_file(),
            )
            self.assertEqual(row["relpath"], "output/output/og/2026-07-06/demo/clip.mp4")
            self.assertTrue(row["url"].startswith("/files/"))
            self.assertEqual(row["thumb_relpath"], "output/output/og/2026-07-06/demo/clip.png")
            self.assertTrue(row["thumb_url"].startswith("/files/"))

    def test_resolve_output_relpath_under_output_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out_root = Path(td) / "output"
            rel_dir = out_root / "output" / "og" / "day"
            rel_dir.mkdir(parents=True)
            f = rel_dir / "a.png"
            f.write_bytes(b"x")
            rel = resolve_output_relpath(str(f), out_root)
            self.assertEqual(rel, "output/og/day/a.png")

    def test_resolve_output_relpath_workspace_input(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            inp = ws / "input"
            inp.mkdir(parents=True)
            still = inp / "001302_LF_00001_.png"
            still.write_bytes(b"png")
            rel = resolve_output_relpath(str(still), ws / "output", workspace_root=ws)
            self.assertEqual(rel, "input/001302_LF_00001_.png")

    def test_job_binding_source_still_media(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            out_root = ws / "output"
            inp = ws / "input"
            inp.mkdir(parents=True)
            still = inp / "frame.png"
            still.write_bytes(b"png")
            job = {
                "job_key": "kneel",
                "family_slug": "X-KNEEL-FB9",
                "bindings": {
                    "source_still": {
                        "path": str(still),
                        "role": "A",
                        "binding_type": "load_image",
                    }
                },
            }
            row = _job_summary(
                job,
                output_root=out_root,
                workspace_root=ws,
                file_exists=lambda rel: (ws / rel).is_file(),
            )
            binding = row["bindings"]["source_still"]
            self.assertEqual(binding["relpath"], "input/frame.png")
            self.assertTrue(binding["url"].startswith("/files/"))

    def test_job_binding_source_video_media(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out_root = Path(td) / "output"
            og = out_root / "output" / "og" / "2026-04-03"
            og.mkdir(parents=True)
            mp4 = og / "clip.mp4"
            png = og / "clip.png"
            mp4.write_bytes(b"mp4")
            png.write_bytes(b"png")
            host_mp4 = str(mp4)
            job = {
                "job_key": "demo",
                "family_slug": "DEMO",
                "bindings": {
                    "source_video": {
                        "path": host_mp4,
                        "role": "B",
                        "binding_type": "vhs_load_video_path",
                    }
                },
            }
            row = _job_summary(
                job,
                output_root=out_root,
                file_exists=lambda rel: (out_root / rel).is_file(),
            )
            binding = row["bindings"]["source_video"]
            self.assertEqual(binding["relpath"], "output/og/2026-04-03/clip.mp4")
            self.assertTrue(binding["thumb_url"].startswith("/files/"))

    def test_projected_pairs_exclude_existing_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / ".data"
            pools_dir = data / "pools" / "DEMO"
            shapes = data / "shapes"
            pools_dir.mkdir(parents=True)
            shapes.mkdir(parents=True)
            prompts = pools_dir / "prompts"
            prompts.mkdir()
            (prompts / "a.json").write_text("{}", encoding="utf-8")
            (prompts / "b.json").write_text("{}", encoding="utf-8")
            out_root = root / "output"
            og = out_root / "output" / "og"
            og.mkdir(parents=True)
            v1 = og / "clip1.mp4"
            v2 = og / "clip2.mp4"
            v1.write_bytes(b"v1")
            v2.write_bytes(b"v2")
            (shapes / "DEMO.shape.yaml").write_text(
                "family_slug: DEMO\nrequires:\n"
                "  - slot: source_video\n  - slot: prompt_profile\n",
                encoding="utf-8",
            )
            pools_yaml = pools_dir / "pools.yaml"
            pools_yaml.write_text(
                "pools:\n"
                "  source_video:\n"
                "    slot: source_video\n"
                f"    members:\n      - glob: {v1}\n      - glob: {v2}\n"
                "  prompt_profile:\n"
                "    slot: prompt_profile\n"
                f"    members:\n      - dir: {prompts}\n        ext: [\".json\"]\n",
                encoding="utf-8",
            )
            existing_key = _combo_key_from_slot_paths(
                {"prompt_profile": str(prompts / "a.json"), "source_video": str(v1)}
            )
            jobs = [
                {
                    "family_slug": "DEMO",
                    "job_key": "demo__existing",
                    "bindings": {
                        "source_video": {"path": str(v1)},
                        "prompt_profile": {"path": str(prompts / "a.json")},
                    },
                }
            ]
            projected = _projected_pairs_for_family(
                pools_yaml,
                {"requires": [{"slot": "source_video"}, {"slot": "prompt_profile"}]},
                jobs,
                output_root=out_root,
                data_root=data,
                file_exists=lambda rel: (out_root / rel).is_file(),
                limit=10,
            )
            keys = {p["combo_key"] for p in projected}
            self.assertNotIn(existing_key, keys)
            self.assertTrue(any(p["phase"] == "future" for p in projected))
            self.assertGreaterEqual(len(projected), 2)

    def test_build_from_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / ".data"
            pools = data / "pools" / "DEMO"
            jobs = data / "shape_factory" / "jobs" / "DEMO"
            shapes = data / "shapes"
            pipelines = data / "pipelines"
            chains = data / "chains"
            for d in (pools, jobs, shapes, pipelines, chains):
                d.mkdir(parents=True)

            (shapes / "DEMO.shape.yaml").write_text(
                "family_slug: DEMO\nshape_id: demo\ngraph_hash: abc\n"
                "requires:\n  - slot: source_video\n    role: B\n"
                "deposits:\n  final_video:\n    to_pool: pool:DEMO_X_og\n",
                encoding="utf-8",
            )
            (pools / "pools.yaml").write_text(
                "pools:\n  source_video:\n    slot: source_video\n    members:\n      - glob: /tmp/*.mp4\n",
                encoding="utf-8",
            )
            (pools / "index.json").write_text(
                json.dumps(
                    {
                        "pools": {
                            "DEMO_X_og": {
                                "pool_id": "DEMO_X_og",
                                "slot": "final_video",
                                "members": [
                                    {
                                        "path": "/tmp/out/demo.mp4",
                                        "source": "seed",
                                        "kind": "video",
                                    }
                                ],
                            }
                        },
                        "shape_path": str(shapes / "DEMO.shape.yaml"),
                    }
                ),
                encoding="utf-8",
            )
            (jobs / "demo.job.json").write_text(
                json.dumps(
                    {
                        "job_key": "demo",
                        "family_slug": "DEMO",
                        "bindings": {},
                        "deposits": {"final_video": {"to_pool": "pool:DEMO_X_og"}},
                    }
                ),
                encoding="utf-8",
            )
            (chains / "best-examples.chain.yaml").write_text(
                "samples:\n  - id: s1\n    pick_index: 0\n",
                encoding="utf-8",
            )

            out_root = root / "output"
            out_root.mkdir()
            payload = build_shape_factory_map(
                data_root=data,
                output_root=out_root,
                skip_queue=True,
                members_limit=5,
                jobs_limit=5,
            )
            self.assertTrue(payload["ok"])
            self.assertEqual(len(payload["families"]), 1)
            self.assertEqual(payload["families"][0]["family_slug"], "DEMO")
            self.assertEqual(payload["jobs"]["total"], 1)
            self.assertTrue(payload["hourly"]["next_sample"])

            slim = build_shape_factory_map(
                data_root=data,
                output_root=out_root,
                skip_queue=True,
                members_limit=0,
                jobs_limit=5,
                jobs_per_family=2,
                projected_pairs_limit=0,
            )
            self.assertTrue(slim["ok"])
            fam = slim["families"][0]
            for pool in fam.get("deposit_pools") or []:
                self.assertEqual(pool.get("members_preview"), [])
                self.assertIsNone(pool.get("latest_member"))
            for pool in fam.get("input_pools") or []:
                self.assertEqual(pool.get("members_preview"), [])
            self.assertEqual(fam.get("projected_pairs"), [])


class ShapeFactoryMapLiveTests(unittest.TestCase):
    @unittest.skipUnless((ROOT / ".data" / "pools").is_dir(), "live .data not present")
    def test_live_repo_map(self) -> None:
        data = resolve_shape_factory_data_root(repo_root=ROOT)
        out_root = Path("/home/yuji/comfyui-runpod-data/output")
        if not out_root.is_dir():
            out_root = ROOT / "workspace" / "output"
        payload = build_shape_factory_map(
            data_root=data,
            output_root=out_root,
            skip_queue=True,
            members_limit=3,
            jobs_limit=10,
        )
        self.assertTrue(payload["ok"])
        slugs = {f["family_slug"] for f in payload["families"]}
        self.assertIn("FB9_GEX2", slugs)
        self.assertTrue(payload["edges"])


if __name__ == "__main__":
    unittest.main()
