#!/usr/bin/env python3
"""Tests for tag judgment queue + scorer."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401

from vision_tag_judgment_api import get_tag_judgment_payload, save_tag_judgment
from vision_tag_judgment_queue import build_judgment_queue
from vision_tag_judgment_score import score_tag_judgments
from vision_tag_judgment_tags import parse_danbooru_tags


class VisionTagJudgmentTests(unittest.TestCase):
    def test_parse_tags(self) -> None:
        tags = parse_danbooru_tags("1girl, solo, blue eyes, long hair, smile")
        self.assertGreaterEqual(len(tags), 3)
        self.assertIn("1girl", tags)
        self.assertEqual(parse_danbooru_tags("A long prose sentence about the scene."), [])

    def test_queue_and_score(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            status = Path(td)
            base_rows = [
                {
                    "asset_relpath": "og/a.mp4",
                    "t0": 0.0,
                    "t1": 2.0,
                    "frame_t": 1.0,
                    "slice": "window",
                    "caption": "1girl, solo, blue eyes, long hair, toothbrush",
                },
                {
                    "asset_relpath": "og/b.mp4",
                    "t0": 0.0,
                    "t1": 2.0,
                    "frame_t": 1.0,
                    "slice": "window",
                    "caption": "1girl, sitting, outdoor, tree, smile",
                },
            ]
            large_rows = [
                {
                    "asset_relpath": "og/a.mp4",
                    "t0": 0.0,
                    "t1": 2.0,
                    "frame_t": 1.0,
                    "slice": "window",
                    "caption": "1girl, solo, blue eyes, long hair, freckles",
                },
                {
                    "asset_relpath": "og/b.mp4",
                    "t0": 0.0,
                    "t1": 2.0,
                    "frame_t": 1.0,
                    "slice": "window",
                    "caption": "1girl, sitting, outdoor, tree, hat",
                },
            ]
            (status / "vision_slice_captions__cohort_x2_pg_tags.ndjson").write_text(
                "\n".join(json.dumps(r) for r in base_rows) + "\n", encoding="utf-8"
            )
            (status / "vision_slice_captions__cohort_x2_pg_large_tags.ndjson").write_text(
                "\n".join(json.dumps(r) for r in large_rows) + "\n", encoding="utf-8"
            )

            q = build_judgment_queue(status, target_samples=10, seed=1)
            self.assertEqual(q["item_count"], 2)
            item = q["items"][0]
            self.assertIn("emitted_by", item)
            self.assertTrue(item["tags"])

            payload = get_tag_judgment_payload(status)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["total_count"], 2)
            # Blind: no emitted_by in public items
            self.assertNotIn("emitted_by", payload["items"][0])

            sid = payload["items"][0]["sample_id"]
            tags = payload["items"][0]["tags"]
            labels = {tags[0]: "good", tags[1]: "bad"}
            saved = save_tag_judgment(
                status,
                {
                    "sample_id": sid,
                    "asset_relpath": payload["items"][0]["asset_relpath"],
                    "t0": payload["items"][0]["t0"],
                    "t1": payload["items"][0]["t1"],
                    "slice": "window",
                    "labels": labels,
                    "important": [tags[0]],
                },
            )
            self.assertTrue(saved["ok"])
            self.assertEqual(saved["done_count"], 1)
            self.assertIsNotNone(saved.get("leaderboard"))
            self.assertEqual(saved["saved"]["important"], [tags[0]])

            board = score_tag_judgments(status)
            self.assertEqual(board["judged_samples"], 1)
            self.assertTrue(board["models"])
            ids = {m["id"] for m in board["models"]}
            self.assertIn("cohort_x2_pg_tags", ids)
            self.assertIn("important_recall", board["models"][0])
            ts = board.get("tag_stats") or {}
            self.assertGreaterEqual(int(ts.get("tag_count") or 0), 1)
            self.assertTrue(ts.get("commonly_important") is not None)
            self.assertTrue((status / "vision_tag_judgment_tag_stats.json").is_file())

            again = get_tag_judgment_payload(status)
            self.assertIn(tags[0], again["important_vocabulary"])
            self.assertIn(tags[0], again["items"][0].get("important") or [])

            # Second sample (unjudged): chronic FP from tags[1] should suggest bad.
            # Mark tags[1] bad enough times via first sample only once — need min_n=2.
            # Save another judgment with same bad tag on sample 0 already has tags[1]=bad once;
            # add a second save by judging sample 1 with tags[1] bad if present.
            item1 = again["items"][1]
            sid1 = item1["sample_id"]
            # Force priors: update first judgment to also have a shared bad tag history.
            # Re-save sample 0 with two bad marks... priors need 2 labels of same tag.
            # Easiest: save sample 1 with labels including a tag marked bad on sample 0.
            shared_bad = tags[1]
            save_tag_judgment(
                status,
                {
                    "sample_id": sid1,
                    "asset_relpath": item1["asset_relpath"],
                    "t0": item1["t0"],
                    "t1": item1["t1"],
                    "slice": "window",
                    "labels": {shared_bad: "bad", (item1["tags"] or [shared_bad])[0]: "good"},
                    "important": [],
                },
            )
            # Clear sample 1 judgment to test suggestion on a fresh item — simulate by
            # checking priors list includes shared_bad after 2 bad marks.
            payload2 = get_tag_judgment_payload(status)
            self.assertIn(shared_bad, payload2.get("label_priors", {}).get("default_bad_tags") or [])

            # Chronic good prior (e.g. 1girl): two good marks → default_good.
            shared_good = tags[0]
            save_tag_judgment(
                status,
                {
                    "sample_id": sid1,
                    "asset_relpath": item1["asset_relpath"],
                    "t0": item1["t0"],
                    "t1": item1["t1"],
                    "slice": "window",
                    "labels": {shared_bad: "bad", shared_good: "good"},
                    "important": [],
                },
            )
            payload3 = get_tag_judgment_payload(status)
            goods = payload3.get("label_priors", {}).get("default_good_tags") or []
            self.assertIn(shared_good, goods)

            # Missing pass: ★ important tags absent from the union — mark if they should have been.
            item0 = payload3["items"][0]
            # Force a known-absent tag (simulates ★-class FN not yet in vocab / freeform).
            miss_tag = "definitely_missing_tag_xyz"
            self.assertNotIn(miss_tag, item0.get("tags") or [])
            # Candidates are ★ important only (not prior-good).
            for it in payload3["items"]:
                cand = set(it.get("missing_candidates") or [])
                tag_set = {str(t).strip().lower() for t in (it.get("tags") or [])}
                for t in cand:
                    self.assertIn(t, payload3.get("important_vocabulary") or [])
                    self.assertNotIn(t, tag_set)
            saved_m = save_tag_judgment(
                status,
                {
                    "sample_id": item0["sample_id"],
                    "asset_relpath": item0["asset_relpath"],
                    "t0": item0["t0"],
                    "t1": item0["t1"],
                    "slice": "window",
                    "labels": item0.get("labels") or {tags[0]: "good", tags[1]: "bad"},
                    "important": [tags[0]],
                    "missing": [miss_tag],
                },
            )
            self.assertTrue(saved_m["ok"])
            self.assertEqual(saved_m["saved"]["missing"], [miss_tag])
            board2 = score_tag_judgments(status)
            self.assertGreaterEqual(int(board2.get("missing_tags") or 0), 1)
            self.assertTrue(board2["models"])
            self.assertIn("missing_n", board2["models"][0])
            ts2 = board2.get("tag_stats") or {}
            miss_tags = {r["tag"] for r in (ts2.get("commonly_missing") or [])}
            self.assertIn(miss_tag, miss_tags)
            payload4 = get_tag_judgment_payload(status)
            self.assertIn(miss_tag, payload4.get("missing_vocabulary") or [])
            # After ★ on tags[0], samples lacking that tag list it as a missing candidate.
            for it in payload4["items"]:
                tag_set = {str(t).strip().lower() for t in (it.get("tags") or [])}
                if tags[0] not in tag_set:
                    self.assertIn(tags[0], it.get("missing_candidates") or [])
                    break
            else:
                # All samples contain tags[0] — still ok as long as candidates ⊆ important vocab.
                pass


if __name__ == "__main__":
    unittest.main()
