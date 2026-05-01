# Documentation index

This file is the **entry point** for human-readable documentation in this repository. The main narrative for setup and day-to-day use is still **[README.md](README.md)**; use this page to find everything else.

## Tooling quick reference (not duplicated here)

Authoritative command lists live in the repo itself:

| Surface | How to list commands |
|--------|----------------------|
| **npm** | Run `npm run` from the repo root (see `package.json` `scripts`). Default Docker workflows use `docker-compose.yml` plus `docker-compose.output-sftp.yml` for the SFTP sidecar. |
| **Make** | Run `make help` (see `Makefile`). Mirrors many npm scripts. |
| **Compose** | `docker-compose.yml` — core stack and ops profile sidecars. `docker-compose.output-sftp.yml` — read-only SFTP to a host output tree. |

Use **`npm run up:minimal`** or **`make up-minimal`** only if you intentionally skip the SFTP container.

---

## Start here

| Document | What it is |
|----------|------------|
| [README.md](README.md) | Main project guide: Quick Start, Docker, Experiments UI, WSL cutover, ops runbook, tests. |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Common failures (nodes, Docker, Experiments UI ports, etc.). |
| [CREDENTIALS.md](CREDENTIALS.md) | Where tokens live and how they are loaded (gitignored paths). |
| [RUNPOD.md](RUNPOD.md) | Deploying on RunPod (image, volumes, env). |

---

## Deployment, Docker, registry

| Document | What it is |
|----------|------------|
| [RUNPOD.md](RUNPOD.md) | RunPod deployment and volume/env conventions. |
| [BASE_IMAGE_INFO.md](BASE_IMAGE_INFO.md) | Notes on the base ComfyUI / image pinning. |
| [DOCKER_HUB_SETUP.md](DOCKER_HUB_SETUP.md) | Docker Hub setup notes. |
| [DOCKER_HUB_RESOLVED.md](DOCKER_HUB_RESOLVED.md) | Follow-up / resolution notes. |
| [DOCKER_HUB_CORRECTED.md](DOCKER_HUB_CORRECTED.md) | Corrections / iterations. |
| [DOCKER_HUB_FINAL.md](DOCKER_HUB_FINAL.md) | Final-state notes (historical thread). |

---

## GPU

| Document | What it is |
|----------|------------|
| [GPU_CONFIGURATION_GUIDE.md](GPU_CONFIGURATION_GUIDE.md) | Multi-GPU / layout configuration. |
| [GPU_SELECTION_GUIDE.md](GPU_SELECTION_GUIDE.md) | Choosing / assigning GPUs. |
| [platform/windows/gpu-monitor/README.md](platform/windows/gpu-monitor/README.md) | Windows GPU monitor utility in this repo. |

---

## Models, WAN, GGUF

| Document | What it is |
|----------|------------|
| [WAN_INSTALLATION_COMPLETE.md](WAN_INSTALLATION_COMPLETE.md) | WAN installation summary. |
| [WAN_MODELS_COMPLETE.md](WAN_MODELS_COMPLETE.md) | WAN model inventory / completion notes. |
| [GGUF_MODEL_FIX_COMPLETE.md](GGUF_MODEL_FIX_COMPLETE.md) | GGUF loading fixes. |
| [SETUP_COMPLETE.md](SETUP_COMPLETE.md) | One-time setup completion notes (includes automatic downloads). |

---

## Workspace, workflows, media tooling

| Document | What it is |
|----------|------------|
| [workspace/README.md](workspace/README.md) | Workspace layout and Python/Git-oriented workflow tooling. |
| [workspace/workflows/README.md](workspace/workflows/README.md) | Workflow bundle overview. |
| [workspace/workflows/image_sorting_tools/README.md](workspace/workflows/image_sorting_tools/README.md) | Image sorting tools intro. |
| [workspace/workflows/image_sorting_tools/IMAGE_SORTER_GUIDE.md](workspace/workflows/image_sorting_tools/IMAGE_SORTER_GUIDE.md) | Image sorter usage guide. |
| [workspace/workflows/ponyflow_v2/README.md](workspace/workflows/ponyflow_v2/README.md) | Ponyflow v2 workflow-specific notes. |
| [workspace/workflows/ponyflowPonyIllustriousSDXL_v3/ponyflow_v3/README.md](workspace/workflows/ponyflowPonyIllustriousSDXL_v3/ponyflow_v3/README.md) | Ponyflow v3 workflow-specific notes. |
| [workspace/tests/fixtures/media/README.md](workspace/tests/fixtures/media/README.md) | Test fixture media notes. |

---

## Experiments UI (in-repo docs)

| Document | What it is |
|----------|------------|
| [workspace/experiments_ui/docs/VIEWPORT_DEVICES.md](workspace/experiments_ui/docs/VIEWPORT_DEVICES.md) | Viewport / device behavior. |
| [workspace/experiments_ui/docs/FEATURE_WIP_TUNE_LAUNCHER.md](workspace/experiments_ui/docs/FEATURE_WIP_TUNE_LAUNCHER.md) | WIP tune launcher feature notes. |

---

## Top-level `docs/` (design and rundowns)

| Document | What it is |
|----------|------------|
| [docs/CHECKIN_STRATEGY.md](docs/CHECKIN_STRATEGY.md) | Check-in / layering strategy for repo changes. |
| [docs/KRITA_AI_SETUP.md](docs/KRITA_AI_SETUP.md) | Krita AI setup notes. |
| [docs/LINEAGE_INDEX_SKETCH.md](docs/LINEAGE_INDEX_SKETCH.md) | Lineage index sketch / planning. |
| [docs/PROJECT_ORGANIZATION_PROPOSAL.md](docs/PROJECT_ORGANIZATION_PROPOSAL.md) | Project organization proposal. |
| [docs/RDP_UBUNTU_SETUP.md](docs/RDP_UBUNTU_SETUP.md) | RDP / Ubuntu setup notes. |
| [docs/SCHEDULED_AND_CONTAINER_JOBS_RUNDOWN.md](docs/SCHEDULED_AND_CONTAINER_JOBS_RUNDOWN.md) | Scheduled and container job rundown. |
| [docs/WORKFLOW_COMPATIBILITY.md](docs/WORKFLOW_COMPATIBILITY.md) | Workflow compatibility notes. |
| [docs/WORKSPACE_PROJECTS_RUNDOWN.md](docs/WORKSPACE_PROJECTS_RUNDOWN.md) | Workspace projects rundown. |

---

## Project meta and history

| Document | What it is |
|----------|------------|
| [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md) | Early implementation summary (structure may drift vs current tree). |

---

## Maintaining this index

When you add a new `*.md` file under the repo, add a row under the appropriate section (or create a section). Optional: run a search for `*.md` from the repo root to catch strays:

```bash
git ls-files '*.md'
```
