# Inferred ratings v1 — plan & session handoff

**Last updated:** 2026-07-03

This document captures **locked design direction** and the **concrete implementation plan** for the next session(s). It complements [`LINEAGE_INDEX_SKETCH.md`](./LINEAGE_INDEX_SKETCH.md) (provenance graph) and [`DISCOVERY_SEARCH_AND_SIMILARITY_VISION.md`](./DISCOVERY_SEARCH_AND_SIMILARITY_VISION.md) (browse/similarity). Factory ops checklist: [`.data/WORKFLOW_FACTORY_NEXT.md`](../.data/WORKFLOW_FACTORY_NEXT.md).

---

## North star

**Optimize how videos are generated**, not only track source/output quality.

| Lens | Question |
|------|----------|
| Quality tracking | Which clips and sources are keepers? |
| Generation optimization | Which **recipes** (workflow + prompt profile + source + chain) reliably produce keepers? |

**Discovery over control:** generate broadly, index what happened, let lineage and ratings **surface** what works. Shape factory hourlies are **coverage and observation**, not a script to perfect upfront.

---

## Design principles (locked 2026-07-03)

1. **Lineage is the primary edge source** for inference — not ad-hoc basename matching alone.
2. **Workflows are first-class rating targets** — same as output media and source assets.
3. **Explicit ratings** live mainly on **outputs** (sidecar XMP on `og/` artifacts).
4. **Inferred ratings** propagate backward (and lightly sideways) via lineage: output → workflow, source, factory recipe.
5. **Derived index is rebuildable** — canonical truth remains files on disk + embeds; index lives under `_status/`.

---

## Two-axis ratings: quality vs appetite (added 2026-07-08)

A single 1–5 star conflates two different judgments. We split them:

| Axis | Question | Store | Scale | Drives |
|------|----------|-------|-------|--------|
| **Quality** ("do more OF this") | Is this *well-executed*? | XMP `xmp:Rating` → `ratings_index.json` | 1–5 stars (see sub-axes) | **Replay** — reproduce the recipe |
| **Appetite** ("do more WITH this") | Do I *want more of this direction* (even if rough)? | `appetite_index.json` | `less < neutral < more < fast_track` | **Derive/Extend** — build new descendants from it |

### Quality sub-axes (added 2026-07-10)

Single quality is insufficient. Explicit quality is three 1–5 axes; `explicit` is their rounded mean (also written to XMP for external tools):

| Axis id | UI label | Question |
|---------|----------|----------|
| `subject_beauty` | Subject | How good is the subject / look of the person? |
| `render_quality` | Render | How clean is the image/video render? |
| `action_quality` | Action | How good is the motion / action? |

Stored on each `by_output_relpath` row as:

```json
{
  "explicit": 4,
  "axes": {
    "subject_beauty": 5,
    "render_quality": 3,
    "action_quality": 4
  }
}
```

**Rate-queue done** = all three axes set **and** appetite. Legacy rows with only `explicit` (no `axes`) are incomplete and re-enter the rate pool. Heuristics / disposition / hourly keep consuming the aggregate `explicit`.

Key properties:

- **Separate store.** Appetite lives in `_status/appetite_index.json`, never in XMP, so it survives `ratings build` (which only rewrites `ratings_index.json`).
- **Facet attribution.** Each appetite carries a `facet`: `both` (default), `source` (the material), or `processing` (the look — prompt + lora). Facet routes credit:
  - `processing` / `both` → `by_pattern_appetite` (+ `by_tag_appetite` for prompt-derived tags).
  - `source` / `both` → `by_group_lineage_appetite` (credit walks lineage ancestors).
- **`fast_track` acts now.** Setting `fast_track` records the appetite **and** fires an immediate Extend (chain output → video slot) via the existing replay machinery; still-source families fall back to a plain replay.
- **Hourly step is a dispatcher.** `plan-step` chooses **Replay** (quality-dominant) or **Derive** (appetite-dominant, facet-aware rewire) per cursor via `HOURLY_DERIVE_SHARE` (default `0.5`); `fast_track` seeds pin Derive.
- **Tag-coupled discovery.** `asset_tags.json` (bootstrap: embedded-prompt keywords; future: Florence/WD14) lets appetite generalize by content through `by_tag_appetite`, biasing the sampler and derive-source ranking.

