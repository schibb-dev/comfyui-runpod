"""Tests for job-owned Power LoRA stacks."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


ZOOMOUT_READABLE = Path(
    "/home/yuji/comfyui-runpod-data/comfyui_user/default/workflows/generated/catalog/FB8VA5-ZOOMOUT-readable.json"
)


class OwnedLorasExtractTests(unittest.TestCase):
    @unittest.skipUnless(ZOOMOUT_READABLE.is_file(), "ZOOMOUT readable not on this host")
    def test_extract_zoomout_node_416(self) -> None:
        from shape_factory_owned_loras import extract_loras_from_workflow

        wf = json.loads(ZOOMOUT_READABLE.read_text(encoding="utf-8"))
        entries, node_id = extract_loras_from_workflow(wf)
        self.assertEqual(node_id, 416)
        self.assertGreaterEqual(len(entries), 2)
        names = {e["lora"] for e in entries}
        self.assertIn("WAN_dr34mj0b.safetensors", names)
        on = [e for e in entries if e.get("on")]
        self.assertGreaterEqual(len(on), 1)

    def test_patch_and_profile_snowflake(self) -> None:
        from shape_factory_owned_loras import (
            extract_loras_from_workflow,
            owned_loras_to_profile,
            patch_power_lora_widgets,
        )

        wf = {
            "nodes": [
                {
                    "id": 416,
                    "type": "Power Lora Loader (rgthree)",
                    "widgets_values": [
                        {"on": True, "lora": "a.safetensors", "strength": 0.9, "strengthTwo": None},
                        {"on": False, "lora": "b.safetensors", "strength": 0.5, "strengthTwo": None},
                        "",
                    ],
                }
            ],
            "links": [],
        }
        entries, nid = extract_loras_from_workflow(wf)
        self.assertEqual(nid, 416)
        self.assertEqual(len(entries), 2)

        patched = patch_power_lora_widgets(
            wf,
            [
                {"lora": "a.safetensors", "on": True, "strength": 0.7},
                {"lora": "b.safetensors", "on": True, "strength": 0.5},
            ],
            node_id=416,
        )
        self.assertTrue(patched.get("ok"))
        self.assertGreaterEqual(int(patched.get("changed") or 0), 1)
        after, _ = extract_loras_from_workflow(wf)
        self.assertTrue(after[0]["on"])
        self.assertEqual(after[0]["strength"], 0.7)
        self.assertTrue(after[1]["on"])

        job = {
            "loras": {"node_id": 416, "entries": after, "frozen": False},
            "template_path": "",
        }
        # Without a template path, snowflake stays false (nothing to compare).
        profile = owned_loras_to_profile(job, data_root=Path("."))
        self.assertFalse(profile.get("snowflake"))

    def test_promote_overwrite_writes_bak(self) -> None:
        from shape_factory_owned_loras import extract_loras_from_workflow, promote_loras_to_catalog

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tpl = root / "family-readable.json"
            wf = {
                "nodes": [
                    {
                        "id": 10,
                        "type": "Power Lora Loader (rgthree)",
                        "widgets_values": [
                            {"on": True, "lora": "a.safetensors", "strength": 1.0, "strengthTwo": None},
                            {"on": False, "lora": "b.safetensors", "strength": 0.5, "strengthTwo": None},
                        ],
                    }
                ],
                "links": [],
            }
            tpl.write_text(json.dumps(wf), encoding="utf-8")
            job = {
                "template_path": str(tpl),
                "loras": {
                    "node_id": 10,
                    "entries": [
                        {"lora": "a.safetensors", "on": False, "strength": 0.2},
                        {"lora": "b.safetensors", "on": True, "strength": 0.8},
                    ],
                },
            }
            res = promote_loras_to_catalog(data_root=root, job=job, mode="overwrite")
            self.assertTrue(res.get("ok"), res)
            self.assertTrue(Path(str(res.get("bak_path") or "")).is_file())
            updated = json.loads(tpl.read_text(encoding="utf-8"))
            entries, _ = extract_loras_from_workflow(updated)
            self.assertFalse(entries[0]["on"])
            self.assertEqual(entries[0]["strength"], 0.2)
            self.assertTrue(entries[1]["on"])


class OwnedLorasApiPromptTests(unittest.TestCase):
    def test_extract_from_api_prompt(self) -> None:
        from shape_factory_owned_loras import extract_loras_from_api_prompt

        prompt = {
            "416": {
                "class_type": "Power Lora Loader (rgthree)",
                "inputs": {
                    "lora_1": {"on": True, "lora": "x.safetensors", "strength": 0.9},
                    "lora_2": {"on": False, "lora": "y.safetensors", "strength": 0.4},
                },
            }
        }
        entries, nid = extract_loras_from_api_prompt(prompt)
        self.assertEqual(nid, "416")
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["lora"], "x.safetensors")


if __name__ == "__main__":
    unittest.main()
