#!/usr/bin/env python3
"""Tests for hourly video/clip steer bins (Phase 1b)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shape_factory_hourly_bins import (
    bin_summary,
    list_steer_targets,
    set_bin_item,
)
from shape_factory_hourly_video_steer import (
    ensure_steer_bins_for_source_videos,
    is_steer_item_id,
    list_video_bin_candidates,
    resolve_video_bin_id_for_family,
    set_bin_curation_unit,
    video_steer_bias_mult,
    whole_file_clip_id,
)


class HourlyVideoSteerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        (self.root / "shape_factory").mkdir()
        (self.root / "output").mkdir()

    def tearDown(self) -> None:
        self._td.cleanup()

    def _write_video_pool(self, fam: str, members: list[Path] | None = None) -> None:
        d = self.root / "pools" / fam
        d.mkdir(parents=True)
        member_lines = ""
        if members:
            member_lines = "\n".join(f"      - {p}" for p in members)
        (d / "pools.yaml").write_text(
            "schema_version: comfyui-runpod.pools.v0\n"
            "pools:\n"
            "  source_video:\n"
            "    slot: source_video\n"
            "    members:\n"
            f"{member_lines}\n"
            if members
            else "schema_version: comfyui-runpod.pools.v0\n"
            "pools:\n"
            "  source_video:\n"
            "    slot: source_video\n"
            "    members: []\n",
            encoding="utf-8",
        )

    def test_ids_and_whole_unit(self) -> None:
        self.assertTrue(is_steer_item_id("a" * 64))
        self.assertTrue(is_steer_item_id("clip_" + "b" * 32))
        self.assertTrue(is_steer_item_id(whole_file_clip_id("c" * 64)))
        self.assertFalse(is_steer_item_id("not-an-id"))
        self.assertEqual(whole_file_clip_id("AbC"), "whole:abc")

    def test_ensure_video_bins_1to1(self) -> None:
        self._write_video_pool("ClipFamA")
        self._write_video_pool("ClipFamB")
        # Still-only family must not get a video bin.
        still = self.root / "pools" / "StillOnly"
        still.mkdir(parents=True)
        (still / "pools.yaml").write_text(
            "pools:\n  source_still:\n    slot: source_still\n    members: []\n",
            encoding="utf-8",
        )
        created = ensure_steer_bins_for_source_videos(data_root=self.root)
        self.assertIn("steer-video-ClipFamA", created)
        self.assertIn("steer-video-ClipFamB", created)
        self.assertEqual(
            resolve_video_bin_id_for_family("ClipFamA", data_root=self.root),
            "steer-video-ClipFamA",
        )
        self.assertIsNone(resolve_video_bin_id_for_family("StillOnly", data_root=self.root))
        summary = bin_summary("steer-video-ClipFamA", data_root=self.root)
        self.assertEqual(summary["pool_slot"], "source_video")
        self.assertEqual(summary["curation_unit"], "auto")

    def test_clip_and_whole_bias(self) -> None:
        self._write_video_pool("VidBias")
        ensure_steer_bins_for_source_videos(data_root=self.root)
        bid = resolve_video_bin_id_for_family("VidBias", data_root=self.root)
        assert bid
        parent = "d" * 64
        clip_id = "clip_" + "e" * 32
        whole_id = whole_file_clip_id(parent)
        rel = f"og/demo/{parent}.mp4"
        set_bin_item(
            bin_id=bid,
            content_id=clip_id,
            status="pin",
            relpath=rel,
            unit="span",
            parent_content_id=parent,
            surface="test",
            data_root=self.root,
        )
        self.assertGreaterEqual(
            video_steer_bias_mult(family="VidBias", clip_id=clip_id, data_root=self.root),
            16.0,
        )
        self.assertEqual(
            video_steer_bias_mult(family="VidBias", clip_id="clip_" + "f" * 32, data_root=self.root),
            1.0,
        )
        set_bin_item(
            bin_id=bid,
            content_id=whole_id,
            status="out",
            relpath=rel,
            unit="whole",
            parent_content_id=parent,
            surface="test",
            data_root=self.root,
        )
        self.assertEqual(
            video_steer_bias_mult(
                family="VidBias",
                parent_content_id=parent,
                path=rel,
                data_root=self.root,
            ),
            0.0,
        )

    def test_curation_unit_and_hybrid_deck(self) -> None:
        parent_hex = "1" * 64
        vid = self.root / "output" / "og" / f"SSS{parent_hex}.mp4"
        vid.parent.mkdir(parents=True)
        vid.write_bytes(b"fake-mp4")
        self._write_video_pool("HybridFam", members=[vid])
        ensure_steer_bins_for_source_videos(data_root=self.root)
        bid = resolve_video_bin_id_for_family("HybridFam", data_root=self.root)
        assert bid

        clip_row = {
            "clip_id": "clip_" + "2" * 32,
            "mark_in_s": 1.0,
            "mark_out_s": 3.5,
            "label": "intro",
            "is_starred": True,
            "updated_at": "2026-09-25T00:00:00Z",
        }

        with mock.patch(
            "shape_factory_hourly_video_steer._collect_video_members_scored",
            return_value=[(1.0, vid)],
        ), mock.patch(
            "shape_factory_hourly_video_steer._open_clips_ro",
            return_value=mock.MagicMock(),
        ), mock.patch(
            "shape_factory_hourly_video_steer._batch_clips_by_parent",
            return_value={parent_hex: [clip_row]},
        ), mock.patch(
            "shape_factory.default_asset_registry_path",
            return_value=self.root / "registry.sqlite",
        ):
            # Ensure clip_row has parent_content_id for batch map keying in real path;
            # here we inject via _batch_clips_by_parent mock.
            auto = list_video_bin_candidates(bin_id=bid, data_root=self.root, limit=20)
            self.assertTrue(auto.get("ok"), auto)
            units = [str(it.get("unit")) for it in auto.get("items") or []]
            self.assertIn("span", units)
            self.assertIn("whole", units)
            # ★ span listed before whole
            self.assertEqual(units[0], "span")
            self.assertTrue(any(str(it.get("content_id") or "").startswith("whole:") for it in auto["items"]))

            set_bin_curation_unit(bid, "clips", data_root=self.root)
            clips_only = list_video_bin_candidates(bin_id=bid, data_root=self.root, limit=20)
            self.assertTrue(all(str(it.get("unit")) == "span" for it in clips_only.get("items") or []))

            set_bin_curation_unit(bid, "videos", data_root=self.root)
            vids_only = list_video_bin_candidates(bin_id=bid, data_root=self.root, limit=20)
            self.assertTrue(all(str(it.get("unit")) == "whole" for it in vids_only.get("items") or []))
            # Virtual whole ids must not look like span clip ids written to clips DB.
            for it in vids_only.get("items") or []:
                self.assertTrue(str(it.get("content_id") or "").startswith("whole:"))

    def test_steer_targets_include_video(self) -> None:
        self._write_video_pool("ListedVid")
        still = self.root / "pools" / "ListedStill"
        still.mkdir(parents=True)
        (still / "pools.yaml").write_text(
            "pools:\n  source_still:\n    slot: source_still\n    members: []\n",
            encoding="utf-8",
        )
        from shape_factory_hourly_bins import ensure_steer_bins_for_source_stills

        ensure_steer_bins_for_source_stills(data_root=self.root)
        ensure_steer_bins_for_source_videos(data_root=self.root)
        targets = list_steer_targets(data_root=self.root)
        slots = {str(t.get("pool_slot")) for t in targets}
        fams = {str(t.get("pool_family")) for t in targets}
        self.assertIn("source_still", slots)
        self.assertIn("source_video", slots)
        self.assertIn("ListedVid", fams)
        self.assertIn("ListedStill", fams)


if __name__ == "__main__":
    unittest.main()
