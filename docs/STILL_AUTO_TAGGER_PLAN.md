# Still auto-tagger — plan

**Status:** Planning (2026-08-27). Do **not** implement until T0 exits below are accepted.  
**Home:** Gallery enrichment (G4) + P1 V3a day-one pin — not a new model bake-off.

**Related:**
[`STILL_GALLERY_HUB_PLAN.md`](./STILL_GALLERY_HUB_PLAN.md),
[`VISION_V1_TIME_SLICE_CAPTION_SPIKE.md`](./VISION_V1_TIME_SLICE_CAPTION_SPIKE.md) (portable runners + tag judgment),
[`DISCOVERY_SEARCH_AND_SIMILARITY_VISION.md`](./DISCOVERY_SEARCH_AND_SIMILARITY_VISION.md) (V3a),
[`DISCOVERY_INDEX_WATCHER_PLAN.md`](./DISCOVERY_INDEX_WATCHER_PLAN.md) (V2 job framework; `input/` deferred),
[`SCALE_INDEX_ARCHITECTURE.md`](./SCALE_INDEX_ARCHITECTURE.md) (SQLite indexes, not monolith blobs),
[`PLANNING_OVERVIEW.md`](./PLANNING_OVERVIEW.md) (P1 / P8).

---

## Locked decisions (operator notes 2026-08-27)

1. **No monolith JSON blob** for tags — never was the right store, never will be.
   G1’s `input_still_tags.json` is a **temporary** editorial scratch; migrate off it.
   Prefer **SQLite** (content_id rows) + optional **append-only NDJSON** audit, same posture as
   `ratings.sqlite` / still catalog / [`SCALE_INDEX_ARCHITECTURE.md`](./SCALE_INDEX_ARCHITECTURE.md).
2. **Gallery UI must trigger tagging.** CLI is for runners/debug only — not the operator path.
   UI **enqueues** work; it does **not** run Florence inside the HTTP request.
3. **Always a background process** with **simple event monitoring** (not “wait for V2 queue”).
   UI polls run/events; worker appends progress. Same job body as CLI drain.
4. **Batch size is empirical.** Dev/smoke stays **small** (default enqueue **12**) so the loop is
   debuggable; the **practical ops target is large batches** once we know GPU/queue cost —
   we only learn that by trying. Limit is a knob, not a product identity.
5. **DB default (proposal — change only if it hurts):** sit beside the still catalog under
   factory data, not inside `output/_status/` vision dumps. Schema below; revisit if joins
   with catalog get awkward.
6. **GPU placement stays flexible until load is known.** Do not hard-pick “always drain” vs
   “always remote.” Worker targets a **CaptionRunner** endpoint (same V1 contract); ops
   chooses local Comfy (concert / quiet periods) or remote Comfy (RunPod) per run or env.

---

## Why now

Stills gallery (`/discovery/stills`) has a thin manual-tag path today; filter/search will
stay weak until tags exist at scale. We already ran the hard part of “which tagger?”:

| Artifact | Result |
|----------|--------|
| Blind tag judgment (48 samples) | PromptGen-**large** wins day-one (F1 ≈ 0.86) |
| Pin | Still default: `cohort_x2_pg_large_tags` (same PromptGen-large weights; richer ★ recall than day-one `cohort_pg_large_tags`). FP blocklist still from `vision_v3a_tag_pin.json`. |
| Model | `MiaoshouAI/Florence-2-large-PromptGen-v2.0` via Comfy Florence |
| Helpers | `vision_tag_judgment_tags.parse_danbooru_tags`, FP blocklist + ★ vocab on the pin |
| Runner | `vision_slice_runner` / `vision_slice_caption_run.py` (run-anywhere) |

Stills are a **simpler** first production consumer than video V3a: one image per
asset, no time slices, keys already match gallery (`content_id` / sha256 in name).

