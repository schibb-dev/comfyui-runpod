#!/usr/bin/env python3
"""Tests for input curation schema and merge behavior."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401

import shape_factory_input_curation as curation


class TestShapeFactoryInputCuration(unittest.TestCase):
    def test_load_collections_normalizes_legacy_items(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            data_root = Path(td)
            p = curation.collections_path(data_root)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                json.dumps(
                    {
                        "schema_version": "legacy",
                        "items": [
                            {
                                "collection_id": "alpha",
                                "name": "Alpha",
                                "items": [
                                    "/tmp/one.png",
                                    {"path": "/tmp/two.png", "note": "x"},
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            doc = curation.load_collections(data_root)
        self.assertEqual(doc.get("schema_version"), "v1")
        rows = doc.get("collections") or []
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "alpha")
        self.assertEqual(len(rows[0]["items"]), 2)

    def test_merged_source_stills_attached_add_and_dedupe(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_root = root / ".data"
            (data_root / "shape_factory").mkdir(parents=True, exist_ok=True)
            workspace_root = root / "workspace"
            output_root = workspace_root / "output"
            output_root.mkdir(parents=True, exist_ok=True)

            same_hash = "a" * 64
            base = root / f"SSS{same_hash}.png"
            alt_same = root / f"ALT{same_hash}.png"
            extra = root / ("BBB" + "b" * 64 + ".png")
            for p in (base, alt_same, extra):
                p.write_bytes(b"x")

            curation.save_collections(
                curation.collections_path(data_root),
                {
                    "collections": [
                        {
                            "id": "c1",
                            "name": "Collection 1",
                            "items": [{"path": str(alt_same)}, {"path": str(extra)}],
                        }
                    ]
                },
            )
            curation.save_bindings(
                curation.bindings_path(data_root),
                {"families": {"FAM": ["c1"]}},
            )

            merged = curation.merged_source_stills(
                family_slug="FAM",
                base_members=[base],
                data_root=data_root,
                workspace_root=workspace_root,
                output_root=output_root,
            )
            members = [str(p) for p in merged.get("members") or []]
        self.assertEqual(len(members), 2)
        self.assertIn(str(base.resolve()), members)
        self.assertIn(str(extra.resolve()), members)
        self.assertEqual(int(merged.get("added_count") or 0), 1)
        self.assertGreaterEqual(int(merged.get("deduped_count") or 0), 1)


if __name__ == "__main__":
    unittest.main()
