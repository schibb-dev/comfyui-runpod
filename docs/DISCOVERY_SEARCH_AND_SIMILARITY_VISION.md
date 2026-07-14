# Discovery search & similarity — vision sketch

This document captures **aspirational targets** from design discussion: how **browsing and semantic search** relate to **lineage**, what “similarity” can mean across modalities, and where complexity tends to land. It is **not** a specification—no commitment to stacks, hashes, or UI layout until narrowed deliberately.

**Companion:** provenance and durable graph concepts live in [`LINEAGE_INDEX_SKETCH.md`](./LINEAGE_INDEX_SKETCH.md). Lineage and similarity are **separate capabilities** that meet **at the selected asset**. **Inferred ratings** (workflow/source/recipe scores from downstream keepers) are specified in [`RATINGS_V1_PLAN.md`](./RATINGS_V1_PLAN.md). **Hourly source variety via structured facets** (appearance / expression / identity): [`SOURCE_FACET_SIMILARITY_PLAN.md`](./SOURCE_FACET_SIMILARITY_PLAN.md). **All programs (queue, orchestration, infra, etc.):** [`PLANNING_OVERVIEW.md`](./PLANNING_OVERVIEW.md).

**Related (separate tracks, same repo):** experiment **job runners / Comfy queuing** and **orchestration sketches** are documented below and in [`SCHEDULED_AND_CONTAINER_JOBS_RUNDOWN.md`](./SCHEDULED_AND_CONTAINER_JOBS_RUNDOWN.md), [`WORKSPACE_PROJECTS_RUNDOWN.md`](./WORKSPACE_PROJECTS_RUNDOWN.md) (§4.1 resubmit/replay/extend; orchestration deferred), and [`PROJECT_ORGANIZATION_PROPOSAL.md`](./PROJECT_ORGANIZATION_PROPOSAL.md) (experiment-pipeline as Project A). They share **GPU time** and **output artifacts** with Discovery but are **not** the looks-like spike.

---

## User-facing framing: two questions at once

Around any corpus item it should eventually feel natural to ask:

1. **Where did this come from?** — **Lineage / provenance** (parents, inputs, workflow hops, synthetic sources such as Comfy `input/` uploads when modeled).
2. **What else is relevant?** — **Similarity and related retrieval**: visually or semantically like this, **or** produced under a **similar recipe**, **without** requiring the same graph edge.

Those questions are **orthogonal** but **joined in the UI** at one artifact: origin vs neighborhood.

---

## Three complementary “lenses” (aspirational)

| Lens | Intent | Typical signals (examples only) |
|------|--------|----------------------------------|
| **Provenance** | Causal chain | Persisted + inferred edges, embedded paths, queue logs |
| **Looks / reads like** | Perceptual & language similarity | Image/text embeddings (e.g. CLIP family), captions/tags (e.g. Florence-style), hybrid keyword search on prompts and metadata |
| **Cooked like** | Same *kind* of process, different *ingredients* | Workflow shape / API-topology fingerprints, LoRA or adapter stack, coarse “settings profile” (sampler family, steps bucket, resolution), **not** identical to strict full-prompt equality |

The **recipe** lens matches the intuition: **same kitchen, lamb vs chicken**—shared scaffold, interchangeable starters or prompts.

---

## Embeddings and ANN (minimal vocabulary)

- **Embedding:** a fixed-length vector produced by a model; **nearby vectors** are intended to represent **similar content** in that model’s training sense.
- **Semantic search** here usually means: embed queries and corpus items, then retrieve **nearest neighbors** in vector space (often combined with **keyword/BM25** on paths, prompts, captions—**hybrid** retrieval).

**ANN (approximate nearest neighbor):** algorithms that return vectors **close** to a query vector **much faster** than an exact scan over the whole corpus, usually trading a small amount of recall for latency and scale (e.g. HNSW-style indexes, IVF variants).

---

## Rough difficulty tiers (engineering intuition)

These describe **product realism**, not model trivia.

### Easier (commonly shipped first)

- **Global image embedding + ANN** (CLIP / SigLIP-class): text→image and image→image *gist* similarity.
- **Caption or tag generation + keyword / BM25** (Florence-class or similar): human-readable retrieval on descriptions.
- **Hybrid** retrieval merging lexical scores with embedding neighbors.
- **Offline batch indexing** for new/changed assets.
- **Near-duplicate** helpers (perceptual hash or embedding threshold)—distinct from semantics.