```text
gallery UI (tag selected / missing / collection)
    → enqueue still-tag jobs (API)
    → worker / portable runner (PromptGen-large, pinned)
    → parse + FP filter
    → SQLite provisional rows (+ NDJSON audit)
    → gallery filter / sort / launch pad
         (editorial tags remain source of truth)
```

---

## Goals

1. **Propose** Danbooru-style tags for `input/` stills with the **pinned** model.
2. Persist proposals in a **row-oriented store** so gallery can filter without live VLM calls.
3. Keep **humans authoritative**: auto never silently overwrites editorial tags.
4. **Trigger from the gallery UI** (selected still, selection set, untagged pool, collection).
5. Stay on the **portable job** shape (local idle / Docker / RunPod) — off interactive Comfy GPU unless drained.
6. Leave a clean door into **V2** `asset_job_worker` later; do not block gallery value on FS watch.

## Non-goals

- Growing or “versioning” a monolith `*.json` tag dump as the system of record.
- Re-running the PromptGen base vs large bake-off (pin stands; informed `base∪large` deferred).
- Live VLM on every gallery scroll / search / tag-button click (enqueue only).
- Merging into video `asset_tags.json` on day one (shared helpers later; separate store now).
- CLIP / ANN still sorter (P8) — orthogonal; tags feed lexical filter first.
- Taxonomy product / HITL queues (V5) — reuse judgment stats only as filters.
- Watching `input/` via Discovery V2 (still deferred in watcher plan).

---

## Reuse map (do not rebuild)

| Piece | Use as |
|-------|--------|
| `vision_v3a_tag_pin.json` | Default model id + `fp_blocklist` + important vocab hints |
| `vision_slice_runner.make_runner` + Comfy provider | GPU forward (worker side) |
| `parse_danbooru_tags` / `tags_from_row` | Normalize caption → tag list |
| Input still **catalog SQLite** | Source of stills / missing resolve (same as gallery list) |
| Gallery UI | **Enqueue** + browse effective tags |
| V1 NDJSON + manifest pattern | Append-only audit of runs (not the query store) |

---

## Storage (default path + schema)

**Do not** stuff provisional (or long-term editorial) tags into one JSON object keyed by thousands of content_ids.

| Layer | Store | Notes |
|-------|--------|--------|
| **query + editorial + provisional** | SQLite | Row per `content_id`; JSON arrays in columns are fine (row-oriented DB ≠ monolith file) |
| **runs / events** | Same SQLite | Simple event monitoring for UI |
| **audit** | `output/_status/vision_still_tags.ndjson` | Append-only proposals; rebuildable into SQLite |

**Path (default):**  
`<data_root>/shape_factory/still_tags.sqlite`  
— same tree as `input_still_catalog.sqlite` / collections.  
(`data_root` ≈ `.data` / workspace data bind used by factory APIs.)

**Schema sketch (v1 — deliberately boring):**

```sql
-- one row per still
CREATE TABLE still_tag_items (
  content_id TEXT PRIMARY KEY,
  editorial_tags TEXT NOT NULL DEFAULT '[]',  -- JSON string array
  note TEXT,
  provisional_tags TEXT NOT NULL DEFAULT '[]',
  provisional_model_pin TEXT,
  provisional_pin_policy TEXT,
  provisional_run_id TEXT,
  provisional_tagged_at TEXT,
  suppressed_tags TEXT NOT NULL DEFAULT '[]',  -- JSON; skip on later auto
  updated_at TEXT NOT NULL
);

-- optional inverted index when filter-by-tag gets slow (add in T2 if needed)
-- CREATE TABLE still_tag_index (tag TEXT, content_id TEXT, layer TEXT, PRIMARY KEY (tag, content_id, layer));

CREATE TABLE still_tag_runs (
  run_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,          -- queued|running|done|error|cancelled
  scope_json TEXT NOT NULL,     -- request body snapshot
  enqueued_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  total INT NOT NULL DEFAULT 0,
  done_count INT NOT NULL DEFAULT 0,
  error_count INT NOT NULL DEFAULT 0,
  pin_policy TEXT,
  model_pin TEXT,
  detail TEXT
);

CREATE TABLE still_tag_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,            -- enqueued|started|item_done|item_error|finished|log
  content_id TEXT,
  message TEXT,
  payload_json TEXT
);
CREATE INDEX still_tag_events_run ON still_tag_events(run_id, id);
```

