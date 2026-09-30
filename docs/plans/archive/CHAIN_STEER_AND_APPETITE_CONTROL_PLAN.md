> **Archived draft** — see [MAP.md](MAP.md) and live docs under [`docs/plans/`](../). Do not treat this file as current authority.

# Chain steer & appetite control

**Program:** A2 Experimentation (steer marks) + A3 consumption via hourly.
Hub: [`PLANNING_OVERVIEW.md`](./PLANNING_OVERVIEW.md). Owns **marks**; hourly
*consumption* of marks is under
[`HOURLY_UTILITY_PLAN.md`](./HOURLY_UTILITY_PLAN.md) /
[`HOURLY_DRAIN_POLICY_PLAN.md`](./HOURLY_DRAIN_POLICY_PLAN.md) — no second scheduler.

**Status:** Design locked for implementation sequencing (2026-09-29).  
**Parents:** [`HOURLY_GUIDE_PIPELINE_PLAN.md`](./HOURLY_GUIDE_PIPELINE_PLAN.md) (Phase 3 hard-drop landed),
[`RATINGS_V1_PLAN.md`](./RATINGS_V1_PLAN.md), [`HEURISTIC_ENGINE_NORTH_STAR.md`](./HEURISTIC_ENGINE_NORTH_STAR.md),
[`APPETITE_SIMILARITY_BIAS_PLAN.md`](./APPETITE_SIMILARITY_BIAS_PLAN.md).  
**Custody / broken refs:** [`LINEAGE_REMEDIATION_PLAN.md`](./LINEAGE_REMEDIATION_PLAN.md),
[`APPETITE_REMOVE_LIFECYCLE.md`](./APPETITE_REMOVE_LIFECYCLE.md),
[`ASSET_LIFECYCLE_PLAN.md`](./ASSET_LIFECYCLE_PLAN.md).

---

## Intent

Steer **all** production stages (seed lottery **and** chain drains), not only still bins:

- Soft signals cool or boost **this workproduct** without rewriting history.
- Hard signals remove a unit from live production (and eventually from the live tree).
- Age, habit, and (later) similarity keep large backlogs (~790 i2v→GEX) from being
  “oldest forever” or “never revisit past interest.”

Prefer **shared stores** (appetite index, steer bins, disposition, registry) over
chain-only shadow state.

---

## Factor model

```text
weight ∝ route
       × moment_fresh(A)          # current Less / Later / More / …
       × habit(A)                 # high-water + repetition
       × (1 + α · sim_to_interesting_set)   # later
       × age_survival(A, habit, sim)
```

| Factor | Role | Persistence |
|--------|------|-------------|
| **Moment** | What do I feel about this unit *now*? | Decays / transient |
| **Habit** | What have I wanted over time? | Durable high-water + event trail |
| **Routing** | Stage / bin / family eligibility | Config + hard gates |
| **Similarity** | Neighbors of interesting set | Derived (tags → vision → embeds) |

Marks are **workproduct-scoped** unless a later **rollup analyzer** says otherwise.
No automatic inheritance to parent or descendants.

---

## Mark semantics

### Appetite

| Mark | Scope | Persistence | Chain / lottery |
|------|--------|-------------|-----------------|
| `more` / `fast_track` | This asset | Raises habit high-water | Boost |
| `less` | This asset only | **Transient.** Does not erase historically higher marks; cools moment weight | Soft demote |
| `remove` | This asset only | Terminal hide + Retire funnel | Hard drop from chain lists |
| (neutral / clear) | — | Restores lists; Retire may remain | — |

**Less does not cascade** to parent or children. If parent P has spawned **many**
Less-marked descendants, that is a **future rollup signal** about P (count/fraction,
time window, stage filter, high-water cancel) — analysis, not an inherited mark.
Design deferred.

### Video / bin steer

| Mark | Seed-still / soft bin | Workproduct video (chain cull) |
|------|----------------------|--------------------------------|
| Keep / Pin | Soft boost | Soft boost |
| Later | Soft cool (×0.25) | Soft cool |
| **Out** | Soft ×0 **or** (when wired) physical leave live tree | **Hard drop** from consumer chain lists; **physical move** to `og/_trash/<date>/` (see below) |

**Constraint:** filesystem move is **global**. Soft per-bin Out for seed stills can
remain bias-only until operator meaning is unified. Physical Out applies to
**workproduct media** (chain cull / clip steer on that file).

### Hard-drop (landed)

`_chain_parent_output_blocked`: drop parent from `list_i2v_needing_gex` /
`list_gex2_needing_facial` when the **direct output clip** has own appetite
`remove` or consumer-bin steer `out`. Not ancestors; not still-only remove on a
source image.

---

## Out → physical relocate

When a workproduct is **ruled Out** (operator intent: leave live production
storage):

1. Call existing `trash_output_media` → `og/_trash/<date>/` (+ companions).
2. Rekey disposition / discovery tips the same way Retire → Trash does.
3. Record relocate alias (`moved_history` / remediation map) so old paths resolve.
4. Clearing Out should **restore from trash** when bytes remain (mirror recover).

