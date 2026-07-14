#!/usr/bin/env python3
"""Tests for shape-factory submit path resolution and workflow rebuild."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _first_resolvable_job() -> Path | None:
    from shape_factory_map import resolve_existing_path

    jobs_dir = REPO_ROOT / ".data/shape_factory/jobs/FB9_GEX2"
    if not jobs_dir.is_dir():
        return None
    data_root = REPO_ROOT / ".data"
    workspace_root = REPO_ROOT / "workspace"
    output_root = Path("/home/yuji/comfyui-runpod-data/output")
    for job_path in sorted(jobs_dir.glob("*.job.json")):
        try:
            job = json.loads(job_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        bindings = job.get("bindings") if isinstance(job.get("bindings"), dict) else {}
        ok = True
        for meta in bindings.values():
            if not isinstance(meta, dict):
                continue
            raw = meta.get("path")
            if not raw:
                continue
            try:
                resolve_existing_path(
                    str(raw),
                    output_root=output_root,
                    data_root=data_root,
                    workspace_root=workspace_root,
                )
            except Exception:
                ok = False
                break
        if ok:
            return job_path
    return None


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
        job_path = _first_resolvable_job()
        if job_path is None:
            self.skipTest("no resolvable FB9_GEX2 job fixtures")

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
        job_path = _first_resolvable_job()
        if job_path is None:
            self.skipTest("no resolvable FB9_GEX2 job fixtures")

        from shape_factory import DEFAULT_DATA_ROOT, ensure_job_workflow_path

        job = json.loads(job_path.read_text(encoding="utf-8"))
        wf = ensure_job_workflow_path(job, data_root=DEFAULT_DATA_ROOT)
        self.assertTrue(wf.is_file())
        self.assertIn("comfyui-runpod-data", str(job.get("generated_workflow_path") or ""))

    def test_pending_only_retries_error_until_cap(self) -> None:
        import os
        from shape_factory import (
            job_abandoned,
            job_pending_submit,
            record_submit_failure,
            submit_job_file,
            submit_max_attempts,
        )

        prev = os.environ.get("SHAPE_FACTORY_SUBMIT_MAX_ATTEMPTS")
        try:
            os.environ["SHAPE_FACTORY_SUBMIT_MAX_ATTEMPTS"] = "3"
            with tempfile.TemporaryDirectory() as tmp:
                job_path = Path(tmp) / "broken.job.json"
                job = {
                    "job_key": "broken",
                    "submit": {
                        "status": "error",
                        "error": "Comfy rejected prompt (Invalid image file)",
                        "attempts": 1,
                    },
                }
                job_path.write_text(json.dumps(job), encoding="utf-8")
                self.assertTrue(job_pending_submit(job))
                # Still under cap: submit_job_file should attempt (will fail missing workflow later
                # only if not skipped). With pending_only + retryable error it must NOT skip.
                # Exhaust the remaining attempts via record_submit_failure.
                job2 = json.loads(job_path.read_text(encoding="utf-8"))
                self.assertEqual(record_submit_failure(job2, error="fail-2"), "error")
                self.assertEqual(job2["submit"]["attempts"], 2)
                self.assertTrue(job_pending_submit(job2))
                self.assertEqual(record_submit_failure(job2, error="fail-3"), "abandoned")
                self.assertEqual(job2["submit"]["attempts"], 3)
                self.assertTrue(job_abandoned(job2))
                self.assertFalse(job_pending_submit(job2))
                job_path.write_text(json.dumps(job2), encoding="utf-8")
                result = submit_job_file(
                    job_path,
                    server="http://127.0.0.1:8188",
                    data_root=REPO_ROOT / ".data",
                    pending_only=True,
                )
                self.assertTrue(result.get("skipped"))
                self.assertEqual(result.get("reason"), "abandoned")
                self.assertEqual(submit_max_attempts(), 3)
        finally:
            if prev is None:
                os.environ.pop("SHAPE_FACTORY_SUBMIT_MAX_ATTEMPTS", None)
            else:
                os.environ["SHAPE_FACTORY_SUBMIT_MAX_ATTEMPTS"] = prev

    def test_permanent_submit_failure_hint(self) -> None:
        from shape_factory import abandon_submit_failure, is_permanent_submit_failure, job_abandoned

        self.assertTrue(is_permanent_submit_failure("Invalid image file: input/x.png"))
        self.assertTrue(is_permanent_submit_failure("no companion PNG for bindings"))
        self.assertFalse(is_permanent_submit_failure("Connection reset by peer"))
        job: dict = {}
        abandon_submit_failure(job, error="Invalid image file: x.png", server="http://x", attempts=3)
        self.assertTrue(job_abandoned(job))
        self.assertEqual(job["submit"]["attempts"], 3)

    def test_hostify_repo_path_maps_workspace_data(self) -> None:
        from shape_factory import hostify_repo_path, shape_factory_repo_root

        self.assertEqual(shape_factory_repo_root(), REPO_ROOT.resolve())
        mapped = hostify_repo_path("/workspace/.data/pools/FB9_GEX_FACIAL/index.json")
        self.assertEqual(
            mapped,
            (REPO_ROOT / ".data/pools/FB9_GEX_FACIAL/index.json").resolve(),
        )
        mapped_user = hostify_repo_path(
            "/workspace/comfyui_user/default/workflows/generated/catalog/x.json"
        )
        self.assertEqual(
            mapped_user,
            Path(
                "/home/yuji/comfyui-runpod-data/comfyui_user/default/workflows/generated/catalog/x.json"
            ).resolve(),
        )

    def test_hostify_keeps_workspace_paths_in_docker(self) -> None:
        from unittest.mock import patch

        from shape_factory import hostify_job_paths, hostify_repo_path

        raw = "/workspace/.data/shapes/FB9_GEX_FACIAL.shape.yaml"
        with patch("shape_factory._running_in_docker", return_value=True):
            self.assertEqual(str(hostify_repo_path(raw)), raw)
            job = {
                "pools_path": "/workspace/.data/pools/FB9_GEX_FACIAL/pools.yaml",
                "bindings": {
                    "source_video": {"path": "/workspace/output/og/2026-04-03/x.mp4"},
                },
            }
            self.assertFalse(hostify_job_paths(job))
            self.assertTrue(job["pools_path"].startswith("/workspace/"))

    def test_resolve_existing_path_recovers_corrupt_dot_data(self) -> None:
        from shape_factory_map import resolve_existing_path

        data_root = REPO_ROOT / ".data"
        shape = data_root / "shapes" / "FB9_GEX2.shape.yaml"
        if not shape.is_file():
            self.skipTest("FB9_GEX2.shape.yaml missing")
        resolved = resolve_existing_path(
            "/.data/shapes/FB9_GEX2.shape.yaml",
            output_root=Path("/home/yuji/comfyui-runpod-data/output"),
            data_root=data_root,
        )
        self.assertEqual(resolved, shape.resolve())

    def test_hostify_job_paths_rewrites_fields(self) -> None:
        from shape_factory import hostify_job_paths

        job = {
            "pools_path": "/workspace/.data/pools/FB9_GEX_FACIAL/pools.yaml",
            "bindings": {
                "source_video": {"path": "/workspace/output/og/2026-04-03/x.mp4"},
            },
            "deposit": {"index_path": "/workspace/.data/pools/FB9_GEX_FACIAL/index.json"},
        }
        self.assertTrue(hostify_job_paths(job))
        self.assertTrue(str(job["pools_path"]).startswith(str(REPO_ROOT / ".data")))
        self.assertIn("comfyui-runpod-data/output", job["bindings"]["source_video"]["path"])
        self.assertTrue(str(job["deposit"]["index_path"]).startswith(str(REPO_ROOT / ".data")))


if __name__ == "__main__":
    unittest.main()
