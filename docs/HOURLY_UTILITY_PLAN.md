# Hourly as a ruleset utility

**Status:** Active plan (merged 2026-08-27). Formerly split across
`HOURLY_RULESET_UTILITY.md` and `HOURLY_CLIP_GUIDANCE_PLAN.md` — those files
redirect here.

**Product models (not duplicated):**
[`CLIP_SELECTION_MODEL.md`](./CLIP_SELECTION_MODEL.md) (★ / Use / retire),
[`DISPOSITION_BUCKET_MODEL.md`](./DISPOSITION_BUCKET_MODEL.md) (rate → dispose → Advance),
[`WORKFLOW_INTENT.md`](./WORKFLOW_INTENT.md) (station vocab / pipelines).

Ops crumbs also live in [`.data/WORKFLOW_FACTORY_NEXT.md`](../.data/WORKFLOW_FACTORY_NEXT.md).

---

## 1. Goal

Hourly should read as one small **rules engine**:

1. **Declared rules** — cadences, seed weights, lookbacks, chain priority, clip
   biases — live in durable data, not only code/`HOURLY_*` env.
2. **Clip-honest seeds** — when a video binds, the seed unit is a **clip Use
   window** (★ lottery + newer bias), not “whatever the file is.”
3. **Explainable picks** — every tick can answer *why this family / step /
   source / clip*.
4. **Operator surface** — Home shows next-N, active rules, backlogs, last-tick
   decision — not just interval / queue caps.
5. **Thin runtime** — `shape_factory_hourly.py` + shell timer read and execute
   the ruleset; env stays an override for experiments.

**Still out of scope here:** rewriting Comfy submit/drain; Bucket Advance
multi-route UI; ratings-weighted asset pick (`rating_effective` — after clip
honesty); inventing a second clip-preference system (consume
[`CLIP_SELECTION_MODEL.md`](./CLIP_SELECTION_MODEL.md)).

---

## 2. What a fill tick does today

```text
queue policy (Comfy waiting / factory pending / caps)
        │
        ▼
chain? ──► GEX2→FACIAL (every N cursors, lookback days, seed-over-chain share)
        │
        ├──► i2v→FB9_GEX (every M cursors; IMAGE_TO_GEX producers)
        │
        └──► seed family (weighted table) → plan-step
                 replay | derive | predicted | pool_product …
                 + pick_seed_clip / Use resolve when video binds
```

| Layer | Where it lives today | Visibility |
|-------|----------------------|------------|
| Interval / queue caps / submit mode | `hourly-schedule.json` + Home | **Good** |
| Seed weights, drain cadences, lookbacks | code + `HOURLY_*` | Log / `simulate-picks` |
| Clip ★ + newer (per parent) | clips registry + `pick_seed_clip` | Job `source_clip_id` when set |
| Pool-level ★ lottery (clip-first) | **partial** (recipe ★ boost) | simulate / trail |
| Last tick / next preview | `hourly-state.json`, `hourly.log`, Factory Map strip | Raw / partial |

Dry-run:

```bash
python3 workspace/scripts/shape_factory_hourly.py simulate-picks --count 32
python3 workspace/scripts/shape_factory_hourly.py schedule-status
```

---

## 3. How the pieces fit

```text
                    ┌─ policy file (cadences, weights, clip knobs)
hourly ruleset ─────┤
                    ├─ tick executor (drain → seed → bind)
                    ├─ clip bind (★ + newer → Use window)
                    └─ decision ledger + Home UI
```

| Concern | Owns |
|---------|------|
| ★ / usable trim / retire / used | Clip selection model |
| What *work* to do after rate/dispose | Disposition / buckets |
| **This plan** | Hourly *consumes* both: policy shape, bind sequence, observability |
| Station `chain_role` / pipelines | WORKFLOW_INTENT — named drains later (U5) |

Disposition’s old “Phase 2: hourly prefer default clip” is the **clip bind**
slice below — not a second preference system.

---

## 4. Phased movement

### U0 — Clip foundation *(mostly shipped)*

Was H0–H3. Do not redo.

| Piece | Status |
|-------|--------|
| Soft-delete / used-unused / library filters | Shipped |
| Multi-★ prefs (`asset_clip_stars`); legacy `default_clip_id` alias | Shipped |
| `pick_seed_clip` (★ recency → usable trim → full) | Shipped |
| Hourly/pool bind writes `source_clip_id` + window before submit | Landing |
| Job trail / simulate shows clip id + pick source | Landing |

**Exit (acceptance):** parent with N ★ → hourly seeds one of those N (newer-weighted);
0 ★ → trim or full, never unstarred bookmark; soft-deleted ★ out of lottery.

---

### U1 — Policy file *(lift knobs)*

Add `.data/shape_factory/hourly-policy.yaml` (or extend `hourly-schedule.json`):

- Drain: `facial_drain_every`, `i2v_gex_drain_every`, `seed_over_chain_share`,
  `facial_lookback_days`
- Seeds: `seed_families: [{ family, weight }]`, `image_to_gex_families: [...]`
- Optional boosts: fresh-still / kneel / 2025 / archive / derive shares
- Clip knobs (even before U2 finishes): recency half-life / rank curve,
  `require_starred_in_pool` flag for video-seed families

Reader order: **file → env override → code fallback**. Allowlist in git for
history; runtime-only overrides stay uncommitted.

**Exit:** change GEX2 weight or facial cadence without editing Python.

