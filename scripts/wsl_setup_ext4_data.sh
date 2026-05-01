#!/usr/bin/env bash
# Create hot-data dirs on WSL ext4 and optionally point COMFYUI_BIND_* at them.
# Default root: ~/data/comfyui-runpod (override with COMFYUI_EXT4_DATA_ROOT).
#
# Usage (from repo root in WSL):
#   ./scripts/wsl_setup_ext4_data.sh --mkdir-only     # dirs only, no .env
#   ./scripts/wsl_setup_ext4_data.sh --write-env      # update .env COMFYUI_BIND_* (backs up .env)
#   ./scripts/wsl_setup_ext4_data.sh                  # mkdir + write-env
#
# After syncing data (see wsl_sync_workspace_from_windows.sh), run ./scripts/wsl_dev_check.sh.
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

MKDIR_ONLY=0
WRITE_ENV=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mkdir-only) MKDIR_ONLY=1 ;;
    --write-env) WRITE_ENV=1 ;;
    --no-write-env) WRITE_ENV=0 ;;
    *) echo "Unknown option: $1"; exit 2 ;;
  esac
  shift
done

DATA_ROOT="${COMFYUI_EXT4_DATA_ROOT:-$HOME/data/comfyui-runpod}"

if [[ "$MKDIR_ONLY" -eq 1 ]]; then
  WRITE_ENV=0
fi

mkdir -p "$DATA_ROOT/input" "$DATA_ROOT/output" "$DATA_ROOT/comfyui_user"
chmod 755 "$DATA_ROOT" "$DATA_ROOT/input" "$DATA_ROOT/output" "$DATA_ROOT/comfyui_user" 2>/dev/null || true

echo "OK: ext4 data dirs at $DATA_ROOT"

ENV_FILE="$REPO_ROOT/.env"
stamp="$(date +%Y%m%d%H%M%S)"

update_env_binds() {
  [[ "$WRITE_ENV" -eq 1 ]] || return 0
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "WARN: no .env — copy from .env.example, then re-run with --write-env"
    return 0
  fi

  cp -a "$ENV_FILE" "${ENV_FILE}.bak.${stamp}"
  echo "OK: backed up .env to ${ENV_FILE}.bak.${stamp}"

  # Strip prior ext4-bind block if we added one before (idempotent re-run).
  if grep -q '^# comfyui-runpod ext4 binds (wsl_setup_ext4_data)' "$ENV_FILE" 2>/dev/null; then
    sed -i '/^# comfyui-runpod ext4 binds (wsl_setup_ext4_data)/,/^# end ext4 binds/d' "$ENV_FILE"
  fi

  # Remove standalone COMFYUI_BIND_* lines so we do not duplicate keys.
  sed -i '/^COMFYUI_BIND_INPUT_DIR=/d;/^COMFYUI_BIND_OUTPUT_DIR=/d;/^COMFYUI_BIND_USER_DIR=/d;/^COMFYUI_BIND_CREDENTIALS_DIR=/d' "$ENV_FILE"

  {
    echo ""
    echo "# comfyui-runpod ext4 binds (wsl_setup_ext4_data)"
    echo "COMFYUI_BIND_INPUT_DIR=${DATA_ROOT}/input"
    echo "COMFYUI_BIND_OUTPUT_DIR=${DATA_ROOT}/output"
    echo "COMFYUI_BIND_USER_DIR=${DATA_ROOT}/comfyui_user"
    echo "# Credentials: keep in-repo ./credentials or set an explicit host path:"
    echo "# COMFYUI_BIND_CREDENTIALS_DIR=${REPO_ROOT}/credentials"
    echo "# end ext4 binds"
  } >>"$ENV_FILE"

  # Normalize models path for WSL if still Windows-style.
  sed -i 's|^COMFYUI_MODELS_DIR=E:/models|COMFYUI_MODELS_DIR=/mnt/e/models|' "$ENV_FILE" || true
  sed -i 's|^COMFYUI_MODELS_DIR=E:\\\\models|COMFYUI_MODELS_DIR=/mnt/e/models|' "$ENV_FILE" || true

  echo "OK: wrote COMFYUI_BIND_* for INPUT/OUTPUT/USER to .env"
}

update_env_binds

echo "Next: ./scripts/wsl_sync_workspace_from_windows.sh [--dry-run]"
echo "       ./scripts/wsl_dev_check.sh"
