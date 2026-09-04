#!/usr/bin/env bash
set -euo pipefail

echo "🧩 ComfyUI RunPod entrypoint starting..."

WORKSPACE_PATH="${WORKSPACE_PATH:-/workspace}"
COMFYUI_PATH="${COMFYUI_PATH:-/ComfyUI}"
CREDS_DIR="${CREDS_DIR:-$WORKSPACE_PATH/credentials}"

# Workloads that write renders / UI artifacts onto bind mounts should use the same numeric
# uid:gid as OUTPUT_SFTP (atmoz/sftp … :uid:gid:…) so SFTP clients can manage those files.
# Bootstrap stays root so `python3 -m pip install` into the image Python keeps working.
COMFYUI_RUN_UID="${COMFYUI_RUN_UID:-1000}"
COMFYUI_RUN_GID="${COMFYUI_RUN_GID:-1000}"
COMFYUI_RUN_AS_ROOT="${COMFYUI_RUN_AS_ROOT:-false}"

# ComfyUI-Manager runs prestartup hooks via `python3 -m uv ...` and `uv` defaults
# to caching under $HOME/.cache (commonly /root/.cache). When ComfyUI runs as a
# non-root uid (via setpriv), that HOME can remain /root, causing:
#   "failed to create directory /root/.cache/uv: Permission denied"
# Force uv cache into the (writeable) workspace cache.
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$WORKSPACE_PATH/.cache}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$XDG_CACHE_HOME/uv}"
mkdir -p "$XDG_CACHE_HOME" "$UV_CACHE_DIR"
chown -R "${COMFYUI_RUN_UID}:${COMFYUI_RUN_GID}" "$XDG_CACHE_HOME" 2>/dev/null || true

run_as_comfy_writer() {
  if [[ "${COMFYUI_RUN_AS_ROOT}" == "true" ]] || [[ "$(id -u)" -ne 0 ]]; then
    "$@"
    return $?
  fi
  if command -v setpriv >/dev/null 2>&1; then
    setpriv --reuid "${COMFYUI_RUN_UID}" --regid "${COMFYUI_RUN_GID}" --init-groups -- "$@"
    return $?
  fi
  "$@"
}

# Ensure workspace directories exist (mounted volumes may be empty)
mkdir -p \
  "$WORKSPACE_PATH" \
  "$WORKSPACE_PATH/workflows" \
  "$WORKSPACE_PATH/input" \
  "$WORKSPACE_PATH/output" \
  "$WORKSPACE_PATH/models" \
  "$CREDS_DIR"

# Recursively fix bind-mounted input/output on the host (same paths as SFTP) so Comfy + SFTP
# share uid:gid and predictable modes. Dedupe /workspace/* vs /ComfyUI/* when they are the same mount.
# Skip on huge trees: COMFYUI_SKIP_INPUT_OUTPUT_CHOWN=true
normalize_bind_mount_input_output() {
  [[ "${COMFYUI_SKIP_INPUT_OUTPUT_CHOWN:-false}" == "true" ]] && return 0
  [[ "${COMFYUI_RUN_AS_ROOT}" == "true" ]] && return 0
  [[ "$(id -u)" -ne 0 ]] && return 0

  local uid="${COMFYUI_RUN_UID:-1000}"
  local gid="${COMFYUI_RUN_GID:-1000}"
  local paths=(
    "$WORKSPACE_PATH/input"
    "$WORKSPACE_PATH/output"
    "$COMFYUI_PATH/input"
    "$COMFYUI_PATH/output"
  )
  local -A seen_mount=()
  for d in "${paths[@]}"; do
    [[ -d "$d" ]] || continue
    local key
    key="$(stat -c '%d:%i' "$d" 2>/dev/null)" || continue
    [[ -n "${seen_mount[$key]:-}" ]] && continue
    seen_mount[$key]=1

    echo "🔧 Normalizing ownership/permissions under $d → ${uid}:${gid}"
    if ! chown -R "${uid}:${gid}" "$d" 2>/dev/null; then
      echo "⚠️  chown failed on $d (readonly FS or host mapping); skipping chmod for this tree"
      continue
    fi
    # Directories: rwxrwxr-x; files: rw-rw-r-- (stay on this mount only).
    find "$d" -xdev -type d ! -type l -exec chmod 775 {} + 2>/dev/null || true
    find "$d" -xdev -type f ! -type l -exec chmod 664 {} + 2>/dev/null || true
  done
}