**Merge for display / filter (v1):**

```text
effective = unique(editorial ∪ (provisional \ fp_blocklist \ suppressed))
```

**Conflict rules:**

1. Writing editorial tags does **not** clear provisional.
2. Re-running auto **replaces** provisional for that `content_id` (same pin/policy) — does not touch editorial.
3. Gallery edit UI edits **editorial** only; provisional chips are distinct + promote / suppress.
4. Suppressions: editorial can reject a provisional tag so later runs skip it.

**Migration:** read existing `input_still_tags.json` once into `editorial_tags`, then stop writing the blob.

---

## UI trigger + background worker (locked)

Gallery must expose explicit actions, for example:

| Action | Scope | Behavior |
|--------|-------|----------|
| **Tag this** | Selected still | Enqueue 1 |
| **Tag selection** | Multi-select (when it exists) | Enqueue N (cap at limit) |
| **Tag untagged** | No provisional yet | Enqueue up to **limit** (UI/default) |
| **Tag collection** | Active collection | Enqueue members (capped by limit) |
| **Retag** | Selected / filtered | `force: true` replace provisional |
| **Tag many** (later) | Untagged / collection / explicit ids | Same path, **large** `limit` once cost is known |

**Limits:** default **12** for first smokes and UI safety. Practical goal once timing is known:
**large batches** (hundreds+) in one run — especially on a drained local Comfy or a short-lived
RunPod — so model load amortizes. Do not bake “always small” into the architecture; only the
**default** starts small. `only_missing: true` by default.

API shape (sketch):

- `POST /api/shape-factory/input-curation/stills/tag`  
  body: `{ content_ids?, collection_id?, only_missing?, limit?, force? }`  
  → `{ ok, run_id, enqueued, skipped }` — starts / signals **background worker**
- `GET …/stills/tag/runs/{run_id}` → run row (status, counts)
- `GET …/stills/tag/runs/{run_id}/events?after_id=` → incremental events (simple monitor)

**Worker (always on for this feature):**

- Long-lived or on-demand **background process** (not request-inline; not blocked on V2).
- Claims `queued` runs → `running`, processes items, writes `still_tag_events`, finishes run.
- UI: poll events every ~1s while a run is active; show strip + per-tile state from latest events.

**Hard rule:** Florence / Comfy only inside the worker.

---

## GPU / runner options (flexible)

The tagger job body always talks to a **CaptionRunner** (`vision_slice_runner`): JPEG in →
tag text out. Where the GPU lives is **config**, not schema.

| Mode | Practical today? | How |
|------|------------------|-----|
| **Local Comfy (concert)** | Yes | Worker uses `--provider comfy --comfy-server http://127.0.0.1:8188` (same Florence graph as V1 PromptGen). Run small batches in quiet gaps, after drain, or **as ordinary Comfy `/prompt` jobs** interleaved with I2V — tagging is already a Comfy workflow, not a separate CUDA app. Priority / “only when queue idle” is ops policy on the worker, not a new engine. |
| **Remote Comfy (RunPod)** | Yes — **already used in V1** | Same code path: `--provider runpod --comfy-server http://<pod>:8188 --image-mode upload`. Upload keeps runner disk ≠ pod disk. Spin pod → tag limit-12 batches → tear down (P9 / V1 rule). See [`VISION_V1_TIME_SLICE_CAPTION_SPIKE.md`](./VISION_V1_TIME_SLICE_CAPTION_SPIKE.md) § runners and [`RUNPOD.md`](../RUNPOD.md). |
| **Dedicated GPU sidecar** | Optional later | Second compose service / second machine with Comfy or transformers; same `--comfy-server` or `--provider transformers`. Only worth it if concert + RunPod both hurt. |
| **Non-Comfy remote APIs** | Not needed day one | No separate RunPod Serverless / Replicate glue unless Comfy-on-pod fails; would duplicate the pin path. |

