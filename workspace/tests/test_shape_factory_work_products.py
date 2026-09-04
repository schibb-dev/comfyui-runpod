import json
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401  — injects workspace/scripts onto sys.path
from shape_factory_work_products import (
    _family_from_output_prefix,
    _filename_prefix_from_prompt,
    _keeper_output_rel,
    _relpath_under,
    _shape_view,
    attach_comfy_history_failures,
    attach_live_comfy_queue,
    construction_from_plan,
    decode_prompt_markup,
    get_work_product,
    is_extend_family_option,
    job_is_hourly_product,
    list_extend_family_defaults,
    list_family_prompt_profiles,
    list_recent_work_products,
    list_shape_families,
    list_submit_family_sets,
    prefer_target_family,
)


class TestWorkProducts(unittest.TestCase):
    def test_prefer_target_family(self):
        self.assertEqual(prefer_target_family("FB9_GEX2", "FB9_GEX"), "FB9_GEX2")
        self.assertEqual(prefer_target_family("", "FB9_GEX"), "FB9_GEX")
        self.assertEqual(prefer_target_family(None, "FB9_GEX"), "FB9_GEX")
        self.assertEqual(prefer_target_family("  ", ""), "")

    def test_job_is_hourly_product_uses_prefix_not_source_name(self):
        self.assertTrue(job_is_hourly_product({"job_key": "hourly__prompt_profile-abc__source_video-x"}))
        self.assertTrue(
            job_is_hourly_product(
                {"job_key": "other"},
                Path("/tmp/hourly__prompt_profile-abc.job.json"),
            )
        )
        # UI derivative of an hourly video — name embeds hourly but is not an hourly run.
        self.assertFalse(
            job_is_hourly_product(
                {
                    "job_key": "FB9_GEX__prompt_profile-abc__source_video-hourly__prompt_profile-abc__x",
                    "bindings": {
                        "source_video": {
                            "path": "/out/og/2026-07-20/hourly/hourly__prompt_profile-abc.mp4",
                        }
                    },
                }
            )
        )

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
                    "flow_events": [
                        {
                            "at": "2026-07-13T12:01:00+00:00",
                            "action": "finish_edit",
                            "actor": "operator",
                            "source_surface": "submit_edit",
                            "ok": True,
                        }
                    ],
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
            self.assertTrue(item.get("is_hourly"))
            self.assertTrue(str(item.get("output_url") or "").startswith("/files/"))
            labels = [r["label"] for r in item["details"]]
            self.assertIn("Pick mode", labels)
            self.assertIn("Appetite facet", labels)
            self.assertIn("Cursor", labels)
            binding = next(r for r in item["details"] if r["label"].startswith("Binding · prompt_profile"))
            self.assertIn("role=C", binding["value"])
            self.assertIn("prompt", binding["value"].lower())
            self.assertEqual(item.get("flow_phase"), "terminal")
            self.assertEqual(len(item.get("flow_events") or []), 1)

    def test_keeper_output_prefers_final_not_preview(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "output"
            hourly = out / "og" / "2026-08-16" / "hourly"
            hourly.mkdir(parents=True)
            preview = hourly / "job_PREVIEW_00001.mp4"
            final = hourly / "job_FINAL_00001.mp4"
            preview.write_bytes(b"p")
            final.write_bytes(b"f")
            rel = _keeper_output_rel([str(preview), str(final)], output_root=out)
            self.assertEqual(rel, "og/2026-08-16/hourly/job_FINAL_00001.mp4")

            from shape_factory_work_products import _work_product_item_from_job

            jobs = Path(td) / "data" / "shape_factory" / "jobs" / "BounceDanceA"
            jobs.mkdir(parents=True)
            job = {
                "family_slug": "BounceDanceA",
                "job_key": "job",
                "output_prefix": "og/2026-08-16/hourly/job",
                "submit": {
                    "status": "complete",
                    "outputs": [str(preview), str(final)],
                },
            }
            item = _work_product_item_from_job(
                jobs / "job.job.json",
                job,
                data_root=Path(td) / "data",
                output_root=out,
            )
            self.assertEqual(item.get("output_relpath"), "og/2026-08-16/hourly/job_FINAL_00001.mp4")

    def test_keeper_upgrades_plain_suffix_to_latest_final_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "output"
            hourly = out / "og" / "2026-08-16" / "hourly"
            hourly.mkdir(parents=True)
            plain = hourly / "job_00002.mp4"
            preview_png = hourly / "job_PREVIEW_00023.png"
            old_final = hourly / "job_FINAL_00001.mp4"
            latest = hourly / "job_FINAL_00024.mp4"
            plain.write_bytes(b"p")
            preview_png.write_bytes(b"i")
            old_final.write_bytes(b"o")
            latest.write_bytes(b"f")
            from shape_factory_work_products import _work_product_item_from_job

            jobs = Path(td) / "data" / "shape_factory" / "jobs" / "BounceDanceA"
            jobs.mkdir(parents=True)
            job = {
                "family_slug": "BounceDanceA",
                "job_key": "job",
                "output_prefix": "og/2026-08-16/hourly/job",
                "submit": {
                    "status": "complete",
                    "outputs": [str(plain)],
                    "outputs_by_node": {"398": [str(plain)]},
                },
            }
            item = _work_product_item_from_job(
                jobs / "job.job.json",
                job,
                data_root=Path(td) / "data",
                output_root=out,
            )
            self.assertEqual(item.get("output_relpath"), "og/2026-08-16/hourly/job_FINAL_00024.mp4")
            self.assertNotIn("PREVIEW", str(item.get("output_relpath") or ""))
            self.assertNotIn("PREVIEW", str(item.get("output_thumb_url") or ""))

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

    def test_get_work_product_loads_job_outside_recent_window(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            out = root / "output"
            jobs = data / "shape_factory" / "jobs" / "DEMO"
            jobs.mkdir(parents=True)
            out.mkdir(parents=True)
            old = {
                "created_at": "2025-01-01T00:00:00+00:00",
                "family_slug": "DEMO",
                "job_key": "hourly__archive",
                "pick_mode": "pool_product",
                "submit": {"status": "complete", "prompt_id": "pid-archive"},
            }
            new = {
                "created_at": "2026-07-14T12:00:00+00:00",
                "family_slug": "DEMO",
                "job_key": "hourly__fresh",
                "submit": {"status": "complete"},
            }
            (jobs / "hourly__archive.job.json").write_text(json.dumps(old) + "\n", encoding="utf-8")
            (jobs / "hourly__fresh.job.json").write_text(json.dumps(new) + "\n", encoding="utf-8")
            recent = list_recent_work_products(data_root=data, output_root=out, limit=1, hourly_only=True)
            self.assertEqual([it["job_key"] for it in recent["items"]], ["hourly__fresh"])
            payload = get_work_product(data_root=data, output_root=out, job_key="hourly__archive")
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["item"]["job_key"], "hourly__archive")
            self.assertEqual(payload["item"]["prompt_id"], "pid-archive")
            by_pid = get_work_product(data_root=data, output_root=out, prompt_id="pid-archive")
            self.assertEqual(by_pid["item"]["job_key"], "hourly__archive")
            missing = get_work_product(data_root=data, output_root=out, job_key="no-such-job")
            self.assertFalse(missing["ok"])
            self.assertEqual(missing["error"], "not_found")

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
            self.assertNotIn("prompt_profiles", fams[0])

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

    def test_list_family_prompt_profiles_named_catalogs(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            prompts = data / "pools" / "FB9_GEX" / "prompts"
            replay = prompts / "_replay"
            replay.mkdir(parents=True)
            (prompts / "catalog-default.json").write_text(
                '{"label": "catalog-default", "positive": "a", "negative": ""}\n',
                encoding="utf-8",
            )
            (prompts / "catalog-faceblast-extend.json").write_text(
                '{"label": "catalog-faceblast-extend", "positive": "b", "negative": ""}\n',
                encoding="utf-8",
            )
            (replay / "deadbeef.json").write_text('{"label": "replay"}\n', encoding="utf-8")
            rows = list_family_prompt_profiles(data, "FB9_GEX")
            self.assertEqual([r["slug"] for r in rows], ["catalog-default", "catalog-faceblast-extend"])
            shapes = data / "shapes"
            shapes.mkdir(parents=True)
            (shapes / "FB9_GEX.shape.yaml").write_text(
                "family_slug: FB9_GEX\nshape_id: wan_v2v_gex\n",
                encoding="utf-8",
            )
            fams = list_shape_families(data)
            self.assertEqual(len(fams[0].get("prompt_profiles") or []), 2)

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

    def test_list_submit_family_sets_partitions_extend(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            shapes = data / "shapes"
            shapes.mkdir(parents=True)
            (shapes / "FB9_GEX2.shape.yaml").write_text(
                "family_slug: FB9_GEX2\nshape_id: wan_v2v_gex2\n",
                encoding="utf-8",
            )
            (shapes / "X-KNEEL-FB9.shape.yaml").write_text(
                "family_slug: X-KNEEL-FB9\nshape_id: wan_i2v_kneel\n",
                encoding="utf-8",
            )
            (shapes / "FB9_GEX2_identity_anchor.shape.yaml").write_text(
                "family_slug: FB9_GEX2_identity_anchor\n"
                "shape_id: wan-v2v-source+prompt+identity_anchor\n",
                encoding="utf-8",
            )
            pipes = data / "pipelines"
            pipes.mkdir(parents=True)
            (pipes / "kneel-to-gex.pipeline.yaml").write_text(
                "\n".join(
                    [
                        "pipeline_id: kneel-to-gex",
                        "steps:",
                        "  - id: kneel",
                        "    shape: X-KNEEL-FB9.shape.yaml",
                        "  - id: gex",
                        "    shape: FB9_GEX2.shape.yaml",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            payload = list_submit_family_sets(data)
            self.assertTrue(payload.get("ok"))
            self.assertIn("fingerprint", payload)
            extend_slugs = {f["slug"] for f in payload["sets"]["extend"]}
            all_slugs = {f["slug"] for f in payload["families"]}
            self.assertEqual(all_slugs, {"FB9_GEX2", "X-KNEEL-FB9", "FB9_GEX2_identity_anchor"})
            self.assertIn("FB9_GEX2", extend_slugs)
            self.assertNotIn("X-KNEEL-FB9", extend_slugs)
            self.assertIn("FB9_GEX2_identity_anchor", extend_slugs)
            self.assertEqual(payload["extend_family_defaults"], {"X-KNEEL-FB9": "FB9_GEX2"})
            self.assertTrue(is_extend_family_option({"slug": "FB9_GEX2", "shape_id": "wan_v2v_gex2"}))
            self.assertFalse(is_extend_family_option({"slug": "X-KNEEL-FB9", "shape_id": "wan_i2v_kneel"}))
            self.assertTrue(
                is_extend_family_option(
                    {
                        "slug": "FB9_GEX2_identity_anchor",
                        "shape_id": "wan-v2v-source+prompt+identity_anchor",
                    }
                )
            )

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

    def test_attach_comfy_history_failures_synthesizes_and_enriches(self):
        payload = {
            "ok": True,
            "limit": 10,
            "families": [{"slug": "FB9_GEX2"}],
            "items": [
                {
                    "job_key": "hourly__known",
                    "prompt_id": "pid-known",
                    "status": "complete",
                    "error": None,
                }
            ],
        }
        long_msg = "CUDA out of memory\nTried to allocate 2.00 GiB"
        history = {
            "pid-known": {
                "prompt": [3, "pid-known", {}, {"workflow_name": "hourly__known"}],
                "status": {
                    "status_str": "error",
                    "completed": False,
                    "messages": [
                        [
                            "execution_error",
                            {
                                "exception_type": "RuntimeError",
                                "exception_message": long_msg,
                                "node_id": "84",
                                "node_type": "SamplerCustomAdvanced",
                                "timestamp": 1_700_000_000_000,
                            },
                        ]
                    ],
                },
            },
            "pid-orphan": {
                "prompt": [
                    2,
                    "pid-orphan",
                    {
                        "80": {
                            "class_type": "VHS_VideoCombine",
                            "inputs": {"filename_prefix": "output/og/x/FB9_GEX2_shape"},
                        }
                    },
                    {},
                ],
                "status": {
                    "status_str": "error",
                    "completed": False,
                    "messages": [
                        [
                            "execution_error",
                            {
                                "exception_message": "VHS_LoadVideoPath: No frames generated",
                                "node_type": "VHS_LoadVideoPath",
                            },
                        ]
                    ],
                },
            },
            "pid-ok": {
                "prompt": [1, "pid-ok", {}, {}],
                "status": {"status_str": "success", "completed": True, "messages": []},
            },
        }
        out = attach_comfy_history_failures(payload, history=history)
        self.assertGreaterEqual(out.get("history_failure_count") or 0, 2)
        # Known prompt enriched in place
        known = next(i for i in out["items"] if i.get("prompt_id") == "pid-known")
        self.assertEqual(known.get("status"), "error")
        self.assertIn("CUDA out of memory", str(known.get("error") or ""))
        self.assertIn("Tried to allocate", str(known.get("error") or ""))
        # Orphan history failure appears as a stub
        orphan = next(i for i in out["items"] if i.get("prompt_id") == "pid-orphan")
        self.assertEqual(orphan.get("status"), "error")
        self.assertIn("No frames generated", str(orphan.get("error") or ""))
        self.assertTrue(orphan.get("history_from_comfy"))

    def test_extract_history_error_fallback_when_no_message(self):
        from shape_factory import extract_history_execution_error, format_history_error_text

        err = extract_history_execution_error(
            {"status": {"status_str": "error", "completed": False, "messages": []}}
        )
        self.assertIsNotNone(err)
        text = format_history_error_text(err)
        self.assertIn("no Comfy exception text", text)

    def test_reconcile_rebinds_interrupted_job_by_workflow_name(self):
        from shape_factory_work_products import reconcile_inflight_jobs_with_comfy

        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            jobs = data / "shape_factory" / "jobs" / "FB9_GEX2"
            jobs.mkdir(parents=True)
            key = "FB9_GEX2__prompt_profile-abc__source_video-x__000_e_ui1"
            path = jobs / f"{key}.job.json"
            path.write_text(
                json.dumps(
                    {
                        "job_key": key,
                        "family_slug": "FB9_GEX2",
                        "submit": {
                            "prompt_id": "old-pid",
                            "status": "interrupted",
                            "interrupted_reason": "missing_from_comfy_queue_and_history",
                        },
                    }
                ),
                encoding="utf-8",
            )
            extra = {"workflow_name": key, "name": key, "filename": f"{key}.json"}
            summary = reconcile_inflight_jobs_with_comfy(
                data_root=data,
                comfy_server="http://example.invalid",
                queue_running=[],
                queue_pending=[[1, "new-pid", {}, extra]],
                persist=True,
                auto_retry_oom=False,
            )
            self.assertGreaterEqual(int(summary.get("rebound") or 0), 1)
            saved = json.loads(path.read_text(encoding="utf-8"))
            submit = saved["submit"]
            self.assertEqual(submit["prompt_id"], "new-pid")
            self.assertEqual(submit["previous_prompt_id"], "old-pid")
            self.assertEqual(submit["status"], "queued")
            self.assertNotIn("interrupted_reason", submit)

    def test_attach_live_matches_interrupted_row_by_job_key(self):
        key = "FB9_GEX2__prompt_profile-abc__source_video-x__000_e_ui1"
        payload = {
            "ok": True,
            "limit": 5,
            "families": [{"slug": "FB9_GEX2"}],
            "items": [
                {
                    "job_key": key,
                    "prompt_id": "old-pid",
                    "status": "interrupted",
                    "family_slug": "FB9_GEX2",
                }
            ],
        }
        extra = {"workflow_name": key, "name": key}
        out = attach_live_comfy_queue(
            payload,
            queue_running=[],
            queue_pending=[[1, "new-pid", {}, extra]],
        )
        self.assertEqual(out.get("live_comfy_count"), 1)
        self.assertEqual(out["items"][0]["job_key"], key)
        self.assertEqual(out["items"][0]["prompt_id"], "new-pid")
        self.assertEqual(out["items"][0]["status"], "queued")
        self.assertTrue(out["items"][0].get("live_from_comfy"))
        # Interrupted row was promoted — not duplicated underneath.
        keys = [it.get("job_key") for it in out["items"]]
        self.assertEqual(keys.count(key), 1)

    def test_attach_live_dedupes_multiple_prompts_for_same_job_key(self):
        key = "FB9_GEX2__prompt_profile-abc__source_video-x__000_e_ui1"
        payload = {
            "ok": True,
            "limit": 10,
            "families": [{"slug": "FB9_GEX2"}],
            "items": [
                {
                    "job_key": key,
                    "prompt_id": "old-pid",
                    "status": "submitted",
                    "family_slug": "FB9_GEX2",
                }
            ],
        }
        extra = {"workflow_name": key, "name": key}
        out = attach_live_comfy_queue(
            payload,
            queue_running=[],
            queue_pending=[[1, "new-pid-1", {}, extra], [2, "new-pid-2", {}, extra]],
        )
        rows = [it for it in out["items"] if it.get("job_key") == key]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("prompt_id"), "new-pid-1")
        self.assertTrue(rows[0].get("live_from_comfy"))

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
                auto_retry_oom=False,
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

    def test_applied_vhs_gleaned_from_generated_workflow(self):
        from shape_factory_work_products import _work_product_item_from_job

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            out = root / "output"
            jobs = data / "shape_factory" / "jobs" / "FB9_GEX2"
            jobs.mkdir(parents=True)
            key = "hourly__trim_from_wf"
            wf = {
                "nodes": [
                    {
                        "id": 10,
                        "type": "VHS_LoadVideoPath",
                        "widgets_values": {
                            "video": "input/demo.mp4",
                            "skip_first_frames": 18,
                            "frame_load_cap": 45,
                        },
                    }
                ],
                "links": [],
            }
            wf_path = jobs / f"{key}.workflow.json"
            wf_path.write_text(json.dumps(wf), encoding="utf-8")
            job = {
                "job_key": key,
                "family_slug": "FB9_GEX2",
                "created_at": "2026-07-13T12:00:00+00:00",
                "generated_workflow_path": str(wf_path),
                "submit": {"status": "pending"},
            }
            job_path = jobs / f"{key}.job.json"
            job_path.write_text(json.dumps(job), encoding="utf-8")
            item = _work_product_item_from_job(job_path, job, data_root=data, output_root=out)
            self.assertEqual(item.get("applied_vhs"), {"skip_first_frames": 18, "frame_load_cap": 45})
            labels = {r["label"]: r["value"] for r in (item.get("details") or [])}
            self.assertEqual(labels.get("VHS skip_first_frames"), "18")
            self.assertEqual(labels.get("VHS frame_load_cap"), "45")

    def test_work_product_includes_timing_summary(self):
        from shape_factory_work_products import _work_product_item_from_job

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            out = root / "output"
            jobs = data / "shape_factory" / "jobs" / "FB9_GEX2"
            jobs.mkdir(parents=True)
            key = "hourly__timing_probe"
            job = {
                "job_key": key,
                "family_slug": "FB9_GEX2",
                "created_at": "2026-07-13T12:00:00+00:00",
                "submit": {"status": "ok", "prompt_id": "abc"},
                "construction": {"frames_before": 80, "frames_after": 80},
                "timings": {
                    "execution": {"sec": 942.5, "terminal": "success", "source": "history"},
                    "queue": {"wait_sec": 120.0},
                    "totals": {"submit_to_complete_sec": 1065.0},
                    "workload": {"frames": 80},
                    "efficiency": {"exec_sec_per_frame": 11.78},
                },
            }
            job_path = jobs / f"{key}.job.json"
            job_path.write_text(json.dumps(job), encoding="utf-8")
            # Sidecar overrides inline with richer wait + confirms merge preference.
            (jobs / f"{key}.timings.json").write_text(
                json.dumps(
                    {
                        "execution": {"sec": 942.5, "terminal": "success"},
                        "queue": {"wait_sec": 180.0},
                        "totals": {"submit_to_complete_sec": 1125.0},
                        "workload": {"frames": 80},
                        "efficiency": {"exec_sec_per_frame": 11.78},
                    }
                ),
                encoding="utf-8",
            )
            item = _work_product_item_from_job(job_path, job, data_root=data, output_root=out)
            timing = item.get("timing") or {}
            self.assertEqual(timing.get("exec_sec"), 942.5)
            self.assertEqual(timing.get("wait_sec"), 180.0)
            self.assertEqual(timing.get("frames"), 80)
            self.assertEqual(timing.get("terminal"), "success")
            self.assertIn("exec", str(timing.get("label") or "").lower())
            labels = {r["label"]: r["value"] for r in (item.get("details") or [])}
            self.assertIn("Exec", labels)
            self.assertEqual(labels.get("Queue wait sec"), "180.0")
            self.assertEqual(labels.get("Frames before→after"), "80→80")

    def test_synthetic_live_work_product_includes_applied_vhs(self):
        from shape_factory_work_products import _synthetic_live_work_product

        prompt = {
            "12": {
                "class_type": "VHS_LoadVideoPath",
                "inputs": {"video": "output/demo.mp4", "skip_first_frames": 9, "frame_load_cap": 30},
            },
            "80": {
                "class_type": "VHS_VideoCombine",
                "inputs": {"filename_prefix": "output/og/2026-07-13/FB9_GEX2/x", "save_output": True},
            },
        }
        item = _synthetic_live_work_product(
            prompt_id="abcdef0123456789",
            status="running",
            prompt=prompt,
            family_slugs=["FB9_GEX2"],
        )
        self.assertEqual(item.get("applied_vhs"), {"skip_first_frames": 9, "frame_load_cap": 30})
        labels = {r["label"]: r["value"] for r in (item.get("details") or [])}
        self.assertEqual(labels.get("VHS skip_first_frames"), "9")


if __name__ == "__main__":
    unittest.main()
