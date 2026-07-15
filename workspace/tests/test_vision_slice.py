#!/usr/bin/env python3
"""Tests for vision V1 slice sample + dry-run caption."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401

from vision_slice_caption_run import (
    build_row,
    default_status_dir,
    dry_run_caption,
    run_caption,
    tags_from_caption,
)
from vision_slice_pick_inputs import pick_diverse, write_inputs
from vision_slice_runner import (
    build_florence_caption_prompt,
    extract_caption_from_history,
)
from vision_slice_sample import (
    plan_windows,
    resolve_asset_path,
    run_sample,
    safe_stem,
)


class PlanWindowsTests(unittest.TestCase):
    def test_exact_fit(self) -> None:
        wins = plan_windows(6.0, window_sec=2.0, max_windows=30)
        self.assertEqual(len(wins), 3)
        self.assertEqual(wins[0], (0.0, 2.0, 1.0))
        self.assertEqual(wins[2], (4.0, 6.0, 5.0))

    def test_trim_last(self) -> None:
        wins = plan_windows(5.0, window_sec=2.0, max_windows=30)
        self.assertEqual(len(wins), 3)
        self.assertEqual(wins[-1][0], 4.0)
        self.assertEqual(wins[-1][1], 5.0)

    def test_max_windows(self) -> None:
        wins = plan_windows(100.0, window_sec=2.0, max_windows=5)
        self.assertEqual(len(wins), 5)

    def test_empty(self) -> None:
        self.assertEqual(plan_windows(0.0), [])


class ResolvePathTests(unittest.TestCase):
    def test_relative_requires_root(self) -> None:
        with self.assertRaises(ValueError):
            resolve_asset_path("og/a.mp4", data_root=None)

    def test_relative_under_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rel, abs_p = resolve_asset_path("og/clip.mp4", data_root=root)
            self.assertEqual(rel, "og/clip.mp4")
            self.assertEqual(abs_p, (root / "og/clip.mp4").resolve())


class SampleIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not Path("/usr/bin/ffmpeg").is_file() and not Path("/usr/bin/ffprobe").is_file():
            # still try PATH
            pass

    def _make_color_mp4(self, path: Path, *, seconds: float = 3.0) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=blue:s=64x64:d={seconds}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-t",
            str(seconds),
            str(path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            self.skipTest(f"ffmpeg lavfi failed: {proc.stderr}")

    def test_sample_and_dry_caption(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "og" / "sample_clip.mp4"
            self._make_color_mp4(video, seconds=3.0)
            work = root / "work"
            status = root / "output" / "_status"

            doc = run_sample(
                ["og/sample_clip.mp4"],
                data_root=root,
                work_dir=work,
                window_sec=2.0,
                max_windows=30,
                include_whole=True,
            )
            self.assertGreaterEqual(doc["frame_count"], 2)  # 2 windows + whole on 3s
            self.assertTrue((work / "frames_manifest.json").is_file())
            # At least one real jpeg
            frames = doc["frames"]
            jpeg = work / frames[0]["frame_relpath"]
            self.assertTrue(jpeg.is_file())
            self.assertGreater(jpeg.stat().st_size, 0)

            manifest = run_caption(
                work / "frames_manifest.json",
                status_dir=status,
                run_id="vision_v1_test",
                runner="local",
                dry_run=True,
            )
            self.assertEqual(manifest["caption_count"], doc["frame_count"])
            ndjson = status / "vision_slice_captions.ndjson"
            self.assertTrue(ndjson.is_file())
            lines = [json.loads(x) for x in ndjson.read_text(encoding="utf-8").splitlines() if x.strip()]
            self.assertEqual(len(lines), doc["frame_count"])
            self.assertTrue(lines[0]["caption"].startswith("[dry-run]"))
            self.assertEqual(lines[0]["run_id"], "vision_v1_test")
            self.assertIn("slice", lines[0])


class CaptionUnitTests(unittest.TestCase):
    def test_tags_from_caption(self) -> None:
        tags = tags_from_caption("A woman smiles at the camera outdoors")
        self.assertIn("woman", tags)
        self.assertIn("smiles", tags)

    def test_build_row_whole(self) -> None:
        row = build_row(
            {
                "asset_relpath": "og/a.mp4",
                "t0": 0.0,
                "t1": 4.0,
                "frame_t": 2.0,
                "frame_relpath": "frames/a/x.jpg",
                "slice": "whole",
            },
            caption="hello world caption",
            provider="dry-run",
            model_pin="dry-run",
            run_id="r1",
            runner="docker",
        )
        self.assertEqual(row["slice"], "whole")
        self.assertEqual(row["runner"], "docker")
        self.assertIn("hello", row["tags"])

    def test_dry_run_caption_text(self) -> None:
        text = dry_run_caption({"asset_relpath": "og/a.mp4", "slice": "window", "t0": 0, "t1": 2})
        self.assertIn("window", text)
        self.assertIn("og/a.mp4", text)

    def test_safe_stem(self) -> None:
        self.assertEqual(safe_stem("og/foo bar!!.mp4")[:3], "foo")

    def test_default_status_beside_og(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "og").mkdir()
            self.assertEqual(default_status_dir(root), root / "_status")


class PickInputsTests(unittest.TestCase):
    def test_pick_diverse_round_robin(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            og = Path(td) / "og"
            paths = []
            for date, name in (
                ("2026-07-01", "a.mp4"),
                ("2026-07-01", "b.mp4"),
                ("2026-07-10", "c.mp4"),
                ("2026-07-10", "hourly_d.mp4"),
                ("2026-06-01", "e.mp4"),
            ):
                p = og / date / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"x")
                paths.append(p)
            picked = pick_diverse(paths, og_root=og, limit=3, seed=1, prefer_hourly=True)
            self.assertEqual(len(picked), 3)
            buckets = {p.parent.name for p in picked}
            self.assertGreaterEqual(len(buckets), 2)

    def test_write_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            og = root / "og" / "2026-07-01"
            og.mkdir(parents=True)
            v = og / "clip.mp4"
            v.write_bytes(b"x")
            out = root / "_status" / "vision_v1_inputs.txt"
            write_inputs([v], data_root=root, out_path=out)
            text = out.read_text(encoding="utf-8")
            self.assertIn("og/2026-07-01/clip.mp4", text)


class ComfyRunnerApiTests(unittest.TestCase):
    def test_build_florence_prompt_shape(self) -> None:
        prompt = build_florence_caption_prompt(image_name="vision_v1/a.jpg", model="microsoft/Florence-2-base")
        self.assertEqual(prompt["1"]["class_type"], "LoadImage")
        self.assertEqual(prompt["1"]["inputs"]["image"], "vision_v1/a.jpg")
        self.assertEqual(prompt["2"]["class_type"], "DownloadAndLoadFlorence2Model")
        self.assertEqual(prompt["3"]["class_type"], "Florence2Run")
        self.assertEqual(prompt["3"]["inputs"]["task"], "caption")
        self.assertEqual(prompt["3"]["inputs"]["image"], ["1", 0])
        self.assertEqual(prompt["3"]["inputs"]["florence2_model"], ["2", 0])
        self.assertEqual(prompt["4"]["class_type"], "ShowText|pysssss")
        self.assertEqual(prompt["4"]["inputs"]["text"], ["3", 2])

    def test_extract_caption_from_history(self) -> None:
        entry = {"outputs": {"4": {"text": ["a person sitting outdoors"]}}}
        self.assertEqual(extract_caption_from_history(entry), "a person sitting outdoors")
        entry2 = {"outputs": {"3": {"string": ["hello"]}}}
        self.assertEqual(extract_caption_from_history(entry2), "hello")


if __name__ == "__main__":
    unittest.main()
