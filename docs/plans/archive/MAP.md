# Archive map — current ↔ archived + conflicts

**Authority:** live docs under [`docs/plans/`](../).  
**This folder:** archived drafts (evidence).  
**This file:** how they relate, and what in the archive is defunct or in conflict.

---

## How to use

1. Read the live plan for a program or slice.
2. Follow **Archived sources** on that live plan when you need depth or history.
3. Use **this MAP** when archived text disagrees with live docs, or when an old
   link lands here and you need the successor.

Do not “merge” archive prose back into live plans casually — update the live
plan, then note the conflict here if the archive still misleads.

---

## Current ↔ archive

| Live doc | Archived sources (basename under this folder) |
|----------|-----------------------------------------------|
| [`../README.md`](../README.md) (hub) | `PLANNING_OVERVIEW.md` |
| [`../a1-discovery.md`](../a1-discovery.md) | `DISCOVERY_SEARCH_AND_SIMILARITY_VISION.md`, `DISCOVERY_INDEX_WATCHER_PLAN.md`, `HEURISTIC_ENGINE_NORTH_STAR.md`, `VISION_V1_TIME_SLICE_CAPTION_SPIKE.md`, `SOURCE_FACET_SIMILARITY_PLAN.md` |
| [`../a2-experimentation.md`](../a2-experimentation.md) | `CHAIN_STEER_AND_APPETITE_CONTROL_PLAN.md` (marks / steer), `RUN_SPEC_DISPLAY_PLAN.md` |
| [`../a3-refinement.md`](../a3-refinement.md) | `HOURLY_UTILITY_PLAN.md`, `HOURLY_GUIDE_PIPELINE_PLAN.md`, `HOURLY_CLIP_GUIDANCE_PLAN.md`, `HOURLY_RULESET_UTILITY.md`, `WORKFLOW_REPAIR_PLAN.md`, `APPETITE_SIMILARITY_BIAS_PLAN.md` |
| [`../a3-hourly-drain-policy.md`](../a3-hourly-drain-policy.md) | `HOURLY_DRAIN_POLICY_PLAN.md` (U1/U5 vehicle), also `HOURLY_UTILITY_PLAN.md` §U1 |
| [`../a4-production.md`](../a4-production.md) | `PRODUCTION_PATTERNS_PLAN.md` |
| [`../s1-custody.md`](../s1-custody.md) | `ASSET_LIFECYCLE_PLAN.md`, `LINEAGE_REMEDIATION_PLAN.md`, `LINEAGE_INDEX_SKETCH.md` |
| [`../s2-platform.md`](../s2-platform.md) | `CURRENT_GOAL.md` (infra handoff only) |
| [`../asset-gallery.md`](../asset-gallery.md) | `ASSET_GALLERY_MODEL.md`, `STILL_GALLERY_HUB_PLAN.md`, `STILL_AUTO_TAGGER_PLAN.md`, `STILL_TAG_INDEX_HOUR_PLAN.md` |
| [`../judgment.md`](../judgment.md) | `RATINGS_V1_PLAN.md`, `BUCKET_MODEL_PHASE2_PLAN.md` |

**Product models left at `docs/` root** (not archived): `DISPOSITION_BUCKET_MODEL.md`,
`CLIP_SELECTION_MODEL.md`, `CORPUS_LIFECYCLE.md`, `APPETITE_REMOVE_LIFECYCLE.md`.
Live judgment / gallery plans link them.

---

## Defunct / conflict notes

| Topic | Archive tendency | Live resolution |
|-------|------------------|-----------------|
| Program IDs | P1–P9 in older overview / INDEX | **A1–A4 / S1–S2** in [`../README.md`](../README.md) |
| Active handoff | `CURRENT_GOAL.md` as “read this first” | Historical infra only → [`../s2-platform.md`](../s2-platform.md); hub is [`../README.md`](../README.md) |
| INDEX “Primary” | `VISION_V1_TIME_SLICE_CAPTION_SPIKE` as primary spike | V1 **complete**; Discovery next is not that spike — see [`../a1-discovery.md`](../a1-discovery.md) |
| Hourly U1 ownership | `HOURLY_UTILITY_PLAN` describes policy file; drain-policy also claims U1/U5 | **Drain policy is the U1/U5 design vehicle** — [`../a3-hourly-drain-policy.md`](../a3-hourly-drain-policy.md); utility parent is [`../a3-refinement.md`](../a3-refinement.md) |
| Second hourly scheduler | Steer docs can read as a parallel fill engine | Steer owns **marks**; hourly **consumes** them — no second scheduler |
| Still gallery class | Still hub as the privileged gallery | Stills = **first specialization** of asset galleries — [`../asset-gallery.md`](../asset-gallery.md) |
| Appetite similarity | Reads like a parallel rating system | Later A3 refinement only; judgment stays ratings + disposition — [`../judgment.md`](../judgment.md) |
| Production / orchestration | Vision / rundowns imply ready orchestration | **A4 stub only** — [`../a4-production.md`](../a4-production.md) |
| Merged hourly stubs | `HOURLY_CLIP_GUIDANCE_PLAN`, `HOURLY_RULESET_UTILITY` | Already redirects into utility; keep as archive crumbs only |

---

## Orphans / parked archive

These are archived and have a live parent, but no dedicated thin live slice yet
(work stays under the parent program until promoted):

| Archived | Park under |
|----------|------------|
| `DISCOVERY_INDEX_WATCHER_PLAN.md` | A1 |
| `STILL_AUTO_TAGGER_PLAN.md` / `STILL_TAG_INDEX_HOUR_PLAN.md` | asset-gallery (tagging drains) |
| `APPETITE_SIMILARITY_BIAS_PLAN.md` | A3 (later) |
| `WORKFLOW_REPAIR_PLAN.md` / `RUN_SPEC_DISPLAY_PLAN.md` | A3 / A2 as capacity |
| `LINEAGE_INDEX_SKETCH.md` | S1 / A1 lineage sketch |
| `SOURCE_FACET_SIMILARITY_PLAN.md` | A1 / A3 (v1 shipped; later providers parked) |
| `HEURISTIC_ENGINE_NORTH_STAR.md` | Vision note — cite from hub / A1, not a build queue |

---

*Update this MAP when a live plan’s archived sources change or a conflict is resolved.*
