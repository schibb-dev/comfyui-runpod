# Workflow factory — scheduled next steps

Locked decisions (2026-07-03): install `/workflow/convert`, JSON pool index, pipeline FB9_GEX2→FACIAL, layered queue, shape_id + family_slug.

**Next session focus:** optional hourly pick by `rating_effective` (after G); Discovery index watcher — see [`docs/RATINGS_V1_PLAN.md`](../docs/RATINGS_V1_PLAN.md). **Hourly ruleset utility** (policy + clip ★/newer + ledger/UI): [`docs/HOURLY_UTILITY_PLAN.md`](../docs/HOURLY_UTILITY_PLAN.md).

**Deferred (2026-08-03):** Work Products **Re-run** creates a new identical job but does not stamp the old interrupted/error job. Next: on Re-run, write `submit.rerun_job_key` + `submit.rerun_at` on the source job (keep status as `interrupted`/`error`; optional UI badge `re-run → …`).

**Plan (2026-08-20):** Hourly **facial backlog editor** — the GEX2→FACIAL drain set is derived (`list_gex2_needing_facial`), not a curated queue. Want a Workbench/Home surface to inspect, pin/skip, dedupe by source, and cull items (without inventing FACIAL children). Policy knobs today: `HOURLY_FACIAL_LOOKBACK_DAYS`, `HOURLY_SEED_OVER_CHAIN_SHARE`, **`HOURLY_FACIAL_DRAIN_EVERY` (default 6)**, **`HOURLY_I2V_GEX_DRAIN_EVERY` (default 3 — Kneel/FaceBlast/… → FB9_GEX)**. One-shot cull archive: `.data/shape_factory/jobs/_archive/facial_backlog_cull_*` (unique extras reinstated 2026-08-20; same-source dupes of keepers left archived).

**Plan (2026-08-20):** **Input still browser + collections-as-pools** — browse Comfy/`input` stills with search/sort via the tagging work already started (`asset_tags`, V3a PromptGen-large pin, tag-judgment corpus). Let humans build **named collections** that register as factory **pool members** (same shape as today’s `.data/pools/*/pools.yaml` indexes) for hourly/Submit/i2v seeds. Scope: **still images first**; same tagging/collection pattern can later cover Work Products video. Broader input-tree organization is related but secondary to collections→pools. Cross-link: [`docs/PLANNING_OVERVIEW.md`](../docs/PLANNING_OVERVIEW.md) P1/P8.

**Plan (2026-08-20 / merged 2026-08-27):** **Hourly as ruleset utility** — policy file, clip ★+newer bind, decision ledger, Home UI. Single plan: [`docs/HOURLY_UTILITY_PLAN.md`](../docs/HOURLY_UTILITY_PLAN.md). Candidates to lift first into durable config: `facial_drain_every`, `i2v_gex_drain_every`, `seed_over_chain_share`, `facial_lookback_days`, seed family weights / `IMAGE_TO_GEX` families, fresh-still / kneel / 2025 boosts, derive/archive shares, clip recency knobs.

**Plan (2026-08-27):** Station vocabulary + family discovery — `primary_input` /
`input_profile` / `chain_role` / `io_class` on all 12 shapes; FACIAL dead
`source_video_ref` removed; catalog stem parser; Factory Map / Work Products
badges; corpus proposals under `docs/family_discovery/` (human naming gate).
See [`docs/WORKFLOW_INTENT.md`](../docs/WORKFLOW_INTENT.md) and
[`.data/shapes/README.md`](shapes/README.md).

## Session checklist

### A. workflow-to-api-converter

- [x] Add `comfyui-workflow-to-api-converter-endpoint` to `custom_nodes.yaml` (optional)
- [x] `git clone` into running container `comfyui0-runpod`
- [x] Restart ComfyUI container
- [x] Verify GET `/workflow/convert`
- [x] Submit uses `prompt_source=workflow_convert`
- [x] Converter listed in `custom_nodes.yaml` (bakes on next `docker compose build`)
- [ ] Optional: rebuild image to pick up converter in fresh containers

### B. FB9_GEX_FACIAL shape