normalize_bind_mount_input_output

# Make workflows visible to the pysssss workflow picker (Custom-Scripts):
# pysssss defaults to scanning: $COMFYUI_PATH/pysssss-workflows
# Our repo stores workflows in: $WORKSPACE_PATH/workflows
#
# Default behavior: link pysssss-workflows -> /workspace/workflows so they show up in the UI.
LINK_PYSSSSS_WORKFLOWS_TO_WORKSPACE="${LINK_PYSSSSS_WORKFLOWS_TO_WORKSPACE:-true}"
PYSSSSS_WORKFLOWS_DIR="${PYSSSSS_WORKFLOWS_DIR:-$COMFYUI_PATH/pysssss-workflows}"
if [[ "$LINK_PYSSSSS_WORKFLOWS_TO_WORKSPACE" == "true" ]]; then
  mkdir -p "$WORKSPACE_PATH/workflows"
  # Prefer symlink so new workflows appear immediately. If symlinks are disallowed,
  # fall back to copying the files into the default directory.
  if ln -sfn "$WORKSPACE_PATH/workflows" "$PYSSSSS_WORKFLOWS_DIR" 2>/dev/null; then
    echo "🔗 Linked pysssss workflows: $PYSSSSS_WORKFLOWS_DIR -> $WORKSPACE_PATH/workflows"
  else
    mkdir -p "$PYSSSSS_WORKFLOWS_DIR"
    cp -a "$WORKSPACE_PATH/workflows/." "$PYSSSSS_WORKFLOWS_DIR/" 2>/dev/null || true
    echo "📁 Copied workflows into: $PYSSSSS_WORKFLOWS_DIR"
  fi
fi

# Make workflows visible to ComfyUI's built-in workflow browser (not pysssss).
# ComfyUI looks in: $COMFYUI_PATH/user/default/workflows
SYNC_COMFYUI_USER_WORKFLOWS="${SYNC_COMFYUI_USER_WORKFLOWS:-true}"
COMFYUI_USER_WORKFLOWS_DIR="${COMFYUI_USER_WORKFLOWS_DIR:-$COMFYUI_PATH/user/default/workflows}"
if [[ "$SYNC_COMFYUI_USER_WORKFLOWS" == "true" ]]; then
  mkdir -p "$COMFYUI_USER_WORKFLOWS_DIR"
  # Non-destructive sync: copy new/updated workflows in
  cp -a "$WORKSPACE_PATH/workflows/." "$COMFYUI_USER_WORKFLOWS_DIR/" 2>/dev/null || true
  echo "📁 Synced workflows into ComfyUI user dir: $COMFYUI_USER_WORKFLOWS_DIR"
fi

# Load credentials from files if present (RunPod volume friendly)
if [[ -f "$CREDS_DIR/huggingface_token" && -z "${HUGGINGFACE_TOKEN:-}" ]]; then
  export HUGGINGFACE_TOKEN="$(<"$CREDS_DIR/huggingface_token")"
  echo "✅ Hugging Face token loaded from $CREDS_DIR/huggingface_token"
fi

if [[ -f "$CREDS_DIR/civitai_token" && -z "${CIVITAI_TOKEN:-}" ]]; then
  export CIVITAI_TOKEN="$(<"$CREDS_DIR/civitai_token")"
  echo "✅ CivitAI token loaded from $CREDS_DIR/civitai_token"
fi

# Some tools expect lowercase var name
if [[ -n "${CIVITAI_TOKEN:-}" && -z "${civitai_token:-}" ]]; then
  export civitai_token="$CIVITAI_TOKEN"
fi

# Create CivitAI token JSON for downloader scripts (kept out of git)
if [[ -n "${CIVITAI_TOKEN:-}" ]]; then
  printf '{"civitai_token":"%s"}\n' "$CIVITAI_TOKEN" > "$WORKSPACE_PATH/.civitai_token"
  chmod 600 "$WORKSPACE_PATH/.civitai_token" || true
fi

