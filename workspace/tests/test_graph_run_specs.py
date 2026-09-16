#!/usr/bin/env python3
"""Tests for compact generation-spec extraction and filename suffix."""

from __future__ import annotations

from pathlib import Path
import unittest

import support  # noqa: F401
import yaml
from graph_run_specs import (
    append_run_spec_to_prefix,
    apply_run_spec_suffix_to_prompt,
    apply_run_spec_suffix_to_workflow,
    extract_run_spec,
    extract_run_spec_from_template,
    stamp_prefix_with_graph_spec,
    strip_run_spec_suffix,
)


def _api_prompt(*, unet: str, width: int, height: int, duration: float, steps: int, tea: float, vv: float, coeffs: str = "i2v_720") -> dict:
    return {
        "82": {
            "class_type": "mxSlider",
            "inputs": {"Xi": steps, "Xf": float(steps), "isfloatX": 0},
            "_meta": {"title": "Steps"},
        },
        "83": {
            "class_type": "mxSlider2D",
            "inputs": {
                "Xi": width,
                "Xf": float(width),
                "Yi": height,
                "Yf": float(height),
                "isfloatX": 0,
                "isfloatY": 0,
            },
            "_meta": {"title": "Size"},
        },
        "126": {
            "class_type": "mxSlider",
            "inputs": {"Xi": 0, "Xf": tea, "isfloatX": 1},
            "_meta": {"title": "Tea cache"},
        },
        "133": {
            "class_type": "WanImageToVideo",
            "inputs": {"width": ["83", 0], "height": ["83", 1], "length": 105},
        },
        "396": {
            "class_type": "WanVideoTeaCacheKJ",
            "inputs": {"rel_l1_thresh": ["126", 0], "coefficients": coeffs},
        },
        "398": {
            "class_type": "VHS_VideoCombine",
            "inputs": {
                "filename_prefix": "og/2026-09-15/experiments/x-kneel/X-KNEEL-FB9__pp-catalog-default__still-abc__000_adhoc_ui1",
                "save_output": True,
            },
        },
        "426": {
            "class_type": "mxSlider",
            "inputs": {"Xi": 6, "Xf": duration, "isfloatX": 1},
            "_meta": {"title": "Duration"},
        },
        "458": {
            "class_type": "UnetLoaderGGUFDisTorchMultiGPU",
            "inputs": {"unet_name": unet, "device": "cuda:0", "virtual_vram_gb": vv},
            "_meta": {"title": "Model"},
        },
        "468": {
            "class_type": "mxSlider",
            "inputs": {"Xi": 3, "Xf": 3.0, "isfloatX": 1},
            "_meta": {"title": "CFG"},
        },
        "77": {
            "class_type": "BasicScheduler",
            "inputs": {"scheduler": "simple", "steps": ["82", 0], "denoise": 0.87},
        },
        "76": {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": "euler"},
        },
    }


