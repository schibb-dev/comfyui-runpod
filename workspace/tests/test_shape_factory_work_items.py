#!/usr/bin/env python3
"""Tests for shape_factory_work_items."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from shape_factory_work_items import (
    WORK_ITEMS_SCHEMA,
    build_idempotency_key,
    cancel_work_item,
    create_routes_batch,
    create_work_item,
    list_work_items,
    load_work_items_doc,
    record_run_step_work_item,
    route_for_step,
    work_items_for_item,
)


class WorkItemsSchemaTests(unittest.TestCase):
    def test_route_map(self) -> None:
        self.assertEqual(route_for_step("advance.extend"), ("extend", "advance", "normal"))
        self.assertEqual(route_for_step("advance.vary"), ("vary", "advance", "front"))
        self.assertEqual(route_for_step("advance.queue_now"), ("vary", "advance", "front"))
        self.assertIsNone(route_for_step("retire.trash"))

    def test_idempotency_key(self) -> None:
        key = build_idempotency_key(
            pool="extend",
            source_group_id="og:stem:foo",
            factory_family="FB9_GEX2",
            recipe="idle-small-motions",
        )
        self.assertEqual(key, "extend:og:stem:foo:FB9_GEX2:idle-small-motions")


class WorkItemsIndexTests(unittest.TestCase):
    def test_create_two_routes_same_asset(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "_status" / "work_items_index.json"
            a = create_work_item(
                source_relpath="og/2026-04-03/foo.mp4",
                pool="extend",
                disposition_entry="advance",
                disposition_step="advance.extend",
                factory_family="FB9_GEX2",
                recipe="idle-small-motions",
                work_items_index_path=path,
            )
            b = create_work_item(
                source_relpath="og/2026-04-03/foo.mp4",
                pool="vary",
                disposition_entry="advance",
                disposition_step="advance.vary",
                factory_family="FB9_GEX2",
                recipe="idle-small-motions",
                work_items_index_path=path,
                priority="front",
            )
            self.assertTrue(a["created"])
            self.assertTrue(b["created"])
            self.assertNotEqual(a["item"]["work_id"], b["item"]["work_id"])
            doc = load_work_items_doc(path)
            self.assertEqual(doc["schema"], WORK_ITEMS_SCHEMA)
            self.assertEqual(len(doc["items"]), 2)

    def test_idempotency_reuses_within_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "work_items_index.json"
            first = create_work_item(
                source_relpath="og/clip.mp4",
                pool="extend",
                disposition_entry="advance",
                disposition_step="advance.extend",
                factory_family="FAM",
                recipe="advance.extend",
                work_items_index_path=path,
            )
            second = create_work_item(
                source_relpath="og/clip.mp4",
                pool="extend",
                disposition_entry="advance",
                disposition_step="advance.extend",
                factory_family="FAM",
                recipe="advance.extend",
                work_items_index_path=path,
            )
            self.assertTrue(first["created"])
            self.assertTrue(second["reused"])
            self.assertEqual(first["item"]["work_id"], second["item"]["work_id"])
            self.assertEqual(len(load_work_items_doc(path)["items"]), 1)

    def test_queue_now_upgrades_priority_on_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "work_items_index.json"
            create_work_item(
                source_relpath="og/clip.mp4",
                pool="vary",
                disposition_entry="advance",
                disposition_step="advance.vary",
                work_items_index_path=path,
                priority="normal",
            )
            reused = create_work_item(
                source_relpath="og/clip.mp4",
                pool="vary",
                disposition_entry="advance",
                disposition_step="advance.vary",
                work_items_index_path=path,
                priority="front",
            )
            self.assertTrue(reused["reused"])
            self.assertEqual(reused["item"]["priority"], "front")

    def test_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "work_items_index.json"
            created = create_work_item(
                source_relpath="og/clip.mp4",
                pool="extend",
                disposition_entry="advance",
                work_items_index_path=path,
            )
            wid = created["item"]["work_id"]
            out = cancel_work_item(wid, work_items_index_path=path, reason="user")
            self.assertEqual(out["item"]["status"], "cancelled")
            open_rows = list_work_items(load_work_items_doc(path), include_terminal=False)
            self.assertEqual(open_rows, [])

    def test_create_routes_batch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "work_items_index.json"
            out = create_routes_batch(
                source_relpath="og/clip.mp4",
                routes=[
                    {"step_id": "advance.extend"},
                    {"step_id": "advance.vary"},
                ],
                work_items_index_path=path,
                queue_now=True,
            )
            self.assertEqual(out["count"], 2)
            pools = {i["pool"] for i in out["items"]}
            self.assertEqual(pools, {"extend", "vary"})
            self.assertTrue(all(i["priority"] == "front" for i in out["items"]))

    def test_record_run_step_sets_queued(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "work_items_index.json"
            out = record_run_step_work_item(
                source_relpath="og/clip.mp4",
                step_id="advance.extend",
                hook="extend",
                hook_result={"ok": True, "job_key": "FAM::run-1", "family_slug": "FAM"},
                work_items_index_path=path,
            )
            self.assertIsNotNone(out)
            assert out is not None
            self.assertEqual(out["item"]["status"], "queued")
            self.assertEqual(out["item"]["factory_job_key"], "FAM::run-1")
            self.assertEqual(out["item"]["pool"], "extend")

    def test_record_run_step_skips_non_factory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "work_items_index.json"
            out = record_run_step_work_item(
                source_relpath="og/clip.mp4",
                step_id="refine.edit",
                hook="open_trim",
                hook_result={"ok": True, "trim_ui": True},
                work_items_index_path=path,
            )
            self.assertIsNone(out)
            self.assertEqual(load_work_items_doc(path)["items"], [])

    def test_enrichment(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "work_items_index.json"
            create_work_item(
                source_relpath="og/clip.mp4",
                pool="extend",
                disposition_entry="advance",
                work_items_index_path=path,
            )
            doc = load_work_items_doc(path)
            enrich = work_items_for_item({"relpath": "og/clip.mp4"}, doc)
            self.assertEqual(enrich["work_items_open_count"], 1)
            self.assertEqual(enrich["work_items_total_count"], 1)


if __name__ == "__main__":
    unittest.main()
