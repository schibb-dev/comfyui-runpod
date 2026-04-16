# Project organization proposal: independent projects outside workspace

This document suggests a way to organize **your** projects and ideas into **independently developed** units that **do not live inside `workspace/`**. `workspace/` stays a **runtime mount**: ComfyUI, user data, input/output, models, credentials. Projects become separate repos (or sibling directories) that the runpod stack **uses** rather than **contains**.

---

## Recommended: Subrepos (git submodules)

**Recommendation: use separate repos and add them as git submodules.** This keeps runpod configs minimal and makes each project independently developable, versionable, and shareable.

Benefits:
- **Runpod repo stays small:** Only Dockerfile, docker-compose, entrypoint, custom_nodes config, and a small number of scripts that wire subrepos into the container. No Python/React/workflow source to maintain here.
- **Clear boundaries:** Each subrepo has its own CI, dependencies, and release cadence. Changes to the image sorter don’t touch runpod or the pipeline.
- **Easier upgrades:** Update a submodule to a specific tag or commit; runpod just points at it. No copying code between repos.
- **Cleaner config management:** Runpod’s docker-compose and Dockerfile only reference paths like `projects/experiment-pipeline` or copy from them at build time. No `workspace/scripts` mixed with runtime data.

The rest of this doc describes the five projects (A–E) and then **§7** spells out the **clean runpod layout with submodules** and **§8** the **runpod-only config surface**.

---

## 1. Principles

- **Workspace = runtime only:** Input, output, models, credentials, ComfyUI install, user settings. No source-code “projects” here; only data and config produced at runtime.
- **Projects = independently developable:** Each has its own repo (or clear top-level folder), README, tests, versioning. Dependencies between projects are explicit (e.g. “Experiments UI calls Experiment Pipeline API”).
- **Runpod repo = infra + integration:** Dockerfile, docker-compose, entrypoint, and (with subrepos) submodule references only. No project source code; runpod stays config-focused.

---

## 2. Proposed independent projects

### Project A: **ComfyUI Experiment Pipeline** (Python)

**What it is:** All Python tooling for ComfyUI workflow reproducibility and the experiment/tune/queue pipeline.

**Scope:**
- Extract metadata/presets from PNG/MP4, clean workflows, apply presets, XMP sidecars.
- Tune experiments: `tune_experiment.py` (generate, run, materialize, apply).
- Queue: `watch_queue.py`, `experiment_queue_manager.py`, `queue_incomplete_experiments.py`, `refresh_run_status.py`, `report_experiment_queue_status.py`, `experiment_run_queue_rules.py`.
- ComfyUI helpers: `comfy_tool.py`, `extract_comfy_metadata.py`, `extract_comfy_preset.py`, `clean_comfy_workflow.py`, `apply_comfy_preset.py`, `process_wip_dir.py`, `check_roundtrip_dir.py`, `check_wip_agreement.py`, `update_comfy_seed_xmp.py`, `canonicalize_*.py`, `comfyui_submit.py`, `comfy_meta_lib.py`, etc.
- **Experiments UI server:** `experiments_ui_server.py` (serves API + static UI build; belongs here because it’s the API for the pipeline).
- Tests that only need this code: e.g. media roundtrip (`tests/test_integration_media_roundtrip.py`, `tests/fixtures/media/`).

**Not in scope:** React app (that’s Project B), image sorter (Project C), workflow JSON bundles (Project D).

**Suggested name / location:**  
- **Recommended:** New repo `comfyui-experiment-pipeline`; add as submodule at `projects/experiment-pipeline` in runpod.

**Deliverable:** Installable or runnable from a single root (e.g. `python -m experiment_pipeline.comfy_tool` or `pip install -e .`). Docker/runpod mounts this root as `/workspace/ws_scripts` or installs it into the image.

**Depends on:** Nothing (pure Python + ComfyUI API). Experiments UI (B) depends on it (API contract).

---

### Project B: **Experiments UI** (React + API contract)