class GraphRunSpecsTests(unittest.TestCase):
    def test_run_004_style_api_prompt(self) -> None:
        prompt = _api_prompt(
            unet="WAN/wan2.1-i2v-14b-720p-Q5_K_M.gguf",
            width=576,
            height=1024,
            duration=6.5,
            steps=28,
            tea=0.25,
            vv=4.0,
        )
        spec = extract_run_spec(prompt)
        self.assertEqual(spec["unet_family"], "720p")
        self.assertEqual(spec["quant"], "Q5")
        self.assertEqual(spec["width"], 576)
        self.assertEqual(spec["height"], 1024)
        self.assertEqual(spec["duration_sec"], 6.5)
        self.assertEqual(spec["steps"], 28)
        self.assertEqual(spec["teacache"], 0.25)
        self.assertEqual(spec["virtual_vram_gb"], 4.0)
        self.assertEqual(spec["abbrev"], "720p-Q5 virt4.0 576×1024 6.5s 28step cfg3.0 den0.87 euler simple T.25")
        self.assertEqual(spec["spec_model"], "720p-Q5 virt4.0 576×1024")
        self.assertEqual(spec["spec_params"], "6.5s 28step cfg3.0 den0.87")
        self.assertEqual(spec["spec_sampler"], "euler simple T.25")
        self.assertIn("WAN/wan2.1-i2v-14b-720p-Q5_K_M.gguf", spec["title"])
        self.assertIn("i2v_720", spec["title"])
        self.assertEqual(spec["fs_token"], "720p_Q5_576x1024_6p5s_28st_Tea25_vv4")

    def test_480p_q8_variant(self) -> None:
        prompt = _api_prompt(
            unet="wan2.1-i2v-14b-480p-Q8_0.gguf",
            width=576,
            height=1024,
            duration=6.5,
            steps=28,
            tea=0.25,
            vv=4.0,
            coeffs="i2v_480",
        )
        spec = extract_run_spec(prompt)
        self.assertEqual(spec["unet_family"], "480p")
        self.assertEqual(spec["quant"], "Q8")
        self.assertTrue(spec["abbrev"].startswith("480p-Q8"))
        self.assertIn("i2v_480", spec["title"])

    def test_tea_off_and_vv_zero_omitted(self) -> None:
        prompt = _api_prompt(
            unet="WAN/wan2.1-i2v-14b-720p-fp8_e4m3fn.safetensors",
            width=432,
            height=768,
            duration=5,
            steps=20,
            tea=0,
            vv=0,
        )
        spec = extract_run_spec(prompt)
        self.assertEqual(spec["quant"], "fp8")
        self.assertIn("Toff", spec["spec_sampler"])
        self.assertNotIn("T.25", spec["spec_params"])
        self.assertNotIn("virt", spec["spec_model"])
        self.assertEqual(spec["steps"], 20)

    def test_litegraph_size_steps_tea(self) -> None:
        wf = {
            "nodes": [
                {
                    "id": 83,
                    "type": "mxSlider2D",
                    "title": "Size",
                    "widgets_values": [432, 432, 768, 768, 0, 0],
                },
                {
                    "id": 82,
                    "type": "mxSlider",
                    "title": "Steps",
                    "widgets_values": [20, 20, 0],
                },
                {
                    "id": 126,
                    "type": "mxSlider",
                    "title": "Tea cache",
                    "widgets_values": [0.2, 0.2, 1],
                },
                {
                    "id": 458,
                    "type": "UnetLoaderGGUFDisTorchMultiGPU",
                    "title": "Model",
                    "widgets_values": ["WAN/wan2.1-i2v-14b-720p-Q5_K_M.gguf", "cuda:0", 4.0, False, ""],
                },
            ]
        }
        spec = extract_run_spec(wf)
        self.assertEqual(spec["abbrev"], "720p-Q5 virt4.0 432×768 20step T.2")
        self.assertEqual(spec["spec_model"], "720p-Q5 virt4.0 432×768")
        self.assertEqual(spec["spec_params"], "20step")
        self.assertEqual(spec["spec_sampler"], "T.2")

    def test_suffix_roundtrip_and_job_key_stem(self) -> None:
        key = "X-KNEEL-FB9__pp-catalog-default__still-abc__000_adhoc_ui1"
        prefix = f"og/2026-09-15/X-Kneel-FB9_shape/{key}"
        stamped = append_run_spec_to_prefix(prefix, "720p_Q5_576x1024_6p5s_28st_Tea25_vv4")
        self.assertTrue(stamped.endswith("__rs-720p_Q5_576x1024_6p5s_28st_Tea25_vv4"))
        self.assertEqual(strip_run_spec_suffix(stamped), prefix)
        again = append_run_spec_to_prefix(stamped, "720p_Q5_576x1024_6p5s_28st_Tea25_vv4")
        self.assertEqual(again, stamped)
        self.assertEqual(strip_run_spec_suffix(key + "__rs-720p_Q5_x"), key)

    def test_apply_suffix_to_final_combine_skips_preview(self) -> None:
        prompt = _api_prompt(
            unet="WAN/wan2.1-i2v-14b-720p-Q5_K_M.gguf",
            width=576,
            height=1024,
            duration=6.5,
            steps=28,
            tea=0.25,
            vv=4.0,
        )
        prompt["399"] = {
            "class_type": "VHS_VideoCombine",
            "inputs": {"filename_prefix": "og/tmp/clip_PREVIEW", "save_output": True},
        }
        changes = apply_run_spec_suffix_to_prompt(prompt)
        self.assertTrue(any("398" in c for c in changes))
        final = prompt["398"]["inputs"]["filename_prefix"]
        self.assertIn("__rs-", final)
        self.assertTrue(final.startswith("og/2026-09-15/experiments/x-kneel/X-KNEEL-FB9__"))
        self.assertEqual(prompt["399"]["inputs"]["filename_prefix"], "og/tmp/clip_PREVIEW")

    def test_family_template_spec_follows_named_stack(self) -> None:
        data = Path(__file__).resolve().parents[2] / ".data"
        kneel = yaml.safe_load((data / "shapes" / "X-KNEEL-FB9.shape.yaml").read_text(encoding="utf-8"))
        spec = extract_run_spec_from_template(kneel, data_root=data)
        self.assertTrue(str(spec.get("spec_model") or "").startswith("720p-Q5"), spec.get("spec_model"))
        self.assertEqual(spec.get("teacache_coefficients"), "i2v_720")
        gex = yaml.safe_load((data / "shapes" / "FB9_GEX2.shape.yaml").read_text(encoding="utf-8"))
        spec2 = extract_run_spec_from_template(gex, data_root=data)
        self.assertTrue(str(spec2.get("spec_model") or "").startswith("480p-Q8"), spec2.get("spec_model"))
        self.assertEqual(spec2.get("teacache_coefficients"), "i2v_480")

    def test_template_spec_follows_job_stack_override(self) -> None:
        data = Path(__file__).resolve().parents[2] / ".data"
        kneel = yaml.safe_load((data / "shapes" / "X-KNEEL-FB9.shape.yaml").read_text(encoding="utf-8"))
        spec = extract_run_spec_from_template(
            kneel,
            data_root=data,
            job={"adhoc_overrides": {"stack": "i2v-480p-Q5"}},
        )
        self.assertTrue(str(spec.get("spec_model") or "").startswith("480p-Q5"), spec.get("spec_model"))
        self.assertEqual(spec.get("teacache_coefficients"), "i2v_480")
        self.assertIn("480p-Q5", str(spec.get("unet_name") or ""))

    def test_suffix_follows_stacked_unet_not_catalog(self) -> None:
        from shape_factory_stack import apply_stack_api, load_stack

        prompt = _api_prompt(
            unet="WAN/wan2.1-i2v-14b-720p-Q5_K_M.gguf",
            width=576,
            height=1024,
            duration=6.5,
            steps=28,
            tea=0.25,
            vv=4.0,
        )
        apply_stack_api(prompt, load_stack("i2v-480p-Q8"))
        apply_run_spec_suffix_to_prompt(prompt)
        final = prompt["398"]["inputs"]["filename_prefix"]
        self.assertIn("__rs-480p_Q8", final)
        self.assertNotIn("720p_Q5", final)
        spec = extract_run_spec(prompt)
        self.assertTrue(str(spec.get("spec_model") or "").startswith("480p-Q8"), spec.get("spec_model"))
        self.assertEqual(spec.get("teacache_coefficients"), "i2v_480")

    def test_workflow_suffix_and_stamp_follow_litegraph_stack(self) -> None:
        from shape_factory_stack import apply_stack_ui, load_stack

        wf = {
            "nodes": [
                {
                    "id": 458,
                    "type": "UnetLoaderGGUFDisTorchMultiGPU",
                    "title": "Model",
                    "widgets_values": ["WAN/wan2.1-i2v-14b-720p-Q5_K_M.gguf", "cuda:0", 4.0, False, ""],
                },
                {
                    "id": 398,
                    "type": "VHS_VideoCombine",
                    "widgets_values": {
                        "filename_prefix": "og/2026-09-15/hourly/jobkey",
                        "save_output": True,
                        "save_metadata": True,
                    },
                },
            ]
        }
        apply_stack_ui(wf, load_stack("i2v-480p-Q8"))
        prefix = stamp_prefix_with_graph_spec("og/2026-09-15/hourly/jobkey", wf)
        self.assertIn("__rs-480p_Q8", prefix)
        wf["nodes"][1]["widgets_values"]["filename_prefix"] = prefix
        apply_run_spec_suffix_to_workflow(wf)
        self.assertEqual(wf["nodes"][1]["widgets_values"]["filename_prefix"], prefix)
        spec = extract_run_spec(wf)
        self.assertTrue(str(spec.get("spec_model") or "").startswith("480p-Q8"), spec.get("spec_model"))


if __name__ == "__main__":
    unittest.main()
