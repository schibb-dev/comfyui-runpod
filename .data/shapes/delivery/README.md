# Delivery postprocess stations (Phase 2 — not enrolled)

Optional **denouement** shapes for video-in → video-out transforms after generation.
Both are **deferred** — not in production, no enrolled shapes yet.

| Station | Effect | Model | Status |
|---------|--------|-------|--------|
| `wan-delivery-upscale` | 4× resolution | `RealESRGAN_x4plus.pth` | Deferred |
| `wan-delivery-rife` | Frame interpolation | `rife47.pth` | Deferred |

Generation graphs no longer embed these nodes (Phase 1 complete). When either is
needed, add a catalog template + `*.shape.yaml` here with `chain_role: denouement`,
then wire an **opt-in** pipeline tail (not default hourly drains).

See [`docs/WORKFLOW_LAYERS.md`](../../docs/WORKFLOW_LAYERS.md) and
[`graph_hash_migration_delivery_postprocess_2026-09-03.yaml`](../graph_hash_migration_delivery_postprocess_2026-09-03.yaml).

## Denouement shape sketch (future)

```yaml
schema_version: comfyui-runpod.shape.v0
shape_id: wan-delivery-rife
family_slug: wan-delivery-rife
primary_input: video
input_profile: video_only
chain_role: denouement
io_class: V2V
requires:
  - slot: source_video
    role: B
    media: video
    binding:
      type: vhs_load_video_path
      node_id: …
produces:
  - slot: delivered_video
    role: X
    media: video
    binding:
      node_type: VHS_VideoCombine
      node_id: …
```

Pipeline tail binds `source_video` from the prior step's `final_video` pool member.
