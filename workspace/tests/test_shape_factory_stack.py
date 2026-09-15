#!/usr/bin/env python3
"""Tests for named generation stacks (UNet + TeaCache coeffs + virt VRAM)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401
import yaml
from shape_factory_stack import (
    apply_shape_stack_api,
    apply_shape_stack_ui,
    apply_stack_ui,
    load_stack,
    stamp_job_stack,
    validate_stack,
)


class ValidateStackTests(unittest.TestCase):
    def test_repo_stacks_load(self) -> None:
        for sid in ("i2v-720p-Q5", "i2v-480p-Q8", "i2v-480p-Q5"):
            doc = load_stack(sid)
            self.assertEqual(doc["stack_id"], sid)
            self.assertEqual(validate_stack(doc), [])

    def test_rejects_mismatched_coeffs(self) -> None:
        doc = {
            "stack_id": "bad",
            "unet_name": "WAN/wan2.1-i2v-14b-720p-Q5_K_M.gguf",
            "teacache_coefficients": "i2v_480",
            "training_size": "720p",
        }
        errs = validate_stack(doc)
        self.assertTrue(any("does not match" in e for e in errs))


class ApplyStackTests(unittest.TestCase):
    def _origin_wf(self) -> dict:
        return {
            "nodes": [
                {
                    "id": 458,
                    "type": "UnetLoaderGGUFDisTorchMultiGPU",
                    "widgets_values": ["old.gguf", "cuda:0", 2, False, ""],
                    "inputs": [
                        {"name": "unet_name", "widget": {"name": "unet_name"}},
                        {"name": "device", "widget": {"name": "device"}},
                        {"name": "virtual_vram_gb", "widget": {"name": "virtual_vram_gb"}},
                    ],
                },
                {
                    "id": 396,
                    "type": "WanVideoTeaCacheKJ",
                    "widgets_values": [0.19, 0.1, 1, "offload_device", "i2v_480"],
                    "inputs": [
                        {"name": "rel_l1_thresh", "widget": {"name": "rel_l1_thresh"}},
                        {"name": "start_percent", "widget": {"name": "start_percent"}},
                        {"name": "end_percent", "widget": {"name": "end_percent"}},
                        {"name": "cache_device", "widget": {"name": "cache_device"}},
                        {"name": "coefficients", "widget": {"name": "coefficients"}},
                    ],
                },
                {
                    "id": 459,
                    "type": "CLIPLoaderGGUFMultiGPU",
                    "widgets_values": ["old-clip.gguf", "wan", "cpu"],
                    "inputs": [{"name": "clip_name", "widget": {"name": "clip_name"}}],
                },
            ]
        }

    def test_apply_720p_q5_by_type(self) -> None:
        wf = self._origin_wf()
        stack = load_stack("i2v-720p-Q5")
        apply_stack_ui(wf, stack)
        unet = next(n for n in wf["nodes"] if n["id"] == 458)
        tea = next(n for n in wf["nodes"] if n["id"] == 396)
        clip = next(n for n in wf["nodes"] if n["id"] == 459)
        self.assertEqual(unet["widgets_values"][0], "WAN/wan2.1-i2v-14b-720p-Q5_K_M.gguf")
        self.assertEqual(unet["widgets_values"][2], 4)
        self.assertEqual(tea["widgets_values"][4], "i2v_720")
        self.assertEqual(clip["widgets_values"][0], "umt5-xxl-encoder-Q5_K_M.gguf")

    def test_480p_q8_skips_gguf_clip_loader(self) -> None:
        wf = self._origin_wf()
        stack = load_stack("i2v-480p-Q8")
        apply_stack_ui(wf, stack)
        unet = next(n for n in wf["nodes"] if n["id"] == 458)
        tea = next(n for n in wf["nodes"] if n["id"] == 396)
        clip = next(n for n in wf["nodes"] if n["id"] == 459)
        self.assertEqual(unet["widgets_values"][0], "wan2.1-i2v-14b-480p-Q8_0.gguf")
        self.assertEqual(tea["widgets_values"][4], "i2v_480")
        self.assertEqual(clip["widgets_values"][0], "old-clip.gguf")

    def test_apply_api_prompt(self) -> None:
        prompt = {
            "458": {
                "class_type": "UnetLoaderGGUFDisTorchMultiGPU",
                "inputs": {"unet_name": "old.gguf", "virtual_vram_gb": 2},
            },
            "396": {"class_type": "WanVideoTeaCacheKJ", "inputs": {"coefficients": "i2v_480"}},
        }
        apply_shape_stack_api(prompt, {"stack": "i2v-720p-Q5"})
        self.assertEqual(prompt["458"]["inputs"]["unet_name"], "WAN/wan2.1-i2v-14b-720p-Q5_K_M.gguf")
        self.assertEqual(prompt["458"]["inputs"]["virtual_vram_gb"], 4)
        self.assertEqual(prompt["396"]["inputs"]["coefficients"], "i2v_720")

    def test_job_override_and_stamp(self) -> None:
        wf = self._origin_wf()
        changes = apply_shape_stack_ui(
            wf,
            {"stack": "i2v-720p-Q5"},
            {"adhoc_overrides": {"stack": "i2v-480p-Q5"}},
        )
        self.assertEqual(changes["stack_id"], "i2v-480p-Q5")
        unet = next(n for n in wf["nodes"] if n["id"] == 458)
        self.assertIn("480p-Q5", unet["widgets_values"][0])
        job: dict = {}
        stamp_job_stack(job, changes["stack"])
        self.assertEqual(job["stack_id"], "i2v-480p-Q5")
        self.assertEqual(job["stack"]["teacache_coefficients"], "i2v_480")

    def test_enrolled_generation_shapes_resolve(self) -> None:
        root = Path(__file__).resolve().parents[2] / ".data" / "shapes"
        for path in sorted(root.glob("*.shape.yaml")):
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            sid = str(doc.get("stack") or "").strip()
            self.assertTrue(sid, msg=f"{path.name} missing stack")
            load_stack(sid)

    def test_load_from_alternate_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "i2v-720p-Q8.yaml"
            p.write_text(
                "\n".join(
                    [
                        "schema_version: comfyui-runpod.stack.v0",
                        "stack_id: i2v-720p-Q8",
                        "training_size: 720p",
                        "unet_name: WAN/wan2.1-i2v-14b-720p-Q8_0.gguf",
                        "teacache_coefficients: i2v_720",
                        "virtual_vram_gb: 4",
                    ]
                ),
                encoding="utf-8",
            )
            doc = load_stack("i2v-720p-Q8", stacks_dir=Path(tmp))
            self.assertEqual(doc["quant"] if "quant" in doc else None, None)
            self.assertEqual(validate_stack(doc), [])


if __name__ == "__main__":
    unittest.main()