# Prefer persisting models under /workspace/models by mounting it to /ComfyUI/models.
# If the user didn't mount it, keep going (base image may already have models there).
if [[ -d "$WORKSPACE_PATH/models" && ! -L "$COMFYUI_PATH/models" ]]; then
  # If /ComfyUI/models is empty-ish, link it to workspace for persistence.
  if [[ ! -e "$COMFYUI_PATH/models/.linked_to_workspace" ]]; then
    mkdir -p "$COMFYUI_PATH/models"
    touch "$COMFYUI_PATH/models/.linked_to_workspace" 2>/dev/null || true
  fi
fi

# Git 2.35+: `git -C <repo>` run as root refuses repos owned by another uid ("dubious ownership").
# Runtime custom-node bootstrap is opt-in, but when enabled it runs as root against repos
# that may have been chowned to COMFYUI_RUN_UID for Comfy imports.
if [[ "$(id -u)" -eq 0 ]] && command -v git >/dev/null 2>&1; then
  if git config --global --replace-all safe.directory '*' 2>/dev/null; then
    echo "🔧 git: safe.directory=* (root bootstrap vs uid-owned custom_nodes clones)"
  else
    for base in "$COMFYUI_PATH/custom_nodes" "$COMFYUI_PATH/custom_nodes.disabled"; do
      [[ -d "$base" ]] || continue
      while IFS= read -r -d '' gitdir; do
        repo="${gitdir%/.git}"
        git config --global --add safe.directory "$repo" 2>/dev/null || true
      done < <(find "$base" -maxdepth 3 -type d -name .git -print0 2>/dev/null || true)
    done
  fi
fi

# Custom nodes are baked into the image at build time from custom_nodes.yaml. Avoid
# re-fetching/re-pinning them at every boot; enable only for dev/debug or a mounted custom_nodes tree.
INSTALL_KRITA_BACKEND_NODES="${INSTALL_KRITA_BACKEND_NODES:-false}"
export INSTALL_KRITA_BACKEND_NODES

krita_backend_nodes_missing() {
  local name
  for name in comfyui-tooling-nodes comfyui-inpaint-nodes; do
    [[ -d "$COMFYUI_PATH/custom_nodes/$name" ]] || return 0
  done
  return 1
}

COMFYUI_BOOTSTRAP_NODES_ON_START="${COMFYUI_BOOTSTRAP_NODES_ON_START:-false}"
if [[ "$INSTALL_KRITA_BACKEND_NODES" == "true" ]] && krita_backend_nodes_missing; then
  if [[ "$COMFYUI_BOOTSTRAP_NODES_ON_START" != "true" ]]; then
    echo "🔧 INSTALL_KRITA_BACKEND_NODES=true but Acly backend nodes are missing; enabling startup bootstrap"
    COMFYUI_BOOTSTRAP_NODES_ON_START=true
  fi
fi

if [[ "$COMFYUI_BOOTSTRAP_NODES_ON_START" == "true" ]]; then
  if [[ -f "$WORKSPACE_PATH/custom_nodes.yaml" ]]; then
    echo "🚀 Bootstrapping custom nodes from $WORKSPACE_PATH/custom_nodes.yaml"
    python3 "$WORKSPACE_PATH/scripts/bootstrap_nodes.py"
    echo "✅ Custom nodes bootstrap completed"
  else
    echo "ℹ️  No $WORKSPACE_PATH/custom_nodes.yaml found; skipping node bootstrap"
  fi
else
  echo "⏭️  Skipping startup custom-node bootstrap (baked into image; set COMFYUI_BOOTSTRAP_NODES_ON_START=true to refresh at boot)"
fi

# Optional: disable MultiGPU node pack by physically moving it out of custom_nodes.
# This avoids startup import failures when the pack expects newer ComfyUI internals.
INSTALL_MULTIGPU="${INSTALL_MULTIGPU:-true}"
MULTIGPU_DIR="$COMFYUI_PATH/custom_nodes/ComfyUI-MultiGPU"
MULTIGPU_DISABLED_DIR="$COMFYUI_PATH/custom_nodes.disabled/ComfyUI-MultiGPU"
if [[ "$INSTALL_MULTIGPU" == "true" ]]; then
  if [[ -d "$MULTIGPU_DISABLED_DIR" && ! -d "$MULTIGPU_DIR" ]]; then
    mkdir -p "$COMFYUI_PATH/custom_nodes"
    mv "$MULTIGPU_DISABLED_DIR" "$MULTIGPU_DIR" || true
    echo "✅ Re-enabled ComfyUI-MultiGPU"
  fi
