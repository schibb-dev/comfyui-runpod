# Source facet similarity — hourly input-video randomness

**Status:** v1 shipped (editorial catalog + hourly hold/rotate). Auto-tag / face-identity providers are later.

**Related:** [`DISCOVERY_SEARCH_AND_SIMILARITY_VISION.md`](./DISCOVERY_SEARCH_AND_SIMILARITY_VISION.md) (looks-like + identity lane), [`shape_factory_tags.py`](../workspace/scripts/shape_factory_tags.py) (flat output prompt tags — different store).

---

## Intent

When hourly **derive** varies the input video (`appetite_facet=processing`), pick an alternate source that stays in a **similarity family** on one axis while other factors may change:

| Axis | Hold means | Free means |
|------|------------|------------|
| `appearance` | same look family (e.g. blonde) | other looks OK |
| `expression` | same expression family | other expressions OK |
| `identity` | same subject/cluster | other people OK |

**Identity ≠ lineage.** Editorial `identity` labels (and later face-embedding clusters) answer “same person / cluster?” for retrieval and constrained sampling. They do not replace provenance edges.

---

## Hold policy (v1)

Each tick rotates: `appearance → expression → identity → …` via `cursor % 3`.

Override axes with `HOURLY_HOLD_AXES=appearance,expression,identity`.

If the seed source has no values on the held axis, or no family match exists, fall back to unconstrained pool sampling (previous behavior).

---

## Data

| Artifact | Role |
|----------|------|
| [`workspace/source_facet_catalog.yaml`](../workspace/source_facet_catalog.yaml) | Editorial bootstrap for FB9 / X-Kneel seed inputs (provisional) |
| `output/_status/source_facets.json` | Runtime index (`by_source_key` → facets) |
| [`workspace/scripts/shape_factory_source_facets.py`](../workspace/scripts/shape_factory_source_facets.py) | Build / lookup / filter helpers |

Rebuild:

```bash
cd workspace/scripts
PYTHONPATH=. python3 -m shape_factory_source_facets source-facets build
# or: python3 shape_factory.py source-facets build
```

Per-source row shape:

```json
{
  "facets": {
    "appearance": ["blonde"],
    "expression": ["smiling"],
    "identity": ["subj_kneel_b"]
  },
  "provider": "editorial",
  "provisional": true
}
```

---

## Hourly wiring

[`shape_factory_hourly.py`](../workspace/scripts/shape_factory_hourly.py) `_derive_rewire` (processing path):

1. Collect alt sources from the recipe pool.
2. Choose hold axis from cursor.
3. Filter to sources sharing any value on that axis with the seed source.
4. Prefer non-recent combos among survivors.
5. Plan fields: `hold_axis`, `hold_values`, `hold_candidate_count`, `hold_facet_constrained`.

---

## Phase 2 (not in v1)

- Vision providers (`florence` / `wd14`) writing provisional facets into the same store.
- `identity_embed` → cluster id → `facets.identity` (flagging / retrieval only; sensitive).
- Discovery UI similarity browser; temporal slice tags.

Edit the YAML catalog when provisional labels are wrong; rebuild `source_facets.json` after edits.
