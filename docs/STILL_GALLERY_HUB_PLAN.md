# Still image gallery — launch hub

**Status:** North star (2026-08-27). Absorbs and widens the older “input still
browser + collections-as-pools” note.

**Related:** [`SUBMIT_WORKFLOW.md`](./SUBMIT_WORKFLOW.md) (doors → `/submit`),
[`.data/WORKFLOW_FACTORY_NEXT.md`](../.data/WORKFLOW_FACTORY_NEXT.md) (collections),
[`HOURLY_UTILITY_PLAN.md`](./HOURLY_UTILITY_PLAN.md) (still seeds),
[`CORPUS_LIFECYCLE.md`](./CORPUS_LIFECYCLE.md).

---

## Goal

An **image gallery** is the home for stills (Comfy/`input` first; later other
still corpora). Selecting an image opens a **launch pad** — not a private
submit form — into many destinations that already exist as doors:

| Launch | Destination | Intent |
|--------|-------------|--------|
| Queue / template | `/submit?media=…&family=…&origin=gallery` | I2V (Kneel / FaceBlast / Bounce…) |
| Collection | Input curation / pools | Pin into named sets → factory pools |
| Factory map | Family that uses `source_still` | See pairs / possible runs |
| Ratings / triage | Ratings surfaces | Judgment without leaving the still |
| Identity | VI2V / identity_anchor pickers | Use as face/identity still |
| Recover / locate | Asset lifecycle | Missing-hash recovery |

Gallery **browses and hands off**. Compose stays on Submit; status stays on
Workbench; live Comfy stays on Queue — same locks as Submit workflow.

```text
gallery (select still)
   ├─→ Submit (I2V / template)
   ├─→ collection → pool → hourly / map
   ├─→ Factory Map (family)
   ├─→ ratings / disposition
   └─→ identity / recover …
```

---

## What exists today (fragments)

| Piece | Where | Gap |
|-------|--------|-----|
| Discovery Library | `/discovery` | **G0:** stills get Open in Submit |
| **Stills gallery** | `/discovery/stills` | **G1:** grid + collections + tags + Submit |
| Factory Map input curation | still search + collections | Family attach remains here |
| Submit | `/submit?media=…` | **G0:** still seed + I2V picker |

---

## Phased movement (sketch)

### G0 — Still door into Submit *(landing)*

From Library: if item is an image, **Open in Submit** appears on Details (no
clip/marks). Deep link `media=input/…&origin=library`. Submit shows still
preview + I2V family picker (default Kneel) + Now/Later → `shape-factory/queue`
with `source_still`.

**Exit:** Library still → one job in an origin family without Factory Map.

### G1 — Gallery surface *(landing)*

Dedicated **Stills** nav at `/discovery/stills`: thumbnail grid over `input/`,
path search, tag filter/edit (content_id keyed), collections CRUD, and **Open in
Submit** launch pad (origin=`gallery`). Reuses input-curation APIs; Factory Map
still owns family↔collection attach.

**Exit:** Browse / collect / tag stills and hand one into Submit without Library.

### G2 — Launch pad chrome

Selected still: persistent action strip (Submit / collection / map / rate /
identity). All actions are `href` doors or thin APIs — no second compose UI.

### G3 — Collections → pools

Named collections register as pool members for hourly/I2V (existing next-step).
Gallery “Add to collection” is the human entry.

### G4 — Enrichment

Tag-assisted sort / filter via **still auto-tagger** (PromptGen-large V3a pin;
provisional vs editorial in **SQLite**, not a monolith JSON blob). Gallery UI
**enqueues** tagging (selected / untagged / collection) — no live VLM in the
request path. Optional ratings on stills later.

**Plan:** [`STILL_AUTO_TAGGER_PLAN.md`](./STILL_AUTO_TAGGER_PLAN.md),
[`STILL_TAG_INDEX_HOUR_PLAN.md`](./STILL_TAG_INDEX_HOUR_PLAN.md) (enqueue backlog → index-hour drain).

---

## Non-goals

- Replacing Discovery for **video** / clips
- Embedding full Submit compose inside the gallery
- Multi-step pipeline run button on the still (CLI / Factory Map pipeline page)

---

## Open decisions

- Gallery home: extend Discovery Library vs new `/stills` (or Factory Map tab)
- Default I2V family on Submit from still (Kneel vs last-used vs always pick)
- Whether Work Products stills (output companions) join the same gallery later

---

## Related

- [`PLANNING_OVERVIEW.md`](./PLANNING_OVERVIEW.md) — P8 / parking lot
- [`STILL_AUTO_TAGGER_PLAN.md`](./STILL_AUTO_TAGGER_PLAN.md) — G4 auto tags (PromptGen-large pin)
- [`SUBMIT_WORKFLOW.md`](./SUBMIT_WORKFLOW.md)
