# Pipeline catalog (nascent)

Executable plans: `*.pipeline.yaml` in this directory. Soft guidance below is
**descriptive** — which pipeline to reach for — not enforcement.

Schema extras (optional on pipeline YAML):

```yaml
input_guidance: still | video | either
affinity:  # soft dispatch hints
  - when: hourly_facial_drain
  - when: still_seed_to_gex
```

## Enrolled pipelines

| pipeline_id | Steps | Guidance |
|-------------|-------|----------|
| `fb9-gex2-to-facial` | FB9_GEX2 → FB9_GEX_FACIAL | video extend chain; GEX2 deposit → FACIAL source |
| `fb8va5-zoomout-to-fb9-gex-facial` | FB8VA5-ZOOMOUT → … → FACIAL | still origin then V2V |
| `bouncedance-to-gex` | BounceDanceA → GEX | I2V origin → V2V extend |
| `faceblast-to-gex` | FB9-FaceBlast → GEX | I2V origin → V2V extend |
| `kneel-to-gex` | X-KNEEL-FB9 → GEX | I2V origin → V2V extend |
| `kneel-deliver` | X-KNEEL-FB9 → wan-delivery-postprocess | opt-in I2V → delivery (toggle upscale/RIFE/color) |
| `fb9-april03-replay` | replay / archive | historical replay |

Hourly informal pipelines (not YAML): facial drain, I2V→GEX drain, seed families —
see `shape_factory_hourly.py` / `docs/HOURLY_UTILITY_PLAN.md`.

**Factory Map UI:** open `/factory-map/pipeline/<pipeline_id>` → **Run pipeline** (background when wait is on).

## Soft affinities (draft)

- **Fresh still seed** → prefer I2V `origin` families (Kneel / FaceBlast / Bounce / …)
- **Completed GEX2 without FACIAL child** → `fb9-gex2-to-facial` / hourly facial drain
- **Completed I2V without GEX child** → Kneel/FaceBlast → `FB9_GEX` drain
- **Identity continuity** → `FB9_GEX2_identity_anchor` (VI2V)

## Related

- Station vocabulary: [`../shapes/README.md`](../shapes/README.md)
- Intent: [`../../docs/WORKFLOW_INTENT.md`](../../docs/WORKFLOW_INTENT.md)
