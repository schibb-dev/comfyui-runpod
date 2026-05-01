# ComfyUI RunPod Setup

A complete Docker-based ComfyUI setup optimized for RunPod deployment with automatic model downloads, custom node installation, and workflow support. This project extends the base ComfyUI template with WAN video generation capabilities, GGUF model support, and automated LoRA management.

## Documentation

All markdown guides are indexed in **[DOCUMENTATION.md](DOCUMENTATION.md)** (tooling reference, deployment, workflows, and topic docs).

## TL;DR (local dev: bounce the stack)

From the repo root (after `docker compose up -d` and any one-time setup in **Quick Start**):

1. `**npm run restart`** — restarts Docker services `comfyui` and `watch_queue`.
2. `**npm run ui:dev:start**` — host Experiments UI: **nodemon-watched** Python API on `http://127.0.0.1:8791` plus Vite on `**http://127.0.0.1:5178/`** (Vite proxies `/api` and `/files` to the API). Stop both with **Ctrl+C** in that terminal.
3. **ComfyUI** — `**http://localhost:8188/`** (or the host port from `COMFYUI_HOST_PORT` in `.env`).

Optional: `npm run ui:dev:start -- --no-open` skips auto-opening a browser. More UI modes and container-only dev are under **Quick Start → Local Development** (Experiments UI).

**Host dev on WSL2:** clone the repo under `**~/src/...`** (Linux filesystem), set `**COMFYUI_MODELS_DIR=/mnt/e/models**` in `.env` if models stay on `**E:**`, then run the same `**npm run ui:dev:start**` from Ubuntu — see **Quick Start → Host dev on WSL2 (cutover)**.

## Features

- 🐳 **Docker-based**: Complete containerization for consistent deployment
- 🚀 **RunPod Ready**: Optimized for RunPod cloud GPU deployment
- 📦 **Auto Bootstrap**: Automatic custom node installation from configuration
- 🔄 **Model Downloads**: Automatic download of WAN, GGUF, and CivitAI models
- 🎯 **Workflow Support**: Pre-configured for WAN video generation workflows
- 🔧 **Multi-GPU**: Support for distributed GPU processing
- 🎬 **WAN Video**: Full WAN 2.1/2.2 support with text encoders, VAE, and diffusion models
- 🤖 **GGUF Support**: Integrated GGUF model loading for efficient inference
- 💾 **Persistent Storage**: Workspace-based model storage with symlinks
- 📌 **Pinned ComfyUI**: Default build uses commit `38d049382533c6662d815b08ca3395e96cca9f57` so **ComfyUI-TeaCache** (WAN base image) keeps working; override with `COMFYUI_REF` in `.env` (see `.env.example`, `TROUBLESHOOTING.md`).

## Quick Start

### Local Development

1. **Clone and setup:**
  ```bash
   git clone <your-repo-url>
   cd comfyui-runpod
  ```
2. **Configure credentials:**
  ```bash
   ./scripts/setup_credentials.sh
  ```
3. **(Optional) Use an existing local models directory**

This repo supports mounting a host folder as the container’s `models/` base via `COMFYUI_MODELS_DIR`.

- **Windows (non-RunPod)**: create a `.env` file next to `docker-compose.yml`:

```env
COMFYUI_MODELS_DIR=E:\models
```

Make sure Docker Desktop is allowed to access the `E:` drive (Settings → Resources → File Sharing).

Note:

- If you run `docker compose` from **PowerShell**, `E:\\models` (or `E:/models`) is fine.
- If you run `docker compose` from **WSL/Linux**, use the WSL mount path instead (e.g. `/mnt/e/models`).

1. **Build and run:**
  ```bash
   docker compose up -d
  ```
