#!/usr/bin/env python3
"""Tests for LoadImage staging into input/_factory/."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401


class TestLoadImageStage(unittest.TestCase):
    def test_stage_outside_input_uses_content_hash_under_factory(self) -> None:
        from shape_factory import (
            FACTORY_LOAD_IMAGE_SUBDIR,
            comfy_load_image_relpath,
            stage_load_image_for_comfy,
        )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_root = root / "data"
            input_root = data_root / "input"
            output_root = data_root / "output" / "og" / "2026-04-14"
            input_root.mkdir(parents=True)
            output_root.mkdir(parents=True)
            src = output_root / "FB9_GEX2_FACIAL_2026-04-14_00002.png"
            payload = b"\x89PNG\r\n\x1a\nfake-identity-still"
            src.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()

            widget, warns = stage_load_image_for_comfy(src, input_root)
            self.assertEqual(widget, f"{FACTORY_LOAD_IMAGE_SUBDIR}/{digest}.png")
            self.assertTrue(warns)
            staged = input_root / FACTORY_LOAD_IMAGE_SUBDIR / f"{digest}.png"
            self.assertTrue(staged.is_file())
            self.assertEqual(staged.read_bytes(), payload)

            # Idempotent second stage (no duplicate / no error).
            widget2, warns2 = stage_load_image_for_comfy(src, input_root)
            self.assertEqual(widget2, widget)
            self.assertEqual(warns2, [])

            # comfy_load_image_relpath stages via data_root/input when bind dir is absent.
            old = os.environ.pop("COMFYUI_BIND_INPUT_DIR", None)
            try:
                rel, warn = comfy_load_image_relpath(src, data_root)
            finally:
                if old is not None:
                    os.environ["COMFYUI_BIND_INPUT_DIR"] = old
            self.assertEqual(rel, widget)
            self.assertIsNotNone(warn)
            self.assertIn("_factory/", rel)

    def test_stage_prefers_embedded_sha_in_basename(self) -> None:
        from shape_factory import FACTORY_LOAD_IMAGE_SUBDIR, stage_load_image_for_comfy

        sha = "a" * 64
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_root = root / "input"
            input_root.mkdir()
            src = root / "elsewhere" / f"SSS{sha}zzzz.jpeg"
            src.parent.mkdir()
            src.write_bytes(b"jpeg-bytes")
            widget, _ = stage_load_image_for_comfy(src, input_root)
            self.assertEqual(widget, f"{FACTORY_LOAD_IMAGE_SUBDIR}/{sha}.jpeg")
            self.assertTrue((input_root / FACTORY_LOAD_IMAGE_SUBDIR / f"{sha}.jpeg").is_file())

    def test_stage_replaces_truncated_dest(self) -> None:
        from shape_factory import FACTORY_LOAD_IMAGE_SUBDIR, stage_load_image_for_comfy

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_root = root / "input"
            input_root.mkdir()
            src = root / "og.png"
            payload = b"\x89PNG\r\n\x1a\n" + (b"x" * 80)
            src.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            dest = input_root / FACTORY_LOAD_IMAGE_SUBDIR / f"{digest}.png"
            dest.parent.mkdir()
            dest.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 19)
            widget, _ = stage_load_image_for_comfy(src, input_root)
            self.assertEqual(widget, f"{FACTORY_LOAD_IMAGE_SUBDIR}/{digest}.png")
            self.assertEqual(dest.read_bytes(), payload)

    def test_stage_missing_source_raises(self) -> None:
        from shape_factory import stage_load_image_for_comfy

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_root = root / "input"
            input_root.mkdir()
            missing = root / "nope.png"
            with self.assertRaises(FileNotFoundError):
                stage_load_image_for_comfy(missing, input_root)

    def test_path_already_under_input_unchanged(self) -> None:
        from shape_factory import comfy_load_image_relpath

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_root = root / "data"
            input_root = data_root / "input"
            input_root.mkdir(parents=True)
            still = input_root / "already_here.png"
            still.write_bytes(b"x")
            old = os.environ.get("COMFYUI_BIND_INPUT_DIR")
            os.environ["COMFYUI_BIND_INPUT_DIR"] = str(input_root)
            try:
                rel, warn = comfy_load_image_relpath(still, data_root)
            finally:
                if old is None:
                    os.environ.pop("COMFYUI_BIND_INPUT_DIR", None)
                else:
                    os.environ["COMFYUI_BIND_INPUT_DIR"] = old
            self.assertEqual(rel, "already_here.png")
            self.assertIsNone(warn)

    def test_vision_v1_rewrites_to_gallery_twin(self) -> None:
        from shape_factory import comfy_load_image_relpath

        sha = "e" * 64
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_root = root / "data"
            input_root = data_root / "input"
            vision = input_root / "vision_v1"
            vision.mkdir(parents=True)
            gallery = input_root / f"000-{sha}.jpeg"
            gallery.write_bytes(b"same-bytes")
            scratch = vision / f"{sha}.jpg"
            scratch.write_bytes(b"same-bytes")
            old = os.environ.get("COMFYUI_BIND_INPUT_DIR")
            os.environ["COMFYUI_BIND_INPUT_DIR"] = str(input_root)
            try:
                rel, warn = comfy_load_image_relpath(scratch, data_root)
            finally:
                if old is None:
                    os.environ.pop("COMFYUI_BIND_INPUT_DIR", None)
                else:
                    os.environ["COMFYUI_BIND_INPUT_DIR"] = old
            self.assertEqual(rel, gallery.name)
            self.assertIsNotNone(warn)
            self.assertIn("vision_v1", str(warn))

    def test_vision_v1_without_gallery_twin_stages_factory(self) -> None:
        from shape_factory import FACTORY_LOAD_IMAGE_SUBDIR, comfy_load_image_relpath

        sha = "f" * 64
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_root = root / "data"
            input_root = data_root / "input"
            vision = input_root / "vision_v1"
            vision.mkdir(parents=True)
            scratch = vision / f"{sha}.jpg"
            scratch.write_bytes(b"only-scratch")
            old = os.environ.get("COMFYUI_BIND_INPUT_DIR")
            os.environ["COMFYUI_BIND_INPUT_DIR"] = str(input_root)
            try:
                rel, warn = comfy_load_image_relpath(scratch, data_root)
            finally:
                if old is None:
                    os.environ.pop("COMFYUI_BIND_INPUT_DIR", None)
                else:
                    os.environ["COMFYUI_BIND_INPUT_DIR"] = old
            self.assertEqual(rel, f"{FACTORY_LOAD_IMAGE_SUBDIR}/{sha}.jpg")
            self.assertTrue((input_root / FACTORY_LOAD_IMAGE_SUBDIR / f"{sha}.jpg").is_file())
            self.assertIsNotNone(warn)

    def test_rewrite_job_scratch_source_still_prefers_gallery(self) -> None:
        from shape_factory import rewrite_job_scratch_source_stills

        sha = "a1" + ("b" * 62)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_root = root / "input"
            vision = input_root / "vision_v1"
            vision.mkdir(parents=True)
            gallery = input_root / f"FAV{sha}.jpeg"
            gallery.write_bytes(b"px")
            scratch = vision / f"{sha}.jpg"
            scratch.write_bytes(b"px")
            job_path = root / "jobs" / "FAM" / "one.job.json"
            job_path.parent.mkdir(parents=True)
            job_path.write_text(
                json.dumps(
                    {
                        "bindings": {
                            "source_still": {
                                "binding_type": "load_image",
                                "path": str(scratch),
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            stats = rewrite_job_scratch_source_stills(
                jobs_dir=root / "jobs",
                input_root=input_root,
                apply=True,
            )
            self.assertEqual(stats["rewritten"], 1)
            doc = json.loads(job_path.read_text(encoding="utf-8"))
            self.assertEqual(doc["bindings"]["source_still"]["path"], str(gallery.resolve()))


if __name__ == "__main__":
    unittest.main()
