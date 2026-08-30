# Heuristic engine — north star

**Status:** Vision note (2026-08-29). Not a build spec. Corrals the
desire→technique / discover+generate loop so slice plans stay oriented.

**Related:** [`CORPUS_LIFECYCLE.md`](./CORPUS_LIFECYCLE.md) (judgment ↔ generation loop),
[`RATINGS_V1_PLAN.md`](./RATINGS_V1_PLAN.md) (quality vs appetite),
[`APPETITE_SIMILARITY_BIAS_PLAN.md`](./APPETITE_SIMILARITY_BIAS_PLAN.md) (neighbor bias),
[`DISCOVERY_SEARCH_AND_SIMILARITY_VISION.md`](./DISCOVERY_SEARCH_AND_SIMILARITY_VISION.md)
(find / resemble), [`PLANNING_OVERVIEW.md`](./PLANNING_OVERVIEW.md) (programs),
[`DISCOVERY_INDEX_WATCHER_PLAN.md`](./DISCOVERY_INDEX_WATCHER_PLAN.md) (index freshness).

---

## One sentence

Marks of **appetite** (“want more WITH this”), plus **similarity** from tags,
provenance, and (later) embeddings, feed an inspectable **heuristic engine** that
maps **desirable outputs → techniques that get them** — so the system can both
**find** more like this and **generate** more like this, without going stale.

---

## “More like this” is two verbs

| Verb | Meaning | Actuator |
|------|---------|----------|
| **Find** | Retrieve neighbors in a shared space | Discovery browse / search / “related” |
| **Generate** | Raise prior for the next job | Appetite → derive / extend / hourly / Submit |

Same judgment, two doors. Combining **discovery** and **guided generation** is
the product, not two silos.

---

## Signals that inform similarity

| Signal | Role |
|--------|------|
| **Tags** | Soft content axes (`asset_tags`, later VLM) |
| **Provenance** | Causal structure (lineage, bindings) — not the same as looks-like |
| **Embeddings** | Dense neighborhood when labels are thin (CLIP/SigLIP lane — later) |

Appetite **facet** (`source` | `processing` | `both`) chooses which story a mark
credits. Similarity bias fans that credit out to **neighbors**, not only the
marked `relpath`.

---

## Desire → technique map

The long arc is a growing ledger:

1. **Observe** — mark appetite (and quality) on workproducts; tags/lineage as context.
2. **Generalize** — similarity finds other things that might elicit the same appetite.
3. **Attribute** — bind “wanted this” to *how it was made* (family, prompt pattern,
   LoRA stack, params, source choice).
4. **Propose** — heuristics guess the next generate from that map, not only replay
   of one path.

Today’s thin slice: `appetite_index` → pattern/tag/lineage rollups →
`heuristics build` → hourly / sampler. What’s thin is crisp **output-desire ↔
recipe** attribution and evaluate-able neighbor bias
([`APPETITE_SIMILARITY_BIAS_PLAN.md`](./APPETITE_SIMILARITY_BIAS_PLAN.md)).

---

## Exploit and explore

Without exploration, the map collapses to a local maximum — more of what already
worked until everything feels samey.

| Pull | Job |
|------|-----|
| **Exploit** | Appetite + similarity + recipe attribution: “like this, made like that.” |
| **Explore** | Randomness, axis-hold/rotate, pool rewires, new seeds: “adjacent or new.” |

Explore is not noise for its own sake — it produces **new (desire, technique)
pairs** for the heuristic. Loop: explore → mark → attribute → exploit → explore.

Existing leans: hourly derive share, source-facet hold/rotate, promoted-source
rewires, noise seeds.

---

## Discovery index freshness (supporting surface)

Lineage and Library key off `discovery_og_wip_index.json`. That index stays
current via **factory deposit tip-in** and **lineage ensure-on-miss** (no full
rescan on every Workbench open). The FS watcher remains the completeness layer —
see [`DISCOVERY_INDEX_WATCHER_PLAN.md`](./DISCOVERY_INDEX_WATCHER_PLAN.md).

---

## Classical heuristics, model-tuned

Prefer an **inspectable classical core** (rules, indexes, rollups you can debug
without a GPU on the hot path). Models are **calibrators and feature proposers**:

- reweight axes / mix explore vs exploit
- propose tags, clusters, recipe features
- later: embeddings for recall; LoRA / adapters when the corpus of “wanted this”
  is rich enough to train against

Not “replace the heuristic with a black box.” Learn **parameters and features**
of a policy that stays human-readable. Batch/offline AI is fine; day-to-day
discovery and queue picks stay classical.

---

## Later fascinations (parked, intentional)

- **Embeddings** — continuous neighborhood for find + generate-near-mark.
- **LoRA tuning** — absorb soft direction from appetite clusters into learned look.
- **Model-tuned heuristic knobs** — fit classical structure from marks + outcomes.

Order of gravity: marks + attribution + light similarity first; embeddings when
recall hurts; LoRA when labeled desire is thick enough.

---

## What this doc is not

- Not a replacement for P1 V1–V5 spike sequence or hourly policy plans.
- Not a commitment to a particular embed stack or adapter training pipeline.
- Slice plans still live in their own docs; this is the **orientation** they
  should not contradict.
