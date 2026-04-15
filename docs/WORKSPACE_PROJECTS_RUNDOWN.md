# Workspace projects & directories – extended rundown

This document extends the workflows-focused rundown to **all of `workspace/`** and subdirectories. It identifies what looks like **developed projects**, **in-progress work**, **third-party/custom nodes**, and **runtime/config** areas.

---

## 1. Top-level workspace layout

| Directory | Purpose |
|-----------|---------|
| **scripts/** | ComfyUI workflow tooling (extract metadata, presets, tune experiments, watch queue, etc.). Entrypoint: `comfy_tool.py`. |
| **ws_scripts/** | Typically a symlink or copy of `scripts/` for container/runtime use (e.g. `/workspace/ws_scripts` in Docker). |
| **workflows/** | ComfyUI workflow JSONs + a few project folders (image_sorting_tools, ponyflow, WAN 2.1, current/, legacy/). See below. |
| **experiments_ui/** | React dashboard + Python server for browsing tuning experiments, queue, status. |
| **tests/** | Integration tests (e.g. media roundtrip) and fixtures. |
| **ComfyUI/** | ComfyUI install (clone or symlink; runtime). |
| **comfyui-wan** | Wan-related ComfyUI or native Wan install (runtime). |
| **comfyui_user/** | ComfyUI user data (settings, default workflows; includes copy of `workflows/image_sorting_tools` etc.). |
| **input/, output/, models/, credentials/** | Runtime data (inputs, generated outputs, model files, tokens). |
| **_tmp/, .cache/** | Temp and cache. |
| **ComfyUI-*** | Custom node packages (see §3). |

---

## 2. Core “developed” projects (repo-owned)

### 2.1 Workspace tooling (`scripts/` + `workspace/README.md`)

- **What it is:** Python toolkit for making ComfyUI workflows/media reproducible and Git-friendly.
- **Features:** Extract metadata/presets from PNG/MP4, clean workflow JSONs, apply presets, XMP sidecars, **tune experiments** (generate/run/materialize/apply), **watch_queue** (submit + collect histories). Single entrypoint: `comfy_tool.py`.
- **Status:** **Developed** – documented in `workspace/README.md`, used by ops containers and experiments UI.

### 2.2 Experiments UI (`experiments_ui/`)

- **What it is:** React app + Python server (`scripts/experiments_ui_server.py`) to browse experiments, view queue/status, preview outputs.
- **Contents:** `experiments_ui/web/` (React/Vite), `experiments_ui/dist/` (build output), `experiments_ui/docs/` (VIEWPORT_DEVICES.md, FEATURE_WIP_TUNE_LAUNCHER.md).
- **Status:** **Developed** (desktop); **in progress**: tablet/phone targets (VIEWPORT_DEVICES.md), **WIP tune launcher** (FEATURE_WIP_TUNE_LAUNCHER.md – browse WIP output, pick videos, create experiments from UI).

### 2.3 Integration tests (`tests/`)

- **What it is:** End-to-end test for media roundtripping (`test_integration_media_roundtrip.py`), plus fixtures.
- **Contents:** `tests/fixtures/media/` (README.md explains format: MP4+PNG pairs or manifest.json), test runs `process_wip_dir.py` and `check_roundtrip_dir.py`.
- **Status:** **Developed** – documented, skips cleanly when ffprobe or fixtures missing.

### 2.4 Workflows – project-like folders (see earlier rundown)

- **image_sorting_tools/** – CLIP-based image sorter (scripts, config, README, IMAGE_SORTER_GUIDE). **Developed.**
- **ponyflow_v2/**, **ponyflowPonyIllustriousSDXL_v3/ponyflow_v3/** – Pony/SDXL workflows with READMEs. **Documented (external-style).**
- **wan21I2vNativeGGUFSelf_V21Singlesamp/**, **wan21I2vNativeGGUFSelf_V21Dualsamp/** – WAN 2.1 single/dual sampling variants. **Developed (your workflow sets).**
- **current/** – Organized “in use” workflows (video-generation FaceBlast8*, flux-generation, experimental). **Developed / in use.**
- **legacy/archived/** – Old workflows. **Reference only.**

---

## 3. Custom nodes (`ComfyUI-*`)

These live under `workspace/` (and are likely mounted or linked into ComfyUI’s `custom_nodes` at runtime). All are **third-party** repos; some describe themselves as “work in progress.”

| Package | Purpose | Notes |
|---------|---------|--------|
| **ComfyUI-WanVideoWrapper** | WAN video wrapper nodes for Wan2.1 and related models. | README: “WORK IN PROGRESS (perpetually)”; personal sandbox, VRAM/memory notes. |
| **ComfyUI-Wan22FMLF** | Multi-frame reference conditioning for Wan2.2 A14B I2V. | README in Chinese + English; SVI PRO continuity optimizations, image-selection node. |
| **ComfyUI-WanAnimatePreprocess** | Helper nodes for Wan 2.2 Animate preprocessing (ViTPose, face crops, SAM2). | Short readme; model paths. |
| **ComfyUI-WanMoEScheduler** | Scheduler that finds optimal `shift` for high/low step boundary (WAN, etc.). | Documented features, install, concepts. |
| **ComfyUI-VAE-Utils** | Load/use VAEs not in base ComfyUI (e.g. Wan upscale VAE). | spacepxl; install + nodes. |
| **ComfyUI-FSampler** | FSampler – fast skips via epsilon extrapolation (training-free acceleration). | arXiv, changelog, compatibility notes; web/docs/FSampler.md. |
| **ComfyUI-KJNodes** | QoL and masking nodes. | README: “still work in progress, like everything else.” |
| **ComfyUI-VibeVoice** | Microsoft VibeVoice integration for expressive long-form audio. | Third-party (wildminder). |
| **ComfyUI-WanVideoWrapper** | (Duplicate listing; same as first.) | — |

None of these are “your” in-house projects; they are dependencies that extend ComfyUI for WAN, FLUX, audio, etc.

---

## 4. In-progress / planned work

| Item | Location | Description |
|------|-----------|-------------|
| **WIP tune launcher** | `experiments_ui/docs/FEATURE_WIP_TUNE_LAUNCHER.md` | Feature spec: browse WIP dir, select videos, set tune params, create experiments from UI. Backend: GET /api/wip, POST /api/create-experiment. Frontend: WipBrowser, TuneParamsForm, “New from WIP” section. |
| **Viewport / device targets** | `experiments_ui/docs/VIEWPORT_DEVICES.md` | Desktop (current), tablet and phone (later); breakpoints and hooks in place. |
| **Resubmit / replay / extend** | (spec: this section §4.1) | **Next MVP (planned):** from a visible artifact, queue Comfy again with sensible defaults; optional overrides (seed, prompts, etc.); **extend** = same pipeline but a **user-picked extend template** workflow with the **current video** as starter (not the asset’s embedded graph). Quick access to a **small pinned list** (2–3 templates). |

### 4.1 Resubmit, replay, extend — product intent and evolution

**Near-term MVP (in scope):**

- **Replay (same workflow):** Re-queue “this workflow with these settings again” for a given image/video artifact, with **defaults = true replay** (one click submit).
- **Overrides in one activity:** Same UI path; advanced fields optional (seed, prompts, parameters) so “replay” does not require a separate product verb unless we later want one.
- **Extend:** Same resubmit primitive: **template workflow** (from a short pinned list) + **current video as input**; not the embedded workflow from the asset.
- **Compatibility:** **Liberal pairing** for MVP—do **not** maintain a full asset↔workflow matrix up front. **Fail fast:** clear errors when Comfy or the wrapper rejects a run; surface mismatches so they inform later design.
- **Logging:** Record template id, artifact type/path, success/fail, and error strings to guide the next phase.

**Explicitly later (out of MVP):**

- **Collections / buckets / lists** of assets for pickers and batch input.
- **Orchestration** (A→B→C workflows across buckets) — needs durable run state and stronger compatibility rules.
- **Workflow analysis / “smart” validation:** Scan workflow JSON (at queue time or in a **cached configuration library**) to infer **input requirements**, inject **validation or prompt steps** for missing data, and optionally block or warn before submit. **Tension:** if analysis is **fast and reliable**, run it at queue or config time; if it is **slow or fragile**, persist **precomputed** workflow profiles (inputs, types, node hints) and version them with the template. **Mismatches observed in MVP** (via fail-fast errors + logs) should drive **which** of those directions to invest in first.

**Doc hygiene:** When this ships, add a short feature note under `workspace/experiments_ui/docs/` (e.g. `FEATURE_RESUBMIT.md`) and link it from this table.

### 4.2 Post–feature-spike retrospectives (habit)

After each **MVP-sized feature spike** (e.g. resubmit/replay/extend), do a **short retrospective** before moving on—no ceremony required, just a durable note:

- **What shipped** (PR or commit range) and **what was explicitly deferred**
- **What we learned** (Comfy/API/UI—especially surprises and fail-fast errors)
- **What we are not deciding yet** (so discovery stays honest)

Capture it in the feature doc, an issue comment, or a few bullets at the bottom of this section’s linked `FEATURE_*.md`. Goal: **reversible memory** when the next spike reopens the same files.

---

## 5. Runtime / config (not “projects”)

- **ComfyUI, comfyui-wan** – ComfyUI (and Wan) installs.
- **comfyui_user/** – User defaults; includes copies of workflow folders (e.g. `image_sorting_tools` with `sorter_config.yaml`).
- **input/, output/, models/, credentials/** – Runtime data.
- **_tmp/, .cache/** – Temp/cache.
- **ws_scripts/** – Scripts as seen by containers (same content as `scripts/` in practice).

---

## 6. Summary table (workspace-wide)

| Area | Type | Status |
|------|------|--------|
| **scripts/** + workspace README | Repo tooling | Developed |
| **experiments_ui/** | React + server | Developed (desktop); tablet/phone + WIP launcher in progress; **resubmit/replay/extend** (see §4.1) planned |
| **tests/** | Integration tests | Developed |
| **workflows/image_sorting_tools** | Image sorter toolkit | Developed |
| **workflows/ponyflow_*** | Pony workflows | Documented (external-style) |
| **workflows/wan21*Singlesamp, *Dualsamp** | WAN 2.1 workflow sets | Developed (yours) |
| **workflows/current/** | Active workflows | In use / developed |
| **workflows/legacy/** | Archive | Reference |
| **ComfyUI-*** (all) | Custom nodes | Third-party (some WIP) |
| **FEATURE_WIP_TUNE_LAUNCHER.md** | Spec | In progress |
| **VIEWPORT_DEVICES.md** | Spec | Desktop done; tablet/phone later |

---

## 7. Quick reference: where things live

- **Experiment/tune tooling:** `workspace/scripts/` (and `comfy_tool.py`).
- **Experiments UI app:** `workspace/experiments_ui/web/`; server: **`scripts/experiments_ui_server.py`** (repo root; container path `$WORKSPACE_PATH/scripts/` when that bind mount is used).
- **WIP launcher spec:** `workspace/experiments_ui/docs/FEATURE_WIP_TUNE_LAUNCHER.md`.
- **Resubmit / replay / extend (plan):** `docs/WORKSPACE_PROJECTS_RUNDOWN.md` §4.1 (future feature note: `workspace/experiments_ui/docs/FEATURE_RESUBMIT.md` when implemented).
- **Image sorter:** `workspace/workflows/image_sorting_tools/` (and under `comfyui_user/default/workflows/image_sorting_tools/`).
- **Roundtrip test:** `workspace/tests/test_integration_media_roundtrip.py`; fixtures: `workspace/tests/fixtures/media/`.
- **Custom nodes:** All `workspace/ComfyUI-*` (WanVideoWrapper, Wan22FMLF, VAE-Utils, FSampler, KJNodes, VibeVoice, WanAnimatePreprocess, WanMoEScheduler).
