# Delivery postprocess station (Phase 2)

One **denouement** workflow, completely separate from generation. Generation templates
no longer contain ColorMatch (delivery), upscale, or RIFE for final delivery — those live
here only.

## Optional components (`delivery:` block)

All nodes exist in the graph; factory apply toggles bypass (`mode` 0/2) per shape or job:

| Key | Node(s) | Effect |
|-----|---------|--------|
| `color_match` | `ColorMatch` | Color transfer vs source reference frames |
| `upscale` | `UpscaleModelLoader`, `ImageUpscaleWithModel` | 4× resolution (`RealESRGAN_x4plus.pth`) |
| `interpolate` | `RIFE VFI` | Frame interpolation (`rife47.pth`) |

```yaml
delivery:
  profile_id: delivery-default
  color_match: false
  upscale: false
  interpolate: true   # example: RIFE only
```

Per-job override: `adhoc_overrides.delivery` with the same keys.

## Graph

```text
VHS_LoadVideoPath → ColorMatch → ImageUpscaleWithModel → RIFE VFI → VHS_VideoCombine
                      (opt)            (opt)                 (opt)
```

Catalog: `wan-delivery-postprocess-readable.json`  
Builder: [`workspace/scripts/build_delivery_catalogs.py`](../../workspace/scripts/build_delivery_catalogs.py)  
Apply module: [`workspace/scripts/shape_factory_delivery_postprocess.py`](../../workspace/scripts/shape_factory_delivery_postprocess.py)

Example pipeline: [`kneel-deliver.pipeline.yaml`](../pipelines/kneel-deliver.pipeline.yaml)

**Models:** `rife47.pth` and `RealESRGAN_x4plus.pth` on the ComfyUI host when those options are enabled.

Generation editorial (`postprocess.color_match` / `merge_frames` on origin/extend shapes) is unrelated — it stays in generation graphs only.
