# Hourly clip guidance — sequenced plan

**Status:** Plan (2026-08-26). **H1–H3 landing** (multi-★ + `pick_seed_clip` + resolve/hourly bind); **H4 partial** (recipe ★ boost). Target: hourlies guided by **clips**, preferring **starred** then **newer**.

**Product model:** [`CLIP_SELECTION_MODEL.md`](./CLIP_SELECTION_MODEL.md)  
**Review / work intent:** [`DISPOSITION_BUCKET_MODEL.md`](./DISPOSITION_BUCKET_MODEL.md)  
**Bucket fan-out (orthogonal):** [`BUCKET_MODEL_PHASE2_PLAN.md`](./BUCKET_MODEL_PHASE2_PLAN.md)  
**Hourly ops knobs (orthogonal):** `.data/WORKFLOW_FACTORY_NEXT.md` (policy-as-config, ratings pick)

---

## 1. Goal

When factory hourly (and pool fills that bind `source_video`) create a job:

1. The **seed unit** is a **clip** whenever preference or hygiene says so — not “whatever the file is.”
2. **Starred clips** are the curated lottery pool (N ★ → N units).
3. Among eligible units, **newer clips win more often** (recency bias on `created_at` / `updated_at`).
4. Unstarred bookmarks stay human-only; they do not enter the hourly lottery.
5. Soft-deleted clips never enter; used/unused remains a cleanup axis only.

**Non-goals for this plan:** multi-route Advance UI, pool pages, hourly policy YAML, ratings-weighted asset pick (`rating_effective`). Those stay on their own tracks; this plan only defines **which Use window** gets bound once a parent (or clip unit) is chosen.

---

## 2. How the three docs overlap

```text
CLIP_SELECTION_MODEL     Disposition “Phase 2 prefer default”
        │                         │
        │   same seam:            │
        └──────────► hourly/pool bind Use ◄──────────┘
                              │
                    HOURLY_CLIP_GUIDANCE (this doc)
```

| Doc | Owns |
|-----|------|
| Clip selection model | ★ / usable trim / resolve rules / retire / used |
| Disposition | Rate → dispose → Advance → work items (what *work* to do) |
| **This plan** | Sequence to make hourly **consume** the clip model with ★ + newer bias |
| Bucket Phase 2B+ | Multi-route Advance / pool pages — **after or beside** bind; does not define ★ |

Disposition’s old line *“Phase 2: hourly prefer default clip”* is **implemented here** as clip-model consumer work — not a second preference system.

---

## 3. Already shipped (do not redo)

| Piece | Where |
|-------|--------|
| Asset / Clip / Use resolve | `seed_job_use_window_from_clips` / `resolve_job_use_window` |
| Singular `default_clip_id` | `asset_clip_prefs` |
| Manual Queue-from-clip | Discovery / clips library |
| Soft-delete + restore | clips schema `deleted_at`; library Retired |
| Used / unused annotate + filter | jobs scan; library Usage filter; hard-delete guard |
| Locked selection rules | [`CLIP_SELECTION_MODEL.md`](./CLIP_SELECTION_MODEL.md) |

**Gap today:** hourly often picks a `source_video` path and resolve still falls through to sibling / full-file more than operators intend; preference is singular default, not multi-★; no explicit **newer** weight.

---

## 4. Sequence (implement in order)

### H0 — Commit longevity slice *(prerequisite hygiene)*

Ship the uncommitted soft-delete + used/unused + docs as one themed commit so cleanup and library filters are on `main` before prefs change.

**Exit:** retire/restore and used badges live on main.

---

### H1 — Multi-star preference (replace singular default)

**Schema** (clips registry / `asset_clip_prefs` or sibling table):

- Per parent: set of `starred_clip_id`s (0–N).
- Migrate: existing `default_clip_id` → one starred entry; keep reading `default_clip_id` as legacy alias until UI/callers move.
- Soft-delete clears that id from the starred set (already clears default).

**API / CLI**

- `star` / `unstar` / `list_starred` (and library `starred_only`).
- Resolve: if any ★ → sample among ★; else usable trim; else full (per clip model).

**UI (minimal)**

- Clips library + rail: ★ toggle (replaces or sits beside “Set default”).
- Default list filter: starred + recent (optional later).

**Exit:** N ★ on a parent are durable; resolve uses the set; default is deprecated alias.

---

### H2 — Seed picker helper (★ + newer)

Add a single factory helper used by job create and hourly:

```text
pick_seed_clip(parent_content_id, *, rng, jobs_root?) ->
  { clip_id?, mark_in, mark_out, source: starred|usable_trim|full|… }
```

**Selection among N starred (same parent):**

1. Exclude `deleted_at`.
2. Weight by **recency** — prefer newer `updated_at` then `created_at` (e.g. exponential or rank-linear; exact curve tunable).
3. Uniform among top-K is OK for v1 if weights feel heavy; document the curve in code + this plan.

**No ★:** return usable trim if set, else full asset (unless future `clip-required`).

