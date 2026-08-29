# UI navigation backlog (punch list)

Captured 2026-08-28. Parking lot for first-order navigation and browse affordances across Discovery surfaces.

Related today: Submit deep links ([`SUBMIT_WORKFLOW.md`](./SUBMIT_WORKFLOW.md)), Workbench name filter, Still gallery hub ([`STILL_GALLERY_HUB_PLAN.md`](./STILL_GALLERY_HUB_PLAN.md)), corpus lifecycle notes ([`CORPUS_LIFECYCLE.md`](./CORPUS_LIFECYCLE.md)).

---

## Punch list

### 1. Recent submits in the Submit window — **done (2026-08-28)**

- Submit empty state + compose footer show **Recent submits** (ok + err) via `RecentSubmitsPanel`.
- Each row links to **Queue** (`/comfy-queue?prompt_id=&job=`) and **Workbench** (`/workbench?job=` or `?prompt_id=`).
- API: `GET /api/shape-factory/submit-attempts` with `errors_only=0`.

### 2. Deep links everywhere (first-order navigation)

**Shipped:**

- Queue: `?prompt_id=` / `?job=` → expand section, scroll, highlight.
- Workbench: `?job=` / `?prompt_id=` / `?q=` → seed filter, exact match + scroll/highlight.
- Library: `?relpath=` → select + scroll + deep-link flash (`discoveryLibraryHref`).
- Clips: `?clip_id=` / `?media=` / `?view=` → select + scroll + flash (`clipsLibraryHref`).
- Stills: `?content_id=` / `?relpath=` / `?q=` → filter, select tile, scroll + flash; URL sync on select (`stillsHref` / `parseStillDeepLink`). Workbench still bindings use `content_id` when present.
- Helpers in [`discoveryDeepLink.ts`](../workspace/experiments_ui/web/src/ui/discoveryDeepLink.ts).

**Still open / thinner:**

- Rating queue, Factory Map cell, and other multi-asset surfaces that still lack stable URL targeting.
- First-order door links everywhere (every surface that *knows* an id should emit the matching href).

### 3. Filesystem-style navigation to videos

- Browse videos via a **filesystem-style navigator** toward each video’s **de facto canonical location** on disk (output/og/wip layout, not a flat dump of thumbs).
- Operator can walk the tree / path and open the same object the rest of the app knows by content id / work product.

### 4. Input directory browse (harder) — deferred (needs design)

- Same treatment for **`input/`** (stills and other loaders’ sources).
- Complicated by **ComfyUI loader path conventions** (what LoadImage / VHS / etc. resolve vs what we show as workspace-relative paths). Needs careful mapping so “browse to file” and “what the graph will load” stay aligned.
- Note: Still gallery is the current still entry point; this item is a true FS tree, not the gallery grid.

---

## Notes

- Items 2–4 are one theme: **addressability + browse**, not more one-off search boxes.
- Prefer content-addressed or stable keys in links (`job_key`, `prompt_id`, `content_id`, `clip_id`) over fragile absolute paths.
- Next when picking this up: (3) FS video browse spike, or fill remaining (2) surfaces (Rating / Factory Map).