---

### U2 — Finish clip guidance *(pool + hygiene)*

Was H4–H5. Knobs should read from U1 once that lands.

**U2a — Clip-first lottery (across pool):** for video-seed families, candidate
units = starred clips on eligible parents (not bare paths). Sample with ★
required + recency bias; bind parent + Use from the clip. Fallback when no ★
in pool: today’s asset pick + U0 resolve; log `no_starred_in_pool`.

**U2b — Usable trim clamp:** parent-level usable range; ★ marks clamped into
trim; no-★ automation unit = trim span. UI: one usable in/out editor.

**Exit:** simulate skews toward recently starred windows; glitch head/tail
omittable without fake ★.

---

### U3 — Decision ledger

Per fill tick, append compact JSONL (or stamp `hourly-state.json`):

- `cursor`, `phase` (`facial` | `i2v_gex` | `seed`), `family`, `step` / `pick_mode`
- `reason` (cadence hit, backlog empty → fallback, seed-over-chain, …)
- Clip bind: `source_clip_id`, pick `source` (`starred` | `usable_trim` | `full`), weight note
- `bindings_preview`, `backlog_sizes`, `ruleset_fingerprint`

**Exit:** “what did the last 20 hourlies do?” is a one-screen answer.

---

### U4 — Operator UI

Home **Hourly** panel beyond schedule caps:

- Effective rules (weights, cadences, clip knobs) + edit/save (U1)
- Next-N preview with rule + clip labels
- Backlog counts (facial / i2v→gex) + cull/archive notes
- Last-tick decision from U3

**Exit:** tune hourlies without reading `hourly.log`.

---

### U5 — Align drains with station vocabulary

Once pipelines / `chain_role` stabilize, express chain drains as **named
informal pipelines** in the policy file (e.g. `gex2_to_facial`, `i2v_to_gex`)
instead of hardcoded family strings only — descriptive, not a lockout engine.

---

## 5. Suggested milestones

| Milestone | Delivers | Notes |
|-----------|----------|-------|
| **M0** | U0 complete | Clip foundation on main |
| **M1** | U1 | Policy file; operators edit weights/cadences |
| **M2** | U2a (+ U2b as capacity) | Hourlies *guided by* newer ★ across pool |
| **M3** | U3 | Explainable ticks |
| **M4** | U4 | Home ruleset surface |
| **M5** | U5 | Named pipeline drains |

U2b can start after U0 even if U2a slips. Ratings-weighted asset pick stays
**after** M2 so scores attach to honest seeds. Bucket 2B can call the same
picker whenever it spawns work.

---

## 6. Recency (clip) — lock for implementers

| Context | Rule |
|---------|------|
| Among ★ on one parent (U0) | Higher weight for later `updated_at`, then `created_at` |
| Across pool units (U2a) | Same on the clip; optional mild parent `mtime` boost |
| Unstarred | Never in lottery (manual queue only) |
| Retired | Never in lottery |

Do **not** use “newer” to pull unstarred bookmarks into hourly. Rough v1:
`w_i = half_life_decay(age_i)` or `1/(rank_i+1)`; record weight in trail / ledger.

---

## 7. Near-term operator handle (until U3/U4)

1. `simulate-picks --count 32` — family mix + backlog + clip when bound
2. `hourly-state.json` — last family / step / cursor
3. `hourly.log` — `phase=facial|seed|…`
4. Home schedule — interval / queue min-max / pending max only

Debugging “why not GEX2?”: seed weights → whether tick was facial / i2v→gex
cadence → whether seed path even reached GEX2.

---

## 8. Open decisions

- Kneel→GEX2 **chain** stays off (i2v drains to `FB9_GEX`) while GEX2 is a
  **seed** peer of GEX — or restore chain as an explicit policy rule (U1).
- Facial backlog **editor** UI vs one-shot cull archives (`jobs/_archive/*_backlog_cull_*`).
- Seed weights: integers summing to 100 vs free weights.
- Policy file shape: sibling `hourly-policy.yaml` vs grow `hourly-schedule.json`.

---

## 9. Acceptance (target)

- [ ] Cadences / seed weights / lookbacks editable without Python edits (U1).
- [ ] Parent with N ★ → hourly seeds one of those N (newer-weighted); 0 ★ → trim/full only.
- [ ] Soft-deleted ★ out of lottery; restore can re-★.
- [ ] Video-seed families skew to recently starred clips across pool when ★ exist (U2a).
- [ ] Last-N ticks explainable from ledger / one UI screen (U3–U4).
- [ ] `simulate-picks` shows family, phase reason, `source_clip_id` + pick source.

---

## Related

- [`PLANNING_OVERVIEW.md`](./PLANNING_OVERVIEW.md)
- [`.data/WORKFLOW_FACTORY_NEXT.md`](../.data/WORKFLOW_FACTORY_NEXT.md)
- [`CLIP_SELECTION_MODEL.md`](./CLIP_SELECTION_MODEL.md)
- [`DISPOSITION_BUCKET_MODEL.md`](./DISPOSITION_BUCKET_MODEL.md)
- [`WORKFLOW_INTENT.md`](./WORKFLOW_INTENT.md)
- Stub redirects: [`HOURLY_CLIP_GUIDANCE_PLAN.md`](./HOURLY_CLIP_GUIDANCE_PLAN.md),
  [`HOURLY_RULESET_UTILITY.md`](./HOURLY_RULESET_UTILITY.md)
