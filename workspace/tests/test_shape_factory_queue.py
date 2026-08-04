#!/usr/bin/env python3
"""Tests for shape_factory_queue (UI queue endpoint logic)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]


class ShapeFactoryQueueTests(unittest.TestCase):
    def test_queue_from_request_body_dry_run(self) -> None:
        from shape_factory_queue import queue_from_request_body

        data_root = REPO_ROOT / ".data"
        if not (data_root / "shapes" / "FB9_GEX2.shape.yaml").is_file():
            self.skipTest("FB9_GEX2 shape fixtures missing")

        # Pick a projected combo path from pools (prompt + one video if available).
        pools_yaml = data_root / "pools" / "FB9_GEX2" / "pools.yaml"
        self.assertTrue(pools_yaml.is_file())

        prompt_path = data_root / "pools" / "FB9_GEX2" / "prompts" / "catalog-default.json"
        if not prompt_path.is_file():
            self.skipTest("catalog-default prompt missing")

        video_glob = list((REPO_ROOT / "workspace" / "output" / "output" / "og").rglob("FB9_GEX2*.mp4"))
        if not video_glob:
            self.skipTest("no FB9_GEX2 source videos under workspace/output")
        video_path = video_glob[0]

        workspace_root = REPO_ROOT / "workspace"
        output_root = workspace_root / "output"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_jobs = Path(tmp) / "shape_factory" / "jobs"
            tmp_jobs.mkdir(parents=True)

            def fake_generate(**kwargs):
                job_path = tmp_jobs / "FB9_GEX2" / "test.job.json"
                job_path.parent.mkdir(parents=True, exist_ok=True)
                job_path.write_text(json.dumps({"job_key": "test", "generated_workflow_path": str(tmp / "wf.json")}))
                (tmp / "wf.json").write_text("{}")
                return {
                    "job_key": "test",
                    "job_path": job_path,
                    "workflow_path": tmp / "wf.json",
                    "bindings": {},
                }

            with mock.patch("shape_factory_queue.generate_job_for_picks", side_effect=fake_generate):
                with mock.patch(
                    "shape_factory_queue.submit_job_file",
                    return_value={"ok": True, "dry_run": True, "job_key": "test"},
                ):
                    body = {
                        "family_slug": "FB9_GEX2",
                        "dry_run": True,
                        "bindings": {
                            "prompt_profile": str(prompt_path),
                            "source_video": str(video_path),
                        },
                    }
                    out = queue_from_request_body(
                        body,
                        repo_root=REPO_ROOT,
                        workspace_root=workspace_root,
                        output_root=output_root,
                        comfy_server="http://127.0.0.1:8188",
                    )
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("family_slug"), "FB9_GEX2")
        self.assertTrue(out.get("combo_key"))

    def test_resolve_existing_path_workspace_template(self) -> None:
        from shape_factory_map import resolve_existing_path

        workspace_root = REPO_ROOT / "workspace"
        data_root = REPO_ROOT / ".data"
        output_root = workspace_root / "output"
        host_tpl = "/home/yuji/comfyui-runpod-data/comfyui_user/default/workflows/generated/catalog/FB9_GEX2-readable.json"
        try:
            resolved = resolve_existing_path(
                host_tpl,
                output_root=output_root,
                data_root=data_root,
                workspace_root=workspace_root,
            )
        except FileNotFoundError:
            self.skipTest("FB9_GEX2-readable template not present")
        self.assertTrue(resolved.is_file())
        self.assertIn("comfyui_user", str(resolved))

    def test_comfy_data_root_prefers_output_bind(self) -> None:
        from shape_factory_queue import _comfy_data_root

        workspace_root = REPO_ROOT / "workspace"
        output_root = Path("/home/yuji/comfyui-runpod-data/output")
        if not output_root.is_dir():
            self.skipTest("comfyui-runpod-data output bind missing")
        root = _comfy_data_root(workspace_root=workspace_root, output_root=output_root)
        self.assertEqual(root, output_root.resolve().parent)

    def test_merge_prompt_and_adhoc_tuning(self) -> None:
        from shape_factory_queue import (
            _merge_prompt_profile,
            build_adhoc_dev_tuning,
            write_scratch_prompt_profile,
        )

        data_root = REPO_ROOT / ".data"
        merged = _merge_prompt_profile(
            {"label": "base", "positive": "hello", "negative": ""},
            {"positive": "hello world"},
        )
        self.assertEqual(merged["positive"], "hello world")
        self.assertEqual(merged["label"], "base")

        tuning = build_adhoc_dev_tuning({"frames": 24, "steps": 10}, data_root=data_root)
        self.assertIsNotNone(tuning)
        assert tuning is not None
        self.assertEqual(tuning.get("profile_id"), "adhoc-ui")
        ui = tuning.get("ui_nodes") or {}
        self.assertIn(84, ui)
        self.assertEqual(ui[84]["widgets_values"], [24, 24, 0])
        self.assertIn(82, ui)
        # Unmentioned knobs must not inherit dev-fast defaults (e.g. overlap=4).
        self.assertNotIn(387, ui)

        vhs = build_adhoc_dev_tuning(
            {"skip_first_frames": 12, "frame_load_cap": 40},
            data_root=data_root,
        )
        self.assertIsNotNone(vhs)
        assert vhs is not None
        self.assertEqual(
            vhs.get("vhs_load_video_path"),
            {"skip_first_frames": 12, "frame_load_cap": 40},
        )
        # Trim-only: leave Frames/Steps/Overlap on the template (no ui/api patches).
        self.assertEqual(vhs.get("ui_nodes") or {}, {})
        self.assertEqual(vhs.get("api_nodes") or {}, {})

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            src = tmp_root / "catalog-default.json"
            src.write_text('{"label":"catalog","positive":"a","negative":""}\n', encoding="utf-8")
            out = write_scratch_prompt_profile(
                tmp_root,
                family="FB9_GEX2",
                base=json.loads(src.read_text()),
                override={"positive": "edited"},
                source_path=src,
            )
            self.assertTrue(out.is_file())
            body = json.loads(out.read_text())
            self.assertEqual(body["positive"], "edited")

    def test_extend_length_parameters_doubles_parent_frames(self) -> None:
        from shape_factory_queue import _extend_length_parameters, _parent_frame_count

        job = {"timings": {"workload": {"frames": 80, "overlap": 8, "output_frame_count": 97}}}
        self.assertEqual(_parent_frame_count(job), 80)
        with mock.patch.dict("os.environ", {"SHAPE_FACTORY_EXTEND_EXTRA_FRAMES": ""}, clear=False):
            # Empty string → treat as unset and double.
            import os

            os.environ.pop("SHAPE_FACTORY_EXTEND_EXTRA_FRAMES", None)
            params = _extend_length_parameters(job)
        self.assertEqual(params["frames"], 160)
        self.assertEqual(params["overlap"], 8)
        self.assertEqual(params["frame_load_cap"], 160)
        # Explicit frames win.
        pinned = _extend_length_parameters(job, existing={"frames": 200})
        self.assertEqual(pinned["frames"], 200)
        # Caller-provided frame_load_cap is preserved (trim window).
        trimmed = _extend_length_parameters(job, existing={"skip_first_frames": 10, "frame_load_cap": 32})
        self.assertEqual(trimmed["frames"], 160)
        self.assertEqual(trimmed["frame_load_cap"], 32)
        self.assertEqual(trimmed["skip_first_frames"], 10)

    def test_clamp_vhs_load_window_and_resolve_overrides(self) -> None:
        from shape_factory_queue import clamp_vhs_load_window, resolve_vhs_window_overrides
        from shape_factory import apply_dev_tuning_api, apply_dev_tuning_ui

        skip, cap, clamped = clamp_vhs_load_window(
            skip_first_frames=47,
            frame_load_cap=0,
            frame_count=20,
        )
        self.assertEqual(skip, 19)
        self.assertEqual(cap, 0)
        self.assertTrue(clamped)

        params, meta = resolve_vhs_window_overrides(
            parameters={},
            media_abs=None,
            template_defaults={"skip_first_frames": 47, "frame_load_cap": 0},
            read_sidecar=False,
        )
        # No media → cannot clamp template; leave params alone.
        self.assertNotIn("skip_first_frames", params)

        params2, meta2 = resolve_vhs_window_overrides(
            parameters={"skip_first_frames": 80, "frame_load_cap": 10},
            media_abs=None,
            template_defaults=None,
            read_sidecar=False,
        )
        # Without frame_count, explicit params pass through unchanged.
        self.assertEqual(params2["skip_first_frames"], 80)
        self.assertEqual(params2["frame_load_cap"], 10)
        self.assertIsNone(meta2)

        with mock.patch(
            "shape_factory_queue._probe_media_frame_meta",
            return_value={"fps": 18.0, "frame_count": 20, "duration": 20 / 18},
        ):
            params3, meta3 = resolve_vhs_window_overrides(
                parameters={},
                media_abs=Path("/tmp/fake.mp4"),
                template_defaults={"skip_first_frames": 47, "frame_load_cap": 0},
                read_sidecar=False,
            )
        self.assertEqual(params3["skip_first_frames"], 19)
        self.assertTrue(meta3 and meta3.get("source") == "template_clamped")

        # Lengthen-only cap must not invent skip=0.
        with mock.patch(
            "shape_factory_queue._probe_media_frame_meta",
            return_value={"fps": 18.0, "frame_count": 100, "duration": 5.0},
        ):
            params4, _meta4 = resolve_vhs_window_overrides(
                parameters={"frame_load_cap": 160},
                media_abs=Path("/tmp/fake.mp4"),
                template_defaults={"skip_first_frames": 47, "frame_load_cap": 0},
                read_sidecar=False,
            )
        self.assertNotIn("skip_first_frames", params4)
        self.assertEqual(params4["frame_load_cap"], 100)

        tuning = {"vhs_load_video_path": {"skip_first_frames": 5, "frame_load_cap": 12}}
        ui = {"nodes": [{"id": 1, "type": "VHS_LoadVideoPath", "widgets_values": {}}]}
        api = {"1": {"class_type": "VHS_LoadVideoPath", "inputs": {}}}
        apply_dev_tuning_ui(ui, tuning)
        apply_dev_tuning_api(api, tuning)
        self.assertEqual(ui["nodes"][0]["widgets_values"]["skip_first_frames"], 5)
        self.assertEqual(ui["nodes"][0]["widgets_values"]["frame_load_cap"], 12)
        self.assertEqual(api["1"]["inputs"]["skip_first_frames"], 5)
        self.assertEqual(api["1"]["inputs"]["frame_load_cap"], 12)

    def test_failed_extend_retry_uses_frames_before_and_parent_output(self) -> None:
        from shape_factory_queue import _parent_frame_count, replay_from_request_body

        job = {
            "job_key": "failed_extend",
            "family_slug": "FB9_GEX2",
            "parent_output": "/data/output/og/parent.mp4",
            "construction": {
                "step": "extend",
                "derive_action": "extend",
                "frames_before": 80,
                "frames_after": 160,
                "parent_output": "/data/output/og/parent.mp4",
            },
            "bindings": {
                "prompt_profile": {"path": "/data/prompts/x.json"},
                "source_video": {"path": "/data/output/og/parent.mp4"},
            },
            "submit": {"status": "error", "prompt_id": "abc"},
            "timings": {"workload": {"frames": 160}},  # target length — must NOT be base
        }
        self.assertEqual(_parent_frame_count(job), 80)

        captured: dict = {}

        def fake_queue(**kwargs):
            captured.update(kwargs)
            return {"ok": True, "job_key": "retry", "pick_mode": kwargs.get("pick_mode")}

        with mock.patch("shape_factory_queue._find_job_doc", return_value=(job, Path("x.job.json"))), mock.patch(
            "shape_factory_queue.load_yaml",
            return_value={"requires": [{"slot": "source_video", "media": "video"}, {"slot": "prompt_profile", "binding": {"type": "prompt_bundle"}}]},
        ), mock.patch(
            "shape_factory_queue.resolve_or_recover_prompt_profile_binding",
            side_effect=lambda bindings, **_k: (bindings, None),
        ), mock.patch(
            "shape_factory_queue._resolve_shape_path", return_value=Path("shape.yaml")
        ), mock.patch(
            "shape_factory_queue.queue_shape_factory_combo", side_effect=fake_queue
        ), mock.patch.dict("os.environ", {"SHAPE_FACTORY_EXTEND_EXTRA_FRAMES": "32"}):
            out = replay_from_request_body(
                {"job_key": "failed_extend", "extend": True, "dry_run": True},
                repo_root=REPO_ROOT,
                workspace_root=REPO_ROOT / "workspace",
                output_root=Path("/tmp"),
                comfy_server="http://127.0.0.1:8188",
            )
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(captured.get("pick_mode"), "extend")
        params = (captured.get("overrides") or {}).get("parameters") or {}
        self.assertEqual(int(params.get("frames") or 0), 112)
        self.assertEqual(captured.get("parent_output"), "/data/output/og/parent.mp4")
        self.assertTrue((captured.get("construction") or {}).get("retry_of_failed_extend"))

    def test_oom_retry_halves_extra_frames(self) -> None:
        from shape_factory_queue import (
            compute_oom_retry_frame_target,
            is_oom_error_message,
            maybe_auto_retry_oom_extend,
        )

        self.assertTrue(is_oom_error_message("SamplerCustomAdvanced: out of memory"))
        self.assertFalse(is_oom_error_message("VHS_LoadVideoPath: No frames generated"))

        job = {
            "job_key": "oom_job",
            "family_slug": "FB9_GEX2",
            "pick_mode": "extend",
            "parent_output": "/data/parent.mp4",
            "construction": {
                "step": "extend",
                "derive_action": "extend",
                "frames_before": 80,
                "frames_after": 160,
            },
            "bindings": {"source_video": {"path": "/data/parent.mp4"}, "prompt_profile": {"path": "/p.json"}},
            "submit": {
                "status": "error",
                "error": "SamplerCustomAdvanced: Allocation on device 0 would exceed allowed memory. (out of memory)",
                "prompt_id": "x",
            },
        }
        self.assertEqual(compute_oom_retry_frame_target(job), (80, 120, 40))

        captured: dict = {}

        def fake_replay(body, **kwargs):
            captured["body"] = body
            return {"ok": True, "job_key": "oom_job_retry", "prompt_id": "new-pid"}

        with mock.patch("shape_factory_queue.replay_from_request_body", side_effect=fake_replay), mock.patch(
            "shape_factory.atomic_write_json", lambda *_a, **_k: None
        ), mock.patch.dict("os.environ", {"SHAPE_FACTORY_OOM_EXTEND_AUTO_RETRY": "1"}):
            out = maybe_auto_retry_oom_extend(
                job,
                Path("oom.job.json"),
                repo_root=REPO_ROOT,
                workspace_root=REPO_ROOT / "workspace",
                output_root=Path("/tmp"),
                comfy_server="http://x",
                persist=False,
            )
        self.assertTrue(out and out.get("ok") and out.get("oom_auto_retry"))
        params = ((captured.get("body") or {}).get("overrides") or {}).get("parameters") or {}
        self.assertEqual(params.get("frames"), 120)
        self.assertEqual(job["submit"]["oom_auto_retry"]["spawned_job_key"], "oom_job_retry")

        # Second call is idempotent.
        with mock.patch("shape_factory_queue.replay_from_request_body") as again:
            out2 = maybe_auto_retry_oom_extend(
                job,
                Path("oom.job.json"),
                repo_root=REPO_ROOT,
                workspace_root=REPO_ROOT / "workspace",
                output_root=Path("/tmp"),
                comfy_server="http://x",
                persist=False,
            )
            again.assert_not_called()
        self.assertIsNone(out2)


    def test_replay_extend_bumps_frames_and_stamps_pick_mode(self) -> None:
        from shape_factory_queue import replay_from_request_body

        data_root = REPO_ROOT / ".data"
        parent_key = (
            "hourly__prompt_profile-1ff2227780fb__source_video-X-FB9-POSE-2026-04-16-171828_OG_00001"
            "__000_202607161616"
        )
        parent_path = data_root / "shape_factory" / "jobs" / "FB9_GEX2" / f"{parent_key}.job.json"
        if not parent_path.is_file():
            self.skipTest("example GEX2 parent job missing")

        captured: dict = {}

        def fake_queue(**kwargs):
            captured.update(kwargs)
            return {
                "ok": True,
                "family_slug": kwargs.get("family_slug"),
                "combo_key": "test",
                "job_key": "test",
                "pick_mode": kwargs.get("pick_mode"),
            }

        with mock.patch("shape_factory_queue.queue_shape_factory_combo", side_effect=fake_queue):
            out = replay_from_request_body(
                {
                    "job_key": parent_key,
                    "family_slug": "FB9_GEX_FACIAL",
                    "extend": True,
                    "dry_run": True,
                },
                repo_root=REPO_ROOT,
                workspace_root=REPO_ROOT / "workspace",
                output_root=Path("/home/yuji/comfyui-runpod-data/output"),
                comfy_server="http://127.0.0.1:8188",
            )
        self.assertTrue(out.get("ok"))
        self.assertEqual(captured.get("pick_mode"), "extend")
        self.assertTrue(captured.get("parent_output"))
        params = (captured.get("overrides") or {}).get("parameters") or {}
        self.assertGreater(int(params.get("frames") or 0), 80)
        src = (captured.get("bindings") or {}).get("source_video") or ""
        self.assertIn("hourly__prompt_profile-1ff2227780fb", src)
        self.assertNotIn("X-FB9-POSE-2026-04-16-171828_OG_00001.mp4", src)


if __name__ == "__main__":
    unittest.main()
