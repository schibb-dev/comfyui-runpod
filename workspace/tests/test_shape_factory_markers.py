#!/usr/bin/env python3
"""Tests for work-product markers store + decode.vae classification."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class MarkerValidationTests(unittest.TestCase):
    def test_key_validation(self) -> None:
        from shape_factory_markers import validate_key

        self.assertEqual(validate_key("decode.vae"), "decode.vae")
        self.assertEqual(validate_key("Note.Review"), "note.review")
        with self.assertRaises(ValueError):
            validate_key("decode")
        with self.assertRaises(ValueError):
            validate_key("Decode Vae")
        with self.assertRaises(ValueError):
            validate_key("")

    def test_value_rejects_nested(self) -> None:
        from shape_factory_markers import validate_value

        self.assertEqual(validate_value("tiled"), "tiled")
        with self.assertRaises(ValueError):
            validate_value({"a": 1})
        with self.assertRaises(ValueError):
            validate_value("")


class MarkerStoreTests(unittest.TestCase):
    def test_overwrite_policy_human_wins(self) -> None:
        from shape_factory_markers import connect, get_marker, set_marker

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "markers.sqlite"
            con = connect(db)
            try:
                cid = "a" * 64
                r1 = set_marker(con, cid, "decode.vae", "tiled", source="scan")
                self.assertFalse(r1.get("blocked"))
                r2 = set_marker(con, cid, "decode.vae", "plain", source="scan")
                self.assertFalse(r2.get("blocked"))
                self.assertEqual(get_marker(con, cid, "decode.vae")["value"], "plain")

                r3 = set_marker(con, cid, "decode.vae", "human-note", source="human")
                self.assertFalse(r3.get("blocked"))
                self.assertEqual(get_marker(con, cid, "decode.vae")["value"], "human-note")

                r4 = set_marker(con, cid, "decode.vae", "tiled", source="scan")
                self.assertTrue(r4.get("blocked"))
                self.assertEqual(get_marker(con, cid, "decode.vae")["value"], "human-note")

                r5 = set_marker(con, cid, "decode.vae", "tiled", source="scan", force=True)
                self.assertFalse(r5.get("blocked"))
                self.assertEqual(get_marker(con, cid, "decode.vae")["value"], "tiled")
            finally:
                con.close()

    def test_query_by_key(self) -> None:
        from shape_factory_markers import connect, query_by_key, set_marker

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "markers.sqlite"
            con = connect(db)
            try:
                set_marker(con, "c" * 64, "decode.vae", "tiled", source="scan")
                set_marker(con, "d" * 64, "decode.vae", "plain", source="scan")
                set_marker(con, "e" * 64, "note.review", "check", source="human")
                tiled = query_by_key(con, "decode.vae", value="tiled")
                self.assertEqual(len(tiled), 1)
                self.assertEqual(tiled[0]["content_id"], "c" * 64)
                all_decode = query_by_key(con, "decode.vae")
                self.assertEqual(len(all_decode), 2)
            finally:
                con.close()


class DecodeClassifyTests(unittest.TestCase):
    def test_classify_tiled_and_plain(self) -> None:
        from shape_factory_markers import classify_decode_vae

        tiled = {
            "1": {"class_type": "VAEDecodeTiled", "inputs": {}},
            "2": {"class_type": "VAEDecode", "inputs": {}},
        }
        self.assertEqual(classify_decode_vae(tiled), "tiled")

        plain = {"9": {"class_type": "VAEDecode", "inputs": {}}}
        self.assertEqual(classify_decode_vae(plain), "plain")

        empty = {"3": {"class_type": "KSampler", "inputs": {}}}
        self.assertIsNone(classify_decode_vae(empty))
        self.assertIsNone(classify_decode_vae(None))

    def test_scan_decode_dry_run_fixture(self) -> None:
        from shape_factory_markers import scan_decode_vae

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            jobs = root / "jobs" / "fam"
            jobs.mkdir(parents=True)
            out = root / "output"
            og = out / "og"
            og.mkdir(parents=True)
            # Tiny fake output so register can hash if apply; dry-run skips write.
            vid = og / "clip.mp4"
            vid.write_bytes(b"fake-mp4-bytes-for-hash")

            prompt = {
                "10": {"class_type": "VAEDecodeTiled", "inputs": {"samples": ["1", 0]}},
            }
            prompt_path = jobs / "j1.prompt.json"
            prompt_path.write_text(json.dumps(prompt), encoding="utf-8")
            job = {
                "job_key": "fam__j1",
                "submit": {
                    "prompt_path": str(prompt_path),
                    "outputs": [str(vid)],
                },
            }
            (jobs / "j1.job.json").write_text(json.dumps(job), encoding="utf-8")

            stats = scan_decode_vae(
                jobs_root=root / "jobs",
                output_root=out,
                apply=False,
                register_assets=False,
            )
            self.assertEqual(stats["jobs_scanned"], 1)
            self.assertEqual(stats["jobs_with_decode"], 1)
            self.assertEqual(stats["by_value"]["tiled"], 1)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
