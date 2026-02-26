import unittest
from support import ROOT, SCRIPTS_DIR  # noqa: F401

import apply_comfy_preset as acp  # noqa: E402


class TestApplyComfyPreset(unittest.TestCase):
    def test_applies_by_id_and_updates_widgets(self):
        workflow = {
            "nodes": [
                {"id": 73, "type": "RandomNoise", "title": "Noise", "widgets_values": [0, "fixed"]},
                {"id": 408, "type": "PrimitiveStringMultiline", "title": "Positive", "widgets_values": [""]},
                {"id": 398, "type": "VHS_VideoCombine", "title": "Output", "widgets_values": {"videopreview": {"x": 1}}},
            ]
        }
        preset = {
            "nodes": {
                "73:Noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": 123}},
                "408:Positive": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "hello"}},
                "398:Output": {
                    "class_type": "VHS_VideoCombine",
                    "inputs": {
                        "frame_rate": 24,
                        "filename_prefix": "output/wip/%date:yyyy-MM-dd%/X",
                        "format": "video/h264-mp4",
                        "crf": 19,
                        "pix_fmt": "yuv420p",
                        "save_metadata": True,
                    },
                },
            }
        }
        merged, stats = acp.apply_preset(workflow, preset)
        nodes = {n["id"]: n for n in merged["nodes"]}

        self.assertEqual(nodes[73]["widgets_values"][0], 123)
        self.assertEqual(nodes[408]["widgets_values"][0], "hello")

        wv = nodes[398]["widgets_values"]
        self.assertEqual(wv["frame_rate"], 24)
        self.assertEqual(wv["pix_fmt"], "yuv420p")
        self.assertNotIn("videopreview", wv)

        self.assertEqual(stats["applied"], 3)
        self.assertEqual(stats["missing_nodes"], 0)

    def test_title_fallback_used_when_id_missing(self):
        workflow = {
            "nodes": [
                # ID changed, title stayed canonical
                {"id": 999, "type": "RandomNoise", "title": "Noise", "widgets_values": [0, "fixed"]},
            ]
        }
        preset = {"nodes": {"73:Noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": 321}}}}
        merged, stats = acp.apply_preset(workflow, preset)
        self.assertEqual(merged["nodes"][0]["widgets_values"][0], 321)
        self.assertEqual(stats["title_fallback_used"], 1)
        self.assertEqual(stats["missing_nodes"], 0)

    def test_ambiguous_title_match_is_not_applied(self):
        workflow = {
            "nodes": [
                {"id": 100, "type": "RandomNoise", "title": "Noise", "widgets_values": [0, "fixed"]},
                {"id": 101, "type": "RandomNoise", "title": "Noise", "widgets_values": [0, "fixed"]},
            ]
        }
        preset = {"nodes": {"73:Noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": 555}}}}
        merged, stats = acp.apply_preset(workflow, preset)
        self.assertEqual(merged["nodes"][0]["widgets_values"][0], 0)
        self.assertEqual(merged["nodes"][1]["widgets_values"][0], 0)
        self.assertEqual(stats["ambiguous_title_matches"], 1)

    @unittest.skip("TODO: add apply support for VHS_LoadVideo widgets (EXT workflows)")
    def test_apply_vhs_loadvideo_todo(self):
        workflow = {
            "nodes": [{"id": 463, "type": "VHS_LoadVideo", "title": "Load Video File", "widgets_values": {"video": ""}}]
        }
        preset = {"nodes": {"463:Load Video File": {"class_type": "VHS_LoadVideo", "inputs": {"video": "x.mp4"}}}}
        merged, _ = acp.apply_preset(workflow, preset)
        self.assertEqual(merged["nodes"][0]["widgets_values"]["video"], "x.mp4")


if __name__ == "__main__":
    unittest.main()

