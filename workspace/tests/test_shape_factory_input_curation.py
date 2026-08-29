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

    def test_list_catalog_stills_omits_download_copy_names(self) -> None:
        import os

        from input_still_catalog import scan_input_stills

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_root = root / ".data"
            (data_root / "shape_factory").mkdir(parents=True, exist_ok=True)
            input_root = root / "input"
            input_root.mkdir()
            h = "c" * 64
            canon = input_root / f"{h}.jpeg"
            copy = input_root / f"{h} (1).jpeg"
            only_copy_h = "d" * 64
            only_copy = input_root / f"{only_copy_h} (1).jpeg"
            canon.write_bytes(b"canon")
            copy.write_bytes(b"copy")
            only_copy.write_bytes(b"alone")

            prev = os.environ.get("COMFYUI_BIND_INPUT_DIR")
            prev_cat = os.environ.get("HOURLY_INPUT_STILL_CATALOG_PATH")
            cat = data_root / "shape_factory" / "input_still_catalog.sqlite"
            try:
                os.environ["COMFYUI_BIND_INPUT_DIR"] = str(input_root)
                os.environ["HOURLY_INPUT_STILL_CATALOG_PATH"] = str(cat)
                # Force both names into the catalog (scan skips copy when canon exists).
                scan_input_stills(input_root=input_root, catalog_path=cat)
                import sqlite3

                con = sqlite3.connect(str(cat))
                con.execute(
                    "INSERT OR IGNORE INTO stills(path, size, mtime, ext, first_seen, last_seen) VALUES(?,?,?,?,?,?)",
                    (str(copy.resolve()), 4, 1.0, ".jpeg", 1.0, 1.0),
                )
                con.commit()
                con.close()

                payload = curation.list_catalog_stills(data_root=data_root, limit=50, offset=0)
            finally:
                if prev is None:
                    os.environ.pop("COMFYUI_BIND_INPUT_DIR", None)
                else:
                    os.environ["COMFYUI_BIND_INPUT_DIR"] = prev
                if prev_cat is None:
                    os.environ.pop("HOURLY_INPUT_STILL_CATALOG_PATH", None)
                else:
                    os.environ["HOURLY_INPUT_STILL_CATALOG_PATH"] = prev_cat

        basenames = [it.get("basename") for it in payload.get("items") or []]
        self.assertIn(canon.name, basenames)
        self.assertNotIn(copy.name, basenames)
        self.assertNotIn(only_copy.name, basenames)
        self.assertGreaterEqual(int(payload.get("skipped_download_copies") or 0), 1)


if __name__ == "__main__":
    unittest.main()
