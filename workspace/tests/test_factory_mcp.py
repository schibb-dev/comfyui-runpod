#!/usr/bin/env python3
"""Factory MCP verbs — same ops Home uses."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401

from factory_mcp import handle
from factory_mcp_ops import TOOL_SPECS, dispatch_tool, hourly_explore, reuse_stats_summary


class FactoryMcpTests(unittest.TestCase):
    def test_tool_list_covers_m0_m2(self) -> None:
        names = {row["name"] for row in TOOL_SPECS}
        self.assertTrue(
            {
                "hourly_status",
                "hourly_simulate",
                "hourly_explore",
                "hourly_schedule",
                "reuse_stats_summary",
                "mark_appetite",
                "hourly_backlog",
                "queue_snapshot",
            }
            <= names
        )

    def test_initialize_and_tools_list(self) -> None:
        init = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertIsNotNone(init)
        assert init is not None
        self.assertEqual(init["result"]["serverInfo"]["name"], "factory")
        listed = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        assert listed is not None
        names = [t["name"] for t in listed["result"]["tools"]]
        self.assertIn("hourly_explore", names)

    def test_hourly_explore_dry_run_and_apply(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shape_factory").mkdir()
            dry = hourly_explore(
                action="set",
                kind="family",
                target="X-KNEEL-FB9-bare",
                hours=2,
                dry_run=True,
                data_root=str(root),
            )
            self.assertTrue(dry["ok"])
            self.assertTrue(dry["dry_run"])
            self.assertIsNone(hourly_explore(action="status", data_root=str(root))["explore"])
            applied = dispatch_tool(
                "hourly_explore",
                {
                    "action": "set",
                    "kind": "family",
                    "target": "X-KNEEL-FB9-bare",
                    "hours": 2,
                    "dry_run": False,
                    "data_root": str(root),
                },
            )
            self.assertTrue(applied["ok"])
            self.assertEqual(applied["explore"]["family"], "X-KNEEL-FB9-bare")
            status = dispatch_tool("hourly_status", {"data_root": str(root)})
            self.assertTrue(status["ok"])
            self.assertEqual((status.get("explore") or {}).get("target"), "X-KNEEL-FB9-bare")
            from unittest.mock import patch

            def _fake_plan(*, cursor: int = 0, family: str = "", **_kwargs):
                return {"ok": True, "pick_mode": "pool_product", "step": "pool_product", "family": family}

            with patch("shape_factory_hourly.plan_hourly_step", side_effect=_fake_plan):
                sim = dispatch_tool("hourly_simulate", {"count": 8, "data_root": str(root)})
            self.assertTrue(sim["ok"])
            self.assertEqual((sim.get("policy") or {}).get("explore", {}).get("family"), "X-KNEEL-FB9-bare")
            seed_picks = [p for p in sim.get("picks") or [] if p.get("pick_mode") != "chain"]
            self.assertTrue(any(p.get("family") == "X-KNEEL-FB9-bare" for p in seed_picks))

    def test_hourly_schedule_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shape_factory").mkdir()
            out = dispatch_tool(
                "hourly_schedule",
                {"enabled": False, "dry_run": True, "data_root": str(root)},
            )
            self.assertTrue(out["ok"])
            self.assertTrue(out["dry_run"])
            self.assertFalse(out["schedule"]["enabled"])
            live = dispatch_tool("hourly_status", {"data_root": str(root)})
            self.assertTrue(live.get("enabled", True))

    def test_reuse_stats_summary_from_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            snap = root / "shape_factory" / "reuse_stats.json"
            snap.parent.mkdir(parents=True)
            snap.write_text(
                json.dumps(
                    {
                        "jobs_scanned": 10,
                        "jobs_hourly": 6,
                        "jobs_operator": 4,
                        "jobs_favored": 2,
                        "optimal_unit": {"recommended": "video_asset"},
                        "optimal_recipe_unit": {"recommended": "family"},
                        "guide_hourly": {
                            "source": "appetite+experiment_floor",
                            "suggested_seed_family_weights": [
                                {
                                    "family": "X-KNEEL-FB9-bare",
                                    "weight": 40,
                                    "operator_share": 0.5,
                                    "hourly_share": 0.3,
                                }
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )
            out = reuse_stats_summary(data_root=str(root))
            self.assertTrue(out["ok"])
            self.assertEqual(out["jobs_scanned"], 10)
            self.assertEqual(out["guide_source"], "appetite+experiment_floor")
            self.assertEqual(out["optimal_seed_unit"], "video_asset")

    def test_mark_appetite_dry_run_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            og = root / "og"
            og.mkdir(parents=True)
            media = og / "still.jpg"
            media.write_bytes(b"not-a-real-jpeg")
            prev = os.environ.get("SHAPE_FACTORY_OG_ROOT")
            os.environ["SHAPE_FACTORY_OG_ROOT"] = str(og)
            try:
                out = dispatch_tool(
                    "mark_appetite",
                    {"relpath": "still.jpg", "appetite": "more", "data_root": str(root)},
                )
            finally:
                if prev is None:
                    os.environ.pop("SHAPE_FACTORY_OG_ROOT", None)
                else:
                    os.environ["SHAPE_FACTORY_OG_ROOT"] = prev
            self.assertTrue(out["ok"])
            self.assertTrue(out["dry_run"])
            self.assertEqual(out["appetite"], "more")

    def test_visibility_tools(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shape_factory" / "jobs").mkdir(parents=True)
            backlog = dispatch_tool("hourly_backlog", {"data_root": str(root)})
            self.assertTrue(backlog["ok"])
            self.assertIsInstance(backlog.get("chains"), list)
            snap = dispatch_tool("queue_snapshot", {"data_root": str(root)})
            self.assertTrue(snap["ok"])
            self.assertEqual(snap["factory_pending"], 0)
            self.assertIn("comfy_waiting", snap)

    def test_tools_call_unknown(self) -> None:
        reply = handle(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {"name": "nope", "arguments": {}},
            }
        )
        assert reply is not None
        self.assertTrue(reply["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
