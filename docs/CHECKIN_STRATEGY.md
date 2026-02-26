# Git check-in strategy

This document defines what to commit, what to ignore, and a phased approach for getting the repo into a consistent state. Use it when catching up after a long period of not checking in.

---

## 1. Design principles

- **Custom nodes are bootstrap-installed**: `custom_nodes.yaml` + `scripts/bootstrap_nodes.py` clone nodes at container start. The repo should **not** track the contents of `custom_nodes/` (third-party repos). Track only the **config** (`custom_nodes.yaml` at repo root).
- **Workspace is mixed**: Some of `workspace/` is part of this repo (scripts, tests, Experiments UI source, docs, example workflows). The rest is runtime/user data (outputs, temp, credentials, ComfyUI install) and should not be committed.
- **Credentials and secrets**: Never commit tokens, `.env` with secrets, or `workspace/.civitai_token` (and similar). Already in `.gitignore`; ensure they stay out of history.
- **One logical change per commit**: Group by theme (e.g. “docs”, “ops scripts”, “Experiments UI”) so history stays readable and reverts are easy.

---

## 2. What to COMMIT

### Root / infra
- `.gitignore`, `.gitattributes`
- `Dockerfile`, `docker-compose.yml`
- `entrypoint.sh`
- `Makefile`, `package.json`
- `README.md`, `RUNPOD.md`, `TROUBLESHOOTING.md`
- `custom_nodes.yaml` (single source of node list; entrypoint may expect a copy under workspace at runtime)

### Scripts (repo root)
- `scripts/bootstrap_nodes.py`
- `scripts/dev.sh`, `scripts/dev_experiments_ui.ps1`, `scripts/dev_experiments_ui_container.ps1`
- `scripts/build_experiments_ui.sh`, `scripts/build_experiments_ui_container.ps1`
- `scripts/experiments_ui_server.py`
- `scripts/download_models_manifest.py`, `scripts/model_download_manifest.yaml`
- `scripts/scan_workflows_for_models.py`, `scripts/ensure_model_aliases.py`
- `scripts/ops_up.ps1`, `scripts/ops_down.ps1`, `scripts/ops_up.sh`, `scripts/ops_down.sh`
- `scripts/queue_incomplete_experiments.ps1`, `scripts/refresh_run_status.ps1`, `scripts/report_experiment_queue_status.ps1`
- `scripts/install_*.ps1` (Scheduled Task installers)
- `scripts/setup_rdp_ubuntu.sh`, `scripts/fix_rdp_*.sh`, `scripts/fix_xfce_terminal.sh`
- Any other **generic** utility scripts that belong to the project (not one-off personal scripts)

### Workspace (only these parts)
- `workspace/README.md`
- `workspace/scripts/` (ComfyUI workflow tooling: tune_experiment, extract_comfy_*, etc.)
- `workspace/tests/`
- `workspace/experiments_ui/` (React source under `web/`; **not** `experiments_ui/dist/` — that’s build output and already gitignored)
- `workspace/.civitai_token.example` (template only; no real tokens)
- Example/sample workflows in `workspace/workflows/` that are part of the repo (e.g. cleaned templates, demos). Optional: keep a small set; don’t commit huge or one-off experiment workflows.

### Docs
- `docs/` (any project documentation you want to keep in the repo)

### Optional (decide per repo)
- `comfyui-runpod.code-workspace` — commit if you want to share workspace layout; otherwise add to `.gitignore`.
- `platform/` — commit if it’s shared tooling; otherwise ignore.
- `chrome-jobs/` — commit only if it’s part of the project; otherwise add to `.gitignore`.

---

## 3. What NOT to commit

- **Credentials**: `workspace/.civitai_token`, `workspace/.huggingface_token`, `.env` (with secrets), `credentials/`
- **Custom node source trees**: Entire contents of `custom_nodes/` (they are cloned from `custom_nodes.yaml`). Exception: you may keep `custom_nodes/.disabled/` or `custom_nodes/example_node.py.example` in the repo if you document their purpose; otherwise ignore the whole `custom_nodes/` directory.
- **Runtime / user data**: `workspace/output/`, `workspace/input/`, `workspace/_tmp/`, `workspace/comfyui_user/` (if it’s local ComfyUI state)
- **ComfyUI install / clone**: `workspace/ComfyUI`, `workspace/comfyui-wan` (these are typically clones or symlinks; don’t track)
- **Build artifacts**: `workspace/experiments_ui/dist/`, `node_modules/`
- **Large/generated**: Models, `*.safetensors`, `*.ckpt`, etc. (already in `.gitignore`)
- **Personal / one-off**: One-off workflows, local-only scripts, machine-specific paths

