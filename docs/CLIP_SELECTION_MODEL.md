# Clips, usable trim, and selection

Locked product model for Asset / Clip / Use, starring, usable trim (hygiene),
automation selection, ratings, used/unused cleanup, and soft-delete. Complements
[DISPOSITION_BUCKET_MODEL.md](./DISPOSITION_BUCKET_MODEL.md).

**Last updated:** 2026-08-26

---

## 1. Three layers

| Layer | Role |
|-------|------|
| **Asset** | Parent video (`content_id`) |
| **Clip** | Named bookmark span on an asset — stable `clip_id` |
| **Use** | This job’s VHS window — may match a clip, usable trim, or adhoc marks |

Re-run **As edited** updates **Use** only; it does **not** mint a clip.
Promote to a clip is explicit (Save / Update / “Save clip & re-run”).

---

## 2. Usable trim vs starred clips

| | **Usable trim** (source trim) | **Starred clip** |
|--|------------------------------|------------------|
| Job | Data hygiene | Preference / seed multiplicity |
| Says | Omit artifacted head/tail; file is wrong outside this | Prefer this span as a generation seed |
| Cardinality | 0–1 per asset | 0–N per asset |
| Lottery | Only when nothing is starred (1 unit = clean span) | Each ★ is one selection unit |

Do **not** use ★ to mean “skip the glitch.” Do **not** use usable trim to mean “this peak is special.”

**Planned:** asset-level usable range. **Today:** clips + singular `default_clip_id` still exist; starring (multi) replaces “default” as the preference model.

---

## 3. Automation selection

**Starred clip = selectable seed unit.** Parent file is the container only.

| Parent state | Candidates |
|--------------|------------|
| N starred clips | **N** (sample among ★; clamp into usable trim if set) |
| 0 starred, usable trim set | **1** = usable trim |
| 0 starred, no usable trim | **1** = whole asset (if whole-file OK) |
| Clips exist but none starred | Same as 0 starred (unstarred = human bookmarks only) |

Unstarred clips stay available for manual Queue-from-clip / edit; they do **not** enter the hourly lottery.

**Implementation sequence** (★ schema → picker → hourly bind → pool-level newer bias): [HOURLY_CLIP_GUIDANCE_PLAN.md](./HOURLY_CLIP_GUIDANCE_PLAN.md).

Optional later: asset flag **clip-required** → no full-file fallback until something is starred.

---

## 4. Ratings

| Seed used | Inferred credit |
|-----------|-----------------|
| Starred / any `source_clip_id` | That **clip** |
| Usable trim / whole asset (no clip) | The **asset** |

Same rollup idea (child quality → seed); usable trim is **not** its own rateable object.
Explicit clip ratings may come later as an override; inferred-first is enough for ranking ★.

---

## 5. Used vs unused (cleanup axis)

Separate from **retired** (`deleted_at`) and from **starred** (preference).

| State | Meaning |
|-------|---------|
| **Used** | ≥1 durable job / work record referenced this `clip_id` as seed (`source_clip_id` or `vhs_window.clip_id`) |
| **Unused** | Never referenced that way |

- **Derived**, not a hand-set flag — compute from job / ledger refs (same idea as “derived videos from clip”).
- ★ / default / usable-trim preference is **not** “used.” Manual bookmarks can sit forever unused.
- Ratings inferred onto a clip still imply it was used (or will be once jobs exist); unused clips may still have labels/notes only.

**Cleanup policy**

| Action | Unused | Used |
|--------|--------|------|
| Soft-delete (retire) | Preferred for clutter | Allowed — history + restore stay intact |
| Hard-delete (purge) | Junk / near-dup mistakes only | **Never** (keep tombstone or migrate refs first) |

Cleanup UIs can filter **unused** (+ unstarred / retired) without guessing which bookmarks still matter for lineage.

**Shipped (library):** rows annotated with `used` / `use_count` from a jobs-tree scan; filters `unused_only` / `used_only`; hard-delete refuses used clips when `jobs_root` is provided.

**Shipped (prefs):** `asset_clip_stars` multi-★ set (schema v5); legacy `default_clip_id` migrates into ★ and remains an alias. Automation uses `pick_seed_clip` (★ recency-weighted → usable-trim sidecar → full). See [HOURLY_CLIP_GUIDANCE_PLAN.md](./HOURLY_CLIP_GUIDANCE_PLAN.md).

---

## 6. Longevity: soft-delete and restore

Hard delete is for **unused** junk only (near-dup mistakes, never queued).

**Soft-delete (retire):**

- Keep `clip_id`, marks, rating aggregates
- Set `deleted_at`; hide from default lists / starring / lottery
- Clear preference pointers (e.g. legacy default) that pointed at it
- Jobs that already recorded `source_clip_id` still resolve history

**Restore:** clear `deleted_at` (optionally re-★). Same identity; ratings intact.

**UI:** allow viewing retired clips and restoring them (Clips library / parent detail). Used/unused badges and Usage filter ship in Clips library.

---

## 7. Proliferation mitigations

- No silent mint from Re-run / scrub
- Near-duplicate gate on create (~50 ms)
- ★ set is the curated lottery pool; UI can default to starred (+ recent)
- Soft-delete unused unstarred; hard-delete only unused junk with no refs

---

## 8. Resolve order (factory Use)

1. Explicit Use / `source_clip_id` on the job  
2. Else starred set (when multi-★ ships) / legacy default clip  
3. Else usable trim  
4. Else full asset  

(Implementation today: use → source_clip → default_clip → sibling → sidecar → full; hourly ★ + newer wiring: [HOURLY_CLIP_GUIDANCE_PLAN.md](./HOURLY_CLIP_GUIDANCE_PLAN.md).)