else
  if [[ -d "$MULTIGPU_DIR" ]]; then
    mkdir -p "$COMFYUI_PATH/custom_nodes.disabled"
    rm -rf "$MULTIGPU_DISABLED_DIR" || true
    mv "$MULTIGPU_DIR" "$MULTIGPU_DISABLED_DIR" || true
    echo "⏭️  Disabled ComfyUI-MultiGPU (set INSTALL_MULTIGPU=true to enable)"
  fi
fi

# ComfyUI runs as COMFYUI_RUN_UID (setpriv). Image-built trees are root-owned:
# - custom_nodes*: pysssss.json, Manager .cnr-id, WAS config, etc.
# - ComfyUI-Custom-Scripts install_js() mkdirs under $COMFYUI_PATH/web/extensions/pysssss (PermissionError if missing/unwritable).
normalize_custom_nodes_for_writer() {
  [[ "${COMFYUI_SKIP_CUSTOM_NODES_CHOWN:-false}" == "true" ]] && return 0
  [[ "${COMFYUI_RUN_AS_ROOT}" == "true" ]] && return 0
  [[ "$(id -u)" -ne 0 ]] && return 0

  local uid="${COMFYUI_RUN_UID:-1000}"
  local gid="${COMFYUI_RUN_GID:-1000}"
  local force_chown="${COMFYUI_FORCE_CUSTOM_NODES_CHOWN:-false}"
  chown_tree_if_needed() {
    local d="$1"
    [[ -d "$d" ]] || return 0
    local owner
    owner="$(stat -c '%u:%g' "$d" 2>/dev/null || true)"
    if [[ "$force_chown" != "true" && "$owner" == "${uid}:${gid}" ]]; then
      echo "✅ Ownership already ${uid}:${gid} for $d; skipping recursive chown"
      return 0
    fi

    echo "🔧 Normalizing ownership under $d → ${uid}:${gid} (ComfyUI import + runtime writes)"
    chown -R "${uid}:${gid}" "$d" 2>/dev/null || echo "⚠️  chown failed on $d (skip or fix FS permissions)"
  }

  local d
  for d in "$COMFYUI_PATH/custom_nodes" "$COMFYUI_PATH/custom_nodes.disabled"; do
    chown_tree_if_needed "$d"
  done

  mkdir -p "$COMFYUI_PATH/web/extensions/pysssss"
  chown_tree_if_needed "$COMFYUI_PATH/web"
}

normalize_custom_nodes_for_writer

# Optional: background CivitAI downloader (non-fatal)
if [[ -x "$WORKSPACE_PATH/scripts/run_civitai_downloader.sh" ]]; then
  echo "⬇️  Starting CivitAI downloader in background"
  "$WORKSPACE_PATH/scripts/run_civitai_downloader.sh" || true &
fi

# Optional: curated manifest downloader (non-fatal)
# This downloads commonly-needed models for AnimateDiff + IP-Adapter + ControlNet workflows.
# Opt-in with:
#   AUTO_DOWNLOAD_MANIFEST_MODELS=true
#   MANIFEST_PROFILE=animatediff_ipadapter_controlnet
AUTO_DOWNLOAD_MANIFEST_MODELS="${AUTO_DOWNLOAD_MANIFEST_MODELS:-false}"
MANIFEST_PROFILE="${MANIFEST_PROFILE:-animatediff_ipadapter_controlnet}"
if [[ "$AUTO_DOWNLOAD_MANIFEST_MODELS" == "true" ]]; then
  if [[ -f "$WORKSPACE_PATH/scripts/model_download_manifest.yaml" && -f "$WORKSPACE_PATH/scripts/download_models_manifest.py" ]]; then
    echo "⬇️  Downloading curated model manifest (profile=$MANIFEST_PROFILE)"
    python3 "$WORKSPACE_PATH/scripts/download_models_manifest.py" --profile "$MANIFEST_PROFILE" || true
  else
    echo "⚠️  AUTO_DOWNLOAD_MANIFEST_MODELS=true but manifest downloader is missing in $WORKSPACE_PATH/scripts"
  fi
fi

