"""watch_queue should recognize VHS outputs, not only run_###_*.mp4 names."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401
import watch_queue as wq


class TestWatchQueueVideoDetect(unittest.TestCase):
    def test_has_video_from_history_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output = root / "output"
            exp = output / "experiments" / "x-demo"
            run = exp / "runs" / "run_002"
            run.mkdir(parents=True)
            dated = output / "og" / "2026-09-19" / "experiments" / "x-demo"
            dated.mkdir(parents=True)
            mp4 = dated / "Clip_OG_00001.mp4"
            mp4.write_bytes(b"mp4")
            (run / "history.json").write_text(
                json.dumps(
                    {
                        "pid": {
                            "outputs": {
                                "398": {
                                    "gifs": [
                                        {
                                            "filename": "Clip_OG_00001.mp4",
                                            "subfolder": "og/2026-09-19/experiments/x-demo",
                                        }
                                    ]
                                }
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(
                wq._has_video_for_run(exp, "run_002", run_dir=run, output_root=output)
            )
            paths = wq._find_media_files_for_run(exp, "run_002", run_dir=run, output_root=output)
            self.assertEqual([p.resolve() for p in paths], [mp4.resolve()])

    def test_has_video_from_exact_vhs_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output = root / "output"
            exp = output / "experiments" / "x-demo"
            run = exp / "runs" / "run_001"
            run.mkdir(parents=True)
            dated = output / "og" / "2026-05-09" / "experiments" / "x-demo"
            dated.mkdir(parents=True)
            mp4 = dated / "Clip_OG_00001.mp4"
            mp4.write_bytes(b"mp4")
            (run / "prompt.json").write_text(
                json.dumps(
                    {
                        "398": {
                            "class_type": "VHS_VideoCombine",
                            "inputs": {
                                "filename_prefix": "og/2026-05-09/experiments/x-demo/Clip_OG",
                                "save_output": True,
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(
                wq._has_video_for_run(exp, "run_001", run_dir=run, output_root=output)
            )

    def test_history_missing_file_does_not_steal_prefix_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output = root / "output"
            exp = output / "experiments" / "x-demo"
            run = exp / "runs" / "run_002"
            run.mkdir(parents=True)
            # Sibling / older file under the shared stem — must not count.
            dated = output / "og" / "2026-05-09" / "experiments" / "x-demo"
            dated.mkdir(parents=True)
            (dated / "Clip_OG_00001.mp4").write_bytes(b"old")
            (run / "prompt.json").write_text(
                json.dumps(
                    {
                        "398": {
                            "class_type": "VHS_VideoCombine",
                            "inputs": {
                                "filename_prefix": "og/2026-05-09/experiments/x-demo/Clip_OG",
                                "save_output": True,
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            (run / "history.json").write_text(
                json.dumps(
                    {
                        "pid": {
                            "outputs": {
                                "398": {
                                    "gifs": [
                                        {
                                            "filename": "Clip_OG_00001.mp4",
                                            "subfolder": "og/2026-09-19/experiments/x-demo",
                                        }
                                    ]
                                }
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertFalse(
                wq._has_video_for_run(exp, "run_002", run_dir=run, output_root=output)
            )

    def test_ensure_run_id_in_vhs_prefixes(self) -> None:
        prompt = {
            "398": {
                "class_type": "VHS_VideoCombine",
                "inputs": {"filename_prefix": "og/2026-05-09/experiments/x/Clip_OG"},
            }
        }
        changes = wq._ensure_run_id_in_vhs_prefixes(prompt, run_id="run_002")
        self.assertTrue(changes)
        self.assertEqual(
            prompt["398"]["inputs"]["filename_prefix"],
            "og/2026-05-09/experiments/x/run_002_Clip_OG",
        )
        # Idempotent.
        self.assertEqual(wq._ensure_run_id_in_vhs_prefixes(prompt, run_id="run_002"), [])


if __name__ == "__main__":
    unittest.main()
