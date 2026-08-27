# Shape Factory — station vocabulary

Each enrolled family is a **station** (product line) in the Shape Factory plant.
A **pipeline** (`*.pipeline.yaml`) is the multi-step **plan** that orders stations
and binds outputs into the next step. Hourly drains are informal pipelines.

## Shape fields (station specs)

Declared on every `*.shape.yaml`:

| Field | Values | Meaning |
|-------|--------|---------|
| `primary_input` | `still` \| `video` | Feedstock class |
| `input_profile` | `still_prompt` \| `video_prompt` \| `video_identity_still_prompt` | Slot contract |
| `chain_role` | `origin` \| `extend` \| `mutate` \| `denouement` \| `standalone` | Role inside a pipeline |
| `io_class` | `I2V` \| `V2V` \| `VI2V` \| … | Process class badge (derived from profile) |

These are **descriptive** — operators see badges now; automation can read them later.
They are not a lockout / compatibility engine.

Hard integrity check (labels must not lie): Wan `start_image` ancestry must match
`primary_input` (see `shape_factory_vocab.validate_start_image_vs_primary_input`).

## GEX etymology

**GEX** ≈ **G**eneration + **EX**tension. Family brand for video-extend plates
(`FB9_GEX`, `FB9_GEX2`, `ASTONISH_FB9_GEX`, `FB9_GEX_FACIAL`, …). Natural
`chain_role: extend`. GEX is **not** a catalog stem tag.

## Catalog stems (new files)

```text
{Brand}_{yyyy-MM-dd}_{HHmmss}_{I2V|V2V|VI2V}_{seq}
```

Examples:

- `FB8VA4_2026-01-11_224827_I2V_00001`
- `FB9_GEX2_2026-08-27_120000_V2V_00003`
- `FB9_GEX2_identity_anchor_2026-08-27_120000_VI2V_00001`

Legacy `EXT` in older names parses as `V2V` with an extend hint
(`parse_catalog_stem` in `workspace/scripts/shape_factory_vocab.py`).

Reserved later tags: `II2V`, `IV2V`, `I2I`, `V2I`, `T2V`, `VV2V`.

## Current enrollment (13)

| Family | IO | Role | Profile |
|--------|----|------|---------|
| BounceDanceA | I2V | origin | still_prompt |
| Breast-shake-FB8VA5 | I2V | origin | still_prompt |
| FB8VA4 | I2V | origin | still_prompt |
| FB8VA5-ZOOMOUT | I2V | origin | still_prompt |
| FB8VB2 | I2V | origin | still_prompt |
| FB9-FaceBlast | I2V | origin | still_prompt |
| X-KNEEL-FB9 | I2V | origin | still_prompt |
| X-KNEEL-FB9-bare | I2V | origin | still_prompt |
| ASTONISH_FB9_GEX | V2V | extend | video_prompt |
| FB9_GEX | V2V | extend | video_prompt |
| FB9_GEX2 | V2V | extend | video_prompt |
| FB9_GEX_FACIAL | V2V | extend | video_prompt |
| FB9_GEX2_identity_anchor | VI2V | extend | video_identity_still_prompt |

`mutate` / `denouement` are reserved vocabulary — none assigned yet.

## Related

- Pipelines: [`.data/pipelines/`](../pipelines/) · catalog notes in [`CATALOG.md`](../pipelines/CATALOG.md)
- Intent / metaphor: [`docs/WORKFLOW_INTENT.md`](../../docs/WORKFLOW_INTENT.md)
- Family discovery proposals: [`docs/family_discovery/`](../../docs/family_discovery/)