**Concert with Comfy (recommended default while load is unknown):**

1. UI enqueues `still_tag_runs` as today.
2. Worker submits Florence PromptGen graphs to **whatever Comfy is configured** (local or pod).
3. Policy knobs (env / run flags), not forks:
   - `COMFY_SERVER` / `VISION_COMFY_SERVER`
   - `only_when_comfy_idle` (poll queue depth before claiming items)
   - small `limit` (12 in dev)
4. If local I2V load fights tags → point the same worker at a short-lived RunPod Comfy instead of inventing a second tagger.

### How “ComfyUI jobs” this already is

| Meaning of “Comfy job” | Practicality | Notes |
|------------------------|--------------|--------|
| **Comfy `/prompt` graph** (LoadImage → Florence2 → ShowText) | **Already done** — V1 `vision_slice_runner` | Same queue as I2V on that server; Comfy serializes GPU. Worker polls `/history`, parses caption, writes SQLite. This **is** a ComfyUI job. |
| **Interleave with I2V on one Comfy** | **High** | Just submit tag prompts to the same `:8188`. Optional idle-gate so tags only go when queue is short. No new runtime. |
| **Shape-factory / Workbench “job”** (family, `.job.json`, outputs, ledger) | **Low–medium for day one** | Factory assumes media construction + file outputs. Tagging yields **text in history**, not a video/PNG keep. Fitting it as a factory family means fake outputs, odd completion, and noise in Work Products. Defer unless you want tags visible next to Kneel in the map. |
| **Saved workflow template in Comfy UI** | **Easy later** | Pin the PromptGen-large graph as a workflow JSON; runner already builds the equivalent API prompt. |

**Recommendation:** treat still tags as **first-class Comfy prompts** (shared queue or remote pod), owned by the still-tag **background worker** — not as shape-factory experiment jobs until/unless you explicitly want them in that ledger.

---

## Job contract (portable)

Same spirit as V1: content-keyed, runner-recorded, no host absolutes in outputs.

```text
enqueue (UI or CLI debug)
   → worker picks content_ids
   → resolve path via catalog
   → Comfy Florence PromptGen-large (pinned)
   → parse_danbooru_tags + fp_blocklist
   → upsert SQLite provisional + append NDJSON
```

| Field | Meaning |
|-------|---------|
| `content_id` | sha256 from filename (required) |
| `relpath` | `input/…` style, portable |
| `tags` | parsed list post-filter |
| `model_pin` | `MiaoshouAI/Florence-2-large-PromptGen-v2.0` |
| `pin_policy` | `cohort_x2_pg_large_tags` (override via `STILL_TAG_PIN_POLICY`) |
| `run_id` / `runner` | audit |
| `provider` | `comfy` \| `transformers` \| `dry-run` |

CLI remains a **debug / drain** entrypoint that shares the same job body as the UI worker:

```bash
python3 workspace/scripts/vision_still_tag_run.py \
  --content-ids … --only-missing --limit 12 \
  --pin "$STATUS/vision_v3a_tag_pin.json" \
  --provider comfy --comfy-server http://127.0.0.1:8188
```

---

## Phased movement

### T0 — Contracts *(this doc)* — **mostly closed**

Locked: no monolith JSON; UI enqueue; SQLite default path/schema sketch; small dev batches;
**background worker + event poll**. Still soft: GPU drain vs sidecar (ops, not schema);
whether `effective` feeds pool facets later.

