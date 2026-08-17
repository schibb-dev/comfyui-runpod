#!/usr/bin/env bash
# Wait until Docker Desktop is ready and COMFYUI_BIND_* host paths exist before compose up.
# Used by comfyui-runpod-docker.service (see scripts/install-systemd-boot.sh).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"

DOCKER_WAIT_S="${DOCKER_BOOT_WAIT_S:-180}"
PATH_WAIT_S="${COMFYUI_BIND_WAIT_S:-120}"
CHECK_ONLY=0
if [[ "${1:-}" == "--check-only" ]]; then
  CHECK_ONLY=1
fi

read_env() {
  local key="$1" default="${2:-}"
  if [[ ! -f "$ENV_FILE" ]]; then
    printf '%s' "$default"
    return 0
  fi
  local line
  line="$(grep -E "^${key}=" "$ENV_FILE" | tail -1 || true)"
  if [[ -z "$line" ]]; then
    printf '%s' "$default"
  else
    printf '%s' "${line#*=}"
  fi
}

docker_ready() {
  # Docker Desktop: `docker info` can hang; socket path may not be /var/run/docker.sock.
  timeout 8 docker ps >/dev/null 2>&1
}

wait_for_docker() {
  if [[ "$CHECK_ONLY" -eq 1 ]]; then
    if docker_ready; then
      return 0
    fi
    echo "Docker not ready (socket + docker info)." >&2
    return 1
  fi
  local i max=$((DOCKER_WAIT_S / 2))
  for ((i = 1; i <= max; i++)); do
    if docker_ready; then
      return 0
    fi
    sleep 2
  done
  echo "Timed out after ${DOCKER_WAIT_S}s waiting for Docker (socket + docker info)." >&2
  return 1
}

wait_for_dir() {
  local dir="$1" label="$2"
  if [[ "$CHECK_ONLY" -eq 1 ]]; then
    if [[ -d "$dir" ]]; then
      return 0
    fi
    echo "Missing ${label}: ${dir}" >&2
    return 1
  fi
  local i max=$((PATH_WAIT_S / 2))
  for ((i = 1; i <= max; i++)); do
    if [[ -d "$dir" ]]; then
      return 0
    fi
    sleep 2
  done
  echo "Timed out after ${PATH_WAIT_S}s waiting for ${label}: ${dir}" >&2
  return 1
}

INPUT="$(read_env COMFYUI_BIND_INPUT_DIR "$REPO_ROOT/workspace/input")"
OUTPUT="$(read_env COMFYUI_BIND_OUTPUT_DIR "$REPO_ROOT/workspace/output")"
USER_DIR="$(read_env COMFYUI_BIND_USER_DIR "$REPO_ROOT/workspace/comfyui_user")"
WORKFLOWS="$(read_env COMFYUI_BIND_WORKFLOWS_DIR "$USER_DIR/default/workflows")"

wait_for_docker
wait_for_dir "$INPUT" "COMFYUI_BIND_INPUT_DIR"
wait_for_dir "$OUTPUT" "COMFYUI_BIND_OUTPUT_DIR"
wait_for_dir "$USER_DIR" "COMFYUI_BIND_USER_DIR"
wait_for_dir "$WORKFLOWS" "COMFYUI_BIND_WORKFLOWS_DIR"

printf 'compose boot preflight OK (docker + bind paths)\n'
printf '  input=%s\n' "$INPUT"
printf '  output=%s\n' "$OUTPUT"
printf '  user=%s\n' "$WORKFLOWS"
