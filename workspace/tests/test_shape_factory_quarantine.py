#!/usr/bin/env python3
"""Tests for sticky quarantine release and path-alias lookup."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "workspace" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import shape_factory as sf


class QuarantineStickyReleaseTests(unittest.TestCase):
    def _soft_convert_report(self) -> dict:
        return {
            "ok": False,
            "convert_ok": False,
            "convert_error": "POST /workflow/convert failed (405)",
            "missing_required_node_types": [],
            "missing_node_types": [{"class_type": "PrimitiveNode", "node_id": 17}],
            "node_errors": {},
            "validated_at": "2026-07-16T00:00:00Z",
        }

    def _strong_report(self) -> dict:
        return {
            "ok": False,
            "convert_ok": True,
            "convert_error": None,
            "missing_required_node_types": [{"class_type": "GetNode", "node_id": 1}],
            "node_errors": {},
            "validated_at": "2026-07-16T00:00:00Z",
        }

    def test_path_alias_lookup_by_basename(self) -> None:
        host = Path("/home/yuji/comfyui-runpod-data/comfyui_user/default/workflows/generated/catalog/FB9_GEX_FACIAL_2026-04-26_00001-readable.json")
        container = Path("/workspace/comfyui_user/default/workflows/generated/catalog/FB9_GEX_FACIAL_2026-04-26_00001-readable.json")
        registry = {
            "entries": {
                str(host): {
                    "workflow_path": str(host),
                    "workflow_name": host.name,
                    "status": "quarantined",
                    "category": "convert_error",
                    "reasons": ["convert_failed"],
                }
            }
        }
        key = sf.find_quarantine_entry_key(registry, container)
        self.assertEqual(key, str(host))
        blocked, entry = sf.is_workflow_blocked(registry, container)
        self.assertTrue(blocked)
        self.assertEqual(entry.get("status"), "quarantined")

    def test_sticky_release_keeps_released_on_soft_convert_failure(self) -> None:
        host = Path("/tmp/catalog/FB9_GEX_FACIAL_2026-04-26_00001-readable.json")
        registry: dict = {"entries": {}}
        sf.release_workflow_in_registry(registry, host, note="reviewed convert 405")
        entry = sf.apply_report_to_quarantine_registry(registry, host, self._soft_convert_report())
        self.assertEqual(entry.get("status"), "released")
        self.assertEqual(entry.get("release_note"), "reviewed convert 405")
        self.assertFalse(entry.get("convert_ok"))
        self.assertIn("convert_failed", entry.get("reasons") or [])

    def test_strong_failure_re_quarantines_released(self) -> None:
        host = Path("/tmp/catalog/FB9_GEX_FACIAL_2026-04-26_00001-readable.json")
        registry: dict = {"entries": {}}
        sf.release_workflow_in_registry(registry, host, note="was fine")
        entry = sf.apply_report_to_quarantine_registry(registry, host, self._strong_report())
        self.assertEqual(entry.get("status"), "quarantined")
        self.assertIsNone(entry.get("released_at"))
        self.assertIsNone(entry.get("release_note"))
        self.assertIn("missing_required_nodes", entry.get("reasons") or [])

    def test_list_and_release_helpers(self) -> None:
        host = Path("/tmp/catalog/example-readable.json")
        registry: dict = {"entries": {}}
        sf.apply_report_to_quarantine_registry(registry, host, self._soft_convert_report())
        rows = sf.list_quarantine_entries(registry, status="quarantined")
        self.assertEqual(len(rows), 1)
        out = sf.release_quarantine_entry(registry, workflow_name="example-readable.json", note="ok")
        self.assertEqual(out.get("status"), "released")
        self.assertEqual(sf.list_quarantine_entries(registry, status="quarantined"), [])
        self.assertEqual(len(sf.list_quarantine_entries(registry, status="released")), 1)

    def test_format_block_includes_workflow_name(self) -> None:
        text = sf.format_quarantine_block(
            {
                "workflow_name": "FB9_GEX_FACIAL_2026-04-26_00001-readable.json",
                "status": "quarantined",
                "category": "convert_error",
                "reasons": ["convert_failed"],
            }
        )
        self.assertIn("workflow=FB9_GEX_FACIAL_2026-04-26_00001-readable.json", text)
        self.assertIn("category=convert_error", text)


if __name__ == "__main__":
    unittest.main()
