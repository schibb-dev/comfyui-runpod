#!/usr/bin/env bash
# Run from the repo root inside WSL (or any Linux) after copying `.env`.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== comfyui-runpod WSL/dev check =="
echo "cwd: $ROOT"
uname -a

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker CLI not found. Install Docker / enable WSL integration."
  exit 1
fi
docker compose version

if [[ -f .env ]]; then
  echo "OK: .env present"
else
  echo "WARN: no .env (copy from .env.example; see README Host dev on WSL2)"
fi

if [[ -f .env ]] && grep -q '^COMFYUI_MODELS_DIR=' .env; then
  # shellcheck disable=SC1091
  M="$(grep '^COMFYUI_MODELS_DIR=' .env | head -1 | cut -d= -f2- | tr -d '\r')"
  M="${M//\"/}"
  if [[ -n "$M" ]]; then
    if [[ -d "$M" ]]; then
      echo "OK: COMFYUI_MODELS_DIR is a directory: $M"
    else
      echo "WARN: COMFYUI_MODELS_DIR is not a directory (yet?): $M"
    fi
  fi
fi

check_bind() {
  local var="$1"
  if [[ ! -f .env ]] || ! grep -q "^${var}=" .env; then
    return
  fi
  local p
  p="$(grep "^${var}=" .env | head -1 | cut -d= -f2- | tr -d '\r')"
  p="${p//\"/}"
  if [[ -n "$p" && -d "$p" ]]; then
    echo "OK: $var -> $p"
  elif [[ -n "$p" ]]; then
    echo "WARN: $var set but not a directory: $p"
  fi
}

if [[ -f .env ]]; then
  check_bind COMFYUI_BIND_INPUT_DIR
  check_bind COMFYUI_BIND_OUTPUT_DIR
  check_bind COMFYUI_BIND_USER_DIR
  check_bind COMFYUI_BIND_CREDENTIALS_DIR
fi

docker compose config >/dev/null
echo "OK: docker compose config"

if command -v node >/dev/null 2>&1; then
  echo "OK: node $(node --version)"
else
  echo "NOTE: node not on PATH (install Node in WSL for npm run ui:dev:start)"
fi

echo "== done =="