### Moderate

- **Video:** sampled frames or keyframes as multiple vectors per asset; retrieval that aggregates or jumps to timestamps.
- **Reranking** heavy models only on top candidates.
- **Structured tags** when models expose usable facets (still imperfect).

### Hard (stretch goals)

- **Ordered temporal activity:** queries like “X happens *then* Y” across seconds—needs explicit **timeline / segment** representation and often **audio**, not one vector per clip.
- **Fine demographic claims** from pixels alone—fragile and sensitive; optional editorial tags often behave better than insisting on vision-only truth.

---

## Early spike (narrowed)

**Intent:** learn whether **in-asset time** improves curation and “looks / reads like” retrieval, without committing to **hard-tier** temporal query semantics (“X *then* Y” in natural language).

**In scope for the spike**

- **Time-stamped slices:** index rows are **(asset, `t0`, `t1`, …)** plus whatever signals you already planned for gist search—e.g. **caption/tag text** and/or **embedding** **per slice**. Chunking strategy is deliberately flexible at first (fixed windows, keyframes, or simple shot boundaries—pick one to ship, compare later).
- **Looks / reads like lens only** for auto signals on those slices; recipe and provenance unchanged.
- **Outputs:** search hits can **jump to a span**; optional **rough seeds** for human-curated activity landmarks (precision still editorial).

**Explicitly out of scope for this spike (still nice-to-have later)**

- Ordered temporal **NL** queries across the whole timeline.
- Broadcast-perfect alignment or rich activity **onset** models—unless the spike proves the need.

---

## How much LLM in the loop (operational model)

**North star:** day-to-day **exploration and discovery** should run on a **durable, human-shaped corpus** (tags, landmarks, definitions, embeddings already computed)—not on **live** vision-language or chat calls for every browse or search.

| Phase | Role of models | Role of humans |
|-------|----------------|----------------|
| **Batch / offline** | Slice videos, emit **captions** and/or **embeddings**, propose seeds | Light QA optional; mostly unattended |
| **Review / tagging** | **Guided inquiry:** “I think I see X”—agree, disagree, edit; active-learning style queues | **Source of truth** for labels and evolving categories |
| **Analysis / retrain** | Periodic jobs: refresh indexes, train **thin adapters** or heads on stored judgments, re-rank | Define taxonomy changes, approve training windows |
| **Daily discovery** | **None required** (ideal): search = **ANN + BM25 + facets** over **curated + auto** fields | Browse, filter, jump to spans, refine |

Auto-generated captions/embeddings are **bootstrap and recall**, not the long-term authority. As the **tagging corpus** grows, retrieval should lean on **editorial labels and stable vectors**, with models invoked again when **new media arrives**, **definitions change**, or you deliberately **retrain**.

**GPU posture (spike / ops):** heavy forwards stay **off the interactive Comfy path** when possible—batch on idle hardware, RunPod, or a dedicated worker—so generation and tagging do not fight for the same GPU without a queue policy.

**Meta-goal — balance human, classical indexing, and AI adaptation:** over time, **identify which human-in-the-loop steps** repeatedly consume attention yet exhibit **learnable structure** (agree/disagree patterns, pairwise “same as my taste,” stable vocabulary after taxonomy settles). Those become **candidates for offline refinement**—e.g. LoRA or thin heads on frozen encoders—so the loop **shifts work** from live LLM calls toward **classical retrieval** (ANN, BM25, facets, editorial tags) plus **occasional retrain**. Not every HITL step should be automated; **humans** keep definition changes, edge cases, and policy; **indexing** stays the fast daily path; **AI** absorbs repetitive proposal/ranking labor only where labeled history justifies it.

| Layer | Typical responsibility |
|-------|-------------------------|
| **Human** | Taxonomy, promoted labels, disputes, consent/sensitive facets |
| **Classical index** | Vectors, keywords, time spans, lineage joins, filters |
| **AI (batch / adapter)** | Auto captions/embeds, guided questions, retrain after enough signal |

