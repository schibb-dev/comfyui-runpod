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
        self.assertIn(84, tuning.get("ui_nodes") or tuning["ui_nodes"])

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
        params = _extend_length_parameters(job)
        self.assertEqual(params["frames"], 160)
        self.assertEqual(params["overlap"], 8)
        self.assertEqual(params["frame_load_cap"], 160)
        # Explicit frames win.
        pinned = _extend_length_parameters(job, existing={"frames": 200})
        self.assertEqual(pinned["frames"], 200)

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