- [x] `.data/shapes/FB9_GEX_FACIAL.shape.yaml`
- [x] `.data/pools/FB9_GEX_FACIAL/pools.yaml` + sample prompt
- [x] `.data/pipelines/fb9-gex2-to-facial.pipeline.yaml`
- [x] `shape_factory pool sync` — JSON index for `FB9_GEX2_X_og`
- [x] `shape_factory generate` for FB9_GEX_FACIAL (standalone)
- [x] `shape_factory submit` FB9_GEX_FACIAL via workflow_convert
- [x] Fix `/workflow/convert` misroute on Text Concatenate (UI link relink post-pass)
- [ ] Re-submit FACIAL after repair (was queued with node_errors — do not submit while GEX2 runs unless `--force`)

### C. Pipeline loop

- [x] `shape_factory status` — poll Comfy queue/history
- [x] `shape_factory deposit` — register mp4+png into pool index
- [x] `shape_factory pipeline run` — generate/submit/wait/deposit steps
- [ ] End-to-end: wait for step-1 render → deposit → step-2 with fresh deposit member
- [x] Deposit from completed GEX2 runs → `FB9_GEX2_X_og` (+2 members)
- [ ] Optional: pipeline `--wait` soak test on real GPU queue

### F. Workflow validation & quarantine

- [x] `shape_factory validate` — object_info, convert, sanitize, optional `--comfy-check`
- [x] `shape_factory validate --catalog` — batch all catalog `*.json` + `catalog_summary.validate.json`
- [x] `shape_factory quarantine` — managed registry at `.data/shape_factory/quarantine.json`
  - `quarantine sync` — validate catalog + refresh registry (use `--comfy-check` before factory submit)
  - `quarantine patch` — auto-fix deprecated node types from `scripts/workflow_node_id_map.yaml` (e.g. `LoadImageWithFilename|pysssss` → `LoadImage`)
  - `quarantine apply` — rebuild registry from existing `validation/*.validate.json`
  - `quarantine list [--status quarantined|ok|released]` / `quarantine show` / `quarantine release --note`
- [x] P0 **YAML `prompt_error_rules`** — `scripts/workflow_repair_rules.yaml` + `DeclarativePromptErrorRules`
- [x] P5 **repair exhausted audit** — quarantine keeps `repair_outcome`, `repair_fixes[]`, remaining `node_errors`
  - Rules: `node_type_rename` (YAML map), `prompt_string_image_mismatch` (post-convert)
  - `shape_factory repair rules` / `repair run` — inspect or run loop standalone
- [x] `generate` / `submit` / `pipeline run` block quarantined shape templates (override: `--ignore-quarantine`)
- [x] Reports under `.data/shape_factory/validation/*.validate.json`
- [ ] **UI-managed quarantine status** — expose quarantine as a first-class, operator-managed status surface (view active registry path/overlay, release/re-quarantine actions, reason history, and stale-overlay warning when UI/API path diverges from local CLI path)
- [x] Catalog scan (2026-07-03): **14/20** pass `--comfy-check` after repair passes (was 11/20)
  - **4** missing `GetNode` (Comfy subgraph nodes — not in `/object_info`)
  - **1** missing input asset (`141756_OG`)
  - **1** `prompt_wiring` (FB8VA5-laying-down node 422)
  - **14** pass full `--comfy-check` (incl. FB8VB2 after asset remap fixes)
- [ ] Install / enable subgraph `GetNode`/`SetNode` or re-export affected catalog workflows without subgraphs
- [ ] Sync missing catalog input images/videos into Comfy input folders (or re-bind pools)

### D. Commands (copy-paste)

Use `--dev` while wiring pipelines — shortens ~80-frame runs to ~16 frames / 8 steps (~minutes vs 30+ min).

