import unittest
from support import ROOT, SCRIPTS_DIR  # noqa: F401

import canonicalize_comfy_titles as cct  # noqa: E402


class TestCanonicalizeComfyTitles(unittest.TestCase):
    def test_maps_common_titles_and_ensures_uniqueness(self):
        wf = {
            "nodes": [
                {"id": 10, "title": " Steps ", "type": "mxSlider"},
                {"id": 11, "title": "Steps", "type": "mxSlider"},
                {"id": 12, "title": "Not In Map   ", "type": "Something"},
            ]
        }
        out = cct.canonicalize_titles(wf)
        nodes = {n["id"]: n for n in out["nodes"]}

        self.assertEqual(nodes[10]["title"], "RUN_Steps")
        self.assertEqual(nodes[11]["title"], "RUN_Steps_11")
        self.assertEqual(nodes[12]["title"], "Not In Map")

    def test_does_not_touch_node_ids_or_structure(self):
        wf = {"nodes": [{"id": 1, "title": "CFG", "type": "mxSlider"}], "links": [[1, 2, 3, 4, 5, "X"]]}
        out = cct.canonicalize_titles(wf)
        self.assertEqual(out["nodes"][0]["id"], 1)
        self.assertEqual(out["links"], wf["links"])

    @unittest.skip("TODO: extend title map for EXT workflows (e.g. Load Video File -> IN_Video)")
    def test_ext_titles_should_be_mapped_todo(self):
        wf = {"nodes": [{"id": 463, "title": "Load Video File", "type": "VHS_LoadVideo"}]}
        out = cct.canonicalize_titles(wf)
        self.assertEqual(out["nodes"][0]["title"], "IN_Video")


if __name__ == "__main__":
    unittest.main()

