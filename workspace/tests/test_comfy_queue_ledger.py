#!/usr/bin/env python3
"""Unit tests for comfy_queue_ledger restore helpers."""

from __future__ import annotations

import unittest
from typing import Any, Dict, List
from unittest import mock

from comfy_queue_ledger import (
    _apply_ledger_clear,
    _candidate_ids_from_snapshot,
    _default_state,
    _restore_missing_prompts,
)


class CandidateIdsTests(unittest.TestCase):
    def test_pending_then_running_deduped(self) -> None:
        snap = {"pending": ["a", "b", "a"], "running": ["b", "c"]}
        self.assertEqual(_candidate_ids_from_snapshot(snap), ["a", "b", "c"])


class ApplyLedgerClearTests(unittest.TestCase):
    def test_clear_drops_restore_state_keeps_paused(self) -> None:
        state = _default_state()
        state["paused"] = True
        state["known"] = {"a": {"prompt": {"1": {"class_type": "X"}}}}
        state["backlog"] = [{"prompt_id": "b"}]
        state["last_snapshot"] = {"running": ["a"], "pending": ["c"]}
        state["restore_attempts"] = {"a": 1}
        state["clear_requested_at"] = 123.0
        counts = _apply_ledger_clear(state)
        self.assertEqual(counts, {"known": 1, "backlog": 1, "snapshot": 2})
        self.assertEqual(state["known"], {})
        self.assertEqual(state["backlog"], [])
        self.assertEqual(state["last_snapshot"], {"running": [], "pending": []})
        self.assertEqual(state["restore_attempts"], {})
        self.assertEqual(state["clear_requested_at"], 0.0)
        self.assertTrue(state["paused"])


class RestoreMissingPromptsTests(unittest.TestCase):
    def test_outage_restore_submits_missing(self) -> None:
        state = _default_state()
        state["known"] = {
            "old-1": {"prompt": {"1": {"class_type": "LoadImage", "inputs": {}}}, "extra_data": {"client_id": "ui"}},
            "old-2": {"prompt": {"2": {"class_type": "LoadImage", "inputs": {}}}, "extra_data": {}},
        }
        events: List[Dict[str, Any]] = []

        def log_event(typ: str, **kwargs: Any) -> None:
            events.append({"type": typ, **kwargs})

        submits: List[str] = []

        def fake_submit(server, *, prompt, client_id, extra_data=None, outputs_to_execute=None):
            submits.append(list(prompt.keys())[0])
            return True, {"prompt_id": f"new-{submits[-1]}"}

        with mock.patch("comfy_queue_ledger._submit_prompt", side_effect=fake_submit):
            restored, parked, unrec, live = _restore_missing_prompts(
                state,
                server="http://x",
                client_id="ledger",
                candidates=["old-1", "old-2", "ghost"],
                current_ids=set(),
                spillover=False,
                pending_target=2,
                live_pending=0,
                max_restore_attempts=2,
                expected_add_ttl_s=20.0,
                source="outage",
                log_event=log_event,
                now=1000.0,
            )
        self.assertEqual(restored, 2)
        self.assertEqual(parked, 0)
        self.assertEqual(unrec, 1)
        self.assertEqual(live, {"new-1", "new-2"})
        self.assertEqual(submits, ["1", "2"])
        self.assertEqual(state["stats"]["restored_outage"], 2)
        self.assertTrue(any(e["type"] == "outage_restore_unrecoverable_no_payload" for e in events))

    def test_skips_already_live(self) -> None:
        state = _default_state()
        state["known"] = {
            "old-1": {"prompt": {"1": {"class_type": "LoadImage", "inputs": {}}}},
        }
        events: List[Dict[str, Any]] = []

        def log_event(typ: str, **kwargs: Any) -> None:
            events.append({"type": typ, **kwargs})

        with mock.patch("comfy_queue_ledger._submit_prompt") as submit:
            restored, parked, unrec, live = _restore_missing_prompts(
                state,
                server="http://x",
                client_id="ledger",
                candidates=["old-1"],
                current_ids={"old-1"},
                spillover=False,
                pending_target=2,
                live_pending=1,
                max_restore_attempts=2,
                expected_add_ttl_s=20.0,
                source="startup",
                log_event=log_event,
                now=1000.0,
            )
            submit.assert_not_called()
        self.assertEqual((restored, parked, unrec), (0, 0, 0))
        self.assertEqual(live, {"old-1"})


if __name__ == "__main__":
    unittest.main()
