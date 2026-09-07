from __future__ import annotations

import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from shape_factory_pending_queue import (
    apply_hourly_pending_drain_floor,
    compact_pending_ranks,
    count_hourly_pending_jobs,
    enqueue_pending_job,
    is_pending_queue_job,
    move_pending_job,
    parse_queue_destination,
    pending_queue_sort_key,
    reorder_pending_jobs,
)


def _write_job(root: Path, key: str, *, created: str, rank=None, status="pending", prompt_id=None) -> Path:
    family = root / "Fam"
    family.mkdir(parents=True, exist_ok=True)
    path = family / f"{key}.job.json"
    submit = {"status": status}
    if rank is not None:
        submit["pending_rank"] = rank
    if prompt_id:
        submit["prompt_id"] = prompt_id
    path.write_text(
        json.dumps({"job_key": key, "created_at": created, "submit": submit}),
        encoding="utf-8",
    )
    return path


class PendingQueueTests(unittest.TestCase):
    def test_unranked_sorts_fifo_by_created_at(self) -> None:
        older = {"job_key": "a", "created_at": "2026-01-01T00:00:00+00:00", "submit": {"status": "pending"}}
        newer = {"job_key": "b", "created_at": "2026-01-02T00:00:00+00:00", "submit": {"status": "pending"}}
        self.assertLess(pending_queue_sort_key(older), pending_queue_sort_key(newer))

    def test_explicit_rank_beats_created_at(self) -> None:
        late = {
            "job_key": "late",
            "created_at": "2026-01-01T00:00:00+00:00",
            "submit": {"status": "pending", "pending_rank": 2},
        }
        early = {
            "job_key": "early",
            "created_at": "2026-01-03T00:00:00+00:00",
            "submit": {"status": "pending", "pending_rank": 0},
        }
        self.assertLess(pending_queue_sort_key(early), pending_queue_sort_key(late))

    def test_queued_job_is_not_pending_queue(self) -> None:
        self.assertFalse(
            is_pending_queue_job({"submit": {"status": "queued", "prompt_id": "p1"}})
        )
        self.assertTrue(is_pending_queue_job({"submit": {"status": "pending"}}))
        self.assertTrue(is_pending_queue_job({"created_at": "t"}))

    def test_enqueue_append_and_front(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = _write_job(root, "first", created="2026-01-01T00:00:00+00:00")
            second = _write_job(root, "second", created="2026-01-02T00:00:00+00:00")
            enqueue_pending_job(first, jobs_dir=root, position="append")
            enqueue_pending_job(second, jobs_dir=root, position="append")
            queue = compact_pending_ranks(jobs_dir=root)
            self.assertEqual([r["job_key"] for r in queue], ["first", "second"])

            third = _write_job(root, "third", created="2026-01-03T00:00:00+00:00")
            enqueue_pending_job(third, jobs_dir=root, position="front")
            queue = compact_pending_ranks(jobs_dir=root)
            self.assertEqual([r["job_key"] for r in queue], ["third", "first", "second"])

    def test_move_and_reorder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = _write_job(root, "a", created="2026-01-01T00:00:00+00:00")
            b = _write_job(root, "b", created="2026-01-02T00:00:00+00:00")
            c = _write_job(root, "c", created="2026-01-03T00:00:00+00:00")
            enqueue_pending_job(a, jobs_dir=root)
            enqueue_pending_job(b, jobs_dir=root)
            enqueue_pending_job(c, jobs_dir=root)
            move_pending_job(jobs_dir=root, job_key="c", delta=-2)
            queue = compact_pending_ranks(jobs_dir=root)
            self.assertEqual([r["job_key"] for r in queue], ["c", "a", "b"])
            reorder_pending_jobs(jobs_dir=root, job_keys=["b", "c", "a"])
            queue = compact_pending_ranks(jobs_dir=root)
            self.assertEqual([r["job_key"] for r in queue], ["b", "c", "a"])

    def test_destination_parse(self) -> None:
        self.assertEqual(parse_queue_destination({"destination": "pending"}), "pending")
        self.assertEqual(parse_queue_destination({"skip_submit": True}), "pending")
        self.assertEqual(parse_queue_destination({"front": True}), "comfy")
        self.assertEqual(parse_queue_destination({}), "comfy")

    def test_drain_iter_is_fifo(self) -> None:
        import shape_factory as sf

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "jobs"
            old = _write_job(root, "old", created="2026-01-01T00:00:00+00:00")
            new = _write_job(root, "new", created="2026-01-02T00:00:00+00:00")
            enqueue_pending_job(old, jobs_dir=root)
            enqueue_pending_job(new, jobs_dir=root)
            args = Namespace(job=None, jobs_dir=None, family="Fam", job_dir=str(root), limit=None)
            paths = sf.iter_pending_submit_job_paths(args)
            self.assertEqual([p.name for p in paths], ["old.job.json", "new.job.json"])

    def test_hourly_drain_floor_keeps_min_and_passes_operator_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hourlies = [
                _write_job(root, f"hourly__h{i}", created=f"2026-01-0{i}T00:00:00+00:00")
                for i in range(1, 6)
            ]
            op = _write_job(root, "FB8VA5-user", created="2026-01-09T00:00:00+00:00")
            for path in hourlies + [op]:
                enqueue_pending_job(path, jobs_dir=root)
            self.assertEqual(count_hourly_pending_jobs(root), 5)
            ranked = compact_pending_ranks(jobs_dir=root)
            paths = [Path(r["job_path"]) for r in ranked]
            kept = apply_hourly_pending_drain_floor(
                paths, pending_hourly_min=5, hourly_pending_count=5
            )
            self.assertEqual([p.name for p in kept], ["FB8VA5-user.job.json"])

            extra = _write_job(root, "hourly__h6", created="2026-01-10T00:00:00+00:00")
            enqueue_pending_job(extra, jobs_dir=root)
            ranked = compact_pending_ranks(jobs_dir=root)
            paths = [Path(r["job_path"]) for r in ranked]
            kept = apply_hourly_pending_drain_floor(
                paths, pending_hourly_min=5, hourly_pending_count=6
            )
            self.assertEqual(kept[0].name, "hourly__h1.job.json")
            self.assertIn("FB8VA5-user.job.json", [p.name for p in kept])


if __name__ == "__main__":
    unittest.main()
