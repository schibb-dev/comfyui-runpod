#!/usr/bin/env python3
"""Tests for shape-factory submit path resolution and workflow rebuild."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class ShapeFactorySubmitRebuildTests(unittest.TestCase):
    def test_runtime_path_candidates_workspace_output_flatten(self) -> None:
        from shape_factory_map import _runtime_path_candidates

        output_root = Path("/home/yuji/comfyui-runpod-data/output")
        cands = _runtime_path_candidates(
            "/workspace/output/output/og/2026-04-03/FB9_GEX2_2026-04-03_00002.mp4",
            output_root=output_root,
        )
        self.assertIn(
            "/home/yuji/comfyui-runpod-data/output/og/2026-04-03/FB9_GEX2_2026-04-03_00002.mp4",
            cands,
        )

    def test_rebuild_job_workflow_from_fixture_job(self) -> None:
        job_path = (
            REPO_ROOT
            / ".data/shape_factory/jobs/FB9_GEX2/FB9_GEX2__prompt_profile-idle-small-motions__draft_1783377257__source_video-FB9_GEX2_2026-04-03_0000__000_adhoc_ui178337.job.json"
        )
        if not job_path.is_file():
            self.skipTest("adhoc UI fixture job missing")

        from shape_factory import DEFAULT_DATA_ROOT, rebuild_job_workflow

        job = json.loads(job_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            wf_dir = Path(tmp) / "workflows"
            rebuilt = rebuild_job_workflow(
                job,
                data_root=DEFAULT_DATA_ROOT,
                workspace_root=REPO_ROOT / "workspace",
                workflow_dir=wf_dir,
            )
            self.assertTrue(rebuilt.is_file())
            body = json.loads(rebuilt.read_text(encoding="utf-8"))
            self.assertIn("nodes", body)

    def test_ensure_job_workflow_path_resolves_host_path(self) -> None:
        job_path = (
            REPO_ROOT
            / ".data/shape_factory/jobs/FB9_GEX2/FB9_GEX2__prompt_profile-idle-small-motions__draft_1783377257__source_video-FB9_GEX2_2026-04-03_0000__000_adhoc_ui178337.job.json"
        )
        if not job_path.is_file():
            self.skipTest("adhoc UI fixture job missing")

        from shape_factory import DEFAULT_DATA_ROOT, ensure_job_workflow_path

        job = json.loads(job_path.read_text(encoding="utf-8"))
        wf = ensure_job_workflow_path(job, data_root=DEFAULT_DATA_ROOT)
        self.assertTrue(wf.is_file())
        self.assertIn("comfyui-runpod-data", str(job.get("generated_workflow_path") or ""))


if __name__ == "__main__":
    unittest.main()