```bash
cd workspace/scripts

curl -s http://127.0.0.1:8188/workflow/convert | python3 -m json.tool

python3 shape_factory.py pool sync --pools ../../.data/pools/FB9_GEX2/pools.yaml

python3 shape_factory.py generate \
  --shape ../../.data/shapes/FB9_GEX2.shape.yaml \
  --pools ../../.data/pools/FB9_GEX2/pools.yaml --pick zip --limit 2 --dev
python3 shape_factory.py submit --family FB9_GEX2 --limit 2

python3 shape_factory.py status --family FB9_GEX2 --wait --deposit --timeout 7200
python3 shape_factory.py timings summary --family FB9_GEX2
python3 shape_factory.py timings compare \
  --baseline ../../.data/shape_factory/jobs/FB9_GEX2/prod.job.json \
  --candidate ../../.data/shape_factory/jobs/FB9_GEX2/optimized.job.json
python3 shape_factory.py jobs repair --family FB9_GEX_FACIAL --refresh-prompts

python3 shape_factory.py generate \
  --shape ../../.data/shapes/FB9_GEX_FACIAL.shape.yaml \
  --pools ../../.data/pools/FB9_GEX_FACIAL/pools.yaml --pick zip --limit 2
python3 shape_factory.py submit --family FB9_GEX_FACIAL --limit 2

python3 shape_factory.py pipeline run \
  --pipeline ../../.data/pipelines/fb9-gex2-to-facial.pipeline.yaml \
  --limit 1 --wait --wait-timeout 7200 --dev
```

```bash
# Batch validate entire catalog (object_info + convert + optional Comfy accept check)
python3 shape_factory.py validate --catalog
python3 shape_factory.py validate --catalog --comfy-check
# Summary: .data/shape_factory/validation/catalog_summary.validate.json

# Managed quarantine (blocks generate/submit for quarantined shape templates)
python3 shape_factory.py quarantine sync --comfy-check
python3 shape_factory.py quarantine patch --catalog --dry-run   # preview LoadImageWithFilename fixes
python3 shape_factory.py quarantine list --status quarantined
python3 shape_factory.py quarantine release --workflow /path/to/workflow.json --note "reviewed missing GetNode"
```

## File map

| Artifact | Path |
|----------|------|
| Shapes | `.data/shapes/{FB9_GEX2,FB9_GEX2_identity_anchor,FB9_GEX_FACIAL,FB9_GEX,FB9-FaceBlast,X-KNEEL-FB9,X-KNEEL-FB9-bare,BounceDanceA,Breast-shake-FB8VA5,FB8VA5-ZOOMOUT,FB8VA4,FB8VB2,ASTONISH_FB9_GEX}.shape.yaml` |
| Pools | `.data/pools/<family>/pools.yaml` |
| Pool index | `.data/pools/<family>/index.json` |
| Pipeline | `.data/pipelines/fb9-gex2-to-facial.pipeline.yaml` |
| Jobs | `.data/shape_factory/jobs/<family>/` |
| Timings ledger | `.data/shape_factory/timings.jsonl` |
| Timings sidecar | `.data/shape_factory/jobs/<family>/*.timings.json` |
| Hourly log | `.data/shape_factory/hourly.log` |
| Best chain manifest | `.data/chains/best-examples.chain.yaml` |
| Ratings index (planned) | `<data>/output/output/_status/ratings_index.json` |
| Lineage edges | `<data>/output/output/_status/discovery_lineage_edges.json` |
| Workflows | `comfyui_user/.../generated/shape_factory/` |

### Catalog → factory promotion (2026-08-11)

Re-ran `quarantine sync --comfy-check` (converter healthy). Promoted six named catalog leftovers to shape factories; smoke `generate --dev --limit 1` ok for each.

| Family | Pattern | Catalog stem | Status |
|--------|---------|--------------|--------|
| `BounceDanceA` | I2V still+prompt | `BounceDanceA` | released + shape/pools |
| `Breast-shake-FB8VA5` | I2V | `Breast-shake-FB8VA5` | released + shape/pools |
| `FB8VA5-ZOOMOUT` | I2V | `FB8VA5-ZOOMOUT` | released + shape/pools |
| `FB8VA4` | I2V | `FB8VA4-2026-01-11-224827_OG_00001` | released + shape/pools |
| `FB8VB2` | I2V | `FB8VB2_2026-01-09_225941_EXT_00001` | released + shape/pools |
| `ASTONISH_FB9_GEX` | V2V source+prompt | `ASTONISH_FB9_GEX_2026-03-03_00023-2` | released + shape/pools |
| `FB8VA5-laying-down` | — | same | **still quarantined** — `prompt_wiring` / dead link to missing node `422` via `ImageScaleBy` `418` |
| `FB8VB2_2026-01-06_…` | — | Jan-06 EXT | **still quarantined** — missing `GetNode` (prefer Jan-09 family above) |

