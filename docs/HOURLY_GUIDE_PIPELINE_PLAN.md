# Hourly guide pipeline — Phase 0/1 slice

**Status:** Active (first slice landed 2026-09-25). Parent:
[`HOURLY_UTILITY_PLAN.md`](./HOURLY_UTILITY_PLAN.md).

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

## Not in this slice

- Schedule rewrite / ruleset editor (U1)
- Multi-pipe UI / chain pin-skip bins (Phase 3)
- Auto / hybrid curation (Phase 4) — sketched above
- Clip bins
- `decisions.jsonl` retention / compaction (planned; audit-only today)

## Try it

1. Home → Hourlies → **Steer seed stills** (or open `/discovery/factory-map/hourlies/curate` on phone).
2. Use the **Pool** dropdown to pick the family/step you are sorting for (`?bin=`).
3. Keep / Pin stills you want that family's hourlies to prefer (Out / Later for fights).
4. Next hourly `pool_product` lottery for that family soft-boosts its bin members.
5. From a family map → Curate sources → **Steer seed stills →** opens that family's bin.

```bash
PYTHONPATH=workspace/scripts python3 -m unittest workspace.tests.test_shape_factory_hourly_bins -v
```
