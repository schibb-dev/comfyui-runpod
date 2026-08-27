# Family discovery — rejects & enroll log

Enrollment requires an approved `prop_NNN.json` (`status: new_family` or `approved`)
plus an operator-chosen `family_slug`. Nothing is auto-enrolled.

## Enroll command

```bash
cd workspace/scripts
# After editing prop_NNN.json status → new_family and setting proposed_family_slug:
python3 shape_factory_family_discovery.py enroll \
  --prop prop_003 \
  --slug FB8VA5-laying-down \
  --chain-role origin
```

This scaffolds:

- catalog stem `{Brand}_{date}_{time}_{I2V|V2V|VI2V}_{seq}-readable.json`
- `.data/shapes/{slug}.shape.yaml` with Phase 1 vocab fields
- `.data/pools/{slug}/pools.yaml` (+ empty prompts dir)

Operator must still verify Wan wiring / node ids, run
`validate_shape_document`, then `shape_factory generate` smoke.

## Rejects / deferred (pre-review defaults)

These classes are **not** enrolled without explicit naming:

| Class | Reason |
|-------|--------|
| Numbered `*_OG_*` dumps | Anonymous corpus evidence only (policy) |
| `tune-*` / `TUNETEST` | Noise — excluded from clustering |
| Near-duplicates of enrolled 12 | Prefer merge / skip over new family |
| `LoRA.json` and tooling graphs | Not product-line stations |

As of Phase 2 package generation: **0 families enrolled from proposals**.
All `prop_001`…`prop_040` remain `pending_review` — see [REVIEW.md](REVIEW.md).

## Covered by enrolled shapes

Cluster report marks **12** clusters as covered-by-enrolled (one per current family
template fingerprint). Alternate catalog revisions (e.g. pre-rewire FB8VB2 EXT)
surface as separate proposals for merge/skip judgment.
