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
| **experiments_ui/** | React + server | Developed (desktop); tablet/phone + WIP launcher in progress |
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
- **Experiments UI app:** `workspace/experiments_ui/web/`; server: `workspace/scripts/experiments_ui_server.py`.
- **WIP launcher spec:** `workspace/experiments_ui/docs/FEATURE_WIP_TUNE_LAUNCHER.md`.
- **Image sorter:** `workspace/workflows/image_sorting_tools/` (and under `comfyui_user/default/workflows/image_sorting_tools/`).
- **Roundtrip test:** `workspace/tests/test_integration_media_roundtrip.py`; fixtures: `workspace/tests/fixtures/media/`.
- **Custom nodes:** All `workspace/ComfyUI-*` (WanVideoWrapper, Wan22FMLF, VAE-Utils, FSampler, KJNodes, VibeVoice, WanAnimatePreprocess, WanMoEScheduler).
