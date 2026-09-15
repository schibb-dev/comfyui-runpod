# Run spec display

Show a compact generation spec (UNet family + quant, canvas, duration, steps, TeaCache, DisTorch) on Queue, Workbench, and family/template pickers. Stamp the same token into **new** output names only.

Slices 1–3 are read-only: they parse graphs already in `/queue` and on disk. They do **not** patch live Comfy prompts, restart Comfy, or rewrite in-flight `filename_prefix`. Slice 4 writes prefixes on **new submits** only (idempotent suffix; live queue is untouched).

## Abbreviation

Example: Model `720p-Q5 virt4.0 576×1024` · Tune `6.5s 28step cfg3.0 den0.87` · Sampler `euler simple T.25`

| Token | Group | Source | Omit when |
| --- | --- | --- | --- |
| `720p-Q5` | Model | UNet filename family + quant, hyphenated | unknown |
| `virt4.0` | Model | `virtual_vram_gb` on GGUF DisTorch loader | `0` or absent |
| `576×1024` | Model | Size mxSlider (`Xi`/`Yi`) or WanImageToVideo | missing |
| `6.5s` | Tune (emphasis) | Duration mxSlider (or `81f`) | missing |
| `28step` | Tune (emphasis) | Steps mxSlider | missing |
| `cfg3.0` | Tune (emphasis) | CFG mxSlider / CFGGuider | missing |
| `den0.87` | Tune (emphasis) | BasicScheduler denoise | missing |
| `euler` / `simple` | Sampler (muted) | KSamplerSelect + BasicScheduler | missing |
| `T.25` / `Toff` | Sampler (muted) | Tea cache mxSlider (`Xf`) | node absent |

Tooltip / `spec_title` holds the long form (full UNet path, TeaCache coefficients). Tune is the common lever set; Sampler/TeaCache stay quieter.

Filesystem token (slice 4), after the job_key basename:

`…__000_adhoc_ui…__rs-720p-Q5_576x1024_6p5s_28st_Tea25_vv4`

The `__rs-` marker is the strip gate for [`_job_key_from_filename_prefix`](../workspace/scripts/shape_factory_work_products.py).

## Surfaces

| Surface | Source | Display |
| --- | --- | --- |
| Queue glance | Live Comfy API prompt | Model, emphasized Tune, muted Sampler |
| Workbench list / details | Job `.prompt.json`, else generated workflow, else template | Model chip, Tune chip, muted Sampler chip |
| Family / template pickers | Catalog template after shape `ui_defaults` | `{slug} · {spec_model} · {spec_tune}` |
| Output names (slice 4) | Same extractor, new submits only | `__rs-…` suffix |

Python owns extraction + strings ([`workspace/scripts/graph_run_specs.py`](../workspace/scripts/graph_run_specs.py)). TypeScript displays `spec_model` / `spec_tune` / `spec_sampler` (`spec_abbrev` is a fallback).

## Extractor

Title-based (`Size`, `Steps`, `Tea cache`, `Duration`, `Model`), not hardcoded node ids. Accepts API prompt (`class_type` + `inputs`) and LiteGraph (`nodes` + `widgets_values`).

Call sites:

- [`_extract_key_params_from_prompt`](../scripts/experiments_ui_server.py) / [`_queue_enrich_from_job`](../scripts/experiments_ui_server.py)
- [`list_shape_families`](../workspace/scripts/shape_factory_work_products.py)
- Work-product item builder + live Comfy synthetics
- [`submit_prompt_to_comfyui`](../workspace/scripts/comfyui_submit.py) (suffix on new POSTs)

Do not overload [`extract_workload_from_workflow`](../workspace/scripts/shape_factory.py) (efficiency metrics, node ids 84/82/133).

## Safety

- No Comfy restart, no `docker compose`, no interrupt of in-flight prompts.
- Experiments UI reload is enough for slices 1–3.
- Slice 4 is idempotent (`strip` then append) and skips preview/debug combines.
