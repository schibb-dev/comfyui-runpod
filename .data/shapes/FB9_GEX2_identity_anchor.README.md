# FB9_GEX2 identity-anchor workplate

Template extend workflow derived from a **5★ / fast_track** GEX2 extend:

`hourly__prompt_profile-6ec23767562b__source_video-FB9_GEX2_FACIAL_2026-05-11_00004__000_202607140554`

## What changed vs stock GEX2

| Signal | Stock GEX2 | This plate |
|--------|------------|------------|
| CLIP vision image | ColorMatch of source-video frames | **`identity_anchor` still** (LoadImage #500) |
| Wan `start_image` | Last frame of source video | **Unchanged** (keeps extend continuity) |
| ColorMatch #391 | Fed CLIP | **Muted** (bypass; restore if you want old behavior) |

Wan’s native identity lever here is **CLIP-vision** on `WanImageToVideo.clip_vision_output`
([Comfy docs](https://docs.comfy.org/built-in-nodes/WanImageToVideo)): `start_image` initializes
early frames; `clip_vision_output` adds vision conditioning for subject look. That matches how
current Wan Animate / I2V plates lock a reference still (encode with CLIP ViT‑H, feed identity
embeds) while motion comes from elsewhere.

## Identity-assist landscape (what the web is doing)

For **this GEX2 / native `WanImageToVideo` stack**, CLIP-vision on a clean still is the right
first move — same idea as Wan2.2 Animate “reference image → CLIP encode → identity embeds”
workflows on RunComfy.

Stronger (but different stack / new weights) options if CLIP-only is not enough:

| Approach | What it does | Fit for our extend plate |
|----------|--------------|---------------------------|
| **CLIP vision still → `clip_vision_output`** | Native Wan I2V identity cue | **What this plate does** |
| **Wan2.2 Animate + CLIP + pose/face** | Motion from driver video, ID from still | Different graph (Animate), not GEX2 v2v extend |
| **Stand-In LoRA** ([RunComfy](https://www.runcomfy.com/comfyui-workflows/wan2-1-stand-in-in-comfyui-character-consistent-video-workflow)) | Identity LoRA + `WanVideoAddStandInLatent` | Needs WanVideoWrapper path + Stand-In weights |
| **ByteDance Lynx** ([tutorial](https://www.stablediffusiontutorials.com/2025/10/wan2.1-lynx.html)) | Face-ID IP/ref layers on Wan 2.1 | Needs Lynx IP/ref/resampler models + wrapper nodes |
| **IPAdapterWAN** ([kaaskoek232](https://github.com/kaaskoek232/IPAdapterWAN)) | Sampling-time attention ID inject | Extra custom node; not in stock GEX2 |
| Classic **InstantID / PuLID** | Image face ID (SD/FLUX era) | Poor fit for native Wan GEX2; keep for still gens |

Practical tips echoed across those plates: use a **well-lit, uncropped face / three-quarter**,
minimal occlusion; keep `start_image` from the **clip being extended** so motion continuity
stays, and put the **identity still only on CLIP-vision** (or a dedicated ID adapter).

## Open in Comfy

`/home/yuji/comfyui-runpod-data/comfyui_user/default/workflows/generated/catalog/FB9_GEX2_identity_anchor-readable.json`

1. Set **INPUT: identity anchor** to a clear still of the subject.
2. Set **VHS Load Video** to the clip you want to extend (prefilled with the parent of the source job).
3. Keep / tweak the prompt profile.
4. Queue a short test, then register `graph_hash` into the shape yaml if you want factory generate.

## Factory wiring (draft)

- Shape: `/home/yuji/src/comfyui-runpod/.data/shapes/FB9_GEX2_identity_anchor.shape.yaml`
- Pools: `/home/yuji/src/comfyui-runpod/.data/pools/FB9_GEX2_identity_anchor/pools.yaml`
- Candidate: `/home/yuji/src/comfyui-runpod/.data/template_candidates/FB9_GEX2_identity_anchor.candidate.json`

`graph_hash` may show `PENDING_…` until you open/save once in Comfy or run snowflake fingerprint on the API-format graph.

## Extend hook note

`advance.extend` / Workbench Extend now **auto-binds** `identity_anchor` when you target this family:

1. Explicit `identity_anchor` / `source_still` on the request (optional override)
2. Existing still binding on the seed job (`source_still` or `identity_anchor`)
3. Embedded `LoadImage` still recovered from the clip / parent lineage (FaceBlast-style)

In Workbench: pick **Extend → `FB9_GEX2_identity_anchor`**. If lineage has no recoverable still, pass one or the queue will error with `missing_identity_still`.

Manual generate with all three slots bound still works; Factory Map shows the `identity_anchor` binding when present.

## LoadImage / input staging

Comfy `LoadImage` (#500) only resolves under the input root. Identity anchors that
live under `output/og/…` are **staged** at generate/submit into
`input/_factory/<content_id>.png` (hardlink → symlink → copy). See
`docs/ASSET_LIFECYCLE_PLAN.md` (interim factory LoadImage staging).
