#!/usr/bin/env python3
"""Tests for video_companion_thumbs (mocked ffmpeg)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from video_companion_thumbs import ensure_companion_thumb


class EnsureCompanionThumbTests(unittest.TestCase):
    def test_missing_video(self) -> None:
        row = ensure_companion_thumb(Path("/tmp/does-not-exist-xyz.mp4"))
        self.assertFalse(row["ok"])
        self.assertEqual(row["error"], "video_missing")

    def test_not_a_video(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "note.txt"
            p.write_text("x", encoding="utf-8")
            row = ensure_companion_thumb(p)
            self.assertFalse(row["ok"])
            self.assertEqual(row["error"], "not_a_video")

    def test_skip_when_companion_exists(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "clip.mp4"
            png = root / "clip.png"
            video.write_bytes(b"fake-mp4")
            png.write_bytes(b"fake-png")
            called = {"n": 0}

            def boom(*_a, **_k):
                called["n"] += 1
                raise AssertionError("extract should not run")

            row = ensure_companion_thumb(video, extract_fn=boom, probe_fn=lambda _v: 1.0)
            self.assertTrue(row["ok"])
            self.assertTrue(row["skipped"])
            self.assertEqual(row["reason"], "companion_exists")
            self.assertEqual(called["n"], 0)
            self.assertTrue(str(row["path"]).endswith("clip.png"))

    def test_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "orphan.mp4"
            video.write_bytes(b"fake-mp4")
            target = root / "orphan.png"
            row = ensure_companion_thumb(video, dry_run=True)
            self.assertTrue(row["ok"])
            self.assertTrue(row["skipped"])
            self.assertEqual(row["reason"], "dry_run")
            self.assertFalse(target.exists())

    def test_creates_png_via_extract_fn(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "orphan.mp4"
            video.write_bytes(b"fake-mp4")
            target = root / "orphan.png"

            def fake_extract(_video, out_png, *, frame_t, duration_sec=None):
                self.assertAlmostEqual(frame_t, 0.5, places=3)
                self.assertEqual(duration_sec, 1.0)
                Path(out_png).write_bytes(b"\x89PNG")

            row = ensure_companion_thumb(
                video,
                at_frac=0.5,
                probe_fn=lambda _v: 1.0,
                extract_fn=fake_extract,
            )
            self.assertTrue(row["ok"])
            self.assertTrue(row["created"])
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_bytes()[:4], b"\x89PNG")

    def test_force_overwrites_existing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "clip.mp4"
            png = root / "clip.png"
            video.write_bytes(b"fake-mp4")
            png.write_bytes(b"old")

            def fake_extract(_video, out_png, *, frame_t, duration_sec=None):
                Path(out_png).write_bytes(b"new")

            row = ensure_companion_thumb(
                video,
                force=True,
                probe_fn=lambda _v: 2.0,
                extract_fn=fake_extract,
            )
            self.assertTrue(row["ok"])
            self.assertTrue(row["created"])
            self.assertEqual(png.read_bytes(), b"new")

    def test_target_path_is_same_stem_png(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "nested" / "a.mp4"
            video.parent.mkdir(parents=True)
            video.write_bytes(b"x")
            row = ensure_companion_thumb(video, dry_run=True)
            self.assertEqual(Path(row["path"]), video.with_suffix(".png"))


if __name__ == "__main__":
    unittest.main()
