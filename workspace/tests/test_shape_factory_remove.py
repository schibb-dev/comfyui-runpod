#!/usr/bin/env python3
"""Appetite=remove: hide, factory-block, and read-only lifecycle review."""

from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401

import shape_factory_input_curation as curation
from shape_factory_ratings import (
    appetite_blocks_factory,
    normalize_appetite,
    path_blocks_factory,
)
from shape_factory_remove_review import analyze_remove_item, build_remove_review, list_remove_relpaths

REPO_ROOT = Path(__file__).resolve().parents[2]


class AppetiteRemoveNormalizeTests(unittest.TestCase):
    def test_normalize_aliases(self) -> None:
        self.assertEqual(normalize_appetite("remove"), "remove")
        self.assertEqual(normalize_appetite("bin"), "remove")
        self.assertEqual(normalize_appetite("discard"), "remove")
        self.assertEqual(normalize_appetite("delete"), "remove")
        self.assertEqual(normalize_appetite("junk"), "remove")
        self.assertTrue(appetite_blocks_factory("remove"))
        self.assertFalse(appetite_blocks_factory("more"))

    def test_path_blocks_factory(self) -> None:
        doc = {"by_output_relpath": {"og/clip.mp4": {"appetite": "remove"}}}
        self.assertTrue(path_blocks_factory("og/clip.mp4", doc))
        self.assertTrue(path_blocks_factory("/tmp/output/og/clip.mp4", doc))
        self.assertFalse(path_blocks_factory("og/other.mp4", doc))
        self.assertFalse(path_blocks_factory("og/clip.mp4", {"by_output_relpath": {}}))


class StillCatalogRemoveTests(unittest.TestCase):
    def test_default_filters_exclude_remove(self) -> None:
        self.assertFalse(curation._still_appetite_matches("remove", ""))
        self.assertFalse(curation._still_appetite_matches("remove", "any"))
        self.assertFalse(curation._still_appetite_matches("remove", "none"))
        self.assertFalse(curation._still_appetite_matches("remove", "more"))
        self.assertTrue(curation._still_appetite_matches("remove", "remove"))
        self.assertTrue(curation._still_appetite_matches("more", ""))
        self.assertTrue(curation._still_appetite_matches("", ""))


class HourlyStillPickRemoveTests(unittest.TestCase):
    def test_pick_skips_remove_marked_stills(self) -> None:
        from shape_factory_hourly import _pick_input_still_from_members

        with tempfile.TemporaryDirectory() as td:
            keep = Path(td) / "keep.png"
            drop = Path(td) / "drop.png"
            keep.write_bytes(b"k")
            drop.write_bytes(b"d")
            appetite = {"by_output_relpath": {"drop.png": {"appetite": "remove"}}}
            picked, _meta = _pick_input_still_from_members(
                [keep, drop],
                rng=random.Random(1),
                family="FAM",
                recent_stills=set(),
                appetite_doc=appetite,
            )
            self.assertEqual(Path(picked).name, "keep.png")

    def test_pick_raises_when_all_stills_removed(self) -> None:
        from shape_factory_hourly import _pick_input_still_from_members

        with tempfile.TemporaryDirectory() as td:
            drop = Path(td) / "drop.png"
            drop.write_bytes(b"d")
            appetite = {"by_output_relpath": {"drop.png": {"appetite": "remove"}}}
            with self.assertRaises(ValueError) as ctx:
                _pick_input_still_from_members(
                    [drop],
                    rng=random.Random(1),
                    family="FAM",
                    recent_stills=set(),
                    appetite_doc=appetite,
                )
            self.assertIn("remove", str(ctx.exception).lower())


class QueueRemoveTests(unittest.TestCase):
    def test_queue_from_request_body_rejects_remove_source(self) -> None:
        from shape_factory_queue import queue_from_request_body

        appetite = {"by_output_relpath": {"og/clip.mp4": {"appetite": "remove"}}}
        with mock.patch("shape_factory_ratings.load_appetite_doc", return_value=appetite):
            with self.assertRaises(ValueError) as ctx:
                queue_from_request_body(
                    {"family_slug": "FAM", "bindings": {"source_video": "og/clip.mp4"}},
                    repo_root=REPO_ROOT,
                    workspace_root=REPO_ROOT / "workspace",
                    output_root=REPO_ROOT / "workspace" / "output",
                    comfy_server="http://127.0.0.1:8188",
                )
        self.assertIn("appetite_remove", str(ctx.exception))


