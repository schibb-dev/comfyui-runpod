# Hourly guide pipeline — Phase 0/1 slice

**Program:** A3 Refinement (pipes/bins under hourly). Hub:
[`PLANNING_OVERVIEW.md`](./PLANNING_OVERVIEW.md).

**Status:** Active (first slice landed 2026-09-25). Parent:
[`HOURLY_UTILITY_PLAN.md`](./HOURLY_UTILITY_PLAN.md). Drain policy as data:
[`HOURLY_DRAIN_POLICY_PLAN.md`](./HOURLY_DRAIN_POLICY_PLAN.md).

## Activity model

> **[Asset source] → [Pool(s)] → [Curation bin] → [Workflow(s)] → [Priority + schedule]**

This slice ships **per-family** steer bins for `source_still` pools (manual
Keep/Pin/Later/Out → soft-bias hourly i2v lottery). One shared example bin
(`hourly-seed-stills` → `X-KNEEL-FB9-bare`) was the starting point; ensure now
creates/splits **1:1** bins so each workflow-step has its own steering.

## What landed

| Piece | Location |
|-------|----------|
| Schema + current state | `.data/shape_factory/hourly_guide/{bins,pipes}.json` |
| Decision ledger (audit) | `.data/shape_factory/hourly_guide/decisions.jsonl` |
| Runtime | `workspace/scripts/shape_factory_hourly_bins.py` |
| Soft bias | `_source_promotion_mult` × `still_bin_bias_mult` in `shape_factory_hourly.py` |
| API | `GET …/hourly-bins`, `GET …/hourly-bins/candidates`, `POST …/hourly-bins/item` |
| Phone UI | `/discovery/factory-map/hourlies/curate` (`HourlySeedBinCurateApp`) |
| Home | Hourlies card → **Steer seed stills →** |

### Bin actions

| Action | Hourly weight (bound families) |
|--------|--------------------------------|
| `pin` | ×16 |
| `keep` | ×8 |
| `later` | ×0.25 |
| `out` | ×0 |

**Storage vs ledger:** live Keep/Pin/Later/Out live only in `bins.json` (upsert on change). Hourly bias and the Steering UI read that file — never `decisions.jsonl`. The JSONL is append-only audit for later heuristics; steer changes over time are expected.

**Later:** ledger retention / compaction (rotate or drop old rows; never rebuild bin state from JSONL).

## Defaults

- Bin id: `hourly-seed-stills` (bound to `X-KNEEL-FB9-bare` only)
- Pool slot: `source_still`
- **1:1 policy:** each family / workflow-step with a `source_still` pool gets its
  own steer bin (`workflow_families: [<that family>]`). Workflows are often
  source-sensitive (e.g. a “remove hat” prompt can invent a hat to remove) —
  shared steering across steps is wrong until we have evidence they share.
- `ensure_steer_bins_for_source_stills()` creates missing bins and splits legacy
  multi-family lists. **New family bins start empty** (all candidates New) —
  Keep/Pin/Out are not cloned across pools.

## Later — auto / hybrid curation (Phase 4)

Manual bins are the teacher. Eventually heuristics should propose Keep/Pin/Out
(or soft weights) **per workflow-step**, using signals such as:

- still / vision **tags** (attrs that fight or fit the step’s prompt)
- **embeddings** / similarity (stills that historically produced good outputs for that family)
- the **decision ledger** + outcome ratings as supervision

Human override stays authoritative in `bins.json`; autosuggest never writes the
ledger as source of truth.

## Not in this slice (seed-stills Phase 0/1)

- Schedule rewrite / ruleset editor (U1)
- Auto / hybrid curation (Phase 4) — sketched above
- `decisions.jsonl` retention / compaction (planned; audit-only today)

## Phase 1b — video / clip steer (landed)

Clip-shaped units for `source_video` families (same Keep/Pin/Later/Out soft-bias):

| Piece | Location |
|-------|----------|
| Runtime | `workspace/scripts/shape_factory_hourly_video_steer.py` |
| Ensure / resolve / candidates | `ensure_steer_bins_for_source_videos`, `list_video_bin_candidates` |
| Soft bias | `video_steer_bias_mult` × `_apply_source_promotion` |
| Modes | bin `curation_unit`: `auto` \| `clips` \| `videos` (`POST …/hourly-bins/unit`) |
| Whole-file units | virtual `whole:{parent_content_id}` — **not** inserted into clips DB |

Hybrid deck ranking (auto): ★ span clips → other span clips → whole-file virtual
units. Existence of a span clip is already the strong signal; whole-file remains
available and switchable.

## Phase 3 — chain drain steer (runtime + backlog cull)

Part of steering **all** production stages (not only seed lottery). Chain waiting
lists (`i2v→GEX`, `GEX2→FACIAL`) hard-drop a parent when its **direct output
clip** is:

- appetite **`remove`** (own mark only — not inherited still/ancestor), or
- consumer-bin video-steer **`out`** (`FB9_GEX` / `FB9_GEX_FACIAL`)

| Piece | Location |
|-------|----------|
| Gate | `_chain_parent_output_blocked` in `shape_factory_hourly.py` |
| Applied in | `list_i2v_needing_gex`, `list_gex2_needing_facial` (backlog API + hourly picks) |
| Cull UI | Factory Map → Hourlies → Chain backlogs preview (appetite + Keep/Later/Out) |

How marks map to stages will keep iterating (soft weights, descendant health,
etc.); prefer shared stores over chain-only shadow state. Locked follow-ons
(Less transient, age×appetite, Out→trash, similarity factor, slices):
[`CHAIN_STEER_AND_APPETITE_CONTROL_PLAN.md`](./CHAIN_STEER_AND_APPETITE_CONTROL_PLAN.md).
Custody / broken lineage:
[`LINEAGE_REMEDIATION_PLAN.md`](./LINEAGE_REMEDIATION_PLAN.md).

**Pre-0 (2026-09-29, legacy path):** i2v→extend backlog uses
`HOURLY_I2V_GEX_LOOKBACK_DAYS` (default 30); parents are satisfied when **either**
`FB9_GEX` or `FB9_GEX2` binds `source_video` (equal-weight consumer pick); soft
Later/Less demote picks. Configuration-first drain walker remains later
([`HOURLY_DRAIN_POLICY_PLAN.md`](./HOURLY_DRAIN_POLICY_PLAN.md)).

## Try it

1. Home → Hourlies → **Steer seed stills** (or open `/discovery/factory-map/hourlies/curate` on phone).
2. Use the **Pool** dropdown to pick the family/step you are sorting for (`?bin=`).
3. Keep / Pin stills you want that family's hourlies to prefer (Out / Later for fights).
4. Next hourly `pool_product` lottery for that family soft-boosts its bin members.
5. From a family map → Curate sources → **Steer seed stills →** opens that family's bin.

```bash
PYTHONPATH=workspace/scripts python3 -m unittest workspace.tests.test_shape_factory_hourly_bins -v
```