---

## 4. Suggested `.gitignore` additions

The following are **already in `.gitignore`** (added with this strategy):

- `custom_nodes/` — so cloned node trees are not tracked; only `custom_nodes.yaml` is.
- `workspace/_tmp/`, `workspace/output/`, `workspace/input/`, `workspace/comfyui_user/`, `workspace/ComfyUI`, `workspace/comfyui-wan` — runtime or local state.

If you **revert** the `custom_nodes/` ignore (e.g. to keep local patches in ComfyUI-Custom-Scripts), document that in this file and do not run the `git rm -r --cached custom_nodes/` in §5.

---

## 5. One-time cleanup (if already committed by mistake)

- **Remove `workspace/.civitai_token` from the index** (file stays ignored; no longer tracked):
  ```bash
  git rm --cached workspace/.civitai_token
  ```
- **Stop tracking all of `custom_nodes/`** (after adding `custom_nodes/` to `.gitignore`):
  ```bash
  git rm -r --cached custom_nodes/
  ```
- **Stop tracking workspace ComfyUI / comfyui-wan** (if they were added):
  ```bash
  git rm -r --cached workspace/ComfyUI
  git rm -r --cached workspace/comfyui-wan
  ```
  Then add to `.gitignore` if needed:
  ```gitignore
  workspace/ComfyUI
  workspace/comfyui-wan
  ```

---

## 6. Phased check-in order

Use this order so that the repo stays buildable and understandable after each step.

| Phase | What to commit | Example message |
|-------|----------------|-----------------|
| **1** | `.gitignore` (and `.gitattributes`) | chore: update .gitignore and add .gitattributes |
| **2** | Core infra: `Dockerfile`, `docker-compose.yml`, `entrypoint.sh` | chore: Docker and entrypoint updates for ops and Experiments UI |
| **3** | Scripts at repo root (bootstrap, dev, build, ops, RDP/fix scripts) | feat: add ops and Experiments UI scripts (ops_up/down, queue, report, dev UI) |
| **4** | `custom_nodes.yaml` only (if you are ignoring `custom_nodes/`) | chore: update custom_nodes.yaml (bootstrap-only; stop tracking node trees) |
| **5** | Workspace tooling: `workspace/scripts/`, `workspace/tests/`, `workspace/README.md` | feat: workspace scripts and tests (tune_experiment, comfy_tool, roundtrip) |
| **6** | Experiments UI: `workspace/experiments_ui/` (no `dist/`), `scripts/experiments_ui_server.py` | feat: Experiments UI (React + server) |
| **7** | Docs: `README.md`, `RUNPOD.md`, `TROUBLESHOOTING.md`, `docs/` | docs: README, RunPod, troubleshooting, and docs/ |
| **8** | Convenience: `Makefile`, `package.json` | chore: add Makefile and package.json for ops/UI |
| **9** | Example workflows (optional): a few from `workspace/workflows/` | chore: add example cleaned workflows |
| **10** | Optional: `comfyui-runpod.code-workspace`, `platform/`, `chrome-jobs/` | Only if you decided to track them (see §2). |

After each phase, run a quick sanity check (e.g. `docker compose config`, or `npm run test` if available).

---

## 7. Ongoing habits

- **Before committing**: Run `git status` and scan for credentials, `custom_nodes/` content (if you’re ignoring it), and large files.
- **Branches**: Use short-lived branches for features (e.g. `feat/ops-scripts`), then merge to `main` so history stays clear.
- **Commit messages**: Use a short prefix: `feat:`, `fix:`, `docs:`, `chore:` so you can filter and revert by type.
- **Custom node changes**: If you must keep local patches (e.g. in ComfyUI-Custom-Scripts), either:
  - Track only the patched repo and document it in README or this file, or
  - Prefer contributing upstream and then relying on bootstrap; ignore `custom_nodes/` in the repo.

---

## 8. Quick reference: current status (as of strategy creation)

