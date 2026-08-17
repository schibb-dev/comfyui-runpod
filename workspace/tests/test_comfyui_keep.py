#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from comfyui_keep import (  # noqa: E402
    ContainerFacts,
    Facts,
    KeepState,
    MAX_ATTEMPTS,
    decide,
    parse_docker_time,
)

NOW = datetime(2026, 8, 14, 13, 0, tzinfo=timezone.utc)


def facts(**kwargs) -> Facts:
    base = dict(
        now=NOW,
        boot_unit_active=True,
        hold=False,
        preflight_ok=True,
        comfy=ContainerFacts(
            present=True,
            status="exited",
            exit_code=137,
            oom_killed=True,
            started_at=NOW - timedelta(hours=3),
            finished_at=NOW - timedelta(minutes=20),
        ),
        watch_queue=ContainerFacts(present=True, status="running"),
        http_8188_ok=False,
    )
    base.update(kwargs)
    return Facts(**base)


class ComfyuiKeepTests(unittest.TestCase):
    def test_parse_docker_nano_z(self) -> None:
        dt = parse_docker_time("2026-08-14T07:53:16.123456789Z")
        assert dt is not None
        self.assertEqual(dt.minute, 53)
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_hold_and_inactive_and_preflight(self) -> None:
        self.assertEqual(decide(facts(hold=True), KeepState()).action, "noop")
        self.assertEqual(decide(facts(boot_unit_active=False), KeepState()).action, "noop")
        self.assertEqual(decide(facts(preflight_ok=False), KeepState()).action, "noop")

    def test_running_healthy(self) -> None:
        d = decide(
            facts(
                comfy=ContainerFacts(present=True, status="running", started_at=NOW - timedelta(hours=1)),
                http_8188_ok=True,
            ),
            KeepState(),
        )
        self.assertEqual(d.action, "noop")
        self.assertIn("queue ok", d.reason)

    def test_startup_grace(self) -> None:
        d = decide(
            facts(
                comfy=ContainerFacts(present=True, status="running", started_at=NOW - timedelta(minutes=3)),
                http_8188_ok=False,
            ),
            KeepState(),
        )
        self.assertEqual(d.action, "noop")
        self.assertIn("startup grace", d.reason)

    def test_running_hung_not_killed(self) -> None:
        d = decide(
            facts(
                comfy=ContainerFacts(present=True, status="running", started_at=NOW - timedelta(minutes=30)),
                http_8188_ok=False,
            ),
            KeepState(),
        )
        self.assertEqual(d.action, "noop")
        self.assertIn("not killing", d.reason)

    def test_clean_exit_zero(self) -> None:
        d = decide(
            facts(
                comfy=ContainerFacts(
                    present=True,
                    status="exited",
                    exit_code=0,
                    oom_killed=False,
                    finished_at=NOW - timedelta(minutes=20),
                )
            ),
            KeepState(),
        )
        self.assertEqual(d.action, "noop")
        self.assertIn("clean exit", d.reason)

    def test_oom_cooldown(self) -> None:
        d = decide(
            facts(
                comfy=ContainerFacts(
                    present=True,
                    status="exited",
                    exit_code=137,
                    oom_killed=True,
                    finished_at=NOW - timedelta(minutes=5),
                )
            ),
            KeepState(),
        )
        self.assertEqual(d.action, "noop")
        self.assertEqual(d.reason, "OOM cooldown")

    def test_oom_ready_ups_comfy_only(self) -> None:
        d = decide(facts(), KeepState())
        self.assertEqual(d.action, "up")
        self.assertEqual(d.services, ("comfyui",))

    def test_missing_watch_queue_included(self) -> None:
        d = decide(facts(watch_queue=ContainerFacts(present=False)), KeepState())
        self.assertEqual(d.services, ("comfyui", "watch_queue"))

    def test_min_gap(self) -> None:
        state = KeepState(attempts=[NOW - timedelta(minutes=2)])
        d = decide(facts(), state)
        self.assertEqual(d.action, "noop")
        self.assertIn("min gap", d.reason)

    def test_retry_cap(self) -> None:
        state = KeepState(
            attempts=[
                NOW - timedelta(minutes=90),
                NOW - timedelta(minutes=40),
                NOW - timedelta(minutes=20),
            ]
        )
        d = decide(facts(), state)
        self.assertEqual(d.action, "noop")
        self.assertIn("retry cap", d.reason)
        self.assertEqual(d.attempts_in_window, MAX_ATTEMPTS)

    def test_old_attempts_pruned(self) -> None:
        state = KeepState(
            attempts=[
                NOW - timedelta(hours=3),
                NOW - timedelta(hours=3, minutes=1),
                NOW - timedelta(hours=3, minutes=2),
            ]
        )
        d = decide(facts(), state)
        self.assertEqual(d.action, "up")
        self.assertEqual(d.attempts_in_window, 0)

    def test_missing_container_ups(self) -> None:
        d = decide(facts(comfy=ContainerFacts(present=False)), KeepState())
        self.assertEqual(d.action, "up")


if __name__ == "__main__":
    unittest.main()
