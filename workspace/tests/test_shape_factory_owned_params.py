"""Tests for job-owned simple params snowflake (Phase B)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class OwnedParamsProfileTests(unittest.TestCase):
    def test_snowflake_when_frames_differ(self) -> None:
        from shape_factory_owned_params import owned_params_to_profile, params_equal

        job = {
            "timings": {"workload": {"frames": 96, "steps": 20, "overlap": 8}},
            "adhoc_overrides": {"parameters": {"frames": 96}},
            "template_path": "",
        }
        # Without a readable template, seed is empty → not snowflake.
        profile = owned_params_to_profile(job, data_root=Path("."), job_path=None)
        self.assertFalse(profile.get("snowflake"))
        self.assertEqual(profile["current"].get("frames"), 96)

        seed = {"frames": 80, "steps": 20, "overlap": 8}
        self.assertFalse(params_equal(profile["current"], seed))
        self.assertTrue(params_equal(seed, seed))

    def test_extract_params_from_workflow_mx_sliders(self) -> None:
        from shape_factory_owned_params import extract_params_from_workflow

        wf = {
            "nodes": [
                {"id": 84, "type": "mxSlider", "widgets_values": [80, 80, 0]},
                {"id": 82, "type": "mxSlider", "widgets_values": [25, 25, 0]},
                {"id": 387, "type": "mxSlider", "widgets_values": [8, 8, 0]},
                {"id": 1, "type": "RandomNoise", "widgets_values": [42, "fixed"]},
            ]
        }
        got = extract_params_from_workflow(wf)
        self.assertEqual(got.get("frames"), 80)
        self.assertEqual(got.get("steps"), 25)
        self.assertEqual(got.get("overlap"), 8)
        self.assertEqual(got.get("seed"), 42)


class UpdatePendingParamsTests(unittest.TestCase):
    def test_update_pending_job_params_patches_workflow(self) -> None:
        from shape_factory import update_pending_job_params

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs = root / "jobs" / "demo"
            jobs.mkdir(parents=True)
            wf_path = jobs / "demo__1.workflow.json"
            job_path = jobs / "demo__1.job.json"
            wf = {
                "nodes": [
                    {"id": 84, "type": "mxSlider", "widgets_values": [80, 80, 0]},
                    {"id": 82, "type": "mxSlider", "widgets_values": [20, 20, 0]},
                    {"id": 387, "type": "mxSlider", "widgets_values": [8, 8, 0]},
                ],
                "links": [],
            }
            wf_path.write_text(json.dumps(wf), encoding="utf-8")
            job = {
                "job_key": "demo__1",
                "family_slug": "DEMO",
                "generated_workflow_path": str(wf_path),
                "submit": {"status": "pending"},
                "timings": {"workload": {"frames": 80, "steps": 20, "overlap": 8}},
            }
            job_path.write_text(json.dumps(job), encoding="utf-8")

            res = update_pending_job_params(
                data_root=root,
                job_path=job_path,
                parameters={"frames": 96, "steps": 25},
            )
            self.assertTrue(res.get("ok"), res)
            self.assertEqual(res.get("parameters", {}).get("frames"), 96)
            updated = json.loads(wf_path.read_text(encoding="utf-8"))
            node84 = next(n for n in updated["nodes"] if n["id"] == 84)
            self.assertEqual(node84["widgets_values"][1], 96)
            job2 = json.loads(job_path.read_text(encoding="utf-8"))
            self.assertEqual(job2["adhoc_overrides"]["parameters"]["frames"], 96)
            self.assertEqual(job2["timings"]["workload"]["frames"], 96)


if __name__ == "__main__":
    unittest.main()
