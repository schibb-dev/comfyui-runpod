#!/usr/bin/env python3
"""Tests for identity-still candidates, ranking, and mint."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "workspace" / "scripts"


class IdentityStillCandidatesTests(unittest.TestCase):
    def setUp(self) -> None:
        import sys

        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))

    def test_shape_needs_identity_still(self) -> None:
        from shape_factory_identity_still import shape_needs_identity_still

        self.assertFalse(shape_needs_identity_still({"requires": [{"slot": "source_video", "media": "video"}]}))
        self.assertTrue(
            shape_needs_identity_still(
                {
                    "requires": [
                        {"slot": "identity_anchor", "media": "image", "binding": {"type": "load_image"}},
                    ]
                }
            )
        )

    def test_ranking_prefers_job_binding_over_rated(self) -> None:
        from shape_factory_identity_still import list_identity_still_candidates

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ws = root / "workspace"
            out = root / "output"
            data = root / "data"
            inp = ws / "input"
            og = out / "og"
            for p in (inp, og, data / "shapes"):
                p.mkdir(parents=True)

            still_bind = inp / "bound_face.jpeg"
            still_bind.write_bytes(b"bound")
            still_rated = inp / "rated_face.jpeg"
            still_rated.write_bytes(b"rated")

            shape = {
                "requires": [
                    {"slot": "identity_anchor", "media": "image", "binding": {"type": "load_image"}},
                ]
            }
            (data / "shapes" / "FB9_GEX2_identity_anchor.shape.yaml").write_text(
                "family_slug: FB9_GEX2_identity_anchor\nrequires: []\n",
                encoding="utf-8",
            )

            job = {
                "job_key": "seed",
                "family_slug": "FB9_GEX2",
                "bindings": {"source_still": {"path": str(still_bind)}},
            }
            clip = og / "clip.mp4"
            clip.write_bytes(b"not-a-real-mp4")

            with mock.patch(
                "shape_factory_identity_still._collect_rated_opener_stills",
                return_value=[
                    {
                        "id": "rated",
                        "path": str(still_rated),
                        "relpath": "input/rated_face.jpeg",
                        "url": "/files/input/rated_face.jpeg",
                        "thumb_url": "/files/input/rated_face.jpeg",
                        "evidence": "rated_opener",
                        "label": "Rated",
                    }
                ],
            ), mock.patch(
                "shape_factory_identity_still._infer_still_from_media",
                return_value=None,
            ), mock.patch(
                "shape_factory_identity_still.load_shape_for_family",
                return_value=shape,
            ):
                payload = list_identity_still_candidates(
                    relpath="og/clip.mp4",
                    family_slug="FB9_GEX2_identity_anchor",
                    job_key="seed",
                    workspace_root=ws,
                    output_root=out,
                    data_root=data,
                    media_abs=clip,
                    job=job,
                    shape=shape,
                    include_rated=True,
                )

            self.assertTrue(payload["needed"])
            cands = payload["candidates"]
            self.assertGreaterEqual(len(cands), 1)
            self.assertEqual(cands[0]["evidence"], "job_binding")
            self.assertEqual(Path(cands[0]["path"]).resolve(), still_bind.resolve())
            self.assertEqual(payload["recommended_id"], cands[0]["id"])

    def test_mint_targets_prefer_earliest_ancestor(self) -> None:
        from shape_factory_identity_still import list_identity_still_candidates

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ws = root / "workspace"
            out = root / "output"
            data = root / "data"
            og = out / "og"
            og.mkdir(parents=True)
            (ws / "input").mkdir(parents=True)

            parent = og / "parent.mp4"
            child = og / "child.mp4"
            parent.write_bytes(b"p")
            child.write_bytes(b"c")

            shape = {
                "requires": [
                    {"slot": "identity_anchor", "media": "image", "binding": {"type": "load_image"}},
                ]
            }
            hops = [
                {
                    "relpath": "og/child.mp4",
                    "abs_path": str(child),
                    "depth": 0,
                    "job_key": "child",
                    "family_slug": "FB9_GEX2",
                    "parent_output": str(parent),
                },
                {
                    "relpath": "og/parent.mp4",
                    "abs_path": str(parent),
                    "depth": 1,
                    "job_key": "parent",
                    "family_slug": "FB9-FaceBlast",
                    "parent_output": "",
                },
            ]
            with mock.patch(
                "shape_factory_identity_still.walk_lineage_videos",
                return_value=hops,
            ), mock.patch(
                "shape_factory_identity_still._infer_still_from_media",
                return_value=None,
            ), mock.patch(
                "shape_factory_identity_still._collect_rated_opener_stills",
                return_value=[],
            ):
                payload = list_identity_still_candidates(
                    relpath="og/child.mp4",
                    family_slug="FB9_GEX2_identity_anchor",
                    workspace_root=ws,
                    output_root=out,
                    data_root=data,
                    media_abs=child,
                    shape=shape,
                    include_rated=False,
                )

            targets = payload["mint_targets"]
            self.assertGreaterEqual(len(targets), 1)
            # Earliest ancestor (depth 1) listed first
            self.assertEqual(targets[0]["video_relpath"], "og/parent.mp4")
            self.assertEqual(targets[0]["evidence"], "first_frame")

    def test_mint_writes_content_addressed_jpeg(self) -> None:
        from shape_factory_identity_still import mint_identity_still_from_video

        if not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg not available")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ws = root / "workspace"
            out = root / "output"
            data = root / "data"
            og = out / "og"
            inp = ws / "input"
            for p in (og, inp):
                p.mkdir(parents=True)

            video = og / "tiny.mp4"
            # 1-frame black video
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=64x64:d=0.1",
                    "-frames:v",
                    "1",
                    str(video),
                ],
                check=True,
            )

            with mock.patch(
                "shape_factory_identity_still.default_mint_input_dir",
                return_value=inp,
            ):
                result = mint_identity_still_from_video(
                    video_relpath="og/tiny.mp4",
                    video_path=str(video),
                    at="start",
                    workspace_root=ws,
                    output_root=out,
                    data_root=data,
                )

            cand = result["candidate"]
            self.assertTrue(cand["path"])
            dest = Path(cand["path"])
            self.assertTrue(dest.is_file())
            self.assertTrue(dest.name.startswith("IDM"))
            self.assertTrue(dest.name.endswith(".jpeg"))
            digest = hashlib.sha256(dest.read_bytes()).hexdigest()
            self.assertEqual(result["sha256"], digest)
            self.assertEqual(cand["evidence"], "first_frame")

    def test_disposition_extra_includes_identity_anchor(self) -> None:
        """Sanity: server merge keys include identity_anchor (import-level check)."""
        src = (REPO_ROOT / "scripts" / "experiments_ui_server.py").read_text(encoding="utf-8")
        self.assertIn('"identity_anchor"', src)
        self.assertIn("_identity_still_candidates_payload", src)
        self.assertIn("_identity_still_mint_payload", src)


if __name__ == "__main__":
    unittest.main()