# Optional: Krita AI Diffusion models (for the Krita *plugin* talking to ComfyUI — ComfyUI does not run Krita).
# Default false: avoids startup downloads / conflicts when you only use ComfyUI directly. Opt in: AUTO_DOWNLOAD_KRITA_AI_MODELS=true
# Preset: KRITA_DOWNLOAD_PRESET=--minimal | --recommended | --all
AUTO_DOWNLOAD_KRITA_AI_MODELS="${AUTO_DOWNLOAD_KRITA_AI_MODELS:-false}"
KRITA_DOWNLOAD_PRESET="${KRITA_DOWNLOAD_PRESET:---recommended}"
if [[ "$AUTO_DOWNLOAD_KRITA_AI_MODELS" == "true" ]]; then
  # Background: multi-GB Krita model sets must not block ComfyUI / Experiments UI startup.
  (
    if [[ -f "$WORKSPACE_PATH/scripts/model_download_manifest.yaml" && -f "$WORKSPACE_PATH/scripts/download_models_manifest.py" ]]; then
      echo "⬇️  Downloading Krita AI models (manifest profile krita_ai) into $COMFYUI_PATH/models (background)"
      python3 "$WORKSPACE_PATH/scripts/download_models_manifest.py" --profile krita_ai --models-dir "$COMFYUI_PATH/models" || true
    else
      echo "⚠️  AUTO_DOWNLOAD_KRITA_AI_MODELS=true but manifest downloader is missing in $WORKSPACE_PATH/scripts"
    fi
    if [[ -f /opt/krita-ai-diffusion/scripts/download_models.py ]]; then
      echo "⬇️  Running Krita AI Diffusion download script (preset: $KRITA_DOWNLOAD_PRESET) into $COMFYUI_PATH (background)"
      ( cd /opt/krita-ai-diffusion && python3 scripts/download_models.py "$COMFYUI_PATH" $KRITA_DOWNLOAD_PRESET ) || true
    else
      echo "ℹ️  Krita download script not found at /opt/krita-ai-diffusion/scripts/download_models.py (skip)"
    fi
  ) &
fi

# Optional: model alias/fixups (non-fatal)
# Copies known model files shipped in the repo into /ComfyUI/models when workflows expect them by name.
AUTO_FIXUP_MODEL_ALIASES="${AUTO_FIXUP_MODEL_ALIASES:-true}"
if [[ "$AUTO_FIXUP_MODEL_ALIASES" == "true" ]]; then
  if [[ -f "$WORKSPACE_PATH/scripts/ensure_model_aliases.py" && -f "$WORKSPACE_PATH/scripts/model_download_manifest.yaml" ]]; then
    echo "🔧 Ensuring model aliases"
    python3 "$WORKSPACE_PATH/scripts/ensure_model_aliases.py" || true
  fi
fi

# Base image startup script compatibility:
# `hearmeman/comfyui-wan-template` ships `/start_script.sh` which currently expects a
# repo layout that can change over time (it has broken for some users, causing crash loops).
# Default: start ComfyUI directly. Opt-in to base script by setting:
#   DELEGATE_TO_BASE_START_SCRIPT=true
DELEGATE_TO_BASE_START_SCRIPT="${DELEGATE_TO_BASE_START_SCRIPT:-false}"
if [[ "$DELEGATE_TO_BASE_START_SCRIPT" == "true" && -x "/start_script.sh" ]]; then
  echo "▶️  Delegating to base image /start_script.sh (DELEGATE_TO_BASE_START_SCRIPT=true)"
  exec /start_script.sh
fi