**What it is:** Frontend for browsing experiments, queue, status, and (future) WIP browser + tune launcher.

**Scope:**
- React app: `experiments_ui/web/` (Vite, components, viewport/device logic).
- Build output: `experiments_ui/dist/` (generated; gitignore or commit as deployable).
- Feature/spec docs: `experiments_ui/docs/` (VIEWPORT_DEVICES.md, FEATURE_WIP_TUNE_LAUNCHER.md).
- **No** Python server here: the server is part of Project A and serves this app’s static files + API.

**Planned (see `docs/WORKSPACE_PROJECTS_RUNDOWN.md` §4.1):** **Resubmit / replay / extend** from visible artifacts (liberal template↔asset pairing, **fail-fast** errors, logging). Post-MVP: optional **workflow analysis** (static scan vs cached “workflow profiles”) to infer inputs and guide validation—driven by mismatches observed in production use.

**Process:** After each MVP spike, a **short retrospective** (see rundown **§4.2**) so UI-led discovery leaves a written trail for A/B contract tweaks.

**API contract:** Experiments UI talks to the server (Project A) over HTTP (e.g. `/api/experiments`, `/api/queue`, `/files/`, and future `/api/wip`, `/api/create-experiment`). Document this contract (OpenAPI or a short markdown) so A and B can evolve independently.

**Suggested name / location:**  
- **Recommended:** New repo `comfyui-experiments-ui`; add as submodule at `projects/experiments-ui` in runpod.

**Deliverable:** `npm run build` → static assets. Runpod/Project A serves these from the server’s static path.

**Depends on:** Experiment Pipeline (A) for API and serving.

---

### Project C: **Image content sorter** (standalone)

**What it is:** CLIP-based image categorization/clustering and optional query search.

**Scope:**
- Scripts: `image_content_sorter.py`, `advanced_image_sorter.py`.
- Config: `sorter_config.yaml`.
- Docs: README, IMAGE_SORTER_GUIDE.md.
- Launcher: `sort_images.bat`, `requirements_sorter.txt`.

**Why independent:** No dependency on ComfyUI or the experiment pipeline. Useful for any image library (e.g. sorting WIP outputs, or general photo sets). Can be developed and versioned on its own.

**Suggested name / location:**  
- **Recommended:** New repo `image-content-sorter`; add as submodule at `projects/image-content-sorter` in runpod.

**Deliverable:** `pip install -r requirements_sorter.txt` and run scripts; config path as CLI or env. No need to live under “workflows”; it’s a general-purpose tool that you might **use** from workflows or from the shell.

**Depends on:** None. Optional: document “use from ComfyUI” if you keep a small ComfyUI workflow that points at a sorted output dir.

---

### Project D: **ComfyUI workflow templates** (assets)

**What it is:** Your workflow JSON bundles and short READMEs—no heavy code, just assets and docs.

**Scope:**
- WAN 2.1: `wan21I2vNativeGGUFSelf_V21Singlesamp/`, `wan21I2vNativeGGUFSelf_V21Dualsamp/`.
- FaceBlast / video: `current/video-generation/` (FaceBlast8*, Wan video, etc.).
- Other current: `current/flux-generation/`, `current/character-generation/`, `current/experimental/`.
- Legacy: `legacy/archived/` (reference).
- Optional: PonyFlow v2/v3 if you treat them as “your” curated copies; otherwise leave as third-party refs.

**Why independent:** Version and share workflow sets without mixing them with Python or React. Runpod (or any ComfyUI setup) can mount this repo as a read-only “workflow library” (e.g. `workflows/` in the container or on the host) or copy selected folders into workspace at deploy time.

**Suggested name / location:**  
- **Recommended:** New repo `comfyui-workflows`; add as submodule at `projects/workflows` in runpod.

**Deliverable:** Clear directory layout + README per bundle. No Python/Node deps. ComfyUI and Experiment Pipeline reference paths like `workflow-templates/wan21...` or a mounted path.

