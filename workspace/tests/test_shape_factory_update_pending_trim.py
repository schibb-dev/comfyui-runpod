"""Tests for updating VHS trim on pending factory jobs."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class UpdatePendingJobVhsWindowTests(unittest.TestCase):
    def test_patches_workflow_and_records_vhs_window(self) -> None:
        from shape_factory import apply_job_vhs_window_to_workflow, update_pending_job_vhs_window

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs = root / "shape_factory" / "jobs" / "FB9_GEX2"
            jobs.mkdir(parents=True)
            key = "hourly__demo_trim"
            workflow = {
                "nodes": [
                    {
                        "id": 10,
                        "type": "VHS_LoadVideoPath",
                        "widgets_values": {
                            "video": "input/demo.mp4",
                            "skip_first_frames": 0,
                            "frame_load_cap": 0,
                            "videopreview": {"params": {"skip_first_frames": 0, "frame_load_cap": 0}},
                        },
                    }
                ],
                "links": [],
            }
            wf_path = jobs / f"{key}.workflow.json"
            wf_path.write_text(json.dumps(workflow), encoding="utf-8")
            job = {
                "job_key": key,
                "family_slug": "FB9_GEX2",
                "generated_workflow_path": str(wf_path),
                "submit": {"status": "pending"},
            }
            job_path = jobs / f"{key}.job.json"
            job_path.write_text(json.dumps(job, indent=2), encoding="utf-8")
            prompt_path = jobs / f"{key}.prompt.json"
            prompt_path.write_text(json.dumps({"1": {"class_type": "Stub"}}), encoding="utf-8")

            out = update_pending_job_vhs_window(
                data_root=root,
                job_path=job_path,
                skip_first_frames=12,
                frame_load_cap=40,
                mark_in=0.5,
                mark_out=2.5,
            )
            self.assertTrue(out.get("ok"), out)
            self.assertTrue(out.get("prompt_cleared"))
            self.assertFalse(prompt_path.is_file())

            saved_job = json.loads(job_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_job["vhs_window"]["skip_first_frames"], 12)
            self.assertEqual(saved_job["vhs_window"]["frame_load_cap"], 40)
            self.assertEqual(saved_job["vhs_window"]["mark_in"], 0.5)

            saved_wf = json.loads(wf_path.read_text(encoding="utf-8"))
            widgets = saved_wf["nodes"][0]["widgets_values"]
            self.assertEqual(widgets["skip_first_frames"], 12)
            self.assertEqual(widgets["frame_load_cap"], 40)
            self.assertEqual(widgets["videopreview"]["params"]["skip_first_frames"], 12)

            # Submit-time re-apply helper
            fresh = {
                "nodes": [
                    {
                        "id": 10,
                        "type": "VHS_LoadVideoPath",
                        "widgets_values": {"skip_first_frames": 0, "frame_load_cap": 0},
                    }
                ],
                "links": [],
            }
            changes = apply_job_vhs_window_to_workflow(saved_job, fresh)
            self.assertTrue(changes and changes.get("vhs"))
            self.assertEqual(fresh["nodes"][0]["widgets_values"]["skip_first_frames"], 12)

    def test_vhs_window_wins_over_stale_zero_dev_tuning_on_prompt(self) -> None:
        from shape_factory import apply_dev_tuning_api, apply_job_vhs_window_to_prompt, sync_job_dev_tuning_from_vhs_window

        job = {
            "vhs_window": {"skip_first_frames": 64, "frame_load_cap": 28, "source": "use"},
            "dev_tuning": {
                "spec": {"vhs_load_video_path": {"skip_first_frames": 0, "frame_load_cap": 0}},
            },
        }
        prompt = {
            "377": {
                "class_type": "VHS_LoadVideoPath",
                "inputs": {"skip_first_frames": 64, "frame_load_cap": 28, "video": "x.mp4"},
            }
        }
        spec = job["dev_tuning"]["spec"]
        apply_dev_tuning_api(prompt, spec)
        self.assertEqual(prompt["377"]["inputs"]["skip_first_frames"], 0)
        self.assertEqual(prompt["377"]["inputs"]["frame_load_cap"], 0)

        apply_job_vhs_window_to_prompt(job, prompt)
        self.assertEqual(prompt["377"]["inputs"]["skip_first_frames"], 64)
        self.assertEqual(prompt["377"]["inputs"]["frame_load_cap"], 28)

        self.assertTrue(sync_job_dev_tuning_from_vhs_window(job))
        vhs = job["dev_tuning"]["spec"]["vhs_load_video_path"]
        self.assertEqual(vhs["skip_first_frames"], 64)
        self.assertEqual(vhs["frame_load_cap"], 28)

    def test_refuses_queued_job(self) -> None:
        from shape_factory import update_pending_job_vhs_window

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs = root / "shape_factory" / "jobs" / "FB9_GEX2"
            jobs.mkdir(parents=True)
            key = "hourly__locked"
            wf_path = jobs / f"{key}.workflow.json"
            wf_path.write_text(
                json.dumps(
                    {
                        "nodes": [
                            {
                                "id": 1,
                                "type": "VHS_LoadVideoPath",
                                "widgets_values": {"skip_first_frames": 0, "frame_load_cap": 0},
                            }
                        ],
                        "links": [],
                    }
                ),
                encoding="utf-8",
            )
            job_path = jobs / f"{key}.job.json"
            job_path.write_text(
                json.dumps(
                    {
                        "job_key": key,
                        "generated_workflow_path": str(wf_path),
                        "submit": {"status": "queued", "prompt_id": "abc"},
                    }
                ),
                encoding="utf-8",
            )
            out = update_pending_job_vhs_window(
                data_root=root,
                job_path=job_path,
                skip_first_frames=5,
                frame_load_cap=10,
            )
            self.assertFalse(out.get("ok"))
            self.assertEqual(out.get("error"), "not_pending")


if __name__ == "__main__":
    unittest.main()