Not promoted (out of scope): numbered `*_OG_*`, `TUNETEST`, `tune-*`. Not enrolled in hourly schedule yet.

### G. Inferred ratings v1 (Discovery-first)

Design: [`docs/RATINGS_V1_PLAN.md`](../docs/RATINGS_V1_PLAN.md)

- [x] `shape_factory ratings build` — scan og XMP + embeds; join jobs/deposits; write `ratings_index.json`
- [x] `shape_factory ratings show` — query by `graph_hash`, source, shape recipe, output relpath
- [x] Reuse / extend `workspace/scripts/correlate_output_ratings.py` (no duplicate XMP logic)
- [x] Discovery API: enrich lineage/library with `rating_explicit`, `rating_inferred`, evidence
- [x] Lineage panel badge on source/workflow nodes
- [x] v1.1: lineage edge fan-out from `discovery_lineage_edges.json` (2-hop, weighted; default on `ratings build`)
- [ ] Later: `best refresh`, hourly manifest sort by `rating_effective` (after index is stable)

**Spot-check targets (verified 2026-07-16):** `FB9_GEX2_2026-04-03_00001` (explicit 5, recipe `FB9_GEX2+backfill`); graph_hash `0ac75522…` (inferred ~4.62, n=107).

### H. Hourly automation (observation lane)

- [x] `scripts/shape_factory_hourly.sh` — deposit, submit `--pending-only`; **fill/generate only when factory pending is empty** and Comfy waiting below min
- [x] `scripts/shape_factory_pending_drain.sh` + timer — push pending onto Comfy about every minute when waiting has room
- [x] `scripts/install-shape-factory-hourly.sh` — systemd user timer
- [x] Timer once/hour at `:30` + archive OG sampling (`HOURLY_ARCHIVE_OG_SHARE`, default 0.20)
- [x] Default manifest: `.data/chains/best-examples.chain.yaml`
- [x] `DEV_CHAIN=0` default — prod frames (~80), not dev-fast (~16)
- [x] `--job-suffix _h<UTC-hour>` for fresh jobs each tick
- [ ] Optional: wire hourly sample pick to `rating_effective` (after G complete)
- [ ] Work Products: stamp superseded interrupted jobs on Re-run (`submit.rerun_job_key` / `rerun_at`)

```bash
tail -f .data/shape_factory/hourly.log
DEV_CHAIN=0 /home/yuji/src/comfyui-runpod/scripts/shape_factory_hourly.sh
systemctl --user list-timers shape-factory-hourly.timer
```

### E. Workflow efficiency (timings foundation)

- [x] Per-phase timings on all shape_factory jobs + `timings.jsonl` ledger
- [x] Workload capture (frames, steps, resolution) for normalized metrics
- [x] Derived efficiency: `exec_sec_per_frame`, `exec_sec_per_step`, `frames_per_min_exec`
- [x] `timings summary --group-by graph_hash` and `timings compare`
- [x] `jobs repair` — sync sidecars; `--refresh-prompts` rebuilds fixed API prompts
- [x] ffprobe output frame count on completion (when ffprobe available)
- [x] Per-node timing scaffold from history.messages
- [ ] Per-node timing validation on real completed history
- [ ] Dashboard / export for A/B optimization sweeps


| Family | prompt_id | source |
|--------|-----------|--------|
| FB9_GEX2 | `7e7745f1-bceb-4e2c-a8ff-11d04cb97f94` | workflow_convert |
| FB9_GEX_FACIAL | `94d93922-71ab-4891-b82f-132930aba0b9` | workflow_convert |