class RemoveReviewTests(unittest.TestCase):
    def test_list_and_analyze_refs(self) -> None:
        appetite = {
            "by_output_relpath": {
                "og/gone.mp4": {"appetite": "remove", "facet": "both", "updated_at": "2026-09-08T12:00:00Z"},
                "og/keep.mp4": {"appetite": "more"},
            }
        }
        rows = list_remove_relpaths(appetite)
        self.assertEqual([r["relpath"] for r in rows], ["og/gone.mp4"])

    def test_list_collapses_output_og_and_og_keys(self) -> None:
        appetite = {
            "by_output_relpath": {
                "og/gone.mp4": {"appetite": "remove", "updated_at": "2026-09-08T12:00:00Z"},
                "output/og/gone.mp4": {"appetite": "remove", "updated_at": "2026-09-08T12:00:01Z"},
            }
        }
        rows = list_remove_relpaths(appetite)
        self.assertEqual([r["relpath"] for r in rows], ["og/gone.mp4"])

        as_output = {"og/gone.mp4": [{"job_key": "maker", "family": "FAM", "status": "complete"}]}
        as_source = {
            "og/gone.mp4": [
                {"job_key": "child", "family": "FAM", "status": "pending"},
            ]
        }
        pools = {"og/gone.mp4": [{"family": "FAM", "pool": "source_video", "path": "og/gone.mp4"}]}
        analysis = analyze_remove_item(
            "og/gone.mp4",
            as_output=as_output,
            as_source=as_source,
            pools=pools,
        )
        self.assertFalse(analysis["purge_ready"])
        self.assertGreater(len(analysis["blockers"]), 0)
        self.assertEqual(analysis["deletion"], "blocked")
        self.assertEqual(analysis["counts"]["inflight_source_jobs"], 1)

        lone = analyze_remove_item(
            "og/orphan.mp4",
            as_output={"og/orphan.mp4": [{"job_key": "orig", "status": "complete"}]},
            as_source={},
            pools={},
        )
        self.assertTrue(lone["purge_ready"])
        self.assertEqual(lone["blockers"], [])

    def test_build_remove_review_scans_jobs_and_pools(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            jobs = root / "jobs" / "FAM"
            jobs.mkdir(parents=True)
            pools = root / "pools" / "FAM"
            pools.mkdir(parents=True)
            appetite_path = root / "appetite_index.json"
            appetite_path.write_text(
                json.dumps(
                    {
                        "by_output_relpath": {
                            "og/gone.mp4": {"appetite": "remove", "updated_at": "2026-09-08T12:00:00Z"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            (jobs / "child.job.json").write_text(
                json.dumps(
                    {
                        "job_key": "child",
                        "family_slug": "FAM",
                        "output_prefix": "og/other.mp4",
                        "bindings": {"source_video": {"path": "og/gone.mp4"}},
                        "submit": {"status": "pending", "outputs": []},
                    }
                ),
                encoding="utf-8",
            )
            (pools / "index.json").write_text(
                json.dumps(
                    {
                        "pools": {
                            "source_video": {
                                "members": [{"path": "og/gone.mp4"}],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            payload = build_remove_review(
                appetite_index_path=appetite_path,
                jobs_dir=root / "jobs",
                pools_root=root / "pools",
            )
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["deletion"], "ready_only")
            item = payload["items"][0]
            self.assertEqual(item["relpath"], "og/gone.mp4")
            self.assertFalse(item["purge_ready"])
            self.assertEqual(item["counts"]["as_source_jobs"], 1)
            self.assertEqual(item["counts"]["pool_memberships"], 1)


class RemovePurgeTests(unittest.TestCase):
    def test_purge_ready_deletes_media_sidecars_and_ratings(self) -> None:
        from shape_factory_ratings import (
            load_appetite_doc,
            load_ratings_doc,
            lookup_output_appetite,
            lookup_output_rating,
            set_output_appetite,
            set_output_rating,
        )
        from shape_factory_remove_review import purge_remove_asset

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            og = root / "og"
            og.mkdir()
            media = og / "clip.mp4"
            media.write_bytes(b"fake-video")
            (og / "clip.XMP").write_text("<x:xmpmeta/>", encoding="utf-8")
            (og / "clip.png").write_bytes(b"png")
            jobs = root / "jobs"
            pools = root / "pools"
            jobs.mkdir()
            pools.mkdir()
            appetite_path = root / "appetite_index.json"
            ratings_path = root / "ratings_index.json"
            set_output_rating(
                media_abs=media,
                media_relpath="og/clip.mp4",
                stars=4,
                og_root=og,
                ratings_index_path=ratings_path,
            )
            set_output_appetite(
                media_abs=media,
                media_relpath="og/clip.mp4",
                appetite="remove",
                og_root=og,
                appetite_index_path=appetite_path,
            )
            # Producing job only — not a downstream dependency.
            (jobs / "maker.job.json").write_text(
                json.dumps(
                    {
                        "job_key": "maker",
                        "output_prefix": "og/clip.mp4",
                        "submit": {"status": "complete", "outputs": ["og/clip.mp4"]},
                    }
                ),
                encoding="utf-8",
            )
            out = purge_remove_asset(
                "og/clip.mp4",
                appetite_index_path=appetite_path,
                jobs_dir=jobs,
                pools_root=pools,
                search_roots=[root],
            )
            self.assertTrue(out.get("ok"), out)
            self.assertFalse(media.is_file())
            self.assertFalse((og / "clip.XMP").is_file())
            self.assertFalse((og / "clip.png").is_file())
            self.assertGreaterEqual(int(out.get("appetite_rows") or 0), 1)
            self.assertGreaterEqual(int(out.get("rating_rows") or 0), 1)
            self.assertIsNone(lookup_output_appetite("og/clip.mp4", load_appetite_doc(appetite_path)))
            self.assertIsNone(lookup_output_rating("og/clip.mp4", load_ratings_doc(ratings_path)))
            self.assertFalse((jobs / "maker.job.json").is_file())
            self.assertTrue((jobs / "maker.job.json.discarded").is_file())
            self.assertEqual((out.get("producing_jobs") or [{}])[0].get("action"), "archived")

    def test_purge_refuses_source_dependents(self) -> None:
        from shape_factory_ratings import set_output_appetite
        from shape_factory_remove_review import purge_remove_asset

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            og = root / "og"
            og.mkdir()
            media = og / "clip.mp4"
            media.write_bytes(b"fake")
            jobs = root / "jobs"
            jobs.mkdir()
            pools = root / "pools"
            pools.mkdir()
            appetite_path = root / "appetite_index.json"
            set_output_appetite(
                media_abs=media,
                media_relpath="og/clip.mp4",
                appetite="remove",
                og_root=og,
                appetite_index_path=appetite_path,
            )
            (jobs / "child.job.json").write_text(
                json.dumps(
                    {
                        "job_key": "child",
                        "bindings": {"source_video": {"path": "og/clip.mp4"}},
                        "submit": {"status": "pending"},
                    }
                ),
                encoding="utf-8",
            )
            out = purge_remove_asset(
                "og/clip.mp4",
                appetite_index_path=appetite_path,
                jobs_dir=jobs,
                pools_root=pools,
                search_roots=[root],
            )
            self.assertFalse(out.get("ok"))
            self.assertEqual(out.get("error"), "has_references")
            self.assertTrue(media.is_file())

    def test_purge_refuses_unmarked(self) -> None:
        from shape_factory_ratings import set_output_appetite
        from shape_factory_remove_review import purge_remove_asset

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            og = root / "og"
            og.mkdir()
            media = og / "clip.mp4"
            media.write_bytes(b"fake")
            appetite_path = root / "appetite_index.json"
            set_output_appetite(
                media_abs=media,
                media_relpath="og/clip.mp4",
                appetite="more",
                og_root=og,
                appetite_index_path=appetite_path,
            )
            out = purge_remove_asset(
                "og/clip.mp4",
                appetite_index_path=appetite_path,
                jobs_dir=root / "jobs",
                pools_root=root / "pools",
                search_roots=[root],
            )
            self.assertFalse(out.get("ok"))
            self.assertEqual(out.get("error"), "not_remove")
            self.assertTrue(media.is_file())

    def test_purge_keeps_producing_job_when_other_video_remains(self) -> None:
        from shape_factory_ratings import set_output_appetite
        from shape_factory_remove_review import purge_remove_asset

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            og = root / "og"
            og.mkdir()
            gone = og / "clip_00001.mp4"
            keep = og / "clip_00002.mp4"
            gone.write_bytes(b"a")
            keep.write_bytes(b"b")
            jobs = root / "jobs"
            jobs.mkdir()
            (root / "pools").mkdir()
            appetite_path = root / "appetite_index.json"
            set_output_appetite(
                media_abs=gone,
                media_relpath="og/clip_00001.mp4",
                appetite="remove",
                og_root=og,
                appetite_index_path=appetite_path,
            )
            job_path = jobs / "maker.job.json"
            job_path.write_text(
                json.dumps(
                    {
                        "job_key": "maker",
                        "output_prefix": "og/clip",
                        "submit": {
                            "status": "complete",
                            "outputs": ["og/clip_00001.mp4", "og/clip_00002.mp4"],
                        },
                    }
                ),
                encoding="utf-8",
            )
            out = purge_remove_asset(
                "og/clip_00001.mp4",
                appetite_index_path=appetite_path,
                jobs_dir=jobs,
                pools_root=root / "pools",
                search_roots=[root],
            )
            self.assertTrue(out.get("ok"), out)
            self.assertFalse(gone.is_file())
            self.assertTrue(keep.is_file())
            self.assertTrue(job_path.is_file())
            self.assertEqual((out.get("producing_jobs") or [{}])[0].get("action"), "stripped")
            saved = json.loads(job_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["submit"]["outputs"], ["og/clip_00002.mp4"])


if __name__ == "__main__":
    unittest.main()
