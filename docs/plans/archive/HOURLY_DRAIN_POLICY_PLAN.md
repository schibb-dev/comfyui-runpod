> **Archived draft** — see [MAP.md](MAP.md) and live docs under [`docs/plans/`](../). Do not treat this file as current authority.

# Hourly drain policy — configuration-first design

**Program:** A3 Refinement — **U1/U5 design vehicle** under
[`HOURLY_UTILITY_PLAN.md`](./HOURLY_UTILITY_PLAN.md). Hub:
[`PLANNING_OVERVIEW.md`](./PLANNING_OVERVIEW.md).

**Status:** Design (2026-09-29). Supersedes ad-hoc `HOURLY_*` special cases as the
*model*; migration keeps env as override until readers land. **Primary next
slice** for hourly manifestation (M1).  
**Parents:** [`HOURLY_UTILITY_PLAN.md`](./HOURLY_UTILITY_PLAN.md) (U1 / U5),
[`HOURLY_GUIDE_PIPELINE_PLAN.md`](./HOURLY_GUIDE_PIPELINE_PLAN.md),
[`WORKFLOW_INTENT.md`](./WORKFLOW_INTENT.md),
[`CHAIN_STEER_AND_APPETITE_CONTROL_PLAN.md`](./CHAIN_STEER_AND_APPETITE_CONTROL_PLAN.md)
(steer *marks*; this plan owns how hourlies *consume* them — no second scheduler).  
**Custody:** [`LINEAGE_REMEDIATION_PLAN.md`](./LINEAGE_REMEDIATION_PLAN.md).

---

## Problem

Today’s hourly fill is **arbitrary in form** even when the *intent* is sensible:

| Today | Smell |
|-------|--------|
| Two chain drains hardcoded in `predict_hourly_gex2` | Priority is Python `if` order |
| Cadence / lookback / seed-over via `HOURLY_*` env | Not reviewable in git as product policy |
| Only two backlog cards | UI mirrors code, not a catalog |
| Kneel→GEX2 was off in hourly (helper only) | **Pre-0.4 legacy:** re-enabled hot-gated drain |
| GEX2 fed as **seed peer**, not chain | Same family, two undocumented roles |
| `.data/pipelines/*.pipeline.yaml` | Parallel description; hourly ignores them |
| U1 sketch still names `facial_*` / `i2v_gex_*` knobs | Lifts special cases into YAML without generalizing |

We want **one declarative ruleset**: what drains exist, in what order, with what
eligibility — editable without editing Python.

---

## Design principles

1. **Drains are data.** A drain is a named rule: producers → consumer, plus
   cadence / window / filters. The tick executor is generic.
2. **Priority is an ordered list**, not nested conditionals.
3. **Seed is a drain kind**, not a hidden else-branch — last resort with its own
   weight table.
4. **Pipelines are the plant plan; drains are the hourly schedule over those
   edges.** A drain *may* cite `pipeline_id`; it must not duplicate bind logic.
5. **Backlog UI = enabled drains with `backlog: true`.** No special-case cards.
6. **Steer / appetite / Out** attach to `consumer_family` (and eventually drain
   id), shared with chain cull — not per-hardcoded-list filters.
7. **Reader order:** policy file → env override (escape hatch) → code defaults
   only for bootstrap / tests.
8. **Descriptive first.** Invalid drain (unknown family) logs and skips; does
   not lock out the plant (same spirit as pipeline catalog).

---

## Core concepts

```text
hourly-policy.yaml
  drains[]     ordered priority for one fill tick
  seeds        weight table when a seed drain fires
  globals      cursor, promo hooks, clip knobs (later)

tick:
  for drain in drains (enabled, in order):
    if not cadence_due(drain, cursor): continue
    if drain.kind == chain:
      parent = pick_from_backlog(drain)
      if parent: fill chain job; stop
    if drain.kind == seed:
      family = weighted_pick(seeds); plan_hourly_step; stop
  # if nothing filled: idle / maintain only
```

### Drain (chain)

| Field | Meaning |
|-------|---------|
| `id` | Stable id (`gex2_to_facial`, `i2v_to_gex`, …) |
| `kind` | `chain` |
| `enabled` | bool |
| `label` | UI string |
| `producers` | family slug list (or `producers_from: image_origins`) |
| `consumer` | family that binds `source_video` (or declared slot) |
| `pipeline_id` | optional cite of `.data/pipelines/…` |
| `cadence.every_n` | every N `sample_cursor` (1 = every tick) |
| `cadence.skip_share` | optional Bernoulli skip (today’s seed-over-chain, **per drain**) |
| `eligibility.lookback_days` | `null` = no age cull |
| `eligibility` | also: require complete, not already bound, steer/appetite gates |
| `pick` | `newest` \| `rotate_producers` \| … |
| `backlog` | show on Hourlies chain panel |
| `prompt` | optional catalog prefer / picker hint |

### Drain (seed)

| Field | Meaning |
|-------|---------|
| `id` | e.g. `seed_lottery` |
| `kind` | `seed` |
| `enabled` | bool |
| `cadence.every_n` | usually 1 (runs when higher drains miss) |
| `families` | `[{ family, weight }]` — or top-level `seeds:` shared |

