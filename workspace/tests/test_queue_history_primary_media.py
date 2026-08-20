"""Queue history should play FINAL keepers, not deleted PREVIEW siblings."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = REPO_ROOT / "scripts" / "experiments_ui_server.py"


def _load_server():
    spec = importlib.util.spec_from_file_location("experiments_ui_server_queue_media_test", SERVER_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestQueueHistoryPrimaryMedia(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = _load_server()

    def test_pick_prefers_final_mp4_over_preview(self):
        pv, pi = self.m._pick_primary_media(
            [
                {
                    "relpath": "og/2026-08-16/hourly/job_PREVIEW_00023.mp4",
                    "type": "output",
                    "kind": "gifs",
                },
                {
                    "relpath": "og/2026-08-16/hourly/job_FINAL_00024.mp4",
                    "type": "output",
                    "kind": "gifs",
                },
                {
                    "relpath": "og/2026-08-16/hourly/job_PREVIEW_00023.png",
                    "type": "output",
                    "kind": "images",
                },
            ]
        )
        self.assertEqual(pv, "og/2026-08-16/hourly/job_FINAL_00024.mp4")
        self.assertIsNone(pi)

    def test_job_key_strips_preview_filename(self):
        self.assertEqual(
            self.m._queue_item_job_key(
                "hourly__pp-catalog-default__still-001302_LF_00001__000_202608162207_PREVIEW_00023.mp4"
            ),
            "hourly__pp-catalog-default__still-001302_LF_00001__000_202608162207",
        )

    def test_job_key_strips_final_filename(self):
        self.assertEqual(
            self.m._queue_item_job_key(
                "hourly__pp-catalog-default__still-001302_LF_00001__000_202608162207_FINAL_00024.mp4"
            ),
            "hourly__pp-catalog-default__still-001302_LF_00001__000_202608162207",
        )

    def test_rewrite_missing_plain_suffix_to_latest_final(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            folder = root / "og" / "2026-08-16" / "hourly"
            folder.mkdir(parents=True)
            (folder / "hourly__job_FINAL_00014.mp4").write_bytes(b"x")
            (folder / "hourly__job_FINAL_00024.mp4").write_bytes(b"y")
            cfg = SimpleNamespace(output_root=root, workspace_root=root)
            got = self.m._rewrite_history_media_rel(
                cfg, "og/2026-08-16/hourly/hourly__job_00001.mp4"
            )
            self.assertEqual(got, "og/2026-08-16/hourly/hourly__job_FINAL_00024.mp4")

    def test_rewrite_missing_preview_to_final(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            folder = root / "og" / "2026-08-16" / "hourly"
            folder.mkdir(parents=True)
            (folder / "hourly__job_FINAL_00024.mp4").write_bytes(b"y")
            cfg = SimpleNamespace(output_root=root, workspace_root=root)
            got = self.m._rewrite_history_media_rel(
                cfg, "og/2026-08-16/hourly/hourly__job_PREVIEW_00023.mp4"
            )
            self.assertEqual(got, "og/2026-08-16/hourly/hourly__job_FINAL_00024.mp4")


if __name__ == "__main__":
    unittest.main()
