# Factory MCP

Stdio MCP so a Cursor agent can operate hourlies without opening `shape_factory_hourly.py` or asking for a one-off analysis. Humans keep using the Home Hourly panel. Both write the same `hourly-schedule.json` / explore record.

Project wiring: [`.cursor/mcp.json`](../.cursor/mcp.json) (`factory` + Playwright). No secrets — stdio command only. Data root is `SHAPE_FACTORY_DATA_ROOT` or repo `.data/`. Writes as uid 1000 per [container-runtime-uid](../.cursor/rules/container-runtime-uid.mdc).

Implementation: [`workspace/scripts/factory_mcp.py`](../workspace/scripts/factory_mcp.py) (JSON-RPC Content-Length) + [`factory_mcp_ops.py`](../workspace/scripts/factory_mcp_ops.py). Tools call the same Python Home and `shape_factory.py` use. Not a second planner.

## Verbs

| Tool | Does |
|------|------|
| `hourly_status` | Enabled, interval, pending floor, active explore, last tick |
| `hourly_simulate` | Next N picks (family / phase / reason). Respects explore |
| `hourly_explore` | Set/clear a time-boxed target (family, prompt, still, clip) + `boost`/`focus` + `until` or `ticks` |
| `hourly_schedule` | Pause/resume, interval, pending floor only |
| `reuse_stats_summary` | Operator vs hourly vs appetite mix (read-only) |
| `mark_appetite` | `more` / `fast_track` / `less` / `remove` on an output or still (Discovery store). `dry_run` default **true** |
| `hourly_backlog` | Facial / i2v→GEX waiting lists (preview, not cull) |
| `queue_snapshot` | Comfy waiting/running + factory pending counts (no raw graphs) |

Example: “explore X-KNEEL-FB9-bare for 2 hours” → one `hourly_explore` call. `hourly_simulate` then shows about half of seed ticks as Bare. Home shows the same overlay.

## dry_run

Every mutating tool accepts `dry_run`. Default **true** when the write could change ratings (`mark_appetite`). Explore and schedule writes apply immediately (they do not enqueue GPU work). Anything that would submit, discard, or purge stays out of this server.

## Out of scope

- Dump of `/api/shape-factory/*` or every `shape_factory.py` subcommand
- A second promo / planner path (no parallel `still_promo` hack)
- Silent GPU fill: `submit`, `discard`, `purge`, backlog cull

## Shared explore record

`hourly_explore` (MCP), Home Hourly **Explore** controls, and CLI `schedule-set --explore-family` all call `set_hourly_explore` / `clear_hourly_explore`. `select_seed_family` and `simulate_hourly_picks` read that record. `mark_hourly_tick` consumes `remaining_ticks`; simulate does not.

See [HOURLY_UTILITY_PLAN.md](./HOURLY_UTILITY_PLAN.md).