Putting seed **last** in `drains[]` replaces today’s else-branch.

### Backlog (derived)

For each `kind: chain` drain with `backlog: true`:

```text
complete jobs in producers
  whose chain output is not yet a consumer source_video
  ∧ within lookback (if set)
  ∧ not blocked by appetite remove / consumer steer Out
```

Same list feeds **tick pick** and **Hourlies UI**. No parallel `list_*` forever;
one `list_drain_backlog(drain)` implementation parameterized by the drain record.

---

## Sketch: encode *today* as policy (no behavior change)

```yaml
# .data/shape_factory/hourly-policy.yaml  (allowlisted)
schema_version: comfyui-runpod.hourly_policy.v0

drains:
  - id: gex2_to_facial
    kind: chain
    enabled: true
    label: "GEX2|Zoom → FACIAL"
    producers: [FB9_GEX2, FB8VA5-ZOOMOUT]
    consumer: FB9_GEX_FACIAL
    pipeline_id: fb9-gex2-to-facial  # Zoom may cite fb8va5-zoomout-to-fb9-gex-facial
    cadence: { every_n: 6, skip_share: 0.50 }
    eligibility: { lookback_days: 14 }
    pick: newest
    backlog: true

  - id: chain_self_extend
    kind: chain
    enabled: true
    label: "GEX|GEX2 self-extend (named variant)"
    producers: [FB9_GEX, FB9_GEX2]
    consumer: same_as_producer
    cadence: { every_n: 4, skip_share: 0.50 }
    eligibility:
      lookback_days: 30
      prompt_variant_allow: [catalog-default, faceblast-extend]
      exclude_replay_hex: true
    pick: rotate_producers
    backlog: true

  - id: kneel_to_gex2
    kind: chain
    enabled: true
    label: "Kneel* → GEX2 (hot, faceblast-extend)"
    producers: [X-KNEEL-FB9-bare, X-KNEEL-FB9]
    consumer: FB9_GEX2
    cadence: { every_n: 12 }
    eligibility:
      lookback_days: 30
      parent_appetite: [more, fast_track]
    prompt: { prefer: catalog-faceblast-extend }
    backlog: true

  - id: i2v_to_gex
    kind: chain
    enabled: true
    label: "i2v → GEX|GEX2"
    producers:
      - X-KNEEL-FB9-bare
      - X-KNEEL-FB9
      - BounceDanceA
      - FB9-FaceBlast
      - FB8VB2
      - FB8VA5-ZOOMOUT
      - Breast-shake-FB8VA5
    consumer: FB9_GEX  # multi: GEX|GEX2; Kneel must not take GEX2 slot (see kneel_to_gex2)
    # pipeline_id: optional multi — or omit until one drain ↔ one pipeline
    cadence: { every_n: 3 }
    eligibility: { lookback_days: 30 }
    pick: rotate_producers
    backlog: true

  - id: seed_lottery
    kind: seed
    enabled: true
    cadence: { every_n: 1 }
    backlog: false

seeds:
  - { family: X-KNEEL-FB9-bare, weight: 30 }
  - { family: X-KNEEL-FB9, weight: 5 }
  - { family: FB9-FaceBlast, weight: 16 }
  - { family: BounceDanceA, weight: 16 }
  - { family: FB9_GEX, weight: 5 }
  - { family: FB9_GEX2, weight: 5 }   # peer seed — not a chain (yet)
  - { family: FB8VB2, weight: 8 }
  - { family: FB8VA5-ZOOMOUT, weight: 8 }
  - { family: Breast-shake-FB8VA5, weight: 7 }
```

**Legacy Pre-0.4 (2026-09-29, in `shape_factory_hourly.py` + shell):** appetite×variant
analysis (Aug+ named catalogs; `(replay-hex)` excluded) added three chain drains
before i2v→extend and re-enabled Kneel→GEX2:

| Priority | Drain | Legacy env |
|----------|-------|------------|
| 1 | GEX2\|Zoom → FACIAL | `HOURLY_FACIAL_PRODUCERS`, existing facial cadence/lookback |
| 2 | GEX\|GEX2 self-extend (named variant only) | `HOURLY_SELF_EXTEND_DRAIN_EVERY` (4), `HOURLY_SELF_EXTEND_LOOKBACK_DAYS` (30) |
| 3 | Kneel* → GEX2 (hot parent; `catalog-faceblast-extend`) | `HOURLY_KNEEL_GEX2_DRAIN_EVERY` (12), hot gate |
| 4 | i2v → GEX\|GEX2 | Pre-0.2 (Kneel skips GEX2 consumer here) |
| last | seed lottery | unchanged |

Evidence snapshot (operator analysis, not auto-tuned weights):

- **Zoom → FACIAL** `catalog-default`: ~100% hot outputs (n≈41 Aug+).
- **faceblast-extend** on extend hops: ~87% hot (n≈217 Aug+); prefer on Kneel→GEX2 fill.
- **Self-extend replay-hex**: cold outputs — excluded via `_is_named_extend_variant`.
- **Kneel → GEX2 replay-hex**: ~33% hot — drain uses hot parents only + rare cadence.

