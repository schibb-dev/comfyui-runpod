#!/usr/bin/env python3
"""Canonical flow-state helpers shared by Hourly, Workbench, and Submit paths."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, Tuple

# States owned by Comfy queue execution lifecycle.
COMFY_STATES = frozenset({"queued", "running", "submitted"})

# States that can still be adjusted without mutating immutable history.
EDITABLE_STATES = frozenset({"", "draft", "pending", "deposited", "editing", "error", "interrupted", "abandoned"})

# States that can enter in-place edit lock from Workbench/Submit.
BEGIN_EDIT_STATES = frozenset(
    {"", "draft", "pending", "deposited", "editing", "queued", "submitted", "error", "interrupted", "abandoned"}
)

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


_FAILURE_HEADLINES = {
    "submit_missed": "Never reached Comfy",
    "interrupted": "Interrupted on Comfy — no output",
    "comfy_failed": "Failed during the Comfy run",
    "permanent": "This job cannot be submitted as-is",
    "abandoned": "Retries exhausted",
}


def classify_job_failure(
    status: str,
    *,
    prompt_id: Optional[str] = None,
    error: Optional[str] = None,
    permanent_hint: bool = False,
) -> Optional[dict[str, Any]]:
    """One remediation path; ``primary`` chooses the default subpath."""
    st = normalize_flow_status(status)
    if st not in {"error", "interrupted", "abandoned"}:
        return None
    pid = str(prompt_id or "").strip()
    err = str(error or "").strip()
    err_l = err.lower()
    permanent = bool(permanent_hint)
    if not permanent:
        needles = (
            "invalid image file",
            "custom_validation_failed",
            "no companion png",
            "cannot build api prompt",
            "workflow missing",
            "shape missing",
            "quarantined template",
            "not a litegraph workflow",
        )
        permanent = any(n in err_l for n in needles)
    interrupted = st == "interrupted" or "interrupted" in err_l or "lost from comfy" in err_l
    if st == "abandoned":
        kind = "abandoned"
        primary = "retry_same" if not pid else "replay"
    elif permanent:
        kind = "permanent"
        primary = "edit"
    elif not pid:
        kind = "submit_missed"
        primary = "retry_same"
    elif interrupted:
        kind = "interrupted"
        primary = "replay"
    else:
        kind = "comfy_failed"
        primary = "replay"
    actions = {
        "submit_missed": ("retry_same", "edit", "replay", "discard"),
        "interrupted": ("replay", "edit", "discard"),
        "comfy_failed": ("replay", "edit", "discard"),
        "permanent": ("edit", "discard"),
        "abandoned": ("retry_same", "replay", "edit", "discard") if not pid else ("replay", "edit", "discard"),
    }[kind]
    return {
        "kind": kind,
        "headline": _FAILURE_HEADLINES[kind],
        "primary": primary,
        "actions": list(actions),
        "can_retry_same": "retry_same" in actions,
        "detail": err or None,
    }


def remediation_actions(
    status: str,
    *,
    prompt_id: Optional[str] = None,
    error: Optional[str] = None,
    permanent_hint: bool = False,
) -> Tuple[str, ...]:
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
    classified = classify_job_failure(
        st, prompt_id=prompt_id, error=error, permanent_hint=permanent_hint
    )
    if classified:
        return tuple(classified["actions"])
    if st == "complete":
        return ("derive_successor", "save_as_template")
    return ()


_FAILURE_EVENT_ACTIONS = frozenset({"submit_failed", "comfy_failed", "interrupted", "abandoned"})


def _flow_event_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def failure_event_action(
    status: str,
    *,
    prompt_id: Optional[str] = None,
    error: Optional[str] = None,
) -> Optional[str]:
    classified = classify_job_failure(status, prompt_id=prompt_id, error=error)
    if not classified:
        return None
    return {
        "submit_missed": "submit_failed",
        "interrupted": "interrupted",
        "comfy_failed": "comfy_failed",
        "permanent": "submit_failed",
        "abandoned": "abandoned",
    }[str(classified["kind"])]


def append_flow_event(
    submit: dict[str, Any],
    *,
    action: str,
    actor: str = "system",
    source_surface: str = "factory",
    reason: Optional[str] = None,
    ok: bool = True,
    at: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Append one remediations-timeline event; skip an exact consecutive duplicate."""
    hist = submit.get("flow_events")
    if not isinstance(hist, list):
        hist = []
        submit["flow_events"] = hist
    event = {
        "at": str(at or "").strip() or _flow_event_now(),
        "action": str(action or "").strip() or "action",
        "actor": str(actor or "system").strip() or "system",
        "source_surface": str(source_surface or "factory").strip() or "factory",
        "reason": str(reason or "").strip() or None,
        "ok": bool(ok),
    }
    if hist:
        last = hist[-1] if isinstance(hist[-1], dict) else {}
        if (
            last.get("action") == event["action"]
            and last.get("reason") == event["reason"]
            and last.get("ok") == event["ok"]
        ):
            return hist
    hist.append(event)
    return hist


def ensure_failure_flow_event(
    submit: dict[str, Any],
    status: str,
    *,
    error: Optional[str] = None,
    prompt_id: Optional[str] = None,
    at: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Guarantee a failure-origin event so Errors get the same timeline as interrupts."""
    hist = submit.get("flow_events")
    if not isinstance(hist, list):
        hist = []
        submit["flow_events"] = hist
    action = failure_event_action(status, prompt_id=prompt_id, error=error)
    if not action:
        return hist
    if any(isinstance(ev, dict) and ev.get("action") in _FAILURE_EVENT_ACTIONS for ev in hist):
        return hist
    actor = "comfy" if action in {"interrupted", "comfy_failed"} else "drain"
    source = "comfy" if actor == "comfy" else "submit"
    return append_flow_event(
        submit,
        action=action,
        actor=actor,
        source_surface=source,
        reason=error,
        ok=False,
        at=at,
    )
