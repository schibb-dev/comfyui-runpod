"""Tests for shape_factory_pipeline."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "workspace" / "scripts"


class TestShapeFactoryPipeline(unittest.TestCase):
    def setUp(self) -> None:
        import sys

        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))

    def test_resolve_pipeline_path_by_id(self) -> None:
        from shape_factory_pipeline import resolve_pipeline_path

        data_root = REPO / ".data"
        path = resolve_pipeline_path(data_root=data_root, pipeline_id="faceblast-to-gex")
        self.assertTrue(path.is_file())
        self.assertEqual(path.name, "faceblast-to-gex.pipeline.yaml")

    def test_dry_run_faceblast_to_gex(self) -> None:
        from shape_factory_pipeline import resolve_pipeline_path, run_pipeline

        data_root = REPO / ".data"
        pipeline_path = resolve_pipeline_path(data_root=data_root, pipeline_id="faceblast-to-gex")
        result = run_pipeline(
            pipeline_path=pipeline_path,
            limit=1,
            data_root=data_root,
            dry_run=True,
            wait=False,
        )
        self.assertTrue(result.get("ok"))
        steps = result.get("steps") or []
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0].get("step_id"), "faceblast")
        self.assertEqual(steps[1].get("step_id"), "gex")
        self.assertTrue(steps[0].get("ok"))
        self.assertTrue(steps[1].get("ok"))

    def test_run_state_written(self) -> None:
        from shape_factory_pipeline import load_pipeline_run, resolve_pipeline_path, run_pipeline

        data_root = REPO / ".data"
        pipeline_path = resolve_pipeline_path(data_root=data_root, pipeline_id="faceblast-to-gex")
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "run.json"
            result = run_pipeline(
                pipeline_path=pipeline_path,
                limit=1,
                data_root=data_root,
                dry_run=True,
                wait=False,
                run_state_path=state,
            )
            self.assertTrue(result.get("ok"))
            doc = load_pipeline_run(state)
            self.assertIsNotNone(doc)
            assert doc is not None
            self.assertEqual(doc.get("status"), "complete")
            self.assertEqual(len(doc.get("steps") or []), 2)


if __name__ == "__main__":
    unittest.main()
