import json
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401  — injects workspace/scripts onto sys.path
from shape_factory_work_products import (
    _family_from_output_prefix,
    _filename_prefix_from_prompt,
    _relpath_under,
    _shape_view,
    attach_live_comfy_queue,
    construction_from_plan,
    decode_prompt_markup,
    list_extend_family_defaults,
    list_recent_work_products,
    list_shape_families,
    prefer_target_family,
)


class TestWorkProducts(unittest.TestCase):
    def test_prefer_target_family(self):
        self.assertEqual(prefer_target_family("FB9_GEX2", "FB9_GEX"), "FB9_GEX2")
        self.assertEqual(prefer_target_family("", "FB9_GEX"), "FB9_GEX")
        self.assertEqual(prefer_target_family(None, "FB9_GEX"), "FB9_GEX")
        self.assertEqual(prefer_target_family("  ", ""), "")

    def test_construction_from_plan_keeps_selection_fields(self):
        plan = {
            "ok": True,
            "pick_mode": "extend",
            "step": "derive",
            "derive_action": "extend",
            "appetite": "high",
            "appetite_facet": "both",
            "cursor": 12,
            "combo_key": "prompt_profile-abc__source_video-x",
            "picks": {"source_video": "/tmp/x.mp4"},
            "noise": "ignore-me",
        }
        c = construction_from_plan(plan)
        self.assertEqual(c["step"], "derive")
        self.assertEqual(c["derive_action"], "extend")
        self.assertEqual(c["appetite_facet"], "both")
        self.assertEqual(c["cursor"], 12)
        self.assertNotIn("picks", c)
        self.assertNotIn("noise", c)

    def test_decode_prompt_markup(self):
        rows = decode_prompt_markup(
            "plain line\n"
            "((emphasized))\n"
            "(weighted clause:1.8)\n"
            "mixed ((inline)) text\n"
        )
        self.assertEqual(rows[0]["text"], "plain line")
        self.assertEqual(rows[0]["weight"], 1.0)
        self.assertEqual(rows[1]["text"], "emphasized")
        self.assertAlmostEqual(rows[1]["weight"], 1.21, places=2)
        self.assertEqual(rows[2]["text"], "weighted clause")
        self.assertEqual(rows[2]["weight"], 1.8)
        self.assertIn("inline", rows[3]["text"])
        self.assertNotIn("((", rows[3]["text"])
        self.assertGreater(rows[3]["weight"], 1.0)

    def test_list_recent_work_products_reads_hourly_jobs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            out = root / "output"
            jobs = data / "shape_factory" / "jobs" / "FB9_GEX2"
            jobs.mkdir(parents=True)
            media_dir = out / "og" / "2026-07-13" / "hourly"
            media_dir.mkdir(parents=True)
            video = media_dir / "hourly__demo_00001.mp4"
            video.write_bytes(b"fake")
            job = {
                "schema_version": "comfyui-runpod.shape-job.v0",
                "created_at": "2026-07-13T12:00:00+00:00",
                "family_slug": "FB9_GEX2",
                "job_key": "hourly__demo",
                "pick_mode": "derive",
                "rating_kind": "appetite",
                "shape_id": "wan-v2v-source+prompt",
                "template_path": "/tmp/template.json",
                "output_prefix": "og/2026-07-13/hourly/hourly__demo",
                "bindings": {
                    "source_video": {
                        "path": str(out / "og" / "2026-04-01" / "seed.mp4"),
                        "binding_type": "vhs_load_video_path",
                        "role": "B",
                    },
                    "prompt_profile": {
                        "path": str(data / "missing-prompt.json"),
                        "binding_type": "prompt_bundle",
                        "role": "C",
                    },
                },
                "construction": {
                    "step": "derive",
                    "appetite_facet": "source",
                    "cursor": 3,
                    "combo_key": "prompt_profile-x__source_video-seed",
                },
                "submit": {
                    "status": "complete",
                    "prompt_id": "pid-1",
                    "outputs": [str(video)],
                },
            }
            (jobs / "hourly__demo.job.json").write_text(json.dumps(job) + "\n", encoding="utf-8")

            payload = list_recent_work_products(data_root=data, output_root=out, limit=10, hourly_only=True)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["count"], 1)
            item = payload["items"][0]
            self.assertEqual(item["job_key"], "hourly__demo")
            self.assertEqual(item["pick_mode"], "derive")
            self.assertEqual(item["step"], "derive")
            self.assertTrue(str(item.get("output_url") or "").startswith("/files/"))
            labels = [r["label"] for r in item["details"]]
            self.assertIn("Pick mode", labels)
            self.assertIn("Appetite facet", labels)
            self.assertIn("Cursor", labels)
            binding = next(r for r in item["details"] if r["label"].startswith("Binding · prompt_profile"))
            self.assertIn("role=C", binding["value"])
            self.assertIn("prompt", binding["value"].lower())

    def test_list_recent_sorts_by_created_at_not_mtime(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            out = root / "output"
            jobs = data / "shape_factory" / "jobs" / "DEMO"
            jobs.mkdir(parents=True)
            out.mkdir(parents=True)
            old = {
                "created_at": "2025-09-13T00:00:00+00:00",
                "family_slug": "DEMO",
                "job_key": "hourly__old",
                "submit": {"status": "complete"},
            }
            new = {
                "created_at": "2026-07-14T12:00:00+00:00",
                "family_slug": "DEMO",
                "job_key": "hourly__new",
                "submit": {"status": "complete"},
            }
            old_path = jobs / "hourly__old.job.json"
            new_path = jobs / "hourly__new.job.json"
            old_path.write_text(json.dumps(old) + "\n", encoding="utf-8")
            new_path.write_text(json.dumps(new) + "\n", encoding="utf-8")
            # Inflate mtime on the old job so a naive filesystem sort would pick it first.
            import os
            import time

            os.utime(old_path, (time.time() + 1000, time.time() + 1000))
            payload = list_recent_work_products(data_root=data, output_root=out, limit=10, hourly_only=True)
            self.assertEqual([it["job_key"] for it in payload["items"]], ["hourly__new", "hourly__old"])

    def test_shape_view_parses_contract(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            shape_path = root / "demo.shape.yaml"
            shape_path.write_text(
                "\n".join(
                    [
                        "shape_id: demo-shape",
                        "family_slug: DEMO",
                        "graph_hash: abc123",
                        "template: /tmp/template.json",
                        "requires:",
                        "  - slot: source_video",
                        "    role: B",
                        "    media: video",
                        "    binding:",
                        "      type: vhs_load_video_path",
                        "      node_id: 1",
                        "produces:",
                        "  - slot: final_video",
                        "    role: X",
                        "    media: video",
                        "    binding:",
                        "      node_type: VHS_VideoCombine",
                        "      node_id: 2",
                        "deposits:",
                        "  final_video:",
                        "    to_pool: pool:DEMO_X",
                        "output_prefix_root: og/%date%/demo",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            view = _shape_view(str(shape_path))
            self.assertIsNotNone(view)
            assert view is not None
            self.assertEqual(view["shape_id"], "demo-shape")
            self.assertEqual(view["requires"][0]["slot"], "source_video")
            self.assertEqual(view["requires"][0]["role_gloss"], "video input")
            self.assertEqual(view["produces"][0]["role"], "X")
            self.assertEqual(view["deposits"][0]["to_pool"], "pool:DEMO_X")
            self.assertIn("shape_id: demo-shape", view["text"])

    def test_list_recent_work_products_includes_shape_peek(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            out = root / "output"
            jobs = data / "shape_factory" / "jobs" / "DEMO"
            jobs.mkdir(parents=True)
            shape_path = data / "shapes" / "demo.shape.yaml"
            shape_path.parent.mkdir(parents=True)
            shape_path.write_text(
                "shape_id: demo-shape\nfamily_slug: DEMO\nrequires: []\nproduces: []\ndeposits: {}\n",
                encoding="utf-8",
            )
            job = {
                "created_at": "2026-07-13T12:00:00+00:00",
                "family_slug": "DEMO",
                "job_key": "hourly__shape",
                "shape_id": "demo-shape",
                "shape_path": str(shape_path),
                "submit": {"status": "queued"},
            }
            (jobs / "hourly__shape.job.json").write_text(json.dumps(job) + "\n", encoding="utf-8")
            payload = list_recent_work_products(data_root=data, output_root=out, limit=5, hourly_only=True)
            item = payload["items"][0]
            self.assertEqual(item["shape_profile"]["shape_id"], "demo-shape")
            shape_row = next(r for r in item["details"] if r["label"] == "Shape")
            self.assertEqual(shape_row["peek"], "shape")

    def test_list_recent_work_products_includes_work_items_open(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            out = root / "output"
            jobs = data / "shape_factory" / "jobs" / "DEMO"
            jobs.mkdir(parents=True)
            media_dir = out / "og" / "2026-07-13"
            media_dir.mkdir(parents=True)
            video = media_dir / "hourly__wi.mp4"
            video.write_bytes(b"fake")
            status = out / "_status"
            status.mkdir(parents=True)
            from shape_factory_work_items import create_work_item

            create_work_item(
                source_relpath="og/2026-07-13/hourly__wi.mp4",
                pool="extend",
                disposition_entry="advance",
                disposition_step="advance.extend",
                work_items_index_path=status / "work_items_index.json",
                priority="front",
            )
            job = {
                "created_at": "2026-07-13T12:00:00+00:00",
                "family_slug": "DEMO",
                "job_key": "hourly__wi",
                "output_prefix": "og/2026-07-13/hourly__wi",
                "submit": {"status": "complete", "outputs": [str(video)]},
            }
            (jobs / "hourly__wi.job.json").write_text(json.dumps(job) + "\n", encoding="utf-8")
            payload = list_recent_work_products(data_root=data, output_root=out, limit=5, hourly_only=True)
            item = payload["items"][0]
            self.assertEqual(item.get("work_items_open_count"), 1)
            self.assertEqual(item["work_items_open"][0]["pool"], "extend")
            self.assertEqual(item["work_items_open"][0]["priority"], "front")

    def test_list_shape_families_and_work_products_payload(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            shapes = data / "shapes"
            shapes.mkdir(parents=True)
            (shapes / "Alpha.shape.yaml").write_text(
                "shape_id: alpha-shape\nfamily_slug: Alpha\nrequires: []\nproduces: []\n",
                encoding="utf-8",
            )
            (shapes / "Beta.shape.yaml").write_text(
                "shape_id: beta-shape\nfamily_slug: Beta\nrequires: []\nproduces: []\n",
                encoding="utf-8",
            )
            fams = list_shape_families(data)
            slugs = {f["slug"] for f in fams}
            self.assertEqual(slugs, {"Alpha", "Beta"})

            out = root / "output"
            jobs = data / "shape_factory" / "jobs" / "Alpha"
            jobs.mkdir(parents=True)
            (jobs / "hourly__a.job.json").write_text(
                json.dumps(
                    {
                        "created_at": "2026-07-13T12:00:00+00:00",
                        "family_slug": "Alpha",
                        "job_key": "hourly__a",
                        "submit": {"status": "queued"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            payload = list_recent_work_products(data_root=data, output_root=out, limit=5, hourly_only=True)
            self.assertIn("families", payload)
            self.assertEqual({f["slug"] for f in payload["families"]}, {"Alpha", "Beta"})
            self.assertEqual(payload.get("extend_family_defaults"), {})

    def test_list_extend_family_defaults_from_pipeline(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            pipes = data / "pipelines"
            pipes.mkdir(parents=True)
            (pipes / "a-to-b.pipeline.yaml").write_text(
                "\n".join(
                    [
                        "pipeline_id: a-to-b",
                        "steps:",
                        "  - id: a",
                        "    shape: /x/shapes/Alpha.shape.yaml",
                        "  - id: b",
                        "    shape: /x/shapes/Beta.shape.yaml",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            self.assertEqual(list_extend_family_defaults(data), {"Alpha": "Beta"})

    def test_attach_live_comfy_queue_synthesizes_missing_job(self):
        payload = {
            "ok": True,
            "limit": 5,
            "families": [{"slug": "FB9_GEX_FACIAL"}],
            "items": [
                {
                    "job_key": "hourly__done",
                    "prompt_id": "aaaa",
                    "status": "complete",
                    "output_url": "/files/x.mp4",
                }
            ],
        }
        prompt = {
            "80": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "filename_prefix": "output/og/2026-07-13/FB9_GEX2_FACIAL_2026-07-13",
                    "save_output": True,
                },
            }
        }
        out = attach_live_comfy_queue(
            payload,
            queue_running=[[0, "0bf06bd6-71c5-4dcc-a22b-ddd4cec2daaa", prompt]],
            queue_pending=[],
        )
        self.assertEqual(out.get("live_comfy_count"), 1)
        self.assertEqual(out["items"][0]["prompt_id"], "0bf06bd6-71c5-4dcc-a22b-ddd4cec2daaa")
        self.assertEqual(out["items"][0]["status"], "running")
        self.assertFalse(out["items"][0].get("output_url"))
        self.assertEqual(out["items"][0]["family_slug"], "FB9_GEX_FACIAL")
        self.assertEqual(out["items"][1]["job_key"], "hourly__done")

    def test_demote_stale_inflight_items(self):
        from shape_factory_work_products import demote_stale_inflight_items

        payload = {
            "ok": True,
            "items": [
                {"job_key": "ghost", "prompt_id": "gone", "status": "running"},
                {"job_key": "live", "prompt_id": "alive", "status": "queued", "live_from_comfy": True},
                {"job_key": "done", "prompt_id": "x", "status": "complete"},
            ],
        }
        out = demote_stale_inflight_items(
            payload,
            queue_running=[[0, "alive", {}]],
            queue_pending=[],
        )
        self.assertEqual(out["items"][0]["status"], "interrupted")
        self.assertEqual(out["items"][1]["status"], "queued")
        self.assertEqual(out["items"][2]["status"], "complete")
        self.assertEqual(out.get("comfy_demoted_stale"), 1)

    def test_reconcile_inflight_persists_queued_vs_running(self):
        from shape_factory_work_products import reconcile_inflight_jobs_with_comfy

        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            jobs = data / "shape_factory" / "jobs" / "Fam"
            jobs.mkdir(parents=True)
            path = jobs / "job_a.job.json"
            path.write_text(
                json.dumps(
                    {
                        "job_key": "job_a",
                        "submit": {"prompt_id": "pend-1", "status": "running"},
                    }
                ),
                encoding="utf-8",
            )
            summary = reconcile_inflight_jobs_with_comfy(
                data_root=data,
                comfy_server="http://example.invalid",
                queue_running=[[0, "run-1", {}]],
                queue_pending=[[1, "pend-1", {}]],
                persist=True,
            )
            self.assertTrue(summary.get("ok"))
            self.assertEqual(summary.get("updated"), 1)
            doc = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(doc["submit"]["status"], "queued")

    def test_relpath_under_remaps_host_output(self):
        root = Path("/workspace/output")
        rel = _relpath_under(
            root,
            "/home/yuji/comfyui-runpod-data/output/og/2026-04-13/x.mp4",
        )
        self.assertEqual(rel, "og/2026-04-13/x.mp4")

    def test_attach_live_enriches_found_job_bindings(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            out = data / "output"
            jobs = data / "shape_factory" / "jobs" / "FB9_GEX2"
            jobs.mkdir(parents=True)
            out.mkdir(parents=True)
            src_rel = "og/2026-04-13/src.mp4"
            (out / "og/2026-04-13").mkdir(parents=True)
            (out / src_rel).write_bytes(b"fake")
            prompt_path = data / "pools" / "FB9_GEX2" / "prompts" / "demo.json"
            prompt_path.parent.mkdir(parents=True)
            prompt_path.write_text(
                json.dumps({"label": "demo-prompt", "positive": "a cat", "negative": "dog"}),
                encoding="utf-8",
            )
            job = {
                "job_key": "job_live_bind",
                "family_slug": "FB9_GEX2",
                "created_at": "2026-07-13T00:00:00+00:00",
                "pick_mode": "derive",
                "shape_id": "wan-v2v-source+prompt",
                "shape_path": str(data / "shapes" / "missing.shape.yaml"),
                "template_path": "/tmp/template.json",
                "generated_workflow_path": "/tmp/generated.json",
                "graph_hash": "abc123hash",
                "bindings": {
                    "source_video": {
                        "path": f"/home/yuji/comfyui-runpod-data/output/{src_rel}",
                    },
                    "prompt_profile": {
                        "path": str(prompt_path),
                        "binding_type": "prompt_bundle",
                        "role": "C",
                    },
                },
                "construction": {
                    "step": "derive",
                    "cursor": 9,
                    "selection_weight": 1.5,
                    "recipe_count": 4,
                    "seed_count": 2,
                    "derive_attempts": 1,
                    "fast_track": True,
                    "used_recent_fallback": False,
                },
                "submit": {"prompt_id": "pid-bind-1", "status": "submitted"},
            }
            (jobs / "job_live_bind.job.json").write_text(json.dumps(job), encoding="utf-8")
            payload = {"ok": True, "limit": 5, "families": [{"slug": "FB9_GEX2"}], "items": []}
            attached = attach_live_comfy_queue(
                payload,
                queue_running=[],
                queue_pending=[[1, "pid-bind-1", {}]],
                data_root=data,
                output_root=out,
            )
            row = attached["items"][0]
            self.assertEqual(row["status"], "queued")
            src = (row.get("bindings") or {}).get("source_video") or {}
            self.assertEqual(src.get("relpath"), src_rel)
            self.assertTrue(str(src.get("thumb_url") or "").endswith(".png") or "/files/" in str(src.get("thumb_url") or ""))
            self.assertTrue(src.get("thumb_url") or src.get("url"))
            self.assertEqual(row.get("graph_hash"), "abc123hash")
            self.assertEqual(row.get("template_basename"), "template.json")
            labels = {r["label"]: r["value"] for r in (row.get("details") or [])}
            self.assertIn("Cursor", labels)
            self.assertEqual(labels["Cursor"], "9")
            self.assertIn("Selection weight", labels)
            self.assertIn("Graph hash", labels)
            self.assertTrue(row.get("prompt_profile"))
            self.assertEqual((row.get("prompt_profile") or {}).get("label"), "demo-prompt")

    def test_filename_prefix_and_family_guess(self):
        prompt = {
            "79": {"class_type": "VHS_VideoCombine", "inputs": {"filename_prefix": "WAN", "save_output": False}},
            "80": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "filename_prefix": "output/og/2026-07-13/FB9_GEX2_FACIAL_shape/x",
                    "save_output": True,
                },
            },
        }
        prefix = _filename_prefix_from_prompt(prompt)
        self.assertIn("FACIAL", prefix)
        self.assertEqual(_family_from_output_prefix(prefix, ["FB9_GEX2", "FB9_GEX_FACIAL"]), "FB9_GEX_FACIAL")


if __name__ == "__main__":
    unittest.main()