2. **Access ComfyUI:**
  - Open [http://localhost:8188](http://localhost:8188) in your browser

### Ops: Docker-native “cron” (recommended)

This repo supports **host-independent background machinery** using Docker Compose sidecars (no Windows Scheduled Tasks required).

Canonical entrypoints (pick one style):

- **npm**: `npm run ops:up` / `npm run ops:down`
- **make**: `make ops-up` / `make ops-down`
- **docker compose**: use the commands below directly

Enable the ops profile:

```bash
docker compose --profile ops up -d refresh_run_status report_experiment_queue_status queue_incomplete_experiments ws_event_tap
```

What these do:

- `**refresh_run_status**`: writes per-run `status.json` from live `/queue` (phase: done | running | queued | submitted | not_queued). “submitted” = has submit.json but prompt_id not in queue (e.g. job canceled).
- `**report_experiment_queue_status**`: appends a periodic summary snapshot to `workspace/output/output/experiments/_status/queue_status.log`
- `**queue_incomplete_experiments**`: fetches ComfyUI `/queue`, clears `submit.json` for runs no longer in queue (e.g. canceled), then runs the **experiment queue manager** once to submit eligible runs. The manager applies FIFO rules and caps how many **experiments** can have a run in the queue (default 12, env `EXPERIMENT_QUEUE_MAX_RUNS`). Skips experiments marked stopped (see “Stop an experiment” below).
- **Future queue report**: `python workspace/scripts/report_future_queue.py` lists what needs to be queued, what is queued, and execution order; use `--json` for UI/API.
- `**ws_event_tap`**: listens to ComfyUI `/ws` and records **true execution timing** (start/end) into each run’s `metrics.json` (preferred runtime source)

Thin launchers (optional):

- **Windows**: `scripts/ops_up.ps1`, `scripts/ops_down.ps1`
- **Linux**: `scripts/ops_up.sh`, `scripts/ops_down.sh`

**Experiment Run Queue (ERQ)**  
Submission of experiment runs to ComfyUI is done by the **queue manager** (`workspace/scripts/experiment_queue_manager.py`), not by `tune_experiment run`. The manager scans experiments, applies pluggable **rules** (default: FIFO by experiment mtime, then exp_id, run_id), and submits runs up to an **experiment cap** (max distinct experiments with a run in the queue; default 12, set via `EXPERIMENT_QUEUE_MAX_RUNS`). The ops job `queue_incomplete_experiments` runs the manager once per tick. Optional snapshot: `--write-erq` writes the ordered candidate list to `experiments_root/_status/experiment_run_queue.json`.

Note: Windows Scheduled Tasks are supported but **legacy/optional**. If you previously enabled them (e.g. `ComfyUI-RefreshRunStatus`), disable them to avoid duplicate work.

### Daily routines (runbook)

Common workflows using the canonical entrypoints:

- **npm scripts**: `npm run <task>`
- **Makefile**: `make <task>`

#### DEV: Experiments UI (Vite) development

**HMR** means **Hot Module Replacement**: Vite pushes **small live updates** (e.g. a React file save) over a WebSocket so the browser refreses **that module** without a full page reload. It feels instant when file watching is healthy; it stutters when the dev server cannot see file changes quickly (common with **NTFS → Docker bind mounts** on Windows).

**“Native Vite”** here means **Node + Vite running in your interactive shell’s OS** — not inside the `comfyui` container. The same `**npm run ui:dev:vite`** / `**npm run ui:dev:start**` entrypoints work on **macOS, Linux, and Windows** (see `scripts/experiments-ui-dev.mjs`). A **WSL2 Ubuntu** shell counts as **Linux** for this purpose: install Node in WSL, clone the repo under the **Linux filesystem** (e.g. `~/src/...`, not `/mnt/c/...`), and run the same npm commands there.

**Recommended layout (performance + portability):** keep **day-to-day UI** on **native Vite** in **WSL2** (or Linux/macOS), with **Docker Compose** for ComfyUI + API; use **in-container Vite** (`npm run ui:dev`) or `**npm run ui:build:docker`** when you want a strict “same as the container” check.

**Host dev on WSL2 (cutover from Windows)**

Use this when the **canonical** checkout and `**docker compose`** run from **Ubuntu on WSL**, with models still on `**E:`** if you like.

1. **One-time:** install **WSL2** + **Ubuntu**, **Docker Desktop** with **Settings → Resources → WSL integration** enabled for that distro. Install **Node 20+** inside Ubuntu (`fnm`, `nvm`, or distro packages).
2. **Clone inside Linux home** (not under `/mnt/c/…`):
  ```bash
   mkdir -p ~/src && cd ~/src
   git clone <your-repo-url> comfyui-runpod
   cd comfyui-runpod
  ```
3. **Heavy data on `E:` (shadow tree):** if you copied local-only state to `**E:\comfyui-runpod-shadow`** (see that folder’s `README.txt`), run `**./scripts/wsl_setup_from_shadow.sh**` once from the WSL repo root. It installs workspace token files from the shadow, merges `**.env**`, and appends `**COMFYUI_BIND_***` paths so `**docker-compose.yml**` keeps code on the Linux disk while **input / output / `comfyui_user` / credentials** stay on `**/mnt/e/comfyui-runpod-shadow/...`**. Skip this step if everything still lives under `**./workspace/...**` in the clone.
4. `**.env` next to `docker-compose.yml`:** if you did **not** use the shadow script, copy values from your Windows `.env` (do **not** copy blindly — paths change). At minimum:
  - `**COMFYUI_MODELS_DIR=/mnt/e/models`** if models stay on `**E:**` (confirm `ls /mnt/e` in WSL). Keep **Docker Desktop file sharing** for `**E:`** enabled.
  - Re-apply any `**COMFYUI_HOST_PORT**`, tokens, etc., you had on Windows.
5. **Credentials:** run `./scripts/setup_credentials.sh` again from WSL **or** copy `**./credentials`** from the old tree **or** rely on `**COMFYUI_BIND_CREDENTIALS_DIR`** when using the shadow layout.
6. **Stack:** `docker compose up -d` from `**~/src/comfyui-runpod`**, then `**npm run ui:dev:start**` (API + Vite) or `**npm run ui:dev:vite**`. Optional sanity: `**./scripts/wsl_dev_check.sh**`.
7. **Editor:** open the folder **via Remote WSL** (VS Code / Cursor) so terminals and Git use Ubuntu paths.
8. **FileBrowser / phone:** recreate bookmarks or sync tasks — SFTP host is your PC (Tailscale/LAN), path is `**/home/<you>/src/comfyui-runpod/...`** (not `C:\…`).
9. **Parity check:** `**npm run ui:dev`** for Vite **inside** the container on `**http://127.0.0.1:51780/`** (see `EXPERIMENTS_UI_VITE_HOST_PORT`).

**Transition tooling (explicitly migration-oriented):**


| Script                                     | Where it runs      | Role                                                                                                                                 |
| ------------------------------------------ | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------ |
| `**scripts/sync_comfyui_shadow_to_e.ps1`** | Windows PowerShell | Copies local-only state from a checkout into `**E:\comfyui-runpod-shadow**` (cold backup / pre–WSL-primary). Optional after cutover. |
| `**scripts/wsl_setup_from_shadow.sh**`     | WSL bash           | One-time: wire a `**~/src/...**` clone to that shadow via `**.env**` `**COMFYUI_BIND_***` + token files.                             |
| `**scripts/wsl_migration_inventory.sh**`    | WSL bash           | Read-only: `**df**` / `**du**` for Windows checkout, shadow, and ext4 data paths.                                                  |
| `**scripts/wsl_setup_ext4_data.sh**`        | WSL bash           | Creates `**~/data/comfyui-runpod/{input,output,comfyui_user}**` and writes `**COMFYUI_BIND_***` for fast ext4 I/O.                  |
| `**scripts/wsl_sync_workspace_from_windows.sh**` | WSL bash     | `**rsync**` from `**/mnt/c/...**` or `**E: shadow**` into ext4 (no Windows deletes).                                                |
| `**scripts/wsl_archive_windows_after_verify.sh**` | WSL bash   | **After you verify WSL:** optional secrets archive / tarball; **does not** remove Windows trees unless you do so manually.           |

**WSL ext4 for `input` / `output` (test before retiring Windows):** ensure `**.env**` exists next to `docker-compose.yml`, then from `~/src/comfyui-runpod`: `**./scripts/wsl_migration_inventory.sh**` → `**./scripts/wsl_setup_ext4_data.sh --mkdir-only**` → `**./scripts/wsl_sync_workspace_from_windows.sh --dry-run**` → `**./scripts/wsl_sync_workspace_from_windows.sh**` → `**./scripts/wsl_setup_ext4_data.sh**` (writes `**COMFYUI_BIND_***` + backs up `**.env**`) → `**./scripts/wsl_dev_check.sh**` and `**docker compose up -d**` / `**npm run ui:dev:start**`. Compare trees with `**./scripts/compare_workspace_input.sh**`. **Do not** archive or delete Windows copies until that passes; use `**scripts/wsl_archive_windows_after_verify.sh**` only afterward (it stays dry-run unless you set `**COMFYUI_I_HAVE_VERIFIED_WSL=1**`).

**Windows clone (`~/Code` / `C:\Users\...\Code\...`):** archive or delete when you are satisfied WSL is primary — avoid two active copies diverging. Until then, you can `**rsync`** or selectively `**cp**` updated files from `**/mnt/c/.../comfyui-runpod/**` into `**~/src/...**` if you need compose or script changes before they are pushed to `**origin**`.

- **Run Vite inside the container** (no host Node required; same command on Linux, macOS, and Windows):

```bash
npm run ui:dev
```

This uses `docker compose exec` and serves Vite from the container; Docker maps `**http://127.0.0.1:51780/**` (host) to Vite on port **5178** inside the container (see `EXPERIMENTS_UI_VITE_HOST_PORT` in `docker-compose.yml` / `.env.example`). **Host** `npm run ui:dev:start` still uses `**http://127.0.0.1:5178/`** without conflicting with Docker. After changing that mapping, run `**docker compose up -d**` (or recreate the `comfyui` service) so the old host **5178** publish is released.

- **Run Vite on the host** (best HMR; requires Node.js on host). Cross-platform:

```bash
npm run ui:dev:vite
```

API + Vite on the host (recommended — watched API restarts on `experiments_ui_server.py` saves):

```bash
npm run ui:dev:start
```

Same stack without auto-restart of the Python server (manual restart after API edits):

```bash
npm run ui:dev:all
```

Tailscale / remote HMR: `npm run ui:dev:vite:tailscale`, `npm run ui:dev:start:tailscale`, or `npm run ui:dev:all:tailscale`. Optional shell wrappers: `./scripts/dev_experiments_ui.sh`, `./scripts/dev_experiments_ui_start.sh`, `./scripts/dev_experiments_ui_all.sh`.

- **Windows-only PowerShell** (ensure container, then Vite): `npm run ui:dev:win` or `.\scripts\dev_experiments_ui_container.ps1 -EnsureContainer`.

#### DEV: Rebuild UI after changing the Experiments UI backend API

If you changed the backend (e.g. `scripts/experiments_ui_server.py`) and want the running container to pick it up:

```bash
npm run restart
```

If you changed the frontend and want the built `dist/` served by the Experiments UI server (port `8790`):

```bash
npm run ui:build:docker
npm run comfy:restart
```

#### OPERATIONS: Check queue status quickly

- **One-shot summary**:

```bash
npm run report:once
```

- **Tail the periodic log** (ops profile must be running):

```bash
npm run report:tail
```

The log is stored at `workspace/output/output/experiments/_status/queue_status.log`.

#### OPERATIONS: Check logs

- **All services**:

```bash
npm run logs
```

- **ComfyUI only**:

```bash
npm run logs:comfy
```

- **Queue watcher only**:

```bash
npm run logs:watch
```

#### OPERATIONS: Restart parts safely

- **Restart ComfyUI + watcher**:

```bash
npm run restart
```

- **Restart only ComfyUI**:

```bash
npm run comfy:restart
```

- **Restart ops sidecars**:

```bash
npm run ops:down
npm run ops:up
```

#### OPERATIONS: Backfill missing `history.json`

If you have outputs on disk but missing `history.json` (e.g. after a ComfyUI restart), you can backfill:

```bash
npm run history:backfill
```

#### OPERATIONS: Stop an experiment (no more scheduling)

To indicate that an experiment should **no longer be scheduled** (e.g. you don’t want remaining runs queued), mark it as stopped:

- **From workspace** (paths relative to workspace):
  ```bash
  python scripts/stop_experiment.py output/output/experiments/<exp_id>
  ```
  Or by experiment id (folder name) under the default experiments root:
  ```bash
  python scripts/stop_experiment.py --exp-id tune_FB8VA5L-2026-02-20-203434_OG_00001_20260222-231422
  ```
- **Resume scheduling** for that experiment:
  ```bash
  python scripts/stop_experiment.py --remove output/output/experiments/<exp_id>
  ```

When an experiment is stopped, a sentinel file `experiment_stopped` is created in its directory. Then:

- `**queue_incomplete_experiments**` (and the ops job) skips it, so no further runs are queued.
- `**tune_experiment.py run <exp_dir>**` exits with a message instead of submitting.

Stopping does **not** cancel runs already in the ComfyUI queue or currently running; it only prevents new runs of that experiment from being scheduled.

#### Future queue report (API)

To list what needs to be queued, what is actually queued, and the execution order:

```bash
python workspace/scripts/report_future_queue.py [--server URL] [--json]
```

- **Default**: prints a human-readable summary (needs_queued, queue order from ComfyUI `/queue`).
- `**--json`**: full report as JSON for UI/API: `summary`, `needs_queued`, `queue_order` (position, status, prompt_id, exp_id, run_id when known), and `queue_error` if the server is unreachable.
- `**--no-server**`: only scan disk (needs_queued); do not call ComfyUI.

#### TESTING: Unit / integration / acceptance

This repo currently has **Python `unittest`** suites under `workspace/tests/` and they are runnable **inside the container**.

- **Run all Python tests**:

```bash
npm test
```

- **Unit tests only**:

```bash
npm run test:unit
```

- **Integration tests** (media roundtrip; uses `ffprobe` and fixture references, and may skip if fixtures are absent):

```bash
npm run test:integration
```

- **Acceptance tests**:

```bash
npm run test:acceptance
```

There are no formal acceptance tests defined yet; this is a placeholder so the ops/tooling surface is complete.

### Experiments UI (optional)

This repo includes a small React dashboard for comparing tuning runs side-by-side.

- Enable it via `.env`:

```env
EXPERIMENTS_UI=true
EXPERIMENTS_UI_PORT=8790
```

- Then open `http://127.0.0.1:8790/`.

If you see a message about missing React build output, restart the container once; the entrypoint will build `workspace/experiments_ui/dist` automatically when `EXPERIMENTS_UI_BUILD=true` (default).

### RunPod Deployment

1. **Build and push image:**
  ```bash
   ./scripts/build.sh
   ./scripts/push.sh
  ```
2. **Deploy on RunPod:**
  - Use the pushed Docker image: `schibbdev/comfyui-runpod:latest`
  - Configure environment variables for tokens
  - Mount persistent volumes for models
  - See `RUNPOD.md` for the recommended volume/env setup

## Project Structure

```
comfyui-runpod/
├── Dockerfile                 # Main container definition
├── docker-compose.yml         # Local development setup
├── custom_nodes.yaml         # Custom node configuration
├── scripts/                  # Utility scripts
│   ├── setup_credentials.sh  # Token setup
│   ├── bootstrap_nodes.py    # Node installation
│   ├── download_gguf_models.py # GGUF model downloader
│   ├── build.sh             # Build Docker image
│   └── push.sh              # Push to registry
├── workspace/               # Persistent data
│   ├── ComfyUI/            # ComfyUI installation
│   ├── workflows/          # Workflow files
│   └── setup_tokens.sh     # Token loader
├── workspace/experiments_ui # React dashboard (source in web/, built dist/ served on 8790)
└── credentials/            # Token storage (gitignored)
```

## Configuration

### Custom Nodes

Edit `custom_nodes.yaml` to add or remove custom nodes:

```yaml
nodes:
  essential:
    - name: ComfyUI-GGUF
      repo: https://github.com/city96/ComfyUI-GGUF.git
      branch: main
      required: true
```

### Environment Variables

- `HUGGINGFACE_TOKEN`: For downloading GGUF models
- `CIVITAI_TOKEN`: For downloading CivitAI models
- `download_480p_native_models`: Enable 480p model downloads
- `download_720p_native_models`: Enable 720p model downloads

## Models

The setup automatically downloads:

- **WAN Models**: 480p and 720p UNET models for video generation
- **GGUF Models**: UMT5 encoder and WAN UNET models
- **CivitAI Models**: LoRA and other community models
- **VAE Models**: WAN VAE for proper video encoding

### Optional: workflow helper downloads (IP-Adapter / ControlNet)

For AnimateDiff + IP-Adapter + ControlNet workflows, this repo includes an **optional curated downloader**:

- `scripts/model_download_manifest.yaml`
- `scripts/download_models_manifest.py`
- `scripts/scan_workflows_for_models.py` (reports what your workflows reference + what's missing)

**Opt-in at container startup** by adding to your `.env`:

```env
AUTO_DOWNLOAD_MANIFEST_MODELS=true
MANIFEST_PROFILE=workflows_default
```

Or run it manually inside the container:

```bash
python3 /workspace/scripts/download_models_manifest.py --profile workflows_default
python3 /workspace/scripts/scan_workflows_for_models.py --models-dir /ComfyUI/models
```

## Workflows

Pre-configured workflows are available in `workspace/workflows/`:

- `FaceBlastA.json`: Face-focused video generation
- Additional workflows can be added as needed

## Development

### Adding Custom Nodes

1. Add to `custom_nodes.yaml`
2. Rebuild container: `docker compose build --no-cache`
3. Restart: `docker compose restart`

### Updating Models

Models are downloaded automatically on container start. To force re-download:

```bash
docker compose exec comfyui rm -rf /ComfyUI/models/unet/*.gguf
docker compose restart
```

## Troubleshooting

See **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** for detailed steps, especially **custom node IMPORT FAILED** (getting the traceback from logs, force-reinstalling requirements, OpenCV/VideoHelperSuite in Docker).

### Missing Nodes

- Check `custom_nodes.yaml` configuration
- Verify bootstrap script ran: `docker compose logs comfyui`
- Rebuild container if needed
- If nodes show **IMPORT FAILED**: check container logs for the Python traceback; use `FORCE_REINSTALL_NODE_REQUIREMENTS=true` and restart to reinstall pip deps (see TROUBLESHOOTING.md).

### Missing Models

- Verify tokens are set correctly
- Check download logs: `docker compose logs comfyui`
- Ensure sufficient disk space

## Security Notes

- **Never commit tokens**: store them in `credentials/` (gitignored) or set them as environment variables in RunPod.
- **Do not edit** `workspace/.civitai_token.example` with real credentials; it is only a template.

### GPU Issues

- Verify NVIDIA Docker runtime is installed
- Check GPU availability: `nvidia-smi`
- Review container logs for CUDA errors

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test locally with Docker
5. Submit a pull request

## Recent Updates

### WAN Model Integration ✅

- Complete WAN 2.1/2.2 model support with automatic downloads
- Text encoders (FP16 and FP8 optimized)
- VAE and diffusion models
- CLIP vision models
- See `WAN_INSTALLATION_COMPLETE.md` for details

### GGUF Model Support ✅

- Fixed GGUF model loading in CLIPLoaderGGUFMultiGPU node
- Automatic folder registration before ComfyUI startup
- Symlink-based model organization
- See `GGUF_MODEL_FIX_COMPLETE.md` for details

### GPU Configuration ✅

- Multi-GPU setup support (GT710 + RTX 5060 Ti)
- Compute-only mode configuration
- Display/compute GPU separation
- See `GPU_CONFIGURATION_GUIDE.md` for details

### Script Enhancements

- Added WAN model downloader (`scripts/download_wan_models.py`)
- Added GGUF model downloader with proper folder registration
- Enhanced CivitAI LoRA downloader
- Bootstrap script for custom node installation

## Support

For issues and questions:

- Check the troubleshooting section
- Review Docker logs
- Open an issue on GitHub

