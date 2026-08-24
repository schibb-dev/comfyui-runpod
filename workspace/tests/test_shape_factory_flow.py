#!/usr/bin/env python3
"""Tests for canonical flow-state helpers."""

from __future__ import annotations

import unittest

import support  # noqa: F401  — injects workspace/scripts onto sys.path
from shape_factory_flow import (
    flow_phase,
    normalize_flow_status,
    remediation_actions,
    status_allows_begin_edit,
    status_allows_finish_edit,
    status_is_discardable,
    status_is_on_comfy,
    status_is_pending_editable,
)


class TestShapeFactoryFlow(unittest.TestCase):
    def test_normalize_aliases(self) -> None:
        self.assertEqual(normalize_flow_status("completed"), "complete")
        self.assertEqual(normalize_flow_status("FAILED"), "error")
        self.assertEqual(normalize_flow_status("pending"), "pending")

    def test_on_comfy_rules(self) -> None:
        self.assertTrue(status_is_on_comfy("queued", None))
        self.assertTrue(status_is_on_comfy("running", "pid-1"))
        self.assertFalse(status_is_on_comfy("pending", None))
        self.assertFalse(status_is_on_comfy("error", "pid-stale"))
        self.assertTrue(status_is_on_comfy("mystery_state", "pid-stale"))

    def test_edit_transition_guards(self) -> None:
        self.assertTrue(status_allows_begin_edit("pending"))
        self.assertTrue(status_allows_begin_edit("queued"))
        self.assertFalse(status_allows_begin_edit("complete"))
        self.assertTrue(status_allows_finish_edit("editing"))
        self.assertTrue(status_allows_finish_edit("pending"))
        self.assertFalse(status_allows_finish_edit("running"))

    def test_editable_and_discardable_sets(self) -> None:
        self.assertTrue(status_is_pending_editable("editing"))
        self.assertTrue(status_is_pending_editable("error"))
        self.assertFalse(status_is_pending_editable("queued"))
        self.assertTrue(status_is_discardable("interrupted"))
        self.assertFalse(status_is_discardable("complete"))

    def test_flow_phase_and_remediation(self) -> None:
        self.assertEqual(flow_phase("queued"), "active")
        self.assertEqual(flow_phase("editing"), "planned")
        self.assertEqual(flow_phase("error"), "error")
        self.assertEqual(flow_phase("complete"), "terminal")
        self.assertIn("cancel_to_pending", remediation_actions("queued", prompt_id="pid-1"))
        self.assertIn("queue_now", remediation_actions("editing"))
        self.assertIn("save_as_template", remediation_actions("error"))


if __name__ == "__main__":
    unittest.main()
