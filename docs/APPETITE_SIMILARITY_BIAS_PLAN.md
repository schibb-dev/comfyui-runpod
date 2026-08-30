# Appetite similarity bias — technique track

**Status:** stub (2026-08-29). Phase A shipped: mark appetite on Workbench + Factory Map
inspectors ([`RATINGS_V1_PLAN.md`](./RATINGS_V1_PLAN.md)). This doc tracks **Phase B**:
a mark on one workproduct should raise prior for **neighbors** and **similar recipes**,
not only that `relpath`.

**Orientation (uber-plan):** [`HEURISTIC_ENGINE_NORTH_STAR.md`](./HEURISTIC_ENGINE_NORTH_STAR.md)
— find+generate, desire→technique, exploit/explore, classical heuristics model-tuned.

**Related:** [`SOURCE_FACET_SIMILARITY_PLAN.md`](./SOURCE_FACET_SIMILARITY_PLAN.md),
[`DISCOVERY_SEARCH_AND_SIMILARITY_VISION.md`](./DISCOVERY_SEARCH_AND_SIMILARITY_VISION.md),
Factory Map appetite seeds (source-facet curation).

---

## Intent

Operator marks `more` / `fast_track` (with facet) on a workproduct →

1. **Self-bias (done):** that output seeds hourly derive/extend and rollups.
2. **Similarity bias (open):** raise pull for *other* outputs and workflows that are
   close on one or more axes — without requiring the same graph edge.

The hard part is choosing and evaluating **similarity techniques** across axes.

---

## Axis families (reuse before inventing)

| Axis family | Existing hook | Notes |
|-------------|---------------|--------|
| Source material | facet `source` → lineage / group appetite; Map **Appetite seeds** | Credit walks ancestors |
| Processing / look | facet `processing` → `by_pattern_appetite` + prompt tags | Recipe neighborhood |
| Content tags | `by_tag_appetite` + [`shape_factory_tags.py`](../workspace/scripts/shape_factory_tags.py) | Keyword / later VLM tags |
| Source facets | appearance / expression / identity | Hourly hold/rotate v1 |
| Looks-like / embeds | aspirational (CLIP/SigLIP) | Discovery vision V3b+ |
| Workflow / family / LoRA | thin today | Natural “similar workflow” axis |

---

## First operationalize (when Phase B starts)

**Default first axis:** strengthen **pattern/tag** bias visibility and sampling
(already written by appetite facet `processing`/`both`) — measure whether
`more`/`fast_track` on A increases pull of tag-neighbor B in hourly / derive ranking.

**Eval sketch:** before/after sampler weights or derive-source rank for neighbors of
a marked output; no new UI required beyond facet attribution on the mark control.

---

## Non-goals (yet)

- Auto-propagating appetite rows onto neighbors (confuses operator intent).
- Embedding search as a prerequisite for bias.
- Still-gallery appetite marks (inputs stay on Rate/Library / source facet).