**Exit:** unit tests cover N=0/1/many and recency ordering; job create path can call the helper instead of only “default or full.”

---

### H3 — Hourly / pool bind (Disposition Phase 2 seam)

When hourly (or pool fill) has chosen a `source_video`:

1. Resolve parent `content_id`.
2. Call `pick_seed_clip` → write `source_clip_id` + `vhs_window` on the job **before** submit/deposit.
3. Log pick reason (`starred` + weights / `usable_trim` / `full`) on the job trail for debug.

Do **not** invent a parallel “hourly default” flag.

**Exit:** new hourly jobs that bind video seeds show `source_clip_id` whenever the parent has ★ (or default during migrate); full-file seeds only when the model says so. Dry-run / `simulate_hourly_picks` shows clip id + source.

---

### H4 — Prefer newer clips across the pool *(raises guidance)*

H3 still picks **asset first**, then clip. Target guidance wants **newer clips** to matter globally.

**Lift the lottery unit** for video-seed families:

- Candidate units = starred clips on eligible pool/parent videos (not bare paths).
- Sample units with **★ required** + **recency bias** (clip age; optional mild parent mtime tie-break).
- Bind that `clip_id`’s parent as `source_video` and Use from the clip.

Fallback when a family has **no** starred clips in pool: keep today’s asset pick + H3 resolve (trim/full), and optionally log `no_starred_in_pool`.

**Exit:** simulate table shows clip labels/ids; repeating hourlies skew toward recently starred windows, not only newly added files.

---

### H5 — Usable trim (hygiene clamp)

Asset-level usable range (0–1):

- Stored on parent (not a ★).
- Lottery: ★ marks clamped into trim; no-★ unit = trim span.
- UI: one “usable in/out” editor on parent (Workbench / clips by-source).

Can start after H2 (clamp in picker) even if H4 slips.

**Exit:** glitch head/tail omitted for automation without starring junk spans.

---

### Parallel tracks (do not block H1–H4)

| Track | Relation |
|-------|----------|
| Bucket 2B–2E (Advance multi-route, pools UI) | Uses jobs/work items; bind Use via same helper when instances fire |
| Hourly policy-as-config | Cadences / family weights — orthogonal to clip pick |
| Ratings-weighted asset pick | **After** H3/H4 so scores attach to honest seeds |
| Owned prompt / ledger store | Unrelated WIP |

---

## 5. Recency policy (lock for implementers)

**Prefer newer clips** means:

| Context | Rule |
|---------|------|
| Among ★ on one parent (H2–H3) | Higher weight for later `updated_at`, then `created_at` |
| Across pool units (H4) | Same on the clip; optional small boost for newer parent `mtime` |
| Unstarred | Never in lottery (manual queue only) |
| Retired | Never in lottery |

Do **not** use “newer” to override unstarred → that would reintroduce bookmark proliferation into hourly.

Rough v1 weight (tunable): sort ★ by recency descending, weight `w_i = half_life_decay(age_i)` or `w_i = 1 / (rank_i + 1)`. Record chosen weight in job trail.

---

## 6. Suggested milestones

| Milestone | Delivers | Unblocks |
|-----------|----------|----------|
| **M0** | H0 commit | Clean base |
| **M1** | H1 + H2 | Correct Use on any job create | **in progress / landing** |
| **M2** | H3 | Hourlies stop silent full-file when default/★ exists | **landing via resolve** |
| **M3** | H4 | Hourlies *guided by* newer ★ clips across pool | **partial: recipe ★ boost** |
| **M4** | H5 | Hygiene without fake ★ |

Disposition review UX and Bucket 2B can proceed anytime after M1 if they call the same picker when spawning work.

---

## 7. Acceptance (target reached)

- [ ] Parent with N starred clips → hourly seeds are one of those N (weighted newer), never a random unstarred bookmark.
- [ ] Parent with 0 ★ → usable trim if set, else full; never an unstarred clip.
- [ ] Soft-deleted ★ removed from lottery; restore can re-★.
- [ ] `simulate_hourly_picks` / job meta shows `source_clip_id` + pick `source`.
- [ ] Newer starred clips receive measurably higher pick rate in a dry-run corpus.
- [ ] Docs: Disposition Phase 2 line points here; clip model “Phase 2 wires hourly” marked done at M2+.

---

## 8. Doc updates when implementing

- [`DISPOSITION_BUCKET_MODEL.md`](./DISPOSITION_BUCKET_MODEL.md) — Phase 2 → “see HOURLY_CLIP_GUIDANCE_PLAN (★ + newer).”
- [`CLIP_SELECTION_MODEL.md`](./CLIP_SELECTION_MODEL.md) — link this plan under automation selection.
- [`PLANNING_OVERVIEW.md`](./PLANNING_OVERVIEW.md) / [`INDEX.md`](./INDEX.md) — add this doc.
- `.data/WORKFLOW_FACTORY_NEXT.md` — note clip bind vs policy-as-config split.
