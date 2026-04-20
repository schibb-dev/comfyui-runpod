#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# TRANSITION / MIGRATION HELPER (WSL — bash)
# ---------------------------------------------------------------------------
# One-time (or rare): point a Linux checkout at the E: shadow tree mounted
# as /mnt/e/comfyui-runpod-shadow — copies token files into workspace/,
# seeds .env from the shadow if missing, appends COMFYUI_BIND_* for Compose.
# See README "Host dev on WSL2 (cutover from Windows)". After cutover, normal
# workflow is docker compose + .env on this clone; you do not need to re-run
# this script unless you rebuild the clone or change shadow layout.
# ---------------------------------------------------------------------------
# Usage: from repo root in WSL: ./scripts/wsl_setup_from_shadow.sh
#        or: ./scripts/wsl_setup_from_shadow.sh /path/to/comfyui-runpod
set -euo pipefail

REPO_ROOT="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
SHADOW="/mnt/e/comfyui-runpod-shadow"
cd "$REPO_ROOT"

if [[ ! -d "$SHADOW" ]]; then
  echo "ERROR: shadow not found at $SHADOW (create it from Windows first)."
  exit 1
fi

for need in workspace/input workspace/output workspace/comfyui_user; do
  if [[ ! -d "$SHADOW/$need" ]]; then
    echo "ERROR: expected $SHADOW/$need"
    exit 1
  fi
done

# Workspace token files (ignored by git) — keep using shadow copies in place.
for tok in .civitai_token .hf_token .huggingface_token; do
  if [[ -f "$SHADOW/workspace/$tok" ]]; then
    install -m 0600 "$SHADOW/workspace/$tok" "workspace/$tok"
    echo "OK: workspace/$tok from shadow"
  fi
done

ENV_FILE="$REPO_ROOT/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f "$SHADOW/.env" ]]; then
    cp -a "$SHADOW/.env" "$ENV_FILE"
    sed -i 's/\r$//' "$ENV_FILE" || true
    echo "OK: copied .env from shadow (review paths inside)"
  else
    echo "WARN: no $SHADOW/.env — copy .env.example to .env and fill in."
  fi
fi

append_binds() {
  grep -q '^COMFYUI_BIND_OUTPUT_DIR=' "$ENV_FILE" 2>/dev/null && return
  cat >>"$ENV_FILE" <<'EOF'

# WSL + E: shadow (added by scripts/wsl_setup_from_shadow.sh)
COMFYUI_BIND_INPUT_DIR=/mnt/e/comfyui-runpod-shadow/workspace/input
COMFYUI_BIND_OUTPUT_DIR=/mnt/e/comfyui-runpod-shadow/workspace/output
COMFYUI_BIND_USER_DIR=/mnt/e/comfyui-runpod-shadow/workspace/comfyui_user
COMFYUI_BIND_CREDENTIALS_DIR=/mnt/e/comfyui-runpod-shadow/credentials
EOF
  echo "OK: appended COMFYUI_BIND_* lines to .env"
}

if [[ -f "$ENV_FILE" ]]; then
  # Normalize models path for WSL (best-effort; avoids duplicate keys).
  sed -i 's|^COMFYUI_MODELS_DIR=E:/models|COMFYUI_MODELS_DIR=/mnt/e/models|' "$ENV_FILE" || true
  sed -i 's|^COMFYUI_MODELS_DIR=E:\\\\models|COMFYUI_MODELS_DIR=/mnt/e/models|' "$ENV_FILE" || true
  if ! grep -q '^COMFYUI_MODELS_DIR=' "$ENV_FILE"; then
    printf '\nCOMFYUI_MODELS_DIR=/mnt/e/models\n' >>"$ENV_FILE"
    echo "OK: added COMFYUI_MODELS_DIR=/mnt/e/models"
  fi
  append_binds
fi

echo "Next: docker compose config && ./scripts/wsl_dev_check.sh"
