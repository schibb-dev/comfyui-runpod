# Derive vs Vary vs Extend

**Last updated:** 2026-08-06

Short operator map for Workbench advance actions and hourly “Derived” badges.

## Quick table

| Control / badge | What it does | Same bindings? |
|-----------------|--------------|----------------|
| **Vary** (`advance.vary`) | Front-queue **exact replay** (`hook: replay_front`) | Yes |
| **Extend** (`advance.extend`) | Chain this **output** into the video slot | Source changes to this output |
| **Derive** (`advance.derive`) | **Rewire** prompt and/or source via `_derive_rewire` → `pick_mode: derive` (or `extend` if facet=`both` picks chain) | No — new combo |
| **Derived** badge | Jobs stamped `pick_mode` / construction as derive (Workbench Derive, hourly appetite, CLI) | N/A |

**Vary ≠ Derive.** Vary keeps the seed’s bindings. Derive builds a distinct combo from the seed.

## How to create Derived jobs

1. **Workbench** — Work Products → check **Derive** → Now/Later (`POST /api/shape-factory/derive` via disposition hook `derive`).
2. **Hourly** — appetite seeds / `plan_hourly_derive` / `predicted_derive` (facet-aware rewire).
3. **CLI** — `plan-derive` (and related hourly plan steps).

## Facet (v1)

Derive facet resolution: request body → appetite facet for this output → `both`.

Workbench v1 has **no facet picker**. If rewire returns `extend`, the queued job is stamped as extend (honest badge). Pass `facet` on the API when you need prompt-only or source-only rewire.

## Appetite note

Appetite `fast_track` still fires an **immediate Extend** (chain), not Workbench Derive. Appetite `more` feeds hourly derive share; Workbench Derive is the explicit “new combo from this seed” control.