### Rebuild command order

Tags feed heuristics, and appetite is read at heuristics-build time, so rebuild in this order:

```bash
cd workspace/scripts
python3 shape_factory.py tags build                 # asset_tags.json (prompt keywords)
python3 shape_factory.py ratings build              # ratings_index.json (XMP stars)
python3 shape_factory.py heuristics build           # reads ratings + appetite + tags
# (heuristics build defaults --appetite-index/--asset-tags to <og>/../_status/;
#  pass them explicitly only for non-default locations.)
```

`appetite_index.json` is written live by the UI (`POST /api/discovery/asset-appetite/set`); it is not produced by a build step.

---

## Third axis: disposition (added 2026-07-08)

**Disposition** answers: *what processing does this file need next?* It is separate from quality (keeper judgment) and appetite (breeding direction).

| Layer | Question | Store | UI |
|-------|----------|-------|-----|
| **Quality ★** | Subject / Render / Action (aggregate → XMP) | `ratings_index.json` (+ XMP) | Three star rows |
| **Appetite** | Want more in this direction? | `appetite_index.json` | Appetite bar |
| **Disposition** | What work is committed next? | `disposition_index.json` | Entry chips + router steps |

### Process flow (rate page)

1. Watch clip → set Subject / Render / Action + Appetite (judgments).
2. **Suggestion engine** promotes entry markers from Q×A×facet (promote only — no silent auto-tick).
3. User toggles an **entry marker** (`refine`, `investigate`, `extract`, `advance`, `retire`, `park`).
4. **Router** narrows to **steps** (e.g. `refine.aspect` → replay hook, `refine.edit` → trim UI).
5. Step hooks fire; outcomes recorded in disposition row; markers clear or chain.

### Entry markers (v0)

| Entry | Meaning |
|-------|---------|
| `refine` | Known delta — fix artifact (aspect, quality, in-place edit) |
| `investigate` | Unknown delta — route to refine / extract / advance / retire |
| `extract` | Salvage part (frame, clip, reference) |
| `advance` | Feed next pipeline stage |
| `retire` | Remove from active work |
| `park` | Decide later |

### Catalog (editable)

- Repo seed: [`disposition_catalog.yaml`](../disposition_catalog.yaml)
- Runtime overlay: `_status/disposition_catalog.json` (UI `POST /api/discovery/disposition-catalog`)
- Per-asset state: `_status/disposition_index.json`

API:

- `GET/POST /api/discovery/disposition-catalog`
- `GET /api/discovery/disposition-suggest`
- `POST /api/discovery/asset-disposition/toggle`
- `POST /api/discovery/asset-disposition/run-step`

Implementation: [`workspace/scripts/shape_factory_disposition.py`](../workspace/scripts/shape_factory_disposition.py), rate UI [`DispositionBar.tsx`](../workspace/experiments_ui/web/src/ui/DispositionBar.tsx).

Sampler excludes assets with `retire` disposition from the review pool.

---

## Triage vs disposition (added 2026-07-08)

**See also:** [DISPOSITION_BUCKET_MODEL.md](./DISPOSITION_BUCKET_MODEL.md) — diagram-first reference (markdown + PDF) for review batches, buckets, pools, and Advance routes. **Phase 2:** [BUCKET_MODEL_PHASE2_PLAN.md](./BUCKET_MODEL_PHASE2_PLAN.md) — work items, pool pages, multi-route Advance.

**Triage** / **rating** and **disposition** are separate:

| Concept | Question | Store | Required to finish rating? |
|---------|----------|-------|----------------------------|
| **Rating** | Subject + Render + Action stars, and appetite? | `ratings_index` axes + `appetite_index` | **Yes** — all three axes + appetite |
| **Disposition** | What should happen to this next? | `disposition_index.json` | **No** — optional while rating |

