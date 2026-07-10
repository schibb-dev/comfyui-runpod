# Bucket model — Phase 2 implementation plan

**Status:** Phase **2A shipped** (work item index + APIs + run-step hook). Phase 2B–2E planned.

**Model reference:** [`DISPOSITION_BUCKET_MODEL.md`](./DISPOSITION_BUCKET_MODEL.md) (expanded sections 11–16).

**Depends on:** Phase 1 triage/disposition (shipped), factory replay/extend hooks (partial), lineage index (backfilled).

---

## Goal

Move from “disposition commits intent” to **durable work instances** and **navigable pool views**, without breaking the review batch model.

Phase 2 delivers:

1. `work_items_index.json` — instances linked to source assets and factory jobs — **2A done**
2. Advance UI — **multi-route** (Extend + Vary toggles; Queue now = priority per instance) — 2B
3. Bucket **pool pages** — computed views with actions — 2C
4. Re-triage when work completes or new child outputs land — 2D

---

## Phase 2A — Work item index

### Module

[`workspace/scripts/shape_factory_work_items.py`](../workspace/scripts/shape_factory_work_items.py) (**shipped**)

### Schema: `work_items_index.json`

Path: `output/_status/work_items_index.json`

```json
{
  "schema": "comfyui-runpod.work-items.v0",
  "updated_at": "2026-07-09T…",
  "items": [
    {
      "work_id": "wi:01J…",
      "source_relpath": "og/2026-04-03/foo.mp4",
      "source_group_id": "og:stem:foo",
      "pool": "extend",
      "priority": "normal",
      "disposition_entry": "advance",
      "disposition_step": "advance.extend",
      "status": "queued",
      "created_at": "…",
      "updated_at": "…",
      "factory_job_key": "FB9_GEX2::run-…",
      "factory_family": "FB9_GEX2",
      "child_relpaths": [],
      "error": null,
      "idempotency_key": "extend:og:stem:foo:FB9_GEX2:idle-small-motions"
    }
  ]
}
```

### Status enum

| Status | Meaning |
|--------|---------|
| `draft` | User committed route; hook not fired yet |
| `queued` | Factory job submitted / pending |
| `running` | Comfy queue has work |
| `done` | Terminal success |
| `failed` | Terminal error (retry allowed → new instance) |
| `cancelled` | User or system cancelled |

### Rules

- **0..N instances** per source asset; dedupe via `idempotency_key` when same pool+recipe within cooldown window (configurable).
- **Queue now** sets `priority: "front"` on the instance — not a separate pool.
- Creating an instance does **not** auto-complete triage; dismiss batch still governs triage.
- Hook runner (`run-step`) creates/updates work item row when factory job is enqueued.

### API (shipped)

| Endpoint | Purpose |
|----------|---------|
| `GET /api/discovery/work-items?source_relpath=` | List instances for asset |
| `GET /api/discovery/work-items/pool?pool=extend` | Pool view |
| `POST /api/discovery/work-items/create` | Advance multi-route commit |
| `POST /api/discovery/work-items/cancel` | Cancel draft/queued |

Library / asset-ratings enrichment includes `work_items_open_count` and open rows when the index exists.

---

## Phase 2B — Advance multi-route UI

Replace single-choice Advance menu with:

```
[ ] Extend to chain    [ ] Vary / replay
[ ] Queue now (applies to checked routes above)
```

- User can select **both** Extend and Vary → two work instances on dismiss or explicit “Commit routes”.
- **Queue now** checkbox applies `priority: front` to all checked instances (or per-row priority in v2.1).
- Disposition entry remains `advance`; steps recorded per instance (`advance.extend`, `advance.vary`).

**Files:** [`DiscoveryRatingQueueApp.tsx`](../workspace/experiments_ui/web/src/ui/DiscoveryRatingQueueApp.tsx), [`DispositionBar.tsx`](../workspace/experiments_ui/web/src/ui/DispositionBar.tsx), disposition catalog steps.

---

## Phase 2C — Pool pages (bucket views)

Routes (add to [`routes.ts`](../workspace/experiments_ui/web/src/ui/routes.ts) under Workbench):