- **Modified (M)**: `.gitignore`, `Dockerfile`, `README.md`, `custom_nodes.yaml`, `docker-compose.yml`, `scripts/bootstrap_nodes.py`, `scripts/dev.sh`, many files under `custom_nodes/ComfyUI-Custom-Scripts`, and several custom_node directories (ComfyUI-Florence2, ComfyUI-GGUF, etc.).
- **Deleted (D)**: `workspace/.civitai_token` (correct — keep it deleted/ignored).
- **Untracked (?)**:
  - Root: `.gitattributes`, `Makefile`, `RUNPOD.md`, `TROUBLESHOOTING.md`, `entrypoint.sh`, `package.json`, `docs/`, `platform/`, `chrome-jobs/`, `comfyui-runpod.code-workspace`, and many new scripts.
  - `custom_nodes/`: `.disabled/`, several new node directories (Crystools, Impact-Pack, etc.), `example_node.py.example`, `websocket_image_save.py`.
  - `workspace/`: `README.md`, `scripts/`, `tests/`, `experiments_ui/`, `.civitai_token.example`, ComfyUI-* add-ons, workflows (e.g. `FB8VA5-laying-down.cleaned.json`), `_tmp/`, `comfyui_user/`.

Use the phases above to group these into logical commits. Prefer adding `custom_nodes/` to `.gitignore` and running `git rm -r --cached custom_nodes/` so the repo no longer tracks third-party node trees; then only `custom_nodes.yaml` and any single-file nodes (e.g. `websocket_image_save.py`) need to be committed if desired.

---

## 9. Custom nodes: evidence of local updates (scan results)

A scan was run over each directory under `custom_nodes/` that has its own `.git` (i.e. is a real clone). Nodes **without** a `.git` (e.g. copied or shallow) are tracked only by the parent repo; with `custom_nodes/` in `.gitignore`, the parent no longer tracks them.

### Nodes with their own `.git` — status

| Node | Local changes | Notes |
|------|----------------|--------|
| **ComfyUI-Impact-Pack** | **Yes — many modified** | 18 modified files under `tests/` and `tests/wildcards/` (shell scripts, Python). Clear local edits. |
| **ComfyUI-Manager** | **Yes — modified** | 7 modified files: `check.sh`, `cm-cli.sh`, several `scan.sh` and install scripts. Plus untracked `.requirements.sha256`. |
| **ComfyUI-MultiGPU** | **Yes — many modified** | Many modified files in `assets/`, `ci/example_workflows_api/`, `example_workflows/` (images, JSON, scripts). Could be line-ending (CRLF) or content changes. |
| **ComfyUI-Crystools** | Untracked only | `.requirements.sha256` (generated). |
| **ComfyUI-Florence2** | Untracked only | `.requirements.sha256`. |
| **ComfyUI-GGUF** | Untracked only | `.requirements.sha256`. |
| **rgthree-comfy** | Untracked only | `.requirements.sha256`. |
| **comfyui-mobile-frontend** | Untracked only | `dist/` build artifacts (expected). |
| **ComfyUI-mxToolkit** | Untracked only | `__pycache__/`. |
| **ComfyUI-Frame-Interpolation** | Clean | No uncommitted or untracked changes. |
| **ComfyUI-Upscaler-Tensorrt** | Clean | No changes. |

**None of the scanned nodes had local commits ahead of upstream** (`ahead: 0`). So any "local updates" are **uncommitted working-tree changes** (or untracked files), not extra commits.

### ComfyUI-Custom-Scripts (no nested `.git`)

This node **does not have its own `.git`** under `custom_nodes/ComfyUI-Custom-Scripts`. It was (before adding `custom_nodes/` to `.gitignore`) tracked as normal files by the **parent** repo. The parent had **14 modified JS files** under `web/js/` (autocompleter, betterCombos, common/*, etc.). Those could be line-ending changes (CRLF vs LF on Windows) or real code edits. With `custom_nodes/` now ignored, the parent repo no longer tracks those files. If you need to preserve local patches for ComfyUI-Custom-Scripts, either keep a copy of the modified files elsewhere and document how to apply them, or remove `custom_nodes/` from `.gitignore` and resume tracking that one node (see §4).

### How to re-run the scan

From the repo root run: `.\scripts\scan_custom_nodes_git_status.ps1` — this reports, for each custom node that has a `.git`, whether it has uncommitted changes, untracked files, or is clean, and whether it is ahead of upstream.
