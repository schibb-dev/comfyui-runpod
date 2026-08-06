#!/usr/bin/env python3
"""Tests for shape_factory_job_output_index."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401
from shape_factory_job_output_index import (
    construction_summary_from_job,
    extension_range_from_row,
    job_key_guess_from_output_basename,
    lookup_by_relpath,
    normalize_output_relpath,
    open_job_output_index,
    rebuild_job_output_index,
    upsert_from_job,
)


class JobOutputIndexTests(unittest.TestCase):
    def test_normalize_and_guess(self) -> None:
        self.assertEqual(
            normalize_output_relpath("output/og/2026/foo.mp4"),
            "og/2026/foo.mp4",
        )
        self.assertEqual(
            job_key_guess_from_output_basename("hourly__pp-x__000_202608061901_FINAL_00001.mp4"),
            "hourly__pp-x__000_202608061901",
        )

    def test_upsert_and_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            jobs = root / "jobs"
            jobs.mkdir()
            job = {
                "job_key": "job_a",
                "family_slug": "FB9_GEX",
                "pick_mode": "extend",
                "construction": {"frames_before": 80},
                "timings": {"workload": {"frames": 80, "output_frame_count": 160, "overlap": 16}},
                "deposit": {"videos": [str(root / "og" / "clip_FINAL_00001.mp4")]},
            }
            (jobs / "job_a.job.json").write_text("{}", encoding="utf-8")
            idx = root / "job_output_index.sqlite"
            con = open_job_output_index(idx)
            try:
                n = upsert_from_job(con, job, job_path=jobs / "job_a.job.json", output_root=root)
                self.assertEqual(n, 1)
                row = lookup_by_relpath(con, "og/clip_FINAL_00001.mp4", output_root=root)
                self.assertIsNotNone(row)
                assert row is not None
                self.assertEqual(row["job_key"], "job_a")
                self.assertEqual(row["frames_before"], 80)
                er = extension_range_from_row(row)
                self.assertEqual(er["generation_frames"], 80)
                self.assertEqual(er["overlap"], 16)
            finally:
                con.close()

    def test_rebuild_scans_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            jobs = root / "shape_factory" / "jobs" / "FB9"
            jobs.mkdir(parents=True)
            job = {
                "job_key": "job_b",
                "family_slug": "FB9_GEX",
                "pick_mode": "derive",
                "timings": {"workload": {"frames": 40, "output_frame_count": 40}},
                "submit": {"outputs": [str(root / "output" / "og" / "b.mp4")]},
            }
            import json

            (jobs / "job_b.job.json").write_text(json.dumps(job), encoding="utf-8")
            idx = root / "_status" / "job_output_index.sqlite"
            result = rebuild_job_output_index(
                index_path=idx,
                jobs_root=root / "shape_factory" / "jobs",
                output_root=root / "output",
            )
            self.assertTrue(result["ok"])
            self.assertGreaterEqual(result["rows_upserted"], 1)
            con = open_job_output_index(idx)
            try:
                row = lookup_by_relpath(con, "og/b.mp4", output_root=root / "output")
                self.assertIsNotNone(row)
            finally:
                con.close()

    def test_construction_summary(self) -> None:
        s = construction_summary_from_job(
            {
                "pick_mode": "extend",
                "family_slug": "X",
                "timings": {"workload": {"frames": 10, "output_frame_count": 20}},
            }
        )
        self.assertEqual(s["generation_frames"], 10)
        self.assertEqual(s["output_frame_count"], 20)


if __name__ == "__main__":
    unittest.main()
