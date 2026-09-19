#!/usr/bin/env python3
"""Shared hourly explore record — MCP, Home, and select_seed_family."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import support  # noqa: F401

from shape_factory_hourly import (
    consume_explore_tick,
    hourly_explore_active,
    load_hourly_schedule,
    mark_hourly_tick,
    normalize_hourly_explore,
    save_hourly_schedule,
    select_seed_family,
    set_hourly_explore,
    simulate_hourly_picks,
)


class HourlyExploreTests(unittest.TestCase):
    def test_normalize_family_boost(self) -> None:
        row = normalize_hourly_explore(
            {"kind": "family", "target": "X-KNEEL-FB9-bare", "strength": "boost"}
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["kind"], "family")
        self.assertEqual(row["family"], "X-KNEEL-FB9-bare")
        self.assertEqual(row["share"], 0.5)

    def test_expired_until_is_inactive(self) -> None:
        past = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).isoformat()
        sch = {
            "explore": {
                "kind": "family",
                "target": "X-KNEEL-FB9-bare",
                "until": past,
            }
        }
        self.assertIsNone(hourly_explore_active(schedule=sch))

    def test_select_seed_family_reserves_explore_share(self) -> None:
        target = "X-KNEEL-FB9-bare"
        baseline = [
            select_seed_family(c, schedule={"explore": None}) for c in range(200)
        ]
        focused = [
            select_seed_family(
                c,
                schedule={
                    "explore": {
                        "kind": "family",
                        "target": target,
                        "strength": "focus",
                    }
                },
            )
            for c in range(200)
        ]
        self.assertGreater(focused.count(target) / 200, baseline.count(target) / 200)
        self.assertGreaterEqual(focused.count(target) / 200, 0.7)

    def test_set_and_simulate_share_one_record(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shape_factory").mkdir()
            built = set_hourly_explore(
                kind="family",
                target="X-KNEEL-FB9-bare",
                strength="boost",
                hours=2,
                data_root=root,
            )
            self.assertTrue(built["ok"])
            live = hourly_explore_active(data_root=root)
            self.assertIsNotNone(live)
            assert live is not None
            self.assertEqual(live["family"], "X-KNEEL-FB9-bare")
            status = load_hourly_schedule(data_root=root)
            self.assertEqual((status.get("explore") or {}).get("family"), "X-KNEEL-FB9-bare")

    def test_simulate_does_not_consume_ticks(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shape_factory" / "jobs").mkdir(parents=True)
            set_hourly_explore(
                kind="family",
                target="X-KNEEL-FB9-bare",
                ticks=3,
                data_root=root,
            )

            def _fake_plan(*, cursor: int = 0, family: str = "", **_kwargs):
                return {"ok": True, "pick_mode": "pool_product", "step": "pool_product", "family": family}

            with patch("shape_factory_hourly.plan_hourly_step", side_effect=_fake_plan):
                simulate_hourly_picks(4, hourly_state={"sample_cursor": 10}, data_root=root)
            live = hourly_explore_active(data_root=root)
            self.assertIsNotNone(live)
            assert live is not None
            self.assertEqual(live.get("remaining_ticks"), 3)
            mark_hourly_tick(load_hourly_schedule(data_root=root), data_root=root)
            after = hourly_explore_active(data_root=root)
            self.assertIsNotNone(after)
            assert after is not None
            self.assertEqual(after.get("remaining_ticks"), 2)

    def test_consume_explore_tick_clears_at_zero(self) -> None:
        sch = {
            "explore": normalize_hourly_explore(
                {"kind": "family", "target": "FB9_GEX", "remaining_ticks": 1}
            )
        }
        out = consume_explore_tick(sch)
        self.assertIsNone(out.get("explore"))


if __name__ == "__main__":
    unittest.main()