Instrument review UX so judgments are **auditable training rows**—making it possible to ask, per workflow step: *“Could a small adapter replace most of this, or should it stay human?”*

**Personal design north star (human, not product requirement):** use this corpus and loop to learn **where LLMs are genuinely useful or transformative**—versus where they are **expensive noise**—by watching which guided steps, batch proposals, and retrain cycles **actually change how you find and understand work**. Success is not “more AI in the product”; it is **clarity about the few places models earn their place** (e.g. recall at scale, vocabulary formation, similarity you care about) while everything else stays **fast, inspectable, and human-led**.

---

## Taxonomy drift, guided review, and refinement passes

Tagging is not a one-shot labeling exercise. **Categories evolve** as you see more of the corpus (“sly glance” splits, merges, or gets redefined). That is **ontology drift**, not failure—systems should **record iterations** instead of treating early tags as permanent truth.

**Store judgments with context**

- Each tag/landmark: **label**, **taxonomy / definition version**, **time span**, **who/when**, optional **rationale**.
- **Provisional** vs **promoted** tags: fast discovery labels vs stable training/export labels.

**Explicit taxonomy operations**

- **Split, merge, rename/redefine, deprecate**—each can emit a **reconciliation set**: “N existing tags may be stale under the new definition.”

**Guided inquiry (active learning UX)**

- Model (or a cheap scorer on embeddings) **proposes**; human **agrees / disagrees / edits**.
- After a definition change, **consistency pass** queues: “still X under v3?” plus **high-impact** neighbors (likely stale), not a blind re-click of the entire library.

**Named passes (intent, not shame)**

- **Discovery pass** — seeds, provisional tags, rough slices.
- **Refinement pass** — after schema change or new vocabulary.
- **Consistency pass** — targeted stale-tag sweep.

Optional: **cluster-then-name** when words are still forming—group similar slices, name the cluster, confirm members—reduces premature commitment to a label before variety is visible.

**Training implication:** labels are **versioned rows**; superseded tags can be excluded or down-weighted; agree/disagree under a new definition is strong **negative/positive** signal for adapters later.

---

## Related: job runners, queuing, and orchestration (outline)

Discovery/similarity work runs **beside**—not inside—the experiment **generation** pipeline. This section orients the other plans so spikes do not get conflated.

### Layer 1 — Production experiment queue (running today)

**Purpose:** keep **tune experiments** flowing through ComfyUI with visible status and recovery.

| Component | Role |
|-----------|------|
| **`watch_queue.py`** | Submit `prompt.json`, poll `history.json`, write `submit.json` |
| **Ops containers** (`refresh_run_status`, `queue_incomplete_experiments`, `report_experiment_queue_status`, `ws_event_tap`) | Status, re-queue, logging, timing metrics |
| **`comfy_queue_ledger.py`** | Shadow ledger, restore, optional spillover/refill; UI pause/resume/drain |
| **Experiments UI** | `/api/queue`, requeue, submit-prompt, ledger control |

**Doc:** [`SCHEDULED_AND_CONTAINER_JOBS_RUNDOWN.md`](./SCHEDULED_AND_CONTAINER_JOBS_RUNDOWN.md).

**Near-term product (queue UX, not multi-step orchestration):** **resubmit / replay / extend** from a visible artifact—liberal template pairing, fail-fast errors, logging (`WORKSPACE_PROJECTS_RUNDOWN.md` §4.1). **WIP tune launcher** spec in `workspace/experiments_ui/docs/FEATURE_WIP_TUNE_LAUNCHER.md`.

### Layer 2 — Orchestrator UI (sketch / planning store)

**Purpose:** name and group **projects**, **collections**, **workflow refs**, **pipelines** (ordered steps with rules), **queues**, and **saved queue items**—persisted as JSON via `/api/orchestrator/state`.

**Status:** **Orchestrator** React app exists; today it is primarily **CRUD + summary** (add projects/queues), not a runner that executes pipeline steps against Comfy.

**Data model (conceptual):** `OrchestratorPipeline` = list of `OrchestratorPipelineStep` (workflow ref, input collection or prior step, per-step rules). Intended future: connect steps to **actual submits** and **run state**.

### Layer 3 — Workflow Explorer / Factory (planner DB)

