# Current goal — read this first when resuming work

**Last updated:** 2026-07-02

This file is the **active handoff** for infrastructure and runtime work. Read it at the start of a session before guessing from git history or old chat.

---

## Primary goal

Get **ComfyUI running in Docker on WSL2** with:

1. **NVIDIA GPU support** (RTX 5060 Ti — `nvidia-smi` works in WSL)
2. **Correct filesystem mounts** (heavy data on WSL ext4, models on E:)

Success looks like:

```bash
cd ~/src/comfyui-runpod
npm run up
curl -s http://127.0.0.1:8188/queue   # ComfyUI responds
docker exec comfyui0-runpod nvidia-smi  # GPU visible inside container
```

ComfyUI UI: `http://localhost:8188/` (or `COMFYUI_HOST_PORT` from `.env`).

---

## Expected mount layout (from `.env`)

| Purpose | Host path | Container |
|---------|-----------|-----------|
| Input | `/home/yuji/comfyui-runpod-data/input` | `/workspace/input`, `/ComfyUI/input` |
| Output | `/home/yuji/comfyui-runpod-data/output` | `/workspace/output`, `/ComfyUI/output` |
| User / workflows | `/home/yuji/comfyui-runpod-data/comfyui_user` | `/ComfyUI/user`, `/workspace/comfyui_user` |
| Credentials | `/home/yuji/comfyui-runpod-data/credentials` | `/workspace/credentials` (ro) |
| Models | `/mnt/e/models` | `/ComfyUI/models`, `/workspace/models` |
| Repo / scripts | `./workspace`, `./scripts` | bind-mounted as in `docker-compose.yml` |

Migration history: Comfy bind data moved off `E:\comfyui-runpod-shadow` → `~/comfyui-runpod-data` (see `~/comfyui-runpod-data/.migration_sources.env`). WSL root disk is on E: via junction — see `docs/WSL_MOVE_TO_E_FOLLOWUP.md`.

**Output path drift:** if clips land in the wrong folder (nested `output/og/` or repo `workspace/output`), see [`docs/OUTPUT_PATH_MITIGATION.md`](docs/OUTPUT_PATH_MITIGATION.md). Quick check: `python3 scripts/scan_stray_outputs.py --since-hours 48`.

---

## Docker engine — use Docker Desktop, not snap

**Do not use snap Docker for GPU workloads on WSL.** Snap's sandbox cannot load WSL NVIDIA libs (`libnvidia-ml.so.1`).

| Engine | Status (2026-07-02) | Notes |
|--------|---------------------|-------|
| **Docker Desktop** | Preferred | Per-user install at `C:\Users\yuji\AppData\Local\Programs\DockerDesktop\`. WSL data on `E:\DockerDesktop\wsl`. |
| **Snap docker** | Disable / remove | Had the old 67GB image but **no GPU**. Migration script: `scripts/wsl_migrate_to_docker_desktop.sh`. |

### Docker Desktop checklist

- [x] Docker Desktop installed (per-user, 2026-07-02)
- [x] WSL integration enabled for **Ubuntu** (`IntegratedWslDistros` in `%APPDATA%\Docker\settings-store.json`)
- [x] `docker context use default` → socket at `/var/run/docker.sock`
- [x] `docker info` shows `Runtimes: ... nvidia` and `Name: docker-desktop`
- [x] Test container: `docker run --rm --gpus all nvidia/cuda:12.5.0-base-ubuntu22.04 nvidia-smi` works
- [ ] **Snap docker fully removed** (`bash scripts/wsl_migrate_to_docker_desktop.sh` after compose is down)
- [ ] ComfyUI stack up and GPU verified inside `comfyui0-runpod`

Helper scripts:

- `scripts/windows_enable_docker_wsl_integration.ps1` — enable Ubuntu integration + restart Docker Desktop
- `scripts/windows_docker_desktop_cleanup_and_install.ps1` — cleanup/reinstall Docker Desktop
- `scripts/wsl_migrate_to_docker_desktop.sh` — remove snap/apt docker after Desktop integration works
- `scripts/wait_for_compose_boot.sh` — preflight for systemd boot (docker + bind paths); `--check-only` for the keeper tick
- `scripts/comfyui_keep.py` — capped `compose up` after unexpected ComfyUI exits (`comfyui-runpod-keep.timer`)

---

## Blocker: ComfyUI image not on Docker Desktop

Image: **`schibbdev/comfyui-runpod:v1.2.0`** (see `docker-compose.yml`).

| Attempt | Result |
|---------|--------|
| Snap docker had image locally | ~67GB, but snap can't run GPU containers |
| `docker pull schibbdev/comfyui-runpod:v1.2.0` on Docker Desktop | **Access denied** — private or not on Hub |
| `docker load` from `~/comfyui-runpod-data/.tmp-comfyui-runpod-v1.2.0.tar*` | **Corrupt** — short read / unexpected EOF (~17GB tar incomplete) |

### Next actions (pick one)

1. **Export from snap, import to Desktop** (fastest if snap image still exists):
   - Temporarily start snap docker, `docker save schibbdev/comfyui-runpod:v1.2.0 -o /path/on/E/comfyui-v1.2.0.tar`
   - Switch back to Docker Desktop, `docker load -i ...`
2. **Rebuild locally** (reliable, slow):
   - `cd ~/src/comfyui-runpod && bash scripts/build.sh v1.2.0`
3. **Docker Hub login** if image is private: `docker login` then pull.

After image is present:

```bash
cd ~/src/comfyui-runpod
bash scripts/init_output_sftp_ssh_hostkeys.sh   # npm run up does this
npm run up
docker logs -f comfyui0-runpod
```

---

## Host context (don't re-investigate unless broken)

- **Machine:** WSL2 Ubuntu on Windows, GPU passthrough via WSL (`/usr/lib/wsl/lib`)
- **GPU:** NVIDIA GeForce RTX 5060 Ti, driver 591.86, CUDA 13.1
- **WSL disk:** Junction `C:\Users\yuji\AppData\Local\wsl\{b7fa9724-...}` → `E:\WSL\Ubuntu` (~190 GB vhdx)
- **Optional cleanup:** ~24 GB swap still on C: — see `docs/WSL_MOVE_TO_E_FOLLOWUP.md`
- **Product work parked:** Discovery/lineage UI, Workflow Explorer — 6 unpushed commits + large uncommitted diff; not part of this goal unless user asks

---

## Related docs

| Doc | When |
|-----|------|
| [WSL_MOVE_TO_E_FOLLOWUP.md](./WSL_MOVE_TO_E_FOLLOWUP.md) | WSL vhdx junction, swap move |
| [PLANNING_OVERVIEW.md](./PLANNING_OVERVIEW.md) | Product programs (Discovery, lineage, factory) |
| [TROUBLESHOOTING.md](../TROUBLESHOOTING.md) | Bind-mount races after reboot, MultiGPU pin, etc. |
| `E:\WSL\DOCKER_AND_VHDX_MAINTENANCE.txt` | Windows-side maintenance checklist (available when WSL is down) |

---

## Update this file

When the goal changes or a major step completes, edit **Primary goal**, **Blocker**, and checkboxes above. Keep it short and actionable — not a session log.
