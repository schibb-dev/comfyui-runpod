#!/usr/bin/env python3
"""Tests for shape_factory_prompt_recover and replay prompt recovery."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shape_factory_prompt_recover import (
    extract_prompt_texts_from_ui_workflow,
    recover_prompt_profile_for_job,
    resolve_or_recover_prompt_profile_binding,
    write_replay_prompt_profile,
)


def _shape_with_prompt_nodes(pos_id: int = 380, neg_id: int = 17) -> dict:
    return {
        "family_slug": "TEST_FAM",
        "requires": [
            {
                "slot": "prompt_profile",
                "binding": {
                    "type": "prompt_bundle",
                    "positive": {"node_id": pos_id, "widget_index": 0},
                    "negative": {"node_id": neg_id, "widget_index": 0},
                },
            }
        ],
    }


def _ui_workflow(pos: str, neg: str, pos_id: int = 380, neg_id: int = 17) -> dict:
    return {
        "nodes": [
            {"id": pos_id, "type": "Text Multiline", "widgets_values": [pos]},
            {"id": neg_id, "type": "CLIPTextEncode", "widgets_values": [neg]},
        ]
    }


class PromptRecoverTests(unittest.TestCase):
    def test_extract_prompt_texts(self) -> None:
        shape = _shape_with_prompt_nodes()
        wf = _ui_workflow("hello positive", "neg text")
        pos, neg = extract_prompt_texts_from_ui_workflow(wf, shape)
        self.assertEqual(pos, "hello positive")
        self.assertEqual(neg, "neg text")

    def test_write_replay_profile(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = write_replay_prompt_profile(
                family="TEST_FAM",
                data_root=root,
                label="job1",
                positive="pos",
                negative="neg",
            )
            self.assertTrue(path.is_file())
            self.assertIn("_replay", str(path))
            doc = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(doc["positive"], "pos")
            self.assertEqual(doc["negative"], "neg")

    def test_recover_from_job_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wf_path = root / "wf.json"
            wf_path.write_text(json.dumps(_ui_workflow("recovered pos", "recovered neg")), encoding="utf-8")
            job = {
                "job_key": "TEST_FAM::run-1",
                "family_slug": "TEST_FAM",
                "generated_workflow_path": str(wf_path),
            }
            path = recover_prompt_profile_for_job(job, shape=_shape_with_prompt_nodes(), data_root=root)
            self.assertTrue(path.is_file())
            doc = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(doc["positive"], "recovered pos")

    def test_recover_hard_error_without_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            job = {"job_key": "x", "family_slug": "TEST_FAM"}
            with self.assertRaises(ValueError):
                recover_prompt_profile_for_job(job, shape=_shape_with_prompt_nodes(), data_root=root)

    def test_resolve_or_recover_missing_profile(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wf_path = root / "wf.json"
            wf_path.write_text(json.dumps(_ui_workflow("from wf", "")), encoding="utf-8")
            job = {
                "job_key": "TEST_FAM::run-2",
                "family_slug": "TEST_FAM",
                "generated_workflow_path": str(wf_path),
            }
            bindings = {"prompt_profile": str(root / "pools" / "TEST_FAM" / "prompts" / "gone.json")}
            out, recovered = resolve_or_recover_prompt_profile_binding(
                bindings,
                job=job,
                shape=_shape_with_prompt_nodes(),
                data_root=root,
                family="TEST_FAM",
            )
            self.assertIsNotNone(recovered)
            self.assertTrue(Path(out["prompt_profile"]).is_file())

    def test_resolve_keeps_existing_profile(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            existing = root / "ok.json"
            existing.write_text(json.dumps({"positive": "x", "negative": ""}), encoding="utf-8")
            out, recovered = resolve_or_recover_prompt_profile_binding(
                {"prompt_profile": str(existing)},
                job=None,
                shape=_shape_with_prompt_nodes(),
                data_root=root,
                family="TEST_FAM",
            )
            self.assertIsNone(recovered)
            self.assertEqual(Path(out["prompt_profile"]), existing.resolve())


class ReplayPromptRecoveryTests(unittest.TestCase):
    def test_replay_recovers_missing_prompt_before_queue(self) -> None:
        from shape_factory_queue import replay_from_request_body

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_root = root / ".data"
            shapes = data_root / "shapes"
            shapes.mkdir(parents=True)
            shape = _shape_with_prompt_nodes()
            shape["family_slug"] = "TEST_FAM"
            shape["template"] = "template.json"
            (shapes / "TEST_FAM.shape.yaml").write_text(
                "family_slug: TEST_FAM\n"
                "template: template.json\n"
                "requires:\n"
                "  - slot: prompt_profile\n"
                "    binding:\n"
                "      type: prompt_bundle\n"
                "      positive: {node_id: 380, widget_index: 0}\n"
                "      negative: {node_id: 17, widget_index: 0}\n",
                encoding="utf-8",
            )
            (data_root / "pools" / "TEST_FAM").mkdir(parents=True)
            (data_root / "pools" / "TEST_FAM" / "pools.yaml").write_text("pools: {}\n", encoding="utf-8")
            (data_root / "template.json").write_text("{}", encoding="utf-8")

            wf_path = data_root / "wf.json"
            wf_path.write_text(json.dumps(_ui_workflow("replay pos", "replay neg")), encoding="utf-8")
            jobs_dir = data_root / "shape_factory" / "jobs" / "TEST_FAM"
            jobs_dir.mkdir(parents=True)
            job = {
                "job_key": "TEST_FAM::old",
                "family_slug": "TEST_FAM",
                "generated_workflow_path": str(wf_path),
                "bindings": {
                    "prompt_profile": {"path": str(data_root / "pools" / "TEST_FAM" / "prompts" / "missing.json")},
                },
                "submit": {"outputs": []},
            }
            (jobs_dir / "old.job.json").write_text(json.dumps(job), encoding="utf-8")

            captured: dict = {}

            def fake_queue(**kwargs):
                captured.update(kwargs)
                return {"ok": True, "job_key": "TEST_FAM::new", "family_slug": "TEST_FAM"}

            with mock.patch("shape_factory_queue.resolve_shape_factory_data_root", return_value=data_root):
                with mock.patch("shape_factory_queue.queue_shape_factory_combo", side_effect=fake_queue):
                    with mock.patch(
                        "shape_factory_queue._find_job_doc",
                        return_value=(job, jobs_dir / "old.job.json"),
                    ):
                        out = replay_from_request_body(
                            {"job_key": "TEST_FAM::old", "dry_run": True},
                            repo_root=root,
                            workspace_root=root / "workspace",
                            output_root=root / "output",
                            comfy_server="http://127.0.0.1:8188",
                        )
            self.assertTrue(out.get("ok"))
            self.assertTrue(out.get("prompt_profile_recovered"))
            prompt_path = Path(captured["bindings"]["prompt_profile"])
            self.assertTrue(prompt_path.is_file())
            self.assertEqual(json.loads(prompt_path.read_text())["positive"], "replay pos")

    def test_replay_errors_when_recovery_impossible(self) -> None:
        from shape_factory_queue import replay_from_request_body

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_root = root / ".data"
            shapes = data_root / "shapes"
            shapes.mkdir(parents=True)
            (shapes / "TEST_FAM.shape.yaml").write_text(
                "family_slug: TEST_FAM\n"
                "requires:\n"
                "  - slot: prompt_profile\n"
                "    binding:\n"
                "      type: prompt_bundle\n"
                "      positive: {node_id: 380, widget_index: 0}\n"
                "      negative: {node_id: 17, widget_index: 0}\n",
                encoding="utf-8",
            )
            job = {
                "job_key": "TEST_FAM::old",
                "family_slug": "TEST_FAM",
                "bindings": {
                    "prompt_profile": {"path": str(data_root / "gone.json")},
                },
            }
            with mock.patch("shape_factory_queue.resolve_shape_factory_data_root", return_value=data_root):
                with mock.patch("shape_factory_queue._find_job_doc", return_value=(job, Path("x"))):
                    with self.assertRaises(ValueError) as ctx:
                        replay_from_request_body(
                            {"job_key": "TEST_FAM::old"},
                            repo_root=root,
                            workspace_root=root / "workspace",
                            output_root=root / "output",
                            comfy_server="http://127.0.0.1:8188",
                        )
            self.assertIn("cannot recover prompt_profile", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
