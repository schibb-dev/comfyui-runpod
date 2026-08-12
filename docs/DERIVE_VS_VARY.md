# Derive vs Vary vs Extend

**Last updated:** 2026-08-11

Short operator map for Workbench advance actions and hourly “Derived” badges.

## Quick table

| Control / badge | What it does | Same bindings? |
|-----------------|--------------|----------------|
| **Vary** (`advance.vary`) | Front-queue **exact replay** (`hook: replay_front`) | Yes |
| **Extend** (`advance.extend`) | Chain this **output** into the video slot | Source changes to this output |
| **Derive** (`advance.derive`) | **Rewire** prompt and/or source via `_derive_rewire` → `pick_mode: derive` (or `extend` if facet=`both` picks chain) | No — new combo |
| **Derived** badge | Jobs stamped `pick_mode` / construction as derive (Workbench Derive, hourly appetite, CLI) | N/A |

**Vary ≠ Derive.** Vary keeps the seed’s bindings. Derive builds a distinct combo from the seed.

## Re-run (Workbench)

**Re-run** creates a **new** job from *this job’s* recipe. It is not Advance and not an in-place edit.

Allow-list (everything else is held):

| Axis | Control | Meaning |
|------|---------|---------|
| **Trim** | As job / As edited | Job’s baked Use window vs source marks on the card |
| **Seed** | Same / New (default **New**) | Hold this job’s noise seed vs draw a new one |
| **Priority** | Now / Later | Front vs normal queue |

Factory queue / Submit / Re-run default to a **new random seed** unless an explicit `parameters.seed` is set or `seed_mode=same` is requested. Seed-surfing (nearby seeds) is deferred.

Typical intents:

- **Same trim + new seed** — resample
- **Edited trim + same seed** — same draw, different Use window
- **Same + same** — exact retry (debug / confirm)
- **Edited + new** — both changed (still Re-run; media/workflow swaps are Recompose)

Family, bindings paths, identity, and prompt stay with the seed job.

## How to create Derived jobs

1. **Workbench** — Work Products → check **Derive** → Now/Later (`POST /api/shape-factory/derive` via disposition hook `derive`).
2. **Hourly** — appetite seeds / `plan_hourly_derive` / `predicted_derive` (facet-aware rewire).
3. **CLI** — `plan-derive` (and related hourly plan steps).

## Facet (v1)

Derive facet resolution: request body → appetite facet for this output → `both`.

Workbench v1 has **no facet picker**. If rewire returns `extend`, the queued job is stamped as extend (honest badge). Pass `facet` on the API when you need prompt-only or source-only rewire.

## Appetite note

Appetite `fast_track` still fires an **immediate Extend** (chain), not Workbench Derive. Appetite `more` feeds hourly derive share; Workbench Derive is the explicit “new combo from this seed” control.