**Depends on:** None. Consumed by Runpod/ComfyUI as files.

---

### Project E: **ComfyUI Runpod** (infra only)

**What it is:** Docker and runtime wiring. No “project” source code here—only how to run ComfyUI and the pipeline. **With subrepos (recommended), this repo only holds config and submodule pointers.**

**Scope:**
- Dockerfile, docker-compose.yml, entrypoint.sh.
- Custom nodes config: `custom_nodes.yaml` (and optionally a script that clones ComfyUI-* from a list).
- .env.example, docs (CREDENTIALS.md, this proposal, rundowns).
- Optional: thin PowerShell/shell helpers (ops_up.ps1, etc.) that only invoke docker compose or containers.
- **Workspace:** A **mount** only—runtime dirs (input, output, models, credentials, comfyui_user). No source; not committed as project code. See §7–§8 for the exact layout.

**Depends on:** Projects A–D as git submodules under `projects/` (see §7).

---

## 3. Where things live after reorganization

| Item | Current location | Proposed location |
|------|------------------|-------------------|
| Experiment/tune/queue Python scripts | `workspace/scripts/` | **Project A** `experiment-pipeline/` (or repo root) |
| Experiments UI server | `scripts/experiments_ui_server.py` (repo root) | **Project A** (server) + **Project B** (React app) |
| Experiments UI React app | `workspace/experiments_ui/` | **Project B** `experiments-ui/` |
| Roundtrip tests + fixtures | `workspace/tests/` | **Project A** `experiment-pipeline/tests/` |
| Image sorter | `workspace/workflows/image_sorting_tools/` | **Project C** `image-content-sorter/` |
| WAN 2.1 / FaceBlast / current / legacy workflows | `workspace/workflows/` | **Project D** `workflow-templates/` |
| Docker, compose, entrypoint, custom_nodes.yaml | Repo root | **Project E** comfyui-runpod (unchanged) |
| Runtime data (input, output, models, credentials, comfyui_user) | `workspace/` | **workspace/** (unchanged; mount only, no project source) |
| ComfyUI-* custom nodes | `workspace/ComfyUI-*` | Either stay in workspace as “installed deps” or move to a separate `custom_nodes/` at repo root and mount into container |

---

## 4. Dependency graph

```
Project E (Runpod)
  └── mounts / installs Project A (pipeline)
  └── optionally mounts Project D (workflow-templates) as workflow library
  └── Project A serves Project B’s build (Experiments UI)

Project B (Experiments UI)
  └── depends on Project A (API + static file serving)

Project A (Experiment Pipeline)
  └── no project dependencies (only ComfyUI server and filesystem)

Project C (Image sorter)
  └── no dependencies

Project D (Workflow templates)
  └── no dependencies (consumed as files)
```

---

## 5. Migration steps (subrepos path)

1. **Create the four project repos** (e.g. on GitHub/GitLab): `comfyui-experiment-pipeline`, `comfyui-experiments-ui`, `image-content-sorter`, `comfyui-workflows` (or `comfyui-workflow-templates`).
2. **Populate experiment-pipeline repo:** Move `workspace/scripts/` and `workspace/tests/` into it. Include **`scripts/experiments_ui_server.py`** from the repo root (or move it with the pipeline). Add README, optional `pyproject.toml` or `setup.py`. Commit and push.
3. **Populate experiments-ui repo:** Move `workspace/experiments_ui/web/` and `workspace/experiments_ui/docs/` (and optionally `dist/` or add to .gitignore). Add README, package.json. Commit and push.
4. **Populate image-content-sorter repo:** Move `workspace/workflows/image_sorting_tools/` contents to repo root. Add README. Commit and push.
5. **Populate workflows repo:** Move `workspace/workflows/` WAN 2.1 folders, `current/`, `legacy/` into it. Add a top-level README. Commit and push.
6. **In comfyui-runpod:** Add submodules, e.g.  
   `git submodule add <url-experiment-pipeline> projects/experiment-pipeline`  
   and similarly `projects/experiments-ui`, `projects/image-content-sorter`, `projects/workflows`. Create `projects/` if you prefer that grouping.
7. **Trim runpod repo:** Remove from `workspace/` all code that now lives in subrepos. Keep workspace as runtime-only (see §8). Update Dockerfile and entrypoint to copy or mount from `projects/experiment-pipeline` (and optionally `projects/workflows`) into the image or container.
8. **Document:** Runpod README lists submodules and `git submodule update --init --recursive`. Each subrepo has its own README. Update `docs/WORKSPACE_PROJECTS_RUNDOWN.md` to state workspace is runtime-only and link to this proposal.

---

## 6. Summary (subrepos)

| Project | Purpose | Repo name (suggested) | Submodule path in runpod |
|---------|---------|------------------------|---------------------------|
| **A. Experiment Pipeline** | Python: tune, queue, metadata, Experiments UI server | `comfyui-experiment-pipeline` | `projects/experiment-pipeline` |
| **B. Experiments UI** | React app for experiments/queue/WIP launcher | `comfyui-experiments-ui` | `projects/experiments-ui` |
| **C. Image content sorter** | CLIP-based image sorting | `image-content-sorter` | `projects/image-content-sorter` |
| **D. Workflow templates** | Your ComfyUI workflow JSON bundles | `comfyui-workflows` | `projects/workflows` |
| **E. ComfyUI Runpod** | Docker + entrypoint + workspace mount | `comfyui-runpod` (this repo) | — |

---

## 7. Clean runpod repo layout (submodules)

With subrepos, the **comfyui-runpod** repo stays minimal. Suggested layout:

```
comfyui-runpod/
├── .gitmodules                 # submodule definitions
├── Dockerfile                  # build image; COPY from projects/experiment-pipeline, projects/experiments-ui
├── docker-compose.yml          # compose; mount workspace, optional project mounts
├── entrypoint.sh               # startup; no project source here, only env/wiring
├── custom_nodes.yaml           # list of ComfyUI custom nodes (or script that clones them)
├── credentials/                # optional: example or mount point; real secrets in .env / workspace
├── .env.example
├── README.md                   # how to clone with submodules, how to build/run
├── docs/                       # runpod-specific docs (this proposal, rundowns, troubleshooting)
│   ├── PROJECT_ORGANIZATION_PROPOSAL.md
│   ├── SCHEDULED_AND_CONTAINER_JOBS_RUNDOWN.md
│   └── ...
├── scripts/                    # runpod-only helpers (optional, keep small)
│   ├── ops_up.ps1              # docker compose --profile ops up
│   ├── ops_down.ps1
│   ├── queue_incomplete_experiments.ps1   # thin wrappers that call into container or project A
│   ├── refresh_run_status.ps1
│   └── report_experiment_queue_status.ps1
├── platform/                   # optional: e.g. Windows GPU monitor (if you keep it here)
│   └── windows/
│       └── gpu-monitor/
└── projects/                   # submodules only; no hand-edited code here
    ├── experiment-pipeline/    # submodule → comfyui-experiment-pipeline
    ├── experiments-ui/         # submodule → comfyui-experiments-ui
    ├── image-content-sorter/   # submodule → image-content-sorter
    └── workflows/              # submodule → comfyui-workflows
```

**Workspace** is **not** in the repo as source. It is a host directory (or a named volume) that you mount at runtime, containing only:

- `input/`, `output/`, `models/`, `credentials/`, `comfyui_user/`
- Optionally `ComfyUI/`, `comfyui-wan/`, and `ComfyUI-*` if you still install custom nodes there; or supply them via Dockerfile/entrypoint from a separate `custom_nodes/` in runpod that clones from `custom_nodes.yaml`.

**Dockerfile** can:

- `COPY projects/experiment-pipeline /workspace/ws_scripts` (or install as package).
- Build Experiments UI: `RUN cd projects/experiments-ui && npm ci && npm run build` then copy `dist/` to a path the pipeline server serves.
- Optionally `COPY projects/workflows /workspace/workflow-templates` so the container has a read-only workflow library.

**docker-compose.yml** then:

- Mounts the host **workspace** (runtime dirs) to `/workspace` (or similar).
- No need to mount project source unless you want live-reload; for production, it’s all in the image.

---

## 8. Runpod config surface (what to manage here)

To keep runpod configs clean, **only the following** are maintained in the comfyui-runpod repo:

| What | Where | Purpose |
|------|--------|--------|
| **Dockerfile** | Repo root | Base image, install deps, copy submodules (pipeline, UI build, optional workflows), custom_nodes bootstrap. |
| **docker-compose.yml** | Repo root | Services (comfyui, watch_queue, ops profile), volumes (workspace mount, models), env, ports. |
| **entrypoint.sh** | Repo root | ComfyUI startup, node bootstrap, optional Experiments UI server start; no business logic. |
| **custom_nodes.yaml** | Repo root | List of ComfyUI-* nodes to clone; entrypoint or build script reads this. |
| **.env.example** | Repo root | Document env vars (tokens, paths, EXPERIMENTS_UI, etc.); real .env gitignored. |
| **README.md** | Repo root | Clone with `git submodule update --init --recursive`, build/run, link to subrepos. |
| **docs/** | Repo root | Runpod-specific docs (this proposal, scheduled jobs, troubleshooting). |
| **scripts/*.ps1** (optional) | `scripts/` | Thin wrappers for docker compose or calling into containers; no pipeline/UI logic. |
| **platform/** (optional) | Repo root | e.g. Windows GPU monitor if you keep it in this repo. |

Everything else (pipeline code, UI code, image sorter, workflow JSONs) lives in **subrepos**. Updating runpod “config” means editing Dockerfile, compose, entrypoint, and docs—not touching Python or React. That keeps runpod config management clean and focused.

---

## 9. Deferred: open the **Comfy UI** graph from Experiments UI (“launch workflow”)

### Goal

From **Experiments UI** (Discovery / Comfy tab), coordinate with the **already-running ComfyUI instance** so the **canvas** loads a workflow **as if you had opened it yourself** in that browser session (e.g. PNG-with-embedded-workflow or UI-format graph), not only queue an API-format `prompt`.

### Summary assessment

| Path | Status |
|------|--------|
| **Queue execution** via Comfy’s **`POST /prompt`** with API-format JSON | Supported; Experiments UI / server already use this pattern. |
| **Inject or replace the graph in the Comfy frontend** from another tab or app via a **small, stable HTTP API** | **Not** a first-class, documented Comfy feature today. The SPA owns editor state; loading a file is normally user-driven (open file, drag PNG, etc.). |
| **Open Comfy in a new tab with query params** (`?workflow=…`, `?asset_url=…`) | Only useful if **custom** code runs in Comfy’s frontend (fork, extension, bookmarklet) or a **local helper** interprets those params and talks to the editor. |

So coordinating **“load into the current Comfy UI”** is **integration-heavy**: it needs agreement with whatever Comfy build you run (CORS if the browser calls Comfy directly, or a backend that can reach Comfy and some channel the UI listens on). There are **no standard API hooks** today that mean “replace current graph with this workflow JSON / this PNG URL” the way `/prompt` means “run this API prompt.”

### Plan: **punt for now**

- **Ship / keep** affordances that do **not** require Comfy UI cooperation: e.g. link-with-hints, server-side embed/load, **Send to Comfy** for execution.
- **Defer** first-class **“launch into editor”** until one of: a **custom Comfy frontend hook** or extension, a **documented internal** API if upstream adds one, **desktop automation** (last resort), or a **narrow contract** you own (e.g. same-origin proxy + small script in Comfy’s `web/`).

### When revisiting

Record the chosen contract (query param names, who fetches `asset_url`, same-origin vs server-mediated) in **Project B** docs or a short **A↔B API** note so the Experiments UI and Comfy build stay in sync.
