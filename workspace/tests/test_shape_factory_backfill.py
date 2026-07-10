#!/usr/bin/env python3
"""Tests for shape_factory_backfill (synthetic "jobs that would have been")."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import shape_factory_backfill as sfb


SHAPE_DOC = {
    "shape_id": "wan-i2v-still+prompt",
    "family_slug": "X-KNEEL-FB9",
    "graph_hash": "abc123",
    "requires": [
        {"slot": "source_still", "role": "A", "binding": {"type": "load_image", "node_id": 88}},
        {
            "slot": "prompt_profile",
            "role": "C",
            "binding": {
                "type": "prompt_bundle",
                "positive": {"node_id": 408, "widget_index": 0},
                "negative": {"node_id": 409, "widget_index": 0},
            },
        },
    ],
    "deposits": {"final_video": {"to_pool": "pool:X-KNEEL_X_og"}},
}


class BackfillHelperTests(unittest.TestCase):
    def test_slug(self) -> None:
        self.assertEqual(sfb._slug("A B/c!"), "A-B-c")

    def test_text_from_prompt_node(self) -> None:
        prompt = {
            "408": {"inputs": {"text": "a positive prompt"}},
            "409": {"inputs": {"text": "bad hands"}},
            "500": {"inputs": {"seed": 42, "notes": "hi there longer string"}},
        }
        self.assertEqual(sfb._text_from_prompt_node(prompt, 408), "a positive prompt")
        self.assertEqual(sfb._text_from_prompt_node(prompt, 409), "bad hands")
        # Falls back to longest string when no known key.
        self.assertEqual(sfb._text_from_prompt_node(prompt, 500), "hi there longer string")
        self.assertIsNone(sfb._text_from_prompt_node(prompt, 999))

    def test_merge_lineage_edges_dedups(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "discovery_lineage_edges.json"
            edges = [
                {"child_group_id": "og:stem:out1", "parent_group_id": "input:src.jpg"},
                {"child_group_id": "og:stem:out1", "parent_group_id": "input:src.jpg"},
                {"child_group_id": "og:stem:out2", "parent_group_id": "input:src.jpg"},
            ]
            added = sfb._merge_lineage_edges(path, edges)
            self.assertEqual(added, 2)
            # Re-merging adds nothing new.
            self.assertEqual(sfb._merge_lineage_edges(path, edges), 0)
            doc = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(doc["edges"]), 2)

    def test_append_lineage_edge(self) -> None:
        edges: list = []
        sfb._append_lineage_edge(
            edges,
            output_relpath="output/og/2026-03-18/Clip_00001.mp4",
            bindings={"source_still": {"binding_type": "load_image", "path": "/x/input/S.png", "recovered": True}},
        )
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["child_group_id"], "og:stem:clip_00001")
        self.assertEqual(edges[0]["parent_group_id"], "input:S.png")

    def test_synthesize_job_shape(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "Clip_00001.mp4"
            out.write_bytes(b"video-bytes")
            bindings = {
                "source_still": {"binding_type": "load_image", "path": "/x/input/S.png", "role": "A"},
                "prompt_profile": {"binding_type": "prompt_bundle", "role": "C", "positive": "p"},
            }
            job = sfb.synthesize_job(
                output_abs=out,
                output_relpath="output/og/2026-03-18/Clip_00001.mp4",
                family_slug="X-KNEEL-FB9",
                shape_doc=SHAPE_DOC,
                shape_path=Path("/x/shapes/X-KNEEL-FB9.shape.yaml"),
                pools_path=Path("/x/pools/X-KNEEL-FB9/pools.yaml"),
                bindings=bindings,
                asset_ids={"output": "cid1", "source_still": "cid2"},
                evidence={"source_still": "png_load_image"},
            )
            self.assertEqual(job["origin"], "backfill")
            self.assertEqual(job["graph_hash"], "abc123")
            self.assertEqual(job["submit"]["status"], "completed")
            self.assertTrue(job["submit"]["prompt_id"].startswith("backfill-"))
            self.assertEqual(job["outputs"], ["output/og/2026-03-18/Clip_00001.mp4"])
            self.assertEqual(job["deposits"]["final_video"]["to_pool"], "pool:X-KNEEL_X_og")
            self.assertEqual(job["job_key"], "X-KNEEL-FB9__backfill__Clip_00001")

    def test_reconstruct_bindings_from_embedded(self) -> None:
        orig_still = sfb.sfss.infer_source_still
        orig_extract = sfb.sfss._extract_prompt
        try:
            sfb.sfss.infer_source_still = lambda p, **k: {
                "source_basename": "Face.png",
                "evidence": "png_load_image",
            }
            sfb.sfss._extract_prompt = lambda p, **k: (
                {"408": {"inputs": {"text": "hello pos"}}, "409": {"inputs": {"text": "neg stuff"}}},
                "png",
            )
            bindings, evidence = sfb.reconstruct_bindings(
                output_abs=Path("/x/output/og/x/Clip.mp4"),
                shape_doc=SHAPE_DOC,
                workspace_root=Path("/ws"),
                output_root=Path("/ws/output"),
                ffprobe=None,
            )
            self.assertEqual(bindings["source_still"]["binding_type"], "load_image")
            self.assertEqual(bindings["source_still"]["path"], "/ws/input/Face.png")
            self.assertEqual(bindings["source_still"]["role"], "A")
            self.assertEqual(bindings["prompt_profile"]["positive"], "hello pos")
            self.assertEqual(bindings["prompt_profile"]["negative"], "neg stuff")
            self.assertEqual(evidence["source_still"], "png_load_image")
        finally:
            sfb.sfss.infer_source_still = orig_still
            sfb.sfss._extract_prompt = orig_extract

    def test_reconstruct_video_source_and_lineage(self) -> None:
        video_shape = {
            "shape_id": "wan-v2v",
            "graph_hash": "vid1",
            "requires": [
                {"slot": "source_video", "role": "A", "binding": {"type": "vhs_load_video_path"}},
            ],
            "deposits": {"final_video": {"to_pool": "pool:GEX"}},
        }
        raw = "output/output/og/2026-03-31/X-Kneel-FB9-2026-03-31-031315_OG_00001.mp4"
        orig_extract = sfb.sfss._extract_prompt
        try:
            sfb.sfss._extract_prompt = lambda p, **k: (
                {"1": {"class_type": "VHS_LoadVideoPath", "inputs": {"video": raw}}},
                "png",
            )
            bindings, evidence = sfb.reconstruct_bindings(
                output_abs=Path("/x/output/og/x/GEX_00001.mp4"),
                shape_doc=video_shape,
                workspace_root=Path("/ws"),
                output_root=Path("/ws/output"),
                ffprobe=None,
            )
            self.assertEqual(bindings["source_video"]["binding_type"], "vhs_load_video_path")
            self.assertTrue(bindings["source_video"]["recovered"])
            self.assertEqual(evidence["source_video"], "png")

            edges: list = []
            sfb._append_lineage_edge(
                edges, output_relpath="output/og/2026-04-03/GEX_00001.mp4", bindings=bindings
            )
            self.assertEqual(len(edges), 1)
            self.assertEqual(edges[0]["child_group_id"], "og:stem:gex_00001")
            self.assertEqual(
                edges[0]["parent_group_id"], "og:stem:x-kneel-fb9-2026-03-31-031315_og_00001"
            )
            self.assertEqual(edges[0]["evidence"], "backfill_load_video")
        finally:
            sfb.sfss._extract_prompt = orig_extract


if __name__ == "__main__":
    unittest.main()