#
# Optional: Experiments UI server (separate port; reads /workspace output artifacts)
#
EXPERIMENTS_UI="${EXPERIMENTS_UI:-false}"
EXPERIMENTS_UI_PORT="${EXPERIMENTS_UI_PORT:-8790}"
ui_pid=""
if [[ "$EXPERIMENTS_UI" == "true" ]]; then
  if [[ -f "$WORKSPACE_PATH/scripts/experiments_ui_server.py" ]]; then
    # Build React UI once if dist is missing (uses npm in the container).
    EXPERIMENTS_UI_BUILD="${EXPERIMENTS_UI_BUILD:-true}"
    if [[ "$EXPERIMENTS_UI_BUILD" == "true" ]]; then
      if [[ ! -f "$WORKSPACE_PATH/experiments_ui/dist/index.html" ]]; then
        if command -v npm >/dev/null 2>&1; then
          echo "🧪 Building Experiments UI (React) into $WORKSPACE_PATH/experiments_ui/dist"
          if [[ -d "$WORKSPACE_PATH/experiments_ui/web" ]]; then
            pushd "$WORKSPACE_PATH/experiments_ui/web" >/dev/null
            # Prefer npm ci if lockfile exists.
            if [[ -f package-lock.json ]]; then
              run_as_comfy_writer npm ci
            else
              run_as_comfy_writer npm install
            fi
            run_as_comfy_writer npm run build
            popd >/dev/null
          else
            echo "⚠️  Experiments UI web dir not found: $WORKSPACE_PATH/experiments_ui/web (skipping build)"
          fi
        else
          echo "⚠️  npm not found; cannot build Experiments UI (set EXPERIMENTS_UI_BUILD=false to silence)"
        fi
      fi
    fi

    echo "🧪 Starting Experiments UI server on 0.0.0.0:$EXPERIMENTS_UI_PORT"
    run_as_comfy_writer python3 "$WORKSPACE_PATH/scripts/experiments_ui_server.py" --host 0.0.0.0 --port "$EXPERIMENTS_UI_PORT" --workspace-root "$WORKSPACE_PATH" &
    ui_pid="$!"
  else
    echo "⚠️  EXPERIMENTS_UI=true but missing $WORKSPACE_PATH/scripts/experiments_ui_server.py"
  fi
fi

if [[ "${COMFYUI_RUN_AS_ROOT}" != "true" ]] && [[ "$(id -u)" -eq 0 ]]; then
  echo "👤 ComfyUI + Experiments UI will run as uid:gid ${COMFYUI_RUN_UID}:${COMFYUI_RUN_GID} (setpriv); keep OUTPUT_SFTP / atmoz uid:gid the same."
fi

echo "▶️  Starting ComfyUI directly"
cd "$COMFYUI_PATH"
args=(--listen 0.0.0.0 --port 8188)

# Latent / VHS live previews need a non-"none" preview method. Default to auto so
# Work products and the Comfy UI get forming frames; override with COMFYUI_PREVIEW_METHOD
# (auto|latent2rgb|taesd|none) or pass --preview-method via COMFYUI_EXTRA_ARGS.
preview_method="${COMFYUI_PREVIEW_METHOD:-auto}"
if [[ -n "${preview_method}" && "${preview_method}" != "none" ]]; then
  args+=(--preview-method "${preview_method}")
elif [[ "${preview_method}" == "none" ]]; then
  args+=(--preview-method none)
fi

# Optional logging controls (helpful when diagnosing crash/restart loops).
# - COMFYUI_LOG_STDOUT=true  -> mirror logs to stdout (visible via `docker logs`)
# - COMFYUI_VERBOSE=INFO|DEBUG|WARNING|ERROR|CRITICAL -> set log level
# - COMFYUI_EXTRA_ARGS="--foo bar" -> pass through extra CLI args
# - COMFYUI_PREVIEW_METHOD=auto|latent2rgb|taesd|none -> sampler latent preview method
if [[ "${COMFYUI_LOG_STDOUT:-false}" == "true" ]]; then
  args+=(--log-stdout)
fi
if [[ -n "${COMFYUI_VERBOSE:-}" ]]; then
  args+=(--verbose "${COMFYUI_VERBOSE}")
fi
if [[ -n "${COMFYUI_EXTRA_ARGS:-}" ]]; then
  # shellcheck disable=SC2206
  extra_args=(${COMFYUI_EXTRA_ARGS})
  args+=("${extra_args[@]}")
fi

# Run ComfyUI as a child process so we can log a clear exit code.
# This makes it much easier to distinguish:
# - "real crash" (non-zero exit code, SIGKILL, OOM, etc)
# - vs clean shutdown (SIGTERM / exit 0)
set +e
run_as_comfy_writer python3 main.py "${args[@]}" &
comfy_pid="$!"

on_term() {
  echo "🛑 entrypoint: caught termination signal, forwarding to ComfyUI (pid=$comfy_pid)"
  kill -TERM "$comfy_pid" 2>/dev/null || true
  if [[ -n "${ui_pid:-}" ]]; then
    echo "🛑 entrypoint: stopping Experiments UI (pid=$ui_pid)"
    kill -TERM "$ui_pid" 2>/dev/null || true
  fi
}
trap on_term TERM INT

wait "$comfy_pid"
exit_code="$?"
echo "🧩 entrypoint: ComfyUI exited (exitCode=$exit_code)"
exit "$exit_code"