**Still explicit non-drains:** GEX→GEX2 middle hop (adhoc volume negligible).

When D1 lands, encode the Pre-0.4 table as `drains[]` entries (below) instead of
Python order + env.

---

## What other drains become (when you want them)

Add a YAML entry + ensure consumer shape/pools exist. Examples:

| `id` | producers → consumer | Notes |
|------|----------------------|--------|
| `gex_to_gex2` | `FB9_GEX` → `FB9_GEX2` | Middle hop; may lower GEX2 seed weight |
| `kneel_to_gex2` | Kneel* → `FB9_GEX2` | Re-enable helper as config |
| `facial_to_X` | FACIAL → next station | When enrolled |
| Split i2v | one drain per origin family | Same consumer; separate cadence/UI |

Priority among them is **list order** (+ per-drain `every_n` / `skip_share`).

---

## Relation to existing surfaces

| Surface | Role after this |
|---------|-----------------|
| `hourly-schedule.json` | Tick interval / queue caps / enable — **not** drain graph |
| `hourly-policy.yaml` | Drain graph + seeds + (later) clip knobs |
| Steer bins | Soft bias for seed stills / video units; Out hard-drop on **consumer** |
| Appetite | Workproduct gates on backlog eligibility |
| `*.pipeline.yaml` | How a multi-step job binds; drain cites `pipeline_id` |
| `chain_role` on shapes | Ranking prior / discovery — **not** a substitute for drain list |
| Env `HOURLY_*` | Override layer during migration; deprecate once UI edits file |

---

## Tick executor (target)

Replace `predict_hourly_gex2` / fill chain branches with:

1. Load policy (cached; fingerprint in ledger).
2. Walk `drains` in order.
3. Generic chain fill: resolve parent from backlog, build job for `consumer`
   (prompt picker hooks from drain.prompt).
4. Seed drain: `select_seed_family` from policy seeds.
5. Append ledger row: `drain_id`, reason (`due` / `skip_share` / `empty_backlog` /
   `filled` / `disabled`).

Simulate-picks and Hourlies “next” use the **same** walker.

---

## Phased delivery (proper, not “lift knobs”)

| Phase | Deliverable | Exit |
|-------|-------------|------|
| **D0** | Schema + load `hourly-policy.yaml`; emit effective policy in status/simulate (still execute old code paths if fingerprint matches baked defaults) | Operators see the model |
| **D1** | Encode today’s behavior as committed policy; executor reads cadence/lookback/producers from file | Change `every_n` / lookback / producer list without Python |
| **D2** | Generic `list_drain_backlog` + Hourlies cards from `backlog: true` drains | Third drain appears in UI when added to YAML |
| **D3** | Generic chain fill from drain record; delete facial/i2v special cases | `predict_*` is a thin walker |
| **D4** | Home editor for policy (U4); env overrides optional | Edit without SSH |
| **D5** | Optional: enable `gex_to_gex2` etc. as explicit policy experiments | New edges are data |

**Do not** ship D4 before D1–D2 — UI on a still-special-cased engine re-encodes the mess.

Age×appetite cull / Out→trash / similarity attach as **eligibility plugins** on
the drain record later (`eligibility.age_appetite`, …), not new hardcoded lists.
See [`CHAIN_STEER_AND_APPETITE_CONTROL_PLAN.md`](./CHAIN_STEER_AND_APPETITE_CONTROL_PLAN.md).

---

## Non-goals

- Auto-discovering drains from all `*.pipeline.yaml` (too loose; opt-in cite).
- Replacing pending→Comfy drain (`pending_drain`) — different concern.
- Making `chain_role` alone schedule work.
- One mega-JSON with bins + schedule + policy (keep schedule vs policy split).

---

## Open choices (lock in D0/D1)

1. **One i2v→GEX drain vs per-producer drains** — start with one (today); split
   later if cadences diverge.
2. **`skip_share` on facial only vs global** — per-drain (facial keeps 0.50).
3. **Policy path** — prefer `.data/shape_factory/hourly-policy.yaml` (allowlisted)
   over stuffing `hourly-schedule.json`.
4. **GEX2 dual role** — keep as seed weight until an explicit `gex_to_gex2` or
   `kneel_to_gex2` drain is enabled; document in policy comments.

---

## Implementation slices (when building)

| Slice | Now? |
|-------|------|
| Write this doc + cross-links (done) | yes |
| D0 loader + status fingerprint | yes — small, no behavior change |
| D1 commit default policy = today’s env/code | yes — highest leverage |
| D2 backlog from policy | after D1 |
| D3 generic fill | after D2 |
| Age×appetite as eligibility on `i2v_to_gex` | can follow D1 even before D3 if still using old list fn |
| New drain GEX→GEX2 | only after D2/D3 |

Recommended first build: **D0 + D1**, then age×appetite as an eligibility flag on
the `i2v_to_gex` policy entry — so cull math isn’t another hardcoded fork.