| Route | View query | Primary actions |
|-------|------------|-----------------|
| `/discovery/pools/review` | `needs_triage` | Open in rate queue |
| `/discovery/pools/refine` | `disposition.entry == refine` | Run step, open trim |
| `/discovery/pools/advance` | open work in extend/vary pools | Queue now, cancel |
| `/discovery/pools/park` | `disposition.entry == park` | Clear park, open in rate |
| `/discovery/pools/orchestration` | `work.status in (queued,running)` | Factory map link, cancel |
| `/discovery/pools/retired` | `disposition.entry == retire` | Restore (clear retire) |

Each page is a **filter preset** over discovery index + disposition + work items + triage — not a stored folder.

**Component sketch:** `DiscoveryPoolPage.tsx` parameterized by `poolId` + query spec from YAML.

### Pool query spec: `workspace/pool_views.yaml` (planned)

Declarative bucket definitions so UI and sampler share one source:

```yaml
pools:
  review:
    kind: triage
    query: needs_triage
  refine_backlog:
    kind: disposition
    entries: [refine, investigate]
  extend_pool:
    kind: work
    pool: extend
    status: [draft, queued, running]
```

---

## Phase 2D — Extended re-triage

Add to `needs_triage()` in [`shape_factory_triage.py`](../workspace/scripts/shape_factory_triage.py):

| Trigger | Condition |
|---------|-----------|
| Work terminal | Any work item on asset → `done` or `failed` since `last_triaged_at` |
| New child | Lineage edge: new `child_group_id` with this asset as parent, child mtime > `last_triaged_at` |
| Manual | `POST /api/discovery/asset-triage/request` (explicit “review again”) |

**Parent vs child:** new child file gets its own first triage; parent re-enters review only if disposition says so or policy `re_triage_parent_on_child` (default off in v2.0).

**Integration:** lineage reindex worker (future) emits `asset_job` with `reason: lineage_child_created` → optional hook to flag parent for re-triage.

---

## Phase 2E — Orchestration bucket

Orchestration view = join `work_items` ↔ factory job files ↔ Comfy queue ledger.

Show: source thumb, pool, priority, job status, ETA, link to factory map node.

No new orchestration **runner** — read-only visibility over existing factory + queue.

---

## Implementation order

| Step | Deliverable | Est. dependency |
|------|-------------|-----------------|
| 2A.1 | `shape_factory_work_items.py` + schema + tests | **done** |
| 2A.2 | Hook runner writes work items on `run-step` | **done** |
| 2A.3 | `GET work-items` API + enrich library rows | **done** |
| 2B.1 | Advance multi-checkbox UI | 2A.1 |
| 2B.2 | `POST work-items/create` from Advance commit | 2B.1 (API ready) |
| 2C.1 | `pool_views.yaml` + query engine | 2A.3 |
| 2C.2 | First pool page: `/discovery/pools/orchestration` | 2C.1 |
| 2C.3 | Remaining pool pages | 2C.1 |
| 2D.1 | Re-triage on work terminal | 2A.2 |
| 2D.2 | Re-triage on new child (lineage) | lineage index freshness |
| 2E.1 | Orchestration panel polish | 2C.2 |

---

## Out of scope (Phase 2)

- Full Orchestrator pipeline **execution**
- Auto-clear disposition when child succeeds
- Quality-specific regen UI (still plain replay hook)
- Moving files between physical directories per “bucket”

---

## Test plan

- Advance both Extend + Vary → two work items, one asset, distinct `work_id`
- Queue now → instance `priority: front`; Comfy submit uses front-of-queue path
- Work `done` → source re-enters review pool (2D.1)
- New child in lineage → child `needs_triage`; parent unchanged unless policy on
- Pool pages count matches manual SQL/query over index fixtures
- Retire still excluded from review pool regardless of work state

---

## See also

- [`DISPOSITION_BUCKET_MODEL.md`](./DISPOSITION_BUCKET_MODEL.md)
- [`RATINGS_V1_PLAN.md`](./RATINGS_V1_PLAN.md)
- [`DISCOVERY_INDEX_WATCHER_PLAN.md`](./DISCOVERY_INDEX_WATCHER_PLAN.md) — child-output signals
