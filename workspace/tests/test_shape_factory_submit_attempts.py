#!/usr/bin/env python3
"""Tests for shape_factory_submit_attempts."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class SubmitAttemptsTests(unittest.TestCase):
    def test_classify_permission_denied(self) -> None:
        from shape_factory_submit_attempts import classify_queue_exception

        exc = PermissionError(
            "[Errno 13] Permission denied: '/workspace/comfyui_user/default/workflows/"
            "generated/shape_factory/X-KNEEL-FB9-bare/foo.workflow.json'"
        )
        c = classify_queue_exception(exc)
        self.assertEqual(c["error"], "permission_denied")
        self.assertIn("X-KNEEL-FB9-bare", c.get("path_hint") or "")
        self.assertIn("chown", (c.get("hint") or "").lower())

    def test_classify_quarantine(self) -> None:
        from shape_factory_submit_attempts import classify_queue_exception, http_status_for_error

        exc = RuntimeError(
            "workflow quarantined — fix validation issues:\n"
            "  - FB8VA4-readable.json: status=quarantined"
        )
        c = classify_queue_exception(exc)
        self.assertEqual(c["error"], "workflow_quarantined")
        self.assertEqual(http_status_for_error(c["error"]), 409)

    def test_classify_missing_bindings(self) -> None:
        from shape_factory_submit_attempts import classify_queue_exception

        c = classify_queue_exception(ValueError("missing required bindings: ['source_video']"))
        self.assertEqual(c["error"], "missing_bindings")

    def test_append_and_list_errors_only(self) -> None:
        from shape_factory_submit_attempts import (
            append_attempt,
            build_attempt_record,
            list_attempts_payload,
            summarize_request_body,
        )

        with tempfile.TemporaryDirectory() as td:
            status = Path(td)
            body = {
                "family_slug": "X-KNEEL-FB9-bare",
                "bindings": {"source_still": "input/abc.jpeg"},
                "source_surface": "submit",
            }
            summary = summarize_request_body(body)
            self.assertEqual(summary["bindings"]["source_still"], "abc.jpeg")

            ok = build_attempt_record(
                ok=True,
                request_summary=summary,
                http_status=200,
                job_key="job_ok",
                prompt_id="pid",
            )
            bad = build_attempt_record(
                ok=False,
                request_summary=summary,
                http_status=500,
                error="permission_denied",
                detail="Permission denied: '/tmp/x'",
                hint="chown it",
                path_hint="/tmp/x",
            )
            from shape_factory_submit_attempts import attempts_path

            path = attempts_path(status)
            append_attempt(path, ok)
            append_attempt(path, bad)

            all_items = list_attempts_payload(status, limit=10)
            self.assertEqual(all_items["count"], 2)
            errs = list_attempts_payload(status, limit=10, errors_only=True)
            self.assertEqual(errs["count"], 1)
            self.assertEqual(errs["items"][0]["error"], "permission_denied")
            self.assertEqual(errs["items"][0]["family_slug"], "X-KNEEL-FB9-bare")
            # Newest first
            self.assertFalse(all_items["items"][0]["ok"])

            # Round-trip JSONL
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertTrue(json.loads(lines[0])["ok"])


if __name__ == "__main__":
    unittest.main()
