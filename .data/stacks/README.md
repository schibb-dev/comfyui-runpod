# Generation stacks

A **stack** is the coupled weights bundle on a generation graph: UNet training
family + quant, matching TeaCache coefficients, DisTorch `virtual_vram_gb`, and
(optionally) CLIP filename when the loader class matches.

It is not a workflow (topology) and not a family (station / prompt book). Canvas,
duration, and steps stay runtime.

| stack_id | Default on |
|----------|------------|
| `i2v-720p-Q5` | origin families (Kneel, FaceBlast, …) |
| `i2v-480p-Q8` | GEX extend families |
| `i2v-480p-Q5` | A/B override — not a family default |

Invalid unless UNet `720p`/`480p` agrees with `i2v_720` / `i2v_480`. Apply module:
[`workspace/scripts/shape_factory_stack.py`](../../workspace/scripts/shape_factory_stack.py).

Shape field: `stack: i2v-720p-Q5`. Job override later: `adhoc_overrides.stack`.