**Purpose:** **buckets** of assets and workflows, **run plans** with **planned_jobs** (SQLite `factory` DB), browse roots, workflow **input/output contracts** and fingerprints.

**Status:** UI and API surface **planning** (counts, planned jobs, asset/workflow buckets). **Execution** of those jobs through a durable orchestration engine is **not** the same as `watch_queue` today—treat Factory as **job definition + inventory**, with execution still via experiment/Comfy paths unless wired later.

**Code:** `WorkflowExplorerApp.tsx`, `/api/workflow-explorer/factory*`, `experiments_ui_server.py` factory helpers.

### Layer 4 — Orchestration (explicitly later)

**Stated deferral** (`WORKSPACE_PROJECTS_RUNDOWN.md` §4.1): **A→B→C workflows across buckets/collections** needs:

- **Durable run state** (per step: inputs, outputs, failures, retries)
- **Stronger workflow compatibility** (or cached **workflow profiles** / validation—post-MVP)
- Clear **handoff** from Factory `planned_jobs` and/or Orchestrator `pipelines` into **Comfy submit** primitives

**Lineage sketch** integration point: provenance is recorded at **queue/output** boundaries; multi-step orchestration should emit **run** rows that lineage can reference without redefining similarity.

### How this relates to Discovery + the slice spike

| Concern | Experiment queue / orchestration | Discovery looks-like spike |
|---------|----------------------------------|----------------------------|
| **GPU** | Comfy generation (interactive) | Offline batch (e.g. RunPod); avoid fighting Comfy |
| **Artifacts** | `experiments/`, og/wip outputs | Reads og/wip (or spike folder); writes slice sidecars |
| **Near-term build** | Resubmit/replay/extend, queue stability | Time-slice captions for ~12 videos |
| **Long-term** | Execute pipelines; collections | HITL tags, classical search, optional adapters |

**Repo structure (optional):** [`PROJECT_ORGANIZATION_PROPOSAL.md`](./PROJECT_ORGANIZATION_PROPOSAL.md) would isolate **Project A** (pipeline + queue scripts + experiments server) from runpod infra—orthogonal to Discovery indexing choices.

---

## Identity-oriented similarity (optional lane)

**Separate from lineage.** Identity tooling (face-focused embeddings, consistency checks) answers **“same face / same reference person?”** — **CLIP does not reliably encode stable identity.**

Optional **complementary** uses:

- Find **more clips matching this face** in a scoped corpus.
- **Soft QA** against unwanted **face drift / swaps**: compare embeddings between **known lineage inputs** and outputs when wiring is explicit—**flagging**, not a moral verdict.

Treat face-derived signals as **sensitive**; scope and policy belong in product design.

---

## Relation to lineage (explicit)

- **Lineage** does not subsume similarity; **similarity** does not replace lineage.
- Outputs can **look alike** or share a **recipe cluster** without a graph link; lineage can join items that **look different** after heavy post.
- **Integration** is optional: e.g. “compare this output to **provenance starters** under a consistency or identity budget”—a **overlay**, not part of the lineage definition.

---

## What is intentionally not decided here

- Exact embedding models, index backends, or shard layout.
- Strict vs loose **recipe fingerprints** (topology-only vs LoRA-list vs settings bundles).
- Whether **identity** features ship at all, and under what consent or scope rules.
- UI copy, tab names, or whether the three lenses are tabs, accordions, or one ranked feed.

---

## Summary

**Aspirational north star:** from any Discovery artifact, move fluidly between **how it was caused**, **what resembles it in pixel/text space**, and **what was produced under a comparable workflow recipe**—with heavier temporal and identity semantics deferred until the easy/moderate layers feel solid. An **early spike** may still introduce **time-stamped slice indexing** under the looks-like lens to validate “where in the asset?” UX and recall **without** promising full temporal-query semantics yet. Over time, **models batch in** for analysis, tagging, and retrain; **humans and the curated corpus** carry **day-to-day discovery** without live LLM in the loop. A standing **meta-goal** is to **map HITL steps to adapter candidates**—balancing **human judgment**, **classical indexing**, and **offline AI refinement** (e.g. LoRA) rather than perpetual live model calls.

---

*Captured from conversational design thread; revise when narrowing implementation.*
