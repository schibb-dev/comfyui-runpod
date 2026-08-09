#!/usr/bin/env python3
"""Tests for first-class Clip entities and use-window resolve."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

# Workspace /tmp sqlite commits can hang under this WSL setup; prefer shm.
_TMP_ROOT = Path("/dev/shm") if Path("/dev/shm").is_dir() else None


def _tmpdir() -> tempfile.TemporaryDirectory:
    if _TMP_ROOT is not None:
        return tempfile.TemporaryDirectory(dir=str(_TMP_ROOT))
    return tempfile.TemporaryDirectory()


class TestShapeFactoryClips(unittest.TestCase):
    def test_crud_overlap_edit_default(self) -> None:
        from shape_factory_clips import (
            connect_clips,
            create_clip,
            get_default_clip_id,
            list_clips_for_parent,
            set_default_clip,
            update_clip,
        )

        with _tmpdir() as td:
            reg = Path(td) / "asset_registry.sqlite"
            con = connect_clips(reg)
            parent = "a" * 64
            a = create_clip(
                con,
                parent_content_id=parent,
                mark_in_s=1.0,
                mark_out_s=3.0,
                label="Head",
                origin="test",
                duration_s=10.0,
            )
            b = create_clip(
                con,
                parent_content_id=parent,
                mark_in_s=2.0,
                mark_out_s=5.0,
                label="Overlap",
                origin="test",
                duration_s=10.0,
            )
            self.assertNotEqual(a["clip_id"], b["clip_id"])
            self.assertEqual(len(list_clips_for_parent(con, parent)), 2)

            updated = update_clip(con, a["clip_id"], mark_in_s=0.5, mark_out_s=2.5, duration_s=10.0)
            self.assertEqual(updated["clip_id"], a["clip_id"])
            self.assertAlmostEqual(updated["mark_in_s"], 0.5)

            set_default_clip(con, parent, b["clip_id"])
            self.assertEqual(get_default_clip_id(con, parent), b["clip_id"])
            set_default_clip(con, parent, None)
            self.assertIsNone(get_default_clip_id(con, parent))
            con.close()

    def test_resolve_order_use_clip_default_full(self) -> None:
        from shape_factory_clips import (
            connect_clips,
            create_clip,
            resolve_job_use_window,
            set_default_clip,
        )

        with _tmpdir() as td:
            reg = Path(td) / "asset_registry.sqlite"
            con = connect_clips(reg)
            parent = "b" * 64
            clip = create_clip(
                con,
                parent_content_id=parent,
                mark_in_s=1.0,
                mark_out_s=3.0,
                label="Seg",
                duration_s=10.0,
            )
            set_default_clip(con, parent, clip["clip_id"])
            media = {"fps": 10.0, "frame_count": 100, "duration": 10.0}

            full = resolve_job_use_window(
                job={},
                parent_content_id="c" * 64,
                media_meta=media,
                con=con,
            )
            self.assertEqual(full["source"], "full")
            self.assertEqual(full["skip_first_frames"], 0)

            defaulted = resolve_job_use_window(
                job={},
                parent_content_id=parent,
                media_meta=media,
                con=con,
            )
            self.assertEqual(defaulted["source"], "default_clip")
            self.assertEqual(defaulted["clip_id"], clip["clip_id"])
            self.assertEqual(defaulted["skip_first_frames"], 10)

            explicit = resolve_job_use_window(
                job={"vhs_window": {"mark_in": 0.0, "mark_out": 1.0}},
                parent_content_id=parent,
                media_meta=media,
                con=con,
            )
            self.assertEqual(explicit["source"], "use")
            self.assertEqual(explicit["skip_first_frames"], 0)
            self.assertEqual(explicit["frame_load_cap"], 10)
            con.close()

    def test_import_trims_presets(self) -> None:
        from shape_factory_clips import connect_clips, get_default_clip_id, import_trims_presets_as_clips

        with _tmpdir() as td:
            reg = Path(td) / "asset_registry.sqlite"
            con = connect_clips(reg)
            parent = "d" * 64
            doc = {
                "v": 1,
                "contexts": {
                    "work-products": {
                        "active_preset_id": "p1",
                        "presets": [
                            {"id": "p1", "label": "Good", "in": 0.5, "out": 2.0, "at": 1},
                        ],
                    }
                },
            }
            created = import_trims_presets_as_clips(
                con,
                parent_content_id=parent,
                trims_doc=doc,
                duration_s=5.0,
            )
            self.assertEqual(len(created), 1)
            self.assertEqual(get_default_clip_id(con, parent), created[0]["clip_id"])
            again = import_trims_presets_as_clips(
                con,
                parent_content_id=parent,
                trims_doc=doc,
                duration_s=5.0,
            )
            self.assertEqual(len(again), 0)
            con.close()

    def test_media_path_for_trims_sidecar(self) -> None:
        from shape_factory_clips import media_path_for_trims_sidecar

        with _tmpdir() as td:
            root = Path(td)
            media = root / "clip.mp4"
            media.write_bytes(b"x")
            sc = root / "clip.trims.json"
            sc.write_text("{}", encoding="utf-8")
            self.assertEqual(media_path_for_trims_sidecar(sc), media)
            self.assertIsNone(media_path_for_trims_sidecar(root / "missing.trims.json"))

    def test_list_clips_library_joins_assets(self) -> None:
        from shape_factory_clips import connect_clips, create_clip, list_clips_library, set_default_clip

        with _tmpdir() as td:
            reg = Path(td) / "asset_registry.sqlite"
            con = connect_clips(reg)
            parent = "e" * 64
            con.execute(
                """
                INSERT INTO assets(
                    content_id, size, mtime, ext, kind, width, height,
                    current_relpath, first_seen, last_seen, status
                ) VALUES (?, 1, 1.0, '.mp4', 'video', NULL, NULL, ?, 't', 't', 'present')
                """,
                (parent, "og/demo/parent.mp4"),
            )
            a = create_clip(
                con,
                parent_content_id=parent,
                mark_in_s=1.0,
                mark_out_s=2.5,
                label="Demo",
                origin="workflow_import",
            )
            create_clip(
                con,
                parent_content_id=parent,
                mark_in_s=3.0,
                mark_out_s=4.0,
                label="Other",
                origin="manual",
            )
            set_default_clip(con, parent, a["clip_id"])
            lib = list_clips_library(con, origin="workflow_import", q="parent.mp4")
            self.assertEqual(lib["total"], 1)
            row = lib["clips"][0]
            self.assertEqual(row["clip_id"], a["clip_id"])
            self.assertEqual(row["media_relpath"], "og/demo/parent.mp4")
            self.assertTrue(row["is_default"])
            self.assertAlmostEqual(row["duration_s"], 1.5)
            self.assertIn("workflow_import", lib["origin_counts"])
            con.close()


if __name__ == "__main__":
    unittest.main()