- Disposition is **mutable** (set, change, clear anytime).
- **Batch workflow:** Next/Prev/Skip rotate within a fixed batch. **Dismiss batch** commits clips that have **all three quality axes + appetite**; clips missing either return to the rate pool.
- Rate sampler pool = **needs rating**: missing any quality axis and/or appetite (retired excluded). Disposition does not gate the rate queue.
- A separate **Route** activity (rated but no entry disposition) may come later.

Implementation: [`workspace/scripts/shape_factory_triage.py`](../workspace/scripts/shape_factory_triage.py), [`workspace/scripts/shape_factory_rating_sampler.py`](../workspace/scripts/shape_factory_rating_sampler.py).

---

## Entity types & identity keys

| Entity | Identity key | Explicit today | Inferred from |
|--------|--------------|----------------|---------------|
| Output video/image | `og/.../stem` + relpath | XMP `xmp:Rating` | — |
| Source media | normalized basename / pool member | rare | downstream outputs + lineage fan-out |
| Workflow (topology) | `graph_hash` (shape_factory, catalog) | none | outputs produced under this graph |
| Workflow (full recipe) | `prompt_fingerprint` / embed hash | none | outputs from that prompt snapshot |
| Catalog template | slug e.g. `FB9_GEX2-readable.json` | none | aggregate over runs using template |
| Factory recipe | `shape_id` + `prompt_profile` + binding picks | none | prod jobs + downstream chain quality |
| Pipeline / chain | e.g. `gex2-idle → facial-default` | none | end-to-end keeper rate |

**Score fields (all entity types):**

| Field | Meaning |
|-------|---------|
| `rating_explicit` | Hand-tagged (outputs) or manual override |
| `rating_inferred` | Aggregated from downstream evidence |
| `rating_effective` | Blend for sorting (sparse explicit on workflows is OK) |

---

## Inference model (v1 → v1.1)

### v1 (next session)

- Scan all rated XMPs under `og/`.
- Join PNG/MP4 embed → API prompt; extract `VHS_LoadVideo` / `LoadImage` sources.
- Join shape_factory jobs via deposit/output paths → `graph_hash`, `shape_id`, `prompt_profile`.
- Aggregate explicit stars → `by_graph_hash`, `by_shape_recipe`, `by_source_basename`, `by_output_relpath`.

### v1.1 (follow-on)

- Walk `discovery_lineage_edges.json` for multi-hop uplift (source → GEX2 → FACIAL).
- Weight edges by provenance quality:

| Edge source | Weight |
|-------------|--------|
| shape_factory job + deposit | 1.0 |
| PNG embed prompt | 0.9 |
| discovery lineage persisted | 0.85 |
| basename / heuristic | 0.5 |

---

## Existing infrastructure to reuse

| Piece | Path / command |
|-------|----------------|
| XMP + embed correlation | `workspace/scripts/correlate_output_ratings.py` |
| Lineage backfill | `scripts/backfill_discovery_lineage.py` |
| Lineage API + UI | `scripts/experiments_ui_server.py`, `DiscoveryAssetLineagePanel.tsx` |
| Graph fingerprint | `workspace/scripts/snowflake_inventory.py` (`graph_fingerprint`) |
| Factory jobs / deposits | `.data/shape_factory/jobs/<family>/`, `shape_factory deposit` |
| Discovery index | `<data>/output/output/_status/discovery_og_wip_index.json` |
| Lineage edges | `<data>/output/output/_status/discovery_lineage_edges.json` |

**Data root (host):** `/home/yuji/comfyui-runpod-data`

**Corpus snapshot (2026-07-03):** ~2250 rated XMPs in `og/`; ~2760 persisted lineage edges.

---

## Implementation plan

### Phase 1 — Build & verify (terminal only)

**Add:** `shape_factory ratings build` + `shape_factory ratings show`

**Output:** `<data>/output/output/_status/ratings_index.json`

Example shape:

