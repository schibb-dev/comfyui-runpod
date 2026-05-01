import unittest
from copy import deepcopy
from support import ROOT, SCRIPTS_DIR  # noqa: F401

import clean_comfy_workflow as ccw  # noqa: E402


class TestCleanComfyWorkflow(unittest.TestCase):
    def test_strips_top_level_extra(self):
        obj = {"nodes": [], "extra": {"ds": {"scale": 1.23}}}
        cleaned = ccw.clean_workflow(obj, canonicalize_titles=False)
        self.assertNotIn("extra", cleaned)

    def test_cleans_nodes_and_common_widget_types(self):
        workflow = {
            "nodes": [
                {
                    "id": 88,
                    "type": "LoadImage",
                    "title": "Load Image",
                    "properties": {"ver": "0.3.0", "Node name for S&R": "LoadImage"},
                    "widgets_values": ["foo.png", "image"],
                    "selected": True,
                },
                {
                    "id": 408,
                    "type": "PrimitiveStringMultiline",
                    "title": "Positive",
                    "properties": {"ver": "0.3.0"},
                    "widgets_values": ["hello world\nmore"],
                },
                {"id": 73, "type": "RandomNoise", "title": "Noise", "widgets_values": [123, "fixed"]},
                {
                    "id": 398,
                    "type": "VHS_VideoCombine",
                    "title": "Output",
                    "widgets_values": {
                        "frame_rate": 18,
                        "filename_prefix": "output/wip/2026-01-18/FB8VA5L-2026-01-18-003159_OG",
                        "format": "video/h264-mp4",
                        "videopreview": {"params": {"fullpath": "C:\\abs\\path.mp4"}},
                    },
                },
            ],
            "extra": {"ds": {"scale": 0.5}},
        }

        cleaned = ccw.clean_workflow(workflow, canonicalize_titles=False)
        nodes = {n["id"]: n for n in cleaned["nodes"]}

        # LoadImage: blank filename, keep shape
        self.assertEqual(nodes[88]["widgets_values"][0], "")
        self.assertEqual(nodes[88]["widgets_values"][1], "image")

        # PrimitiveStringMultiline: replace with empty string but keep list shape
        self.assertEqual(nodes[408]["widgets_values"], [""])

        # RandomNoise: zero-out seed, keep flag
        self.assertEqual(nodes[73]["widgets_values"][0], 0)
        self.assertEqual(nodes[73]["widgets_values"][1], "fixed")

        # VHS dict widgets: remove videopreview + delocalize filename_prefix
        wv = nodes[398]["widgets_values"]
        self.assertNotIn("videopreview", wv)
        self.assertIn("%date:yyyy-MM-dd%", wv["filename_prefix"])
        self.assertIn("%date:hhmmss%", wv["filename_prefix"])

        # Per-node volatile UI field removed
        self.assertNotIn("selected", nodes[88])

        # Node properties: drop version pins
        self.assertNotIn("ver", nodes[88].get("properties", {}))
        self.assertIn("Node name for S&R", nodes[88].get("properties", {}))

    def test_canonicalize_titles_option_collapses_whitespace_for_known_types(self):
        workflow = {
            "nodes": [
                {"id": 1, "type": "mxSlider", "title": "  Speed   ", "widgets_values": [9, 9, 1]},
                {"id": 2, "type": "SomeOtherType", "title": "  Keep   Spaces  ", "widgets_values": []},
            ]
        }
        cleaned = ccw.clean_workflow(deepcopy(workflow), canonicalize_titles=True)
        nodes = {n["id"]: n for n in cleaned["nodes"]}
        self.assertEqual(nodes[1]["title"], "Speed")
        # Should NOT touch unrelated node types' titles
        self.assertEqual(nodes[2]["title"], "  Keep   Spaces  ")

    def test_drops_videopreview_for_vhs_loadvideo_dict_widgets(self):
        # EXT workflows use VHS_LoadVideo with dict widgets_values that includes a volatile videopreview blob.
        workflow = {
            "nodes": [
                {
                    "id": 463,
                    "type": "VHS_LoadVideo",
                    "title": "Load Video File",
                    "widgets_values": {"video": "161942_OG_00001.mp4", "videopreview": {"params": {"fullpath": "C:\\x"}}},
                }
            ]
        }
        cleaned = ccw.clean_workflow(workflow, canonicalize_titles=False)
        wv = cleaned["nodes"][0]["widgets_values"]
        self.assertNotIn("videopreview", wv)
        # Current behavior: we do NOT blank video selection yet.
        self.assertEqual(wv["video"], "161942_OG_00001.mp4")

    @unittest.skip("TODO: decide whether templates should blank VHS_LoadVideo.widgets_values.video")
    def test_vhs_loadvideo_video_should_be_blank_in_templates_todo(self):
        workflow = {
            "nodes": [
                {"id": 463, "type": "VHS_LoadVideo", "title": "Load Video File", "widgets_values": {"video": "x.mp4"}}
            ]
        }
        cleaned = ccw.clean_workflow(workflow, canonicalize_titles=False)
        self.assertEqual(cleaned["nodes"][0]["widgets_values"]["video"], "")


if __name__ == "__main__":
    unittest.main()

