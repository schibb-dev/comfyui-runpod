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
            self.assertEqual(defaulted["source"], "starred")
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

    def test_resolve_sidecar_before_full(self) -> None:
        """Workbench .trims.json applies when no clip; default clip still wins over sidecar."""
        import json

        from shape_factory_clips import (
            connect_clips,
            create_clip,
            resolve_job_use_window,
            set_default_clip,
        )

        with _tmpdir() as td:
            root = Path(td)
            media_path = root / "src.mp4"
            media_path.write_bytes(b"fake")
            sidecar = media_path.with_suffix(".trims.json")
            sidecar.write_text(
                json.dumps(
                    {
                        "v": 1,
                        "contexts": {
                            "work-products": {
                                "active_preset_id": "p1",
                                "presets": [
                                    {"id": "p1", "label": "Trim", "in": 1.0, "out": 3.0, "at": 1},
                                ],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            media_meta = {"fps": 10.0, "frame_count": 100, "duration": 10.0}

            # No clips → sidecar window (skip=10, cap=20 at 10fps).
            from_sidecar = resolve_job_use_window(
                job={},
                parent_content_id="f" * 64,
                media_meta=media_meta,
                media_abs=media_path,
                con=None,
            )
            self.assertEqual(from_sidecar["source"], "usable_trim")
            self.assertEqual(from_sidecar["skip_first_frames"], 10)
            self.assertEqual(from_sidecar["frame_load_cap"], 20)
            self.assertEqual(from_sidecar["mark_in"], 1.0)
            self.assertEqual(from_sidecar["mark_out"], 3.0)

            # No sidecar path / missing marks → full.
            full = resolve_job_use_window(
                job={},
                parent_content_id="f" * 64,
                media_meta=media_meta,
                media_abs=None,
                con=None,
            )
            self.assertEqual(full["source"], "full")
            self.assertEqual(full["skip_first_frames"], 0)

            # Default clip beats sidecar.
            reg = root / "asset_registry.sqlite"
            con = connect_clips(reg)
            parent = "g" * 64
            clip = create_clip(
                con,
                parent_content_id=parent,
                mark_in_s=2.0,
                mark_out_s=4.0,
                label="ClipWins",
                duration_s=10.0,
            )
            set_default_clip(con, parent, clip["clip_id"])
            defaulted = resolve_job_use_window(
                job={},
                parent_content_id=parent,
                media_meta=media_meta,
                media_abs=media_path,
                con=con,
            )
            self.assertEqual(defaulted["source"], "starred")
            self.assertEqual(defaulted["clip_id"], clip["clip_id"])
            self.assertEqual(defaulted["skip_first_frames"], 20)
            self.assertEqual(defaulted["frame_load_cap"], 20)
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
            parents = lib.get("parents") or []
            self.assertEqual(len(parents), 1)
            self.assertEqual(parents[0]["media_relpath"], "og/demo/parent.mp4")
            self.assertEqual(parents[0]["clip_count"], 1)
            self.assertTrue(parents[0]["has_default"])
            by_media = list_clips_library(con, media_relpath="og/demo/parent.mp4")
            self.assertEqual(by_media["total"], 2)
            self.assertEqual(by_media["filters"]["media_relpath"], "og/demo/parent.mp4")
            con.close()

    def test_list_clip_derived_videos(self) -> None:
        import json

        from shape_factory_clips import connect_clips, create_clip, list_clip_derived_videos

        with _tmpdir() as td:
            root = Path(td)
            reg = root / "asset_registry.sqlite"
            jobs = root / "jobs" / "FB9_GEX"
            out = root / "output"
            jobs.mkdir(parents=True)
            out.mkdir(parents=True)
            con = connect_clips(reg)
            parent = "f" * 64
            con.execute(
                """
                INSERT INTO assets(
                    content_id, size, mtime, ext, kind, width, height,
                    current_relpath, first_seen, last_seen, status
                ) VALUES (?, 1, 1.0, '.mp4', 'video', NULL, NULL, ?, 't', 't', 'present')
                """,
                (parent, "og/demo/parent.mp4"),
            )
            clip = create_clip(
                con,
                parent_content_id=parent,
                mark_in_s=0.0,
                mark_out_s=2.0,
                label="Seed",
                origin="test",
            )
            prefix = "og/demo/derived_out"
            (out / "og" / "demo").mkdir(parents=True)
            video = out / f"{prefix}_00001.mp4"
            video.write_bytes(b"\x00\x00")
            job = {
                "job_key": "demo_derived_job",
                "family_slug": "FB9_GEX",
                "created_at": "2026-08-09T00:00:00Z",
                "source_clip_id": clip["clip_id"],
                "vhs_window": {"clip_id": clip["clip_id"], "source": "source_clip"},
                "output_prefix": prefix,
                "submit": {"status": "deposited"},
                "deposit": {"deposited_at": "2026-08-09T00:01:00Z", "videos": [str(video)]},
            }
            (jobs / "demo_derived_job.job.json").write_text(json.dumps(job), encoding="utf-8")
            res = list_clip_derived_videos(
                jobs_root=root / "jobs",
                output_root=out,
                con=con,
                clip_id=clip["clip_id"],
            )
            self.assertEqual(res["total"], 1)
            row = res["items"][0]
            self.assertEqual(row["source_clip_id"], clip["clip_id"])
            self.assertEqual(row["output_relpath"], f"{prefix}_00001.mp4")
            self.assertEqual(row["clip_label"], "Seed")
            con.close()

    def test_soft_delete_restore_hides_from_lists(self) -> None:
        from shape_factory_clips import (
            connect_clips,
            create_clip,
            delete_clip,
            get_clip,
            get_default_clip_id,
            list_clips_for_parent,
            list_clips_library,
            restore_clip,
            set_default_clip,
            soft_delete_clip,
        )

        with _tmpdir() as td:
            reg = Path(td) / "asset_registry.sqlite"
            con = connect_clips(reg)
            parent = "c" * 64
            clip = create_clip(
                con,
                parent_content_id=parent,
                mark_in_s=1.0,
                mark_out_s=4.0,
                label="KeepMe",
                origin="test",
                duration_s=10.0,
            )
            cid = clip["clip_id"]
            set_default_clip(con, parent, cid)
            self.assertEqual(get_default_clip_id(con, parent), cid)

            retired = soft_delete_clip(con, cid)
            assert retired is not None
            self.assertTrue(retired["deleted"])
            self.assertTrue(retired["deleted_at"])
            self.assertIsNone(get_default_clip_id(con, parent))
            self.assertEqual(list_clips_for_parent(con, parent), [])
            self.assertEqual(len(list_clips_for_parent(con, parent, include_deleted=True)), 1)

            lib = list_clips_library(con, limit=50)
            self.assertEqual(lib["total"], 0)
            lib_ret = list_clips_library(con, limit=50, deleted_only=True)
            self.assertEqual(lib_ret["total"], 1)
            self.assertEqual(lib_ret["clips"][0]["clip_id"], cid)

            # Explicit get still works (history / restore)
            still = get_clip(con, cid)
            assert still is not None
            self.assertTrue(still["deleted"])

            restored = restore_clip(con, cid)
            assert restored is not None
            self.assertFalse(restored["deleted"])
            self.assertIsNone(restored["deleted_at"])
            self.assertEqual(len(list_clips_for_parent(con, parent)), 1)

            # Default delete_clip is soft
            self.assertTrue(delete_clip(con, cid))
            self.assertTrue(get_clip(con, cid)["deleted"])  # type: ignore[index]
            con.close()

    def test_used_unused_library_and_hard_delete_guard(self) -> None:
        import json

        from shape_factory_clips import (
            collect_used_clip_ids,
            connect_clips,
            create_clip,
            delete_clip,
            get_clip,
            list_clips_library,
        )

        with _tmpdir() as td:
            root = Path(td)
            reg = root / "asset_registry.sqlite"
            jobs = root / "jobs" / "FB9"
            jobs.mkdir(parents=True)
            con = connect_clips(reg)
            parent = "d" * 64
            used_clip = create_clip(
                con,
                parent_content_id=parent,
                mark_in_s=0.0,
                mark_out_s=2.0,
                label="Used",
                origin="test",
                duration_s=10.0,
            )
            unused_clip = create_clip(
                con,
                parent_content_id=parent,
                mark_in_s=3.0,
                mark_out_s=5.0,
                label="Unused",
                origin="test",
                duration_s=10.0,
            )
            job = {
                "job_key": "demo_use",
                "source_clip_id": used_clip["clip_id"],
                "created_at": "2026-08-09T00:00:00Z",
            }
            (jobs / "demo_use.job.json").write_text(json.dumps(job), encoding="utf-8")

            counts = collect_used_clip_ids(root / "jobs")
            self.assertEqual(counts.get(used_clip["clip_id"]), 1)
            self.assertNotIn(unused_clip["clip_id"], counts)

            lib = list_clips_library(con, jobs_root=root / "jobs", limit=50)
            by_id = {c["clip_id"]: c for c in lib["clips"]}
            self.assertTrue(by_id[used_clip["clip_id"]]["used"])
            self.assertEqual(by_id[used_clip["clip_id"]]["use_count"], 1)
            self.assertFalse(by_id[unused_clip["clip_id"]]["used"])

            unused_lib = list_clips_library(con, jobs_root=root / "jobs", unused_only=True, limit=50)
            self.assertEqual([c["clip_id"] for c in unused_lib["clips"]], [unused_clip["clip_id"]])
            used_lib = list_clips_library(con, jobs_root=root / "jobs", used_only=True, limit=50)
            self.assertEqual([c["clip_id"] for c in used_lib["clips"]], [used_clip["clip_id"]])

            used_first = list_clips_library(con, jobs_root=root / "jobs", sort="most_used", limit=50)
            self.assertEqual(used_first["sort"], "most_used")
            self.assertEqual(used_first["clips"][0]["clip_id"], used_clip["clip_id"])
            self.assertEqual(used_first["clips"][0]["use_count"], 1)

            from shape_factory_clips import star_clip

            star_clip(con, unused_clip["clip_id"])
            popular = list_clips_library(con, jobs_root=root / "jobs", sort="most_popular", limit=50)
            self.assertEqual(popular["clips"][0]["clip_id"], unused_clip["clip_id"])
            self.assertTrue(popular["clips"][0]["is_starred"])

            with self.assertRaises(ValueError) as ctx:
                delete_clip(con, used_clip["clip_id"], hard=True, jobs_root=root / "jobs")
            self.assertIn("clip_in_use", str(ctx.exception))
            self.assertIsNotNone(get_clip(con, used_clip["clip_id"]))

            self.assertTrue(delete_clip(con, unused_clip["clip_id"], hard=True, jobs_root=root / "jobs"))
            self.assertIsNone(get_clip(con, unused_clip["clip_id"]))
            con.close()

    def test_list_clips_library_sort_by_parent_rating(self) -> None:
        from shape_factory_clips import connect_clips, create_clip, list_clips_library

        with _tmpdir() as td:
            reg = Path(td) / "asset_registry.sqlite"
            con = connect_clips(reg)
            hi = "a" * 64
            lo = "b" * 64
            con.execute(
                """
                INSERT INTO assets(
                    content_id, size, mtime, ext, kind, width, height,
                    current_relpath, first_seen, last_seen, status
                ) VALUES (?, 1, 1.0, '.mp4', 'video', NULL, NULL, ?, 't', 't', 'present')
                """,
                (hi, "og/hi.mp4"),
            )
            con.execute(
                """
                INSERT INTO assets(
                    content_id, size, mtime, ext, kind, width, height,
                    current_relpath, first_seen, last_seen, status
                ) VALUES (?, 1, 1.0, '.mp4', 'video', NULL, NULL, ?, 't', 't', 'present')
                """,
                (lo, "og/lo.mp4"),
            )
            c_hi = create_clip(con, parent_content_id=hi, mark_in_s=0, mark_out_s=1, label="Hi")
            c_lo = create_clip(con, parent_content_id=lo, mark_in_s=0, mark_out_s=1, label="Lo")
            ratings = {"by_source_basename": {"hi.mp4": {"inferred": 4.5}, "lo.mp4": {"inferred": 2.0}}}
            lib = list_clips_library(con, sort="rating", ratings_doc=ratings, limit=50)
            self.assertEqual(lib["sort"], "rating")
            self.assertEqual(lib["clips"][0]["clip_id"], c_hi["clip_id"])
            self.assertAlmostEqual(float(lib["clips"][0]["parent_rating"]), 4.5)
            self.assertEqual(lib["clips"][1]["clip_id"], c_lo["clip_id"])
            con.close()

    def test_list_clips_library_sql_sorts_longest_and_label(self) -> None:
        from shape_factory_clips import connect_clips, create_clip, list_clips_library

        with _tmpdir() as td:
            reg = Path(td) / "asset_registry.sqlite"
            con = connect_clips(reg)
            parent = "c" * 64
            con.execute(
                """
                INSERT INTO assets(
                    content_id, size, mtime, ext, kind, width, height,
                    current_relpath, first_seen, last_seen, status
                ) VALUES (?, 1, 1.0, '.mp4', 'video', NULL, NULL, ?, 't', 't', 'present')
                """,
                (parent, "og/sort.mp4"),
            )
            short = create_clip(
                con, parent_content_id=parent, mark_in_s=0.0, mark_out_s=1.0, label="Zebra"
            )
            long = create_clip(
                con, parent_content_id=parent, mark_in_s=0.0, mark_out_s=9.0, label="Alpha"
            )
            by_len = list_clips_library(con, sort="longest", limit=50)
            self.assertEqual(by_len["sort"], "longest")
            self.assertEqual(by_len["clips"][0]["clip_id"], long["clip_id"])
            by_label = list_clips_library(con, sort="label", limit=50)
            self.assertEqual(by_label["clips"][0]["clip_id"], long["clip_id"])
            self.assertEqual(by_label["clips"][1]["clip_id"], short["clip_id"])
            unused = list_clips_library(con, jobs_root=Path(td) / "jobs", sort="unused_first", limit=50)
            self.assertEqual(unused["sort"], "unused_first")
            con.close()

    def test_whole_window_and_near_dup_helpers(self) -> None:
        from shape_factory_clips import is_whole_asset_window, marks_near_existing

        self.assertTrue(is_whole_asset_window(0.0, 10.0, 10.0))
        self.assertTrue(is_whole_asset_window(0.1, 9.95, 10.0))
        self.assertFalse(is_whole_asset_window(1.0, 3.0, 10.0))
        self.assertFalse(is_whole_asset_window(0.0, 5.0, 10.0))

        existing = [{"mark_in_s": 1.0, "mark_out_s": 2.0}]
        self.assertTrue(marks_near_existing(1.0, 2.0, existing))
        self.assertTrue(marks_near_existing(1.02, 2.01, existing))
        self.assertFalse(marks_near_existing(1.0, 3.0, existing))

    def test_mine_clips_from_jobs_skips_whole_and_dup(self) -> None:
        import json
        from unittest import mock

        import asset_registry as areg
        from shape_factory_clips import (
            connect_clips,
            create_clip,
            list_clips_for_parent,
            mine_clips_from_jobs,
        )

        with _tmpdir() as td:
            root = Path(td)
            out = root / "output"
            out.mkdir()
            media = out / "src.mp4"
            media.write_bytes(b"\x00\x00")
            jobs = root / "jobs" / "FB9"
            jobs.mkdir(parents=True)
            reg = root / "asset_registry.sqlite"

            def _job(key: str, win: dict) -> None:
                doc = {
                    "job_key": key,
                    "family_slug": "FB9",
                    "bindings": {"source_video": {"path": str(media)}},
                    "vhs_window": win,
                }
                (jobs / f"{key}.job.json").write_text(json.dumps(doc), encoding="utf-8")

            _job("whole", {"mark_in": 0.0, "mark_out": 10.0})
            _job("partial_dup", {"mark_in": 1.0, "mark_out": 3.0})
            _job("empty", {"skip_first_frames": 0, "frame_load_cap": 0})
            _job("fresh", {"mark_in": 4.0, "mark_out": 6.0})

            con = connect_clips(reg)
            parent = areg.register(con, media, relpath="src.mp4", kind="video", with_dims=False)
            create_clip(
                con,
                parent_content_id=parent,
                mark_in_s=1.0,
                mark_out_s=3.0,
                label="Already",
                origin="test",
                duration_s=10.0,
            )
            con.close()

            probe = {"fps": 10.0, "duration": 10.0, "frame_count": 100}
            with mock.patch("shape_factory_queue._probe_media_frame_meta", return_value=probe):
                with mock.patch("shape_factory_queue.hostify_media_abs", return_value=media):
                    dry = mine_clips_from_jobs(
                        jobs_root=root / "jobs",
                        output_root=out,
                        registry_path=reg,
                        apply=False,
                        limit=50,
                    )
            self.assertTrue(dry["ok"])
            self.assertGreaterEqual(dry["skipped_whole"], 1)
            self.assertGreaterEqual(dry["skipped_dup"], 1)
            self.assertEqual(dry["would_create"], 1)
            self.assertAlmostEqual(dry["candidates"][0]["mark_in_s"], 4.0, places=2)

            with mock.patch("shape_factory_queue._probe_media_frame_meta", return_value=probe):
                with mock.patch("shape_factory_queue.hostify_media_abs", return_value=media):
                    applied = mine_clips_from_jobs(
                        jobs_root=root / "jobs",
                        output_root=out,
                        registry_path=reg,
                        apply=True,
                        limit=50,
                    )
            self.assertTrue(applied["ok"])
            self.assertEqual(applied["clips_created"], 1)
            con = connect_clips(reg)
            clips = list_clips_for_parent(con, parent)
            self.assertTrue(any(abs(c["mark_in_s"] - 4.0) < 0.05 for c in clips))
            con.close()
            # Re-apply is idempotent (dup skip). Close first so SQLite isn't locked.
            with mock.patch("shape_factory_queue._probe_media_frame_meta", return_value=probe):
                with mock.patch("shape_factory_queue.hostify_media_abs", return_value=media):
                    again = mine_clips_from_jobs(
                        jobs_root=root / "jobs",
                        output_root=out,
                        registry_path=reg,
                        apply=True,
                        limit=50,
                    )
            self.assertEqual(again["clips_created"], 0)
            self.assertGreaterEqual(again["skipped_dup"], 1)

    def test_multi_star_pick_prefers_newer_and_skips_unstarred(self) -> None:
        import random

        from shape_factory_clips import (
            connect_clips,
            create_clip,
            list_starred_clip_ids,
            pick_seed_clip,
            resolve_job_use_window,
            star_clip,
            unstar_clip,
        )

        with _tmpdir() as td:
            reg = Path(td) / "asset_registry.sqlite"
            con = connect_clips(reg)
            parent = "e" * 64
            old = create_clip(
                con,
                parent_content_id=parent,
                mark_in_s=0.0,
                mark_out_s=1.0,
                label="Old",
                origin="test",
                duration_s=10.0,
            )
            new = create_clip(
                con,
                parent_content_id=parent,
                mark_in_s=2.0,
                mark_out_s=3.0,
                label="New",
                origin="test",
                duration_s=10.0,
            )
            orphan = create_clip(
                con,
                parent_content_id=parent,
                mark_in_s=4.0,
                mark_out_s=5.0,
                label="Orphan",
                origin="test",
                duration_s=10.0,
            )
            star_clip(con, old["clip_id"])
            star_clip(con, new["clip_id"])
            # Bump new clip updated_at so deterministic newest wins.
            con.execute(
                "UPDATE clips SET updated_at=? WHERE clip_id=?",
                ("2099-01-01T00:00:00Z", new["clip_id"]),
            )
            con.commit()
            self.assertEqual(set(list_starred_clip_ids(con, parent)), {old["clip_id"], new["clip_id"]})

            media = {"fps": 10.0, "frame_count": 100, "duration": 10.0}
            picked = pick_seed_clip(con, parent, media_meta=media, rng=None)
            self.assertEqual(picked["source"], "starred")
            self.assertEqual(picked["clip_id"], new["clip_id"])

            # Unstarred orphan must not be auto-selected.
            for _ in range(20):
                use = resolve_job_use_window(
                    job={"job_key": f"j{_}"},
                    parent_content_id=parent,
                    media_meta=media,
                    con=con,
                    rng=random.Random(_),
                )
                self.assertEqual(use["source"], "starred")
                self.assertIn(use["clip_id"], {old["clip_id"], new["clip_id"]})
                self.assertNotEqual(use["clip_id"], orphan["clip_id"])

            unstar_clip(con, old["clip_id"])
            unstar_clip(con, new["clip_id"])
            none = pick_seed_clip(con, parent, media_meta=media, rng=None)
            self.assertEqual(none["source"], "full")
            self.assertIsNone(none.get("clip_id"))
            con.close()


if __name__ == "__main__":
    unittest.main()