**Exit:** this section; no GPU work required to accept.

### T1 — Job body + worker + UI enqueue *(landing)*

- `vision_still_tags.py` + `still_tags.sqlite` (items / runs / events)
- Background worker + gallery **Tag this** / **Tag untagged** (default 12)
- Local Comfy PromptGen-large via existing `vision_slice_runner` (`prompt_gen_tags`)
- Dry-run path for tests; NDJSON audit under `output/_status/vision_still_tags.ndjson`

**Exit:** UI starts a run; events stream; tags land in SQLite.

### T2 — Gallery read path

- List/filter uses **effective** tags from SQLite; details show editorial vs provisional.
- Migrate G1 JSON editorial into SQLite; stop growing the blob.

**Exit:** filter by an auto tag without manual edit.

### T3 — Gallery tag UX polish

- Provisional chips; promote → editorial; suppress.
- Counts: tagged / provisional-only / untagged; per-tile / run status.

**Exit:** operator tags from UI day-to-day without CLI.

### T4 — Batch ops / runners

- Collection / untagged / retag at scale; documented Docker/RunPod drain recipes.

### T5 — V2 handoff (later)

- Catalog job `vision_still_promptgen_tag`; same job body; UI enqueue writes the shared queue.

---

## Relation to V2 / V3a / P8

| Track | Relationship |
|-------|----------------|
| **V2 watcher** | Still tagger is a real handler candidate; **UI enqueue** works before FS watch on `input/`. |
| **V3a video** | Same pin + parse helpers; different keys/store. |
| **P8 CLIP sorter** | Parallel; do not mix embeddings into this tagger. |
| **G4 gallery enrichment** | This plan **is** the tag half of G4. |

---

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Monolith JSON returns via “quick hack” | Locked: SQLite + NDJSON audit only |
| UI blocks on Florence | Enqueue-only API; worker side-channel |
| Auto overwrites humans | Layered rows; editorial wins |
| FP noise | Pin `fp_blocklist` |
| GPU fights Comfy | Flexible runner: idle-gated local Comfy **or** RunPod Comfy; never in request path |
| Domain shift (stills vs slice judgments) | T1 spot-check before trusting filters |

---

## Open decisions

- [x] Store kind: **SQLite + NDJSON audit** (not monolith JSON)
- [x] UI must trigger tagging (enqueue)
- [x] SQLite default: `<data_root>/shape_factory/still_tags.sqlite` + schema sketch above
- [x] Dev default batch: `only_missing`, **limit 12** (smoke); **large batches** are the ops target after we measure
- [x] Worker: **always background** + simple event monitoring (poll `events?after_id=`)
- [x] Pin: read from `output/_status/vision_v3a_tag_pin.json` (no vendored copy unless path missing)
- [x] GPU: **flexible** — local Comfy (concert / idle) and/or RunPod Comfy via existing runner; no hard drain-vs-sidecar pick
- [ ] Default `COMFY_SERVER` + whether UI exposes “use remote” / batch size vs env-only
- [ ] Measured steady tags/min + preferred large-batch size (fill after first real runs)
- [ ] Whether `effective` tags feed factory pool facets later

---

## Success criteria (program)

- [ ] Pin honored; policy id on every provisional row
- [ ] Editorial tags survive re-runs
- [ ] Gallery filters on effective tags including provisional
- [ ] **Tag actions in gallery UI** drive a **background** run with visible events
- [ ] No request-path Florence; no monolith tag corpus file
- [ ] Unpaid pods torn down after remote runs

---

## Suggested first implement slice

**T1:** `still_tags.sqlite` + worker/events + gallery **Tag this / Tag untagged** (default limit 12) + smoke.  
After that, intentionally try a **large** batch on quiet/RunPod Comfy and record tags/min before picking day-to-day limits. Schema path is a default — swap only if it fights the catalog.