Reuse `og/_trash/` (not a sibling `og-trash/`) unless Out and Retire later need
separate bags. Soft cool-down stays on **Less / Later**; Out is terminal for the
live corpus.

See [`LINEAGE_REMEDIATION_PLAN.md`](./LINEAGE_REMEDIATION_PLAN.md) (relocate + rot).

---

## Age × appetite cull

**Problem:** Large chain backlogs accumulate; pure FIFO / “no age cull” on i2v→GEX
keeps uninteresting old parents forever; pure age-drop kills past interest.

**Policy (target):**

- Survival / revisit weight rises with **habit high-water** and (later) similarity
  to the interesting set.
- Low habit + high age → drop or deep demote from active backlog (may still
  appear on a rare revisit pass).
- Facial already has a lookback window (14d); i2v→GEX should gain an
  **age × appetite** gate, not “no cull.”

**Revisit:** prefer **rare resurfacing** of historically interesting aged items
over “off forever until manual mark.” Exact cadence TBD when implementing.

---

## Similarity (fourth factor)

| Phase | Signal | When |
|-------|--------|------|
| 1 | Manual + automatic **tags** (Jaccard / shared attrs) | After tagging corpus is usable |
| 2 | Vision labels | After tag quality trusted |
| 3 | Embeddings (ANN neighbors) | When index online |

Consumer API: `similarity(A,B)` or neighbor list — hourly does not care which
backend filled it. Do **not** block age×appetite on missing similarity; `α = 0`
until signal exists.

Desire can soft-boost neighbors; neglect cools neighbors **gently** (never hard-drop
a cluster from one Less). Detail: [`APPETITE_SIMILARITY_BIAS_PLAN.md`](./APPETITE_SIMILARITY_BIAS_PLAN.md).

---

## Descendant health (judgment, not custody)

| Event | Effect |
|-------|--------|
| Parent Out / remove / trash | Descendants → **in question** review queue (not auto-Out) |
| Child Keep + parent gone | **Orphan remediation** (tombstone / restore / accept orphan) |
| Many Less under parent | Future rollup signal only |

Custody details and remediation menu:
[`LINEAGE_REMEDIATION_PLAN.md`](./LINEAGE_REMEDIATION_PLAN.md).

---

## Already landed (do not redo)

| Piece | Where |
|-------|--------|
| Hard-drop remove / steer Out on chain lists | `shape_factory_hourly._chain_parent_output_blocked` |
| Cull UI on Chain backlog preview | Hourlies panel appetite + Keep/Later/Out |
| Soft bin bias Keep/Pin/Later/Out | hourly bins + video steer |
| Retire Trash → `og/_trash/` | `trash_output_media` in disposition |
| Remove review / purge gate | `shape_factory_remove_review` |

**Uncommitted / adjacent:** Recalculate + backlog delta + steer dock on Hourlies
(manual foreground pass) — check in when asked; orthogonal to cull math.

---

## Implementation slices

### Available now

| Slice | Scope | Depends on |
|-------|--------|------------|
| **S1 — Soft Later/Less on chain fill** | Apply Later soft weight + Less moment demote when ranking/picking chain parents (not only hard Out/remove) | Existing bins + appetite reads |
| **S2 — Age × appetite cull (i2v→GEX)** | Configurable lookback/survival curve; protect high-water; drop stale low-habit parents from active list | Appetite high-water read (may be “current mark” v0) |
| **S3 — Out → trash on workproduct Out** | On video steer Out for chain consumer clips, call `trash_output_media` + rekey; optional restore on clear | Existing trash helpers; careful path resolve |
| **S4 — Check in Recalculate UI** | Commit Hourlies Recalculate / delta / steer dock | Uncommitted UI only |

### Next (needs small design or schema)

| Slice | Scope | Blocker |
|-------|--------|---------|
| **S5 — Habit / appetite events** | Append-only high-water + moment events so Less is truly transient vs history | Event schema + readers |
| **S6 — Descendants-in-question queue** | After parent terminal, flag direct children for review | Lineage walk + disposition or dedicated queue UI |
| **S7 — Tag similarity α** | Soft bias from shared tags to interesting set | Tag coverage good enough on chain media |

### Later

| Slice | Scope |
|-------|--------|
| Less-descendant rollup analyzer | Aggregate signal onto parent |
| Vision / embedding similarity | P1 V3b+ |
| Unify seed-bin Out with physical Out | Only if operator meaning is identical |
| Rare revisit pass for aged high-habit | After S2 + S5 |

### Recommended order

1. **S4** if the Recalculate UI should land in git (no runtime risk).
2. **S2** — highest operational payoff on the 790 backlog.
3. **S1** — soft demote without deleting.
4. **S3** — physical Out (coordinate with remediation aliases).
5. **S5** then **S6** / **S7**.

---

## Non-goals (this plan)

- Auto-cascading Less/Out/remove along lineage.
- Embedding index as a prerequisite for cull.
- Replacing disposition Retire with steer Out (they share trash path; meanings differ).
- Silent purge of descendants when parent is deleted.
