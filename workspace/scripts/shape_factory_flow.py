#!/usr/bin/env python3
"""Canonical flow-state helpers shared by Hourly, Workbench, and Submit paths."""

from __future__ import annotations

from typing import Optional, Tuple

# States owned by Comfy queue execution lifecycle.
COMFY_STATES = frozenset({"queued", "running", "submitted"})

# States that can still be adjusted without mutating immutable history.
EDITABLE_STATES = frozenset({"", "draft", "pending", "deposited", "editing", "error", "interrupted", "abandoned"})

# States that can enter in-place edit lock from Workbench/Submit.
BEGIN_EDIT_STATES = frozenset({"", "draft", "pending", "deposited", "editing", "queued", "submitted", "error"})

# States where finishing edit is valid ("editing" or stale "pending" from re-opened tab).
FINISH_EDIT_STATES = frozenset({"", "pending", "editing"})

# States that may be archived/expunged from active job set.
DISCARDABLE_STATES = frozenset({"", "draft", "pending", "deposited", "editing", "abandoned", "error", "interrupted"})

_ALIASES = {
    "completed": "complete",
    "failed": "error",
}


def normalize_flow_status(raw: object) -> str:
    """Normalize submit status spelling into canonical flow labels."""
    s = str(raw or "").strip().lower()
    return _ALIASES.get(s, s)


def status_is_on_comfy(status: str, prompt_id: Optional[str]) -> bool:
    """
    True when Comfy currently owns the state.

    ``prompt_id`` guard keeps unknown statuses with prompt ownership out of mutable
    flows until explicitly unqueued.
    """
    st = normalize_flow_status(status)
    pid = str(prompt_id or "").strip()
    if st in COMFY_STATES:
        return True
    if pid and st not in EDITABLE_STATES:
        return True
    return False


def status_allows_begin_edit(status: str) -> bool:
    return normalize_flow_status(status) in BEGIN_EDIT_STATES


def status_allows_finish_edit(status: str) -> bool:
    return normalize_flow_status(status) in FINISH_EDIT_STATES


def status_is_pending_editable(status: str) -> bool:
    return normalize_flow_status(status) in EDITABLE_STATES


def status_is_discardable(status: str) -> bool:
    return normalize_flow_status(status) in DISCARDABLE_STATES


def flow_phase(status: str) -> str:
    """Coarse phase for queue/timeline UIs."""
    st = normalize_flow_status(status)
    if st in {"queued", "running", "submitted"}:
        return "active"
    if st in {"complete", "abandoned"}:
        return "terminal"
    if st in {"error", "interrupted"}:
        return "error"
    if st in {"", "draft", "pending", "deposited", "editing"}:
        return "planned"
    return "unknown"


def remediation_actions(status: str, *, prompt_id: Optional[str] = None) -> Tuple[str, ...]:
    """
    Recommended next actions for state-driven UI affordances.

    These are policy hints, not hard authorization checks.
    """
    st = normalize_flow_status(status)
    if status_is_on_comfy(st, prompt_id):
        return ("cancel_to_pending", "inspect_queue")
    if st == "editing":
        return ("save_pending", "queue_now", "cancel_edit")
    if st in {"", "draft", "pending", "deposited"}:
        return ("edit", "queue_now", "discard")
    if st in {"error", "interrupted"}:
        return ("retry_successor", "edit", "discard", "save_as_template")
    if st == "complete":
        return ("derive_successor", "save_as_template")
    if st == "abandoned":
        return ("restore_pending", "derive_successor")
    return ()
