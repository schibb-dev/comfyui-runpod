"""Swap the generation stack on a pre-Comfy job."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class UpdatePendingJobStackTests(unittest.TestCase):
    def test_patches_unet_and_stamps_job(self) -> None:
        from shape_factory import update_pending_job_stack

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs = root / "jobs" / "demo"
            stacks = root / "stacks"
            jobs.mkdir(parents=True)
            stacks.mkdir()
            (stacks / "i2v-720p-q5.yaml").write_text(
                "\n".join(
                    [
                        "stack_id: i2v-720p-q5",
                        "unet_name: wan2.1-i2v-14b-720p-Q5_K_M.gguf",
                        "teacache_coefficients: i2v_720",
                        "virtual_vram_gb: 4.0",
                    ]
                ),
                encoding="utf-8",
            )
            wf_path = jobs / "demo__1.workflow.json"
            job_path = jobs / "demo__1.job.json"
            wf = {
                "nodes": [
                    {
                        "id": 458,
                        "type": "UNETLoader",
                        "widgets_values": ["wan2.1-i2v-14b-480p-Q8_0.gguf", "default", 6.0],
                    }
                ],
                "links": [],
            }
            wf_path.write_text(json.dumps(wf), encoding="utf-8")
            job = {
                "job_key": "demo__1",
                "family_slug": "DEMO",
                "stack_id": "i2v-480p-q8",
                "generated_workflow_path": str(wf_path),
                "submit": {"status": "pending"},
            }
            job_path.write_text(json.dumps(job), encoding="utf-8")

            res = update_pending_job_stack(
                data_root=root,
                job_path=job_path,
                stack_id="i2v-720p-q5",
            )
            self.assertTrue(res.get("ok"), res)
            self.assertEqual(res.get("stack_id"), "i2v-720p-q5")
            updated = json.loads(wf_path.read_text(encoding="utf-8"))
            node = next(n for n in updated["nodes"] if n["id"] == 458)
            self.assertEqual(node["widgets_values"][0], "WAN/wan2.1-i2v-14b-720p-Q5_K_M.gguf")
            self.assertEqual(node["widgets_values"][2], 4.0)
            job2 = json.loads(job_path.read_text(encoding="utf-8"))
            self.assertEqual(job2["stack_id"], "i2v-720p-q5")
            self.assertEqual(job2["adhoc_overrides"]["stack"], "i2v-720p-q5")

    def test_refuses_comfy_status(self) -> None:
        from shape_factory import update_pending_job_stack

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs = root / "jobs" / "demo"
            jobs.mkdir(parents=True)
            job_path = jobs / "demo__1.job.json"
            job_path.write_text(
                json.dumps(
                    {
                        "job_key": "demo__1",
                        "submit": {"status": "running", "prompt_id": "abc"},
                    }
                ),
                encoding="utf-8",
            )
            res = update_pending_job_stack(
                data_root=root,
                job_path=job_path,
                stack_id="i2v-720p-q5",
            )
            self.assertFalse(res.get("ok"))
            self.assertEqual(res.get("error"), "not_pending")
