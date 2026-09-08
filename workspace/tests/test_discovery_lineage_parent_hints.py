"""Unit tests for discovery lineage parent-hint filtering."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = REPO_ROOT / "scripts" / "experiments_ui_server.py"


def _load_server():
    spec = importlib.util.spec_from_file_location("experiments_ui_server_lineage_test", SERVER_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Avoid running main; load module body only.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestLineageParentHints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = _load_server()

    def test_rejects_savevideo_filename_prefix(self):
        child = {
            "name": "FB9_GEX2_2026-04-14_00004.mp4",
            "relpath": "og/2026-04-14/FB9_GEX2_2026-04-14_00004.mp4",
        }
        via = "output/og/2026-04-14/FB9_GEX2_2026-04-14"
        self.assertFalse(self.m._discovery_lineage_source_usable_as_parent_hint(via, child_item=child))
        self.assertTrue(
            self.m._discovery_lineage_edge_looks_spurious(
                {
                    "evidence": "png_prompt_source_path",
                    "via_source_raw": via,
                    "parent_group_id": "a",
                    "child_group_id": "b",
                }
            )
        )

    def test_accepts_concrete_source_video(self):
        child = {
            "name": "FB9_GEX2_2026-04-14_00016.mp4",
            "relpath": "og/2026-04-14/FB9_GEX2_2026-04-14_00016.mp4",
        }
        via = "output/output/og/2026-04-14/X-FB9-POSE-2026-04-14-095502_OG_00001.mp4"
        self.assertTrue(self.m._discovery_lineage_source_usable_as_parent_hint(via, child_item=child))
        self.assertFalse(
            self.m._discovery_lineage_edge_looks_spurious(
                {
                    "evidence": "png_prompt_source_path",
                    "via_source_raw": via,
                    "parent_group_id": "a",
                    "child_group_id": "b",
                }
            )
        )

    def test_prefix_match_rejects_ambiguous_siblings(self):
        idx = {
            "items": [
                {"group_id": "g1", "relpath": "og/2026-04-14/FB9_GEX2_2026-04-14_00004.mp4", "mtime": 1},
                {"group_id": "g2", "relpath": "og/2026-04-14/FB9_GEX2_2026-04-14_00016.mp4", "mtime": 9},
            ]
        }
        self.assertIsNone(
            self.m._discovery_find_item_by_output_relpath_prefix(idx, "og/2026-04-14/FB9_GEX2_2026-04-14")
        )

    def test_prefix_match_unique_ok(self):
        idx = {
            "items": [
                {"group_id": "g1", "relpath": "og/2026-04-14/Foo_OG_00001.mp4", "mtime": 1},
            ]
        }
        hit = self.m._discovery_find_item_by_output_relpath_prefix(idx, "og/2026-04-14/Foo_OG")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["group_id"], "g1")

    def test_orphan_loaders_omitted_from_output_feeding_paths(self):
        prompt = {
            "10": {
                "class_type": "VHS_LoadVideoPath",
                "inputs": {"video": "og/parent_live.mp4", "skip_first_frames": 0},
            },
            "11": {
                "class_type": "VHS_LoadVideoPath",
                "inputs": {"video": "og/parent_orphan.mp4", "skip_first_frames": 0},
            },
            "12": {
                "class_type": "VHS_LoadVideoPath",
                "inputs": {"video": "og/parent_preview_only.mp4", "skip_first_frames": 0},
            },
            "20": {
                "class_type": "ImageScale",
                "inputs": {"image": ["10", 0]},
            },
            "80": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["20", 0],
                    "filename_prefix": "output/og/child",
                    "save_output": True,
                },
            },
            "79": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["12", 0],
                    "filename_prefix": "WAN",
                    "save_output": False,
                },
            },
        }
        got = self.m._api_prompt_output_feeding_loader_paths(prompt)
        self.assertTrue(got["output_feeding_loader_paths_ok"])
        self.assertEqual(got["output_feeding_loader_paths"], ["og/parent_live.mp4"])
        self.assertEqual(got["saved_output_sink_count"], 1)

    def test_multiple_wired_image_loaders_kept(self):
        prompt = {
            "1": {"class_type": "LoadImage", "inputs": {"image": "ref_a.jpeg"}},
            "2": {"class_type": "LoadImage", "inputs": {"image": "ref_b.jpeg"}},
            "3": {"class_type": "LoadImage", "inputs": {"image": "orphan.jpeg"}},
            "4": {
                "class_type": "ImageScale",
                "inputs": {"image": ["1", 0]},
            },
            "5": {
                "class_type": "ColorMatch",
                "inputs": {"image": ["4", 0], "image_ref": ["2", 0]},
            },
            "9": {
                "class_type": "SaveImage",
                "inputs": {"images": ["5", 0], "filename_prefix": "out", "save_output": True},
            },
        }
        got = self.m._api_prompt_output_feeding_loader_paths(prompt)
        self.assertEqual(set(got["output_feeding_loader_paths"]), {"ref_a.jpeg", "ref_b.jpeg"})

    def test_facets_extract_prefers_wired_loader_paths(self):
        payload = {
            "png_workflow_probes": [
                {
                    "facets": {
                        "api_prompt": {
                            "sources": {
                                "source_paths_sample": [
                                    "og/parent_orphan.mp4",
                                    "og/parent_live.mp4",
                                    "output/og/child",
                                ],
                                "output_feeding_loader_paths_ok": True,
                                "output_feeding_loader_paths": ["og/parent_live.mp4"],
                            }
                        }
                    }
                }
            ]
        }
        got = self.m._discovery_extract_source_path_strings_from_facets_payload(payload)
        self.assertEqual(got, ["og/parent_live.mp4"])

    def test_media_citation_match_keys_and_reference(self):
        keys = self.m._discovery_media_citation_match_keys(
            "og/2026-03-03/FB9_GEX_2026-03-03_00017.mp4"
        )
        self.assertIn("fb9_gex_2026-03-03_00017.mp4", keys)
        self.assertTrue(
            self.m._discovery_source_string_references_media(
                "output/output/og/2026-03-03/FB9_GEX_2026-03-03_00017.mp4", keys
            )
        )
        self.assertFalse(
            self.m._discovery_source_string_references_media(
                "output/og/2026-03-03/FB9_GEX_2026-03-03", keys
            )
        )
        self.assertFalse(
            self.m._discovery_source_string_references_media(
                "og/other/FB9_GEX_2026-03-04_00001.mp4", keys
            )
        )

    def test_factory_job_parent_paths_from_bindings(self):
        job = {
            "bindings": {
                "source_video": {
                    "path": "/data/output/og/2026-09-07/FB9_GEX_FACIAL_parent_00001.mp4",
                },
                "identity_anchor": {
                    "path": "/data/output/og/2026-09-07/FB9_GEX_FACIAL_parent_00001.png",
                },
                "prompt_profile": {
                    "path": "/data/pools/FB9_GEX2/prompts/catalog-default.json",
                },
            },
            "parent_output": "/workspace/output/og/2026-09-07/FB9_GEX_FACIAL_parent_00001.mp4",
            "construction": {
                "parent_output": "/workspace/output/og/2026-09-07/FB9_GEX_FACIAL_parent_00001.mp4",
                "identity_anchor": "/workspace/output/og/2026-09-07/FB9_GEX_FACIAL_parent_00001.png",
                "replay_of_job_key": "FB9_GEX_FACIAL_parent",
            },
        }
        got = self.m._discovery_factory_job_parent_path_strings(job)
        self.assertIn("/data/output/og/2026-09-07/FB9_GEX_FACIAL_parent_00001.mp4", got)
        self.assertIn("/data/output/og/2026-09-07/FB9_GEX_FACIAL_parent_00001.png", got)
        self.assertIn("/workspace/output/og/2026-09-07/FB9_GEX_FACIAL_parent_00001.mp4", got)
        self.assertTrue(all(not p.lower().endswith(".json") for p in got))
        self.assertEqual(
            got.count("/workspace/output/og/2026-09-07/FB9_GEX_FACIAL_parent_00001.mp4"),
            1,
        )

    def test_factory_job_parent_paths_from_live_gex2_job(self):
        job_path = (
            REPO_ROOT
            / ".data"
            / "shape_factory"
            / "jobs"
            / "FB9_GEX2_identity_anchor"
            / "FB9_GEX2_identity_anchor__id-FB9_GEX_FACIAL__pp-catalog-default__src-hourly__pp-catalog-default__still-e68b_ui1788826777.job.json"
        )
        if not job_path.is_file():
            self.skipTest("example factory job not on disk")
        job = json.loads(job_path.read_text(encoding="utf-8"))
        got = self.m._discovery_factory_job_parent_path_strings(job)
        self.assertTrue(any("FB9_GEX_FACIAL__" in p and p.lower().endswith(".mp4") for p in got))
        self.assertTrue(any("FB9_GEX_FACIAL__" in p and p.lower().endswith(".png") for p in got))

    def test_factory_binding_edge_not_spurious(self):
        self.assertFalse(
            self.m._discovery_lineage_edge_looks_spurious(
                {
                    "evidence": "factory_job_binding",
                    "via_source_raw": "/data/output/og/parent_00001.mp4",
                    "parent_group_id": "a",
                    "child_group_id": "b",
                }
            )
        )
        self.assertTrue(
            self.m._discovery_lineage_edge_looks_spurious(
                {
                    "evidence": "factory_job_binding",
                    "via_source_raw": "output/og/2026-04-14/FB9_GEX2_2026-04-14",
                    "parent_group_id": "a",
                    "child_group_id": "b",
                }
            )
        )

    def test_citation_index_roundtrip(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "_status").mkdir(parents=True)
            cfg = SimpleNamespace(output_root=root, workspace_root=root)
            # Monkeypatch status dir resolution via real helper expecting ServerConfig-like
            # Use a minimal object with output_root only — helpers use _output_status_dir(cfg.output_root).
            child = {
                "group_id": "og:stem:child_a",
                "relpath": "og/2026-08-11/child_a.mp4",
            }
            n = self.m._discovery_citations_upsert_postings(
                cfg,
                child_group_id=child["group_id"],
                child_relpath=child["relpath"],
                via_paths=["output/og/2026-03-03/FB9_GEX_2026-03-03_00017.mp4"],
                evidence="test",
            )
            self.assertGreater(n, 0)
            seed = {
                "group_id": "og:stem:fb9_gex_2026-03-03_00017",
                "relpath": "og/2026-03-03/FB9_GEX_2026-03-03_00017.mp4",
                "name": "FB9_GEX_2026-03-03_00017.mp4",
            }
            edges = self.m._discovery_citations_lookup_child_edges(cfg, seed, limit=20)
            self.assertEqual(len(edges), 1)
            self.assertEqual(edges[0]["child_group_id"], "og:stem:child_a")
            self.assertEqual(edges[0]["parent_group_id"], seed["group_id"])
            db = self.m._discovery_citations_db_path(cfg)
            self.assertTrue(db.is_file())


if __name__ == "__main__":
    unittest.main()
