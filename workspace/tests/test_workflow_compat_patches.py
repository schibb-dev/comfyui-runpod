#!/usr/bin/env python3
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from workflow_compat_patches import apply_workflow_compat_patches, load_type_mappings


class WorkflowCompatPatchesTest(unittest.TestCase):
    def test_load_image_with_filename_renamed_and_trimmed(self) -> None:
        workflow = {
            "nodes": [
                {
                    "id": 88,
                    "type": "LoadImageWithFilename|pysssss",
                    "properties": {"Node name for S&R": "LoadImageWithFilename|pysssss"},
                    "outputs": [
                        {"name": "IMAGE", "type": "IMAGE"},
                        {"name": "MASK", "type": "MASK"},
                        {"name": "FILENAME", "type": "STRING"},
                    ],
                    "widgets_values": ["foo.png"],
                }
            ]
        }
        patched, records = apply_workflow_compat_patches(workflow, object_info={"LoadImage": {}})
        self.assertEqual(len(records), 1)
        node = patched["nodes"][0]
        self.assertEqual(node["type"], "LoadImage")
        self.assertEqual(len(node["outputs"]), 2)
        self.assertEqual(node["widgets_values"], ["foo.png", "image"])

    def test_map_includes_pysssss_load_image(self) -> None:
        mappings = load_type_mappings()
        self.assertEqual(mappings.get("LoadImageWithFilename|pysssss"), "LoadImage")


if __name__ == "__main__":
    unittest.main()
