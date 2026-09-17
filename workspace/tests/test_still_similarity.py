#!/usr/bin/env python3
"""Slice 1: still similarity orchestrator + embed index."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401

from still_embed_index import SqliteEmbedIndex, cosine_similarity, pack_vector, unpack_vector
from still_similarity import SimilarityOrchestrator, jaccard
from vision_still_tags import connect, ensure_db, upsert_editorial


def _cid(n: int) -> str:
    return f"{n:064x}"


class EmbedIndexTests(unittest.TestCase):
    def test_pack_roundtrip_and_cosine(self) -> None:
        vals = [0.1, -0.2, 0.3, 1.0]
        back = unpack_vector(pack_vector(vals))
        self.assertEqual(len(back), 4)
        self.assertAlmostEqual(back[0], 0.1, places=5)
        self.assertAlmostEqual(cosine_similarity([1.0, 0.0], [1.0, 0.0]), 1.0, places=5)
        self.assertAlmostEqual(cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0, places=5)

    def test_nearest_neighbors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "emb.sqlite"
            idx = SqliteEmbedIndex(db)
            a, b, c = _cid(1), _cid(2), _cid(3)
            idx.upsert(a, [1.0, 0.0, 0.0], model_id="test-model")
            idx.upsert(b, [0.9, 0.1, 0.0], model_id="test-model")
            idx.upsert(c, [0.0, 1.0, 0.0], model_id="test-model")
            hits = idx.nearest(a, limit=2)
            self.assertEqual(hits[0]["content_id"], b)
            self.assertGreater(hits[0]["score"], hits[1]["score"])
            stats = idx.stats()
            self.assertEqual(stats["count"], 3)

    def test_upsert_many_writes_batch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "emb.sqlite"
            idx = SqliteEmbedIndex(db)
            a, b = _cid(10), _cid(11)
            n = idx.upsert_many(
                [
                    (a, [1.0, 0.0], "test-model", "/a.jpg"),
                    (b, [0.0, 1.0], "test-model", "/b.jpg"),
                ]
            )
            self.assertEqual(n, 2)
            self.assertEqual(idx.stats()["count"], 2)
            self.assertEqual(idx.get(a)["source_path"], "/a.jpg")


class TagOverlapTests(unittest.TestCase):
    def test_jaccard(self) -> None:
        self.assertAlmostEqual(jaccard(["a", "b"], ["b", "c"]), 1 / 3)
        self.assertEqual(jaccard([], ["a"]), 0.0)

    def test_tag_provider_ranks_shared_tags(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            (data / "shape_factory").mkdir(parents=True)
            os.environ["SHAPE_FACTORY_DATA_ROOT"] = str(data)
            db = data / "shape_factory" / "still_tags.sqlite"
            ensure_db(db)
            con = connect(db)
            try:
                upsert_editorial(con, content_id=_cid(1), tags=["portrait", "kneel"])
                upsert_editorial(con, content_id=_cid(2), tags=["portrait", "kneel", "studio"])
                upsert_editorial(con, content_id=_cid(3), tags=["landscape"])
                con.commit()
            finally:
                con.close()
            orch = SimilarityOrchestrator(data_root=data, default_provider="tags")
            out = orch.find_similar(_cid(1), provider="tags", limit=8)
            self.assertTrue(out["ok"])
            ids = [h["content_id"] for h in out["items"]]
            self.assertIn(_cid(2), ids)
            self.assertEqual(ids[0], _cid(2))
            self.assertIn("portrait", out["items"][0]["shared_tags"])
            self.assertNotIn(_cid(1), ids)


class ClipAndBlendTests(unittest.TestCase):
    def test_clip_missing_query_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            (data / "shape_factory").mkdir(parents=True)
            os.environ["SHAPE_FACTORY_DATA_ROOT"] = str(data)
            orch = SimilarityOrchestrator(data_root=data, default_provider="clip")
            out = orch.find_similar(_cid(9), provider="clip", limit=4)
            self.assertTrue(out["ok"])
            self.assertEqual(out["items"], [])
            self.assertIn("clip_index_missing_query", out["notes"])

    def test_clip_and_blend_rank(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            (data / "shape_factory").mkdir(parents=True)
            os.environ["SHAPE_FACTORY_DATA_ROOT"] = str(data)
            idx = SqliteEmbedIndex(data / "shape_factory" / "still_clip_embeddings.sqlite")
            a, b, c = _cid(10), _cid(11), _cid(12)
            idx.upsert(a, [1.0, 0.0], model_id="unit")
            idx.upsert(b, [0.95, 0.05], model_id="unit")
            idx.upsert(c, [0.1, 0.99], model_id="unit")
            db = data / "shape_factory" / "still_tags.sqlite"
            ensure_db(db)
            con = connect(db)
            try:
                upsert_editorial(con, content_id=a, tags=["face"])
                upsert_editorial(con, content_id=b, tags=["face"])
                upsert_editorial(con, content_id=c, tags=["other"])
                con.commit()
            finally:
                con.close()
            orch = SimilarityOrchestrator(data_root=data, default_provider="clip")
            clip = orch.find_similar(a, provider="clip", limit=4)
            self.assertTrue(clip["ok"])
            self.assertEqual(clip["items"][0]["content_id"], b)
            blend = orch.find_similar(a, provider="blend", limit=4)
            self.assertTrue(blend["ok"])
            self.assertEqual(blend["provider"], "blend")
            self.assertEqual(blend["items"][0]["content_id"], b)

    def test_unknown_provider(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            (data / "shape_factory").mkdir(parents=True)
            orch = SimilarityOrchestrator(data_root=data)
            out = orch.find_similar(_cid(1), provider="nope")
            self.assertFalse(out["ok"])
            self.assertEqual(out["error"], "unknown_provider")


if __name__ == "__main__":
    unittest.main()
