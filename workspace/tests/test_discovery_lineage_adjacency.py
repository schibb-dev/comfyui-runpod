#!/usr/bin/env python3
"""Tests for discovery_lineage_adjacency."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from discovery_lineage_adjacency import (
    counts_for_media_refs,
    ensure_adjacency,
    ingest_edge_rows,
)


class LineageAdjacencyTest(unittest.TestCase):
    def test_rebuild_and_counts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            edges_path = root / "discovery_lineage_edges.json"
            edges_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "edges": [
                            {
                                "parent_group_id": "input:SSS" + "a" * 64 + ".jpeg",
                                "child_group_id": "og:stem:child1",
                                "via_source_raw": "input/SSS" + "a" * 64 + ".jpeg",
                                "evidence": "test",
                            },
                            {
                                "parent_group_id": "og:stem:child1",
                                "child_group_id": "og:stem:child2",
                                "via_source_raw": "og/child1.mp4",
                                "evidence": "test",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            out = ensure_adjacency(edges_path, force=True)
            self.assertTrue(out.get("ok"))
            self.assertEqual(out.get("edge_count"), 2)
            rows = counts_for_media_refs(
                edges_path,
                [{"relpath": "input/SSS" + "a" * 64 + ".jpeg", "content_id": "a" * 64}],
            )
            self.assertEqual(len(rows), 1)
            self.assertGreaterEqual(rows[0]["descendant_count"], 2)
            self.assertGreaterEqual(rows[0]["direct_child_count"], 1)

            ingest_edge_rows(
                edges_path,
                [
                    {
                        "parent_group_id": "og:stem:child2",
                        "child_group_id": "og:stem:child3",
                        "via_source_raw": "og/child2.mp4",
                    }
                ],
            )
            # Edges JSON not updated — ensure_adjacency still uses old mtime; force after
            # writing JSON so counts stay honest in production (persist updates both).
            doc = json.loads(edges_path.read_text(encoding="utf-8"))
            doc["edges"].append(
                {
                    "parent_group_id": "og:stem:child2",
                    "child_group_id": "og:stem:child3",
                    "via_source_raw": "og/child2.mp4",
                }
            )
            edges_path.write_text(json.dumps(doc), encoding="utf-8")
            ensure_adjacency(edges_path, force=True)
            rows2 = counts_for_media_refs(
                edges_path, [{"relpath": "input/SSS" + "a" * 64 + ".jpeg"}]
            )
            self.assertGreaterEqual(rows2[0]["descendant_count"], 3)


if __name__ == "__main__":
    unittest.main()
