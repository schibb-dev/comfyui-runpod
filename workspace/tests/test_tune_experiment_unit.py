import unittest
from support import ROOT, SCRIPTS_DIR  # noqa: F401


class TestTuneExperimentUnit(unittest.TestCase):
    def test_rewrite_filename_prefix(self):
        # Import inside test to keep test discovery fast.
        from tune_experiment import _rewrite_filename_prefix

        p = "output/wip/%date:yyyy-MM-dd%/FB8VA5L-%date:yyyy-MM-dd%-%date:hhmmss%_OG"
        out = _rewrite_filename_prefix(p, exp_id="EXP123", run_id="run_001")
        self.assertIn("output/experiments/EXP123", out)
        self.assertIn("run_001_", out)
        self.assertIn("%date:yyyy-MM-dd%", out)

    def test_resolve_sweep_semantics(self):
        from tune_experiment import _resolve_sweep

        self.assertIsNone(_resolve_sweep(None, [1, 2]))
        self.assertEqual(_resolve_sweep([], [1, 2]), [1, 2])
        self.assertEqual(_resolve_sweep([3], [1, 2]), [3])

    def test_default_group_applies_to_none_values(self):
        from tune_experiment import _apply_default_groups

        resolved = _apply_default_groups(
            selected_groups=["core"],
            speed=None,
            cfg=None,
            denoise=None,
            steps=None,
            teacache=None,
            crf=None,
            pix_fmt=None,
            skip_blocks=None,
            skip_start=None,
            skip_end=None,
            ta_self_temporal=None,
            ta_cross_temporal=None,
        )
        self.assertEqual(resolved["cfg"], [])
        self.assertEqual(resolved["denoise"], [])
        self.assertEqual(resolved["steps"], [])
        self.assertIsNone(resolved["speed"])

    def test_heuristic_core_is_small(self):
        from tune_experiment import _heuristic_group_values

        # Minimal prompt with mxSlider nodes and canonical titles.
        prompt = {
            "1": {"class_type": "mxSlider", "inputs": {"Xi": 0, "Xf": 3.0, "isfloatX": 1}, "_meta": {"title": "RUN_CFG"}},
            "2": {"class_type": "mxSlider", "inputs": {"Xi": 0, "Xf": 0.9, "isfloatX": 1}, "_meta": {"title": "RUN_Denoise"}},
            "3": {"class_type": "mxSlider", "inputs": {"Xi": 26, "Xf": 26, "isfloatX": 0}, "_meta": {"title": "RUN_Steps"}},
        }
        out = _heuristic_group_values(group="core", prompt_base=prompt, strength=0)
        self.assertEqual(sorted(out.keys()), ["cfg", "denoise", "steps"])
        self.assertEqual(len(out["cfg"]), 2)
        self.assertEqual(len(out["denoise"]), 2)
        self.assertEqual(len(out["steps"]), 2)
        self.assertEqual(out["cfg"], [3.0, 3.5])
        self.assertEqual(out["denoise"], [0.9, 0.85])
        self.assertEqual(out["steps"], [26, 30])


if __name__ == "__main__":
    unittest.main()

