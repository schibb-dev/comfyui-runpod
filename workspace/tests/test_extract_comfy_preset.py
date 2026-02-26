import unittest
from support import ROOT, SCRIPTS_DIR  # noqa: F401

import comfy_meta_lib as cml  # noqa: E402


class TestExtractComfyPreset(unittest.TestCase):
    def test_extracts_whitelisted_inputs_and_uses_title_in_key(self):
        prompt_obj = {
            "73": {"class_type": "RandomNoise", "inputs": {"noise_seed": 123}, "_meta": {"title": "Noise"}},
            "408": {
                "class_type": "PrimitiveStringMultiline",
                "inputs": {"value": "hello"},
                "_meta": {"title": "Positive"},
            },
            "398": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "frame_rate": 18,
                    "filename_prefix": "output/wip/%date:yyyy-MM-dd%/X",
                    "format": "video/h264-mp4",
                    "pix_fmt": "yuv420p",
                    "crf": 19,
                    "save_metadata": True,
                    "trim_to_audio": False,  # not in KEEP, should be ignored
                },
                "_meta": {"title": "Output"},
            },
        }
        preset = cml.extract_preset(prompt_obj)
        assert preset is not None
        nodes = preset["nodes"]

        self.assertIn("73:Noise", nodes)
        self.assertIn("408:Positive", nodes)
        self.assertIn("398:Output", nodes)

        self.assertEqual(nodes["73:Noise"]["inputs"]["noise_seed"], 123)
        self.assertEqual(nodes["408:Positive"]["inputs"]["value"], "hello")
        self.assertEqual(nodes["398:Output"]["inputs"]["frame_rate"], 18)
        self.assertNotIn("trim_to_audio", nodes["398:Output"]["inputs"])

    def test_skips_connection_like_list_inputs(self):
        # In ComfyUI prompt JSON, linked inputs can appear as lists; we intentionally skip those.
        prompt_obj = {
            "135": {
                "class_type": "CFGGuider",
                "inputs": {"cfg": ["468", 0]},  # connection-like
                "_meta": {"title": "Guider 1"},
            },
            "468": {"class_type": "mxSlider", "inputs": {"Xi": 0, "Xf": 3.0, "isfloatX": 1}, "_meta": {"title": "CFG"}},
        }
        preset = cml.extract_preset(prompt_obj)
        assert preset is not None
        nodes = preset["nodes"]

        # Slider is kept, guider is skipped because cfg isn't a literal
        self.assertIn("468:CFG", nodes)
        self.assertNotIn("135:Guider 1", nodes)

    @unittest.skip("TODO: extend preset extraction to include prompt-schedule nodes like ConditioningSetTimestepRange")
    def test_prompt_schedule_nodes_should_be_included_todo(self):
        prompt_obj = {
            "473": {
                "class_type": "ConditioningSetTimestepRange",
                "inputs": {"start": 0.0, "end": 0.5},
                "_meta": {"title": "Phase1 range"},
            }
        }
        preset = cml.extract_preset(prompt_obj)
        assert preset is not None
        self.assertIn("473:Phase1 range", preset["nodes"])

    @unittest.skip("TODO: extend preset extraction to include VHS_LoadVideo (input video selection) for EXT workflows")
    def test_vhs_loadvideo_should_be_included_todo(self):
        prompt_obj = {
            "463": {
                "class_type": "VHS_LoadVideo",
                "inputs": {"video": "161942_OG_00001.mp4", "force_rate": 0},
                "_meta": {"title": "Load Video File"},
            }
        }
        preset = cml.extract_preset(prompt_obj)
        assert preset is not None
        self.assertIn("463:Load Video File", preset["nodes"])


if __name__ == "__main__":
    unittest.main()

