# Work-product markers

Lean, **content_id-keyed** facts about a work product — technical provenance, short notes, A/B links — for filter and automation.

**Not this store:** content vocabulary (`asset_tags`), quality/appetite (ratings), or committed next work (disposition / Workbench pick-mode).

See also: [DISPOSITION_BUCKET_MODEL.md](./DISPOSITION_BUCKET_MODEL.md) (judgment axes), [asset registry](../workspace/scripts/asset_registry.py).

## Rules

| Rule | Detail |
|------|--------|
| Keys | Namespaced only: `decode.vae`, `note.review` (`^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$`) |
| Values | Small strings (enum / id / short note); no nested JSON |
| Source | Every row: `scan` \| `job` \| `human` |
| Overwrite | `human` wins over `scan`/`job`; scan/job may overwrite each other |
| Join | `content_id` (same identity as the asset registry) |

Known keys grow only when a real writer lands — no giant catalog UI.

## Storage

`{output}/_status/work_product_markers.sqlite` (runtime; do not commit).

```sql
CREATE TABLE markers (
  content_id TEXT NOT NULL,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  source TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (content_id, key)
);
```

Module: [`workspace/scripts/shape_factory_markers.py`](../workspace/scripts/shape_factory_markers.py).

## First key: `decode.vae`

Values: `tiled` \| `plain`.

Scanner reads factory job `.prompt.json` graphs (`VAEDecodeTiled` → tiled, else `VAEDecode` → plain), resolves output paths to `content_id`, stamps with `source=scan`.

```bash
python3 workspace/scripts/shape_factory.py markers scan-decode          # dry-run
python3 workspace/scripts/shape_factory.py markers scan-decode --apply
python3 workspace/scripts/shape_factory.py markers list --key decode.vae --value tiled
python3 workspace/scripts/shape_factory.py markers set \
  --content-id <sha256> --key note.review --value "check seams" --source human
```

## Family A/B keys

Written by `shape_factory.py ab-judge` (and queued jobs stamp construction `ab_pair_id`):

| Key | Values | Source |
|-----|--------|--------|
| `ab.pair_id` | `ab_<hex>` | `job` |
| `ab.slot` | `a` \| `b` | `job` |
| `ab.disposition` | `no_distinction` \| `keep_as_variant` \| `improve_base` \| `new_family` \| `inconclusive` | `human` |
| `ab.observed_effect` | short operator phrase (e.g. `more frenetic`) | `human` |

UI: `/family-ab`. Shared compare stage: `workspace/experiments_ui/web/src/ui/compare/MediaCompareStage.tsx`.

## HTTP

- `GET /api/shape-factory/markers?content_id=…`
- `GET /api/shape-factory/markers?key=decode.vae&value=tiled`
- `POST /api/shape-factory/markers` `{ "content_id", "key", "value", "source?" }`

Workbench lists attach `markers` (and `content_id` when known) on work-product rows; filter **vae:all / tiled / plain** is separate from pick-mode disposition toggles.

## Out of scope (for now)

PNG embed scanning; vision tags; freeform tag editor; markers keyed only by `job_key`.
