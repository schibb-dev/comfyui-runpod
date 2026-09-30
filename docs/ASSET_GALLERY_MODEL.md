# Asset gallery model

**Status:** Model stub (2026-09-30).  
**Program:** A1 Discovery (visibility + role sources).  
**Parent hub:** [`PLANNING_OVERVIEW.md`](./PLANNING_OVERVIEW.md).

---

## What this is

An **asset gallery** is a corpus surface for browsing, selecting, and launching
work from assets that can act as **starters** or **role sources**.

The **still image gallery** ([`STILL_GALLERY_HUB_PLAN.md`](./STILL_GALLERY_HUB_PLAN.md))
is the **first specialization** of this class — not a privileged asset type.

Same class includes:

| Specialization | Notes |
|----------------|-------|
| Still images | Concrete UI today (G0–G3 partial) |
| Clips | [`CLIP_SELECTION_MODEL.md`](./CLIP_SELECTION_MODEL.md) |
| Starter videos (no still) | Role: starter; media: video |
| Workproduct-as-starter | Any judged output reused as a seed |
| Specialty sources (later) | Identity, pose, look, etc. for VACE / Production |

---

## Starter is a role

**Starter** means “this asset is used as an input seed / guide / reference for
a generation or chain step.” It is **not** a file-type enum.

- A still can be a starter for I2V or a guide for hourly.
- A clip can be a starter for extend / remix.
- A finished workproduct can become a starter for the next pattern stage.
- Production patterns (A4) will name **role slots** (identity, pose, look, …)
  that galleries (or specialty galleries) fill — see
  [`PRODUCTION_PATTERNS_PLAN.md`](./PRODUCTION_PATTERNS_PLAN.md).

---

## Shared concerns (all specializations)

1. **Browse / filter** — corpus visibility (A1).
2. **Select → launch pad** — destinations (Submit, Workbench, pools, hourlies)
   not a private form ([`STILL_GALLERY_HUB_PLAN.md`](./STILL_GALLERY_HUB_PLAN.md)).
3. **Collections ↔ pools** — curation that hourlies and adhoc can drain.
4. **Custody** — content-addressed paths stay true ([`ASSET_LIFECYCLE_PLAN.md`](./ASSET_LIFECYCLE_PLAN.md)).
5. **Tagging drains** (A3) — auto-tagger / index-hour feed *specializations*;
   they are not a separate gallery program.

---

## Non-goals (this stub)

- No new UI surface beyond existing still gallery / Library / clips.
- No Production orchestration here (A4).
- No second rating system (judgment stays ratings + disposition).

---

## Next

- Prefer “asset gallery” language in planning; keep still-specific delivery in
  the still hub plan.
- When a second specialization needs UI (e.g. starter-video browse), add a thin
  child plan under this parent — do not fork a parallel “gallery” program.
