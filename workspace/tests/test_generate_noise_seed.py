"""generate_job_for_picks must draw a fresh noise seed by default."""

from __future__ import annotations

import unittest

import support  # noqa: F401 — injects workspace/scripts onto sys.path
import shape_factory as sf


class TestGenerateNoiseSeed(unittest.TestCase):
    def test_default_draws_new_seed(self):
        a, ma = sf.resolve_generate_noise_seed()
        b, mb = sf.resolve_generate_noise_seed()
        self.assertEqual(ma, "new")
        self.assertEqual(mb, "new")
        self.assertIsInstance(a, int)
        self.assertIsInstance(b, int)
        # Extremely unlikely to collide for independent draws.
        self.assertNotEqual(a, b)

    def test_explicit_construction_seed_wins(self):
        seed, mode = sf.resolve_generate_noise_seed(
            construction={"noise_seed": 4242, "seed_mode": "new"}
        )
        self.assertEqual(seed, 4242)
        self.assertEqual(mode, "explicit")

    def test_same_keeps_template_seed(self):
        seed, mode = sf.resolve_generate_noise_seed(construction={"seed_mode": "same"})
        self.assertIsNone(seed)
        self.assertEqual(mode, "same")

    def test_dev_tuning_seed_is_explicit(self):
        seed, mode = sf.resolve_generate_noise_seed(dev_tuning={"noise_seed": 99})
        self.assertEqual(seed, 99)
        self.assertEqual(mode, "explicit")

    def test_apply_dev_tuning_updates_random_noise_widget(self):
        workflow = {
            "nodes": [
                {
                    "id": 73,
                    "type": "RandomNoise",
                    "widgets_values": [625116263363405, "randomize"],
                }
            ]
        }
        changes = sf.apply_dev_tuning_ui(workflow, {"noise_seed": 123456})
        self.assertEqual(workflow["nodes"][0]["widgets_values"][0], 123456)
        self.assertEqual(workflow["nodes"][0]["widgets_values"][1], "fixed")
        self.assertTrue(changes.get("seed"))


if __name__ == "__main__":
    unittest.main()