```json
{
  "version": 1,
  "updated_at": "…",
  "by_graph_hash": {
    "0ac755226924fd9875810eca4a7d6894fa288ed7ad95e1be180f0d6e38b31368": {
      "inferred": 4.8,
      "n": 12,
      "keepers_4plus": 10,
      "catalog_slug": "FB9_GEX2"
    }
  },
  "by_shape_recipe": { "FB9_GEX2+catalog-default": { "inferred": 4.9, "n": 8 } },
  "by_source_basename": { "FB9_GEX2_2026-04-03_00001.mp4": { "inferred": 4.5, "favorite_fanout": 12 } },
  "by_output_relpath": { "og/…/foo": { "explicit": 5 } }
}
```

**Extend** `correlate_output_ratings.py` helpers — do not duplicate XMP/embed parsing.

**Commands:**

```bash
cd workspace/scripts

python3 shape_factory.py ratings build \
  --root /home/yuji/comfyui-runpod-data/output/output/og \
  --jobs-root ../../.data/shape_factory/jobs

python3 shape_factory.py ratings show --graph-hash 0ac75522
python3 shape_factory.py ratings show --source FB9_GEX2_2026-04-03_00001
python3 shape_factory.py ratings show --output og/…/stem
```

**Acceptance tests:**

| Test | Expected |
|------|----------|
| Full build | Index written; ~2000+ rated outputs indexed |
| Known keeper | `FB9_GEX2_2026-04-03_00001` → explicit 5★; source basename gets inferred boost |
| Known graph | FB9_GEX2 `graph_hash` `0ac75522…` ranks above rarely-used / quarantined graphs |
| Factory join | Recent shape_factory deposits appear under `by_shape_recipe` |
| Idempotent | Second build → same scores |

**Known reference:** `FB9_GEX2_2026-04-03_00001` has XMP rating 5; shape_factory jobs use `graph_hash` `0ac755226924fd9875810eca4a7d6894fa288ed7ad95e1be180f0d6e38b31368`.

---

### Phase 2 — Discovery manifestation

**Add:**

- Load `ratings_index.json` in `experiments_ui_server.py`.
- Enrich asset lineage (or library row): `rating_explicit`, `rating_inferred`, `rating_evidence` (`n`, keeper count).
- Lineage panel badge on source/workflow nodes — **no new page**.

**Test:** Open a known 5★ asset in Discovery → explicit star visible; lineage parent shows inferred score.

---

### Phase 3 — Lineage uplift (after Phase 1–2)

- Integrate `discovery_lineage_edges.json` for downstream fan-out.
- 2-hop credit with decay (source → GEX2 → FACIAL).
- Optional: `shape_factory ratings enrich --join-lineage`.

---

## Deferred (not next session)

| Item | Reason |
|------|--------|
| `best refresh` / auto hourly manifest | Needs stable ratings index + prod recipe scores over time |
| Chain runner C2–C4 (`pick: parent`, per-sample binds) | Unlocks coverage, not discovery insight |
| Full similarity / CLIP index | Separate program (P1) |
| Quarantine GetNode fixes | Prioritize when a **high-inferred graph** is blocked |

---

## Background ops (no new code)

Keep factory observing while building ratings:

```bash
tail -f .data/shape_factory/hourly.log
systemctl --user list-timers shape-factory-hourly.timer

python3 shape_factory.py status --family FB9_GEX2
python3 scripts/backfill_discovery_lineage.py --limit 100   # after new og outputs
```

Hourly defaults: `DEV_CHAIN=0` (full prod frames); manifest `.data/chains/best-examples.chain.yaml`.

---

## Success criteria (next session done)

1. `ratings_index.json` exists and passes spot checks on known keepers.
2. Discovery shows **at least one inferred score** on lineage (workflow or source node).
3. Hourly/manifest logic **unchanged** — discovery leads; factory keeps observing.

---

## Related programs

| Program | Role |
|---------|------|
| **P2 Lineage** | Edge graph for inference |
| **P1 Discovery** | UI surfacing |
| **P6 Workflow corpus** | `graph_hash`, recipe similarity later |
| **Shape factory** | Jobs, deposits, hourlies — join keys + gentle steering later |

See [`PLANNING_OVERVIEW.md`](./PLANNING_OVERVIEW.md).
