#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from patch_vae_decode_tiled import patch_vae_decode_tiled  # noqa: E402


class PatchVaeDecodeTiledTests(unittest.TestCase):
    def test_converts_decode_nodes(self) -> None:
        wf = {
            "nodes": [
                {
                    "id": 1,
                    "type": "VAEDecode",
                    "title": "DECODE: VAE decode",
                    "inputs": [{"name": "samples"}, {"name": "vae"}],
                    "properties": {"Node name for S&R": "VAEDecode"},
                    "widgets_values": [],
                },
                {"id": 2, "type": "VAELoader"},
            ]
        }
        self.assertEqual(patch_vae_decode_tiled(wf), 1)
        node = wf["nodes"][0]
        self.assertEqual(node["type"], "VAEDecodeTiled")
        self.assertEqual(node["widgets_values"], [256, 64, 8, 4])
        self.assertEqual(node["properties"]["Node name for S&R"], "VAEDecodeTiled")
        self.assertEqual(wf["nodes"][1]["type"], "VAELoader")


if __name__ == "__main__":
    unittest.main()
