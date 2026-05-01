#!/usr/bin/env bash
# Copy workspace/input|output|comfyui_user from NTFS (C: or E: shadow) onto WSL ext4.
# Does not remove Windows sources. Run after ./scripts/wsl_setup_ext4_data.sh --mkdir-only
# or ensure COMFYUI_EXT4_DATA_ROOT directories exist.
#
# Usage:
#   ./scripts/wsl_sync_workspace_from_windows.sh [--dry-run] [--source auto|shadow|c]
#
# Env:
#   COMFYUI_EXT4_DATA_ROOT  default ~/data/comfyui-runpod
#   WIN_REPO                default: first existing under /mnt/c/Users/*/Code/comfyui-runpod
#   COMFYUI_SHADOW_ROOT     default /mnt/e/comfyui-runpod-shadow
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

DRY_RUN=0
SOURCE_MODE="auto"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --source)
      SOURCE_MODE="${2:?}"
      shift 2
      ;;
    *)
      echo "Unknown arg: $1 (try --dry-run, --source auto|shadow|c)"
      exit 2
      ;;
  esac
done

DATA_ROOT="${COMFYUI_EXT4_DATA_ROOT:-$HOME/data/comfyui-runpod}"
SHADOW="${COMFYUI_SHADOW_ROOT:-/mnt/e/comfyui-runpod-shadow}"

case "$SOURCE_MODE" in
  auto|shadow|c|win|windows) ;;
  *)
    echo "ERROR: --source must be auto, shadow, c, or win"
    exit 2
    ;;
esac

pick_win_repo() {
  if [[ -n "${WIN_REPO:-}" ]]; then
    echo "$WIN_REPO"
    return
  fi
  local u wsl_user
  wsl_user="$(whoami)"
  for candidate in \
    "/mnt/c/Users/${wsl_user}/Code/comfyui-runpod" \
    "/mnt/c/Users/yuji/Code/comfyui-runpod"; do
    if [[ -d "$candidate" ]]; then
      echo "$candidate"
      return
    fi
  done
  echo "/mnt/c/Users/${wsl_user}/Code/comfyui-runpod"
}

WIN_REPO="$(pick_win_repo)"

RSYNC_EXCLUDES=(
  --exclude='.git/'
  --exclude='node_modules/'
  --exclude='__pycache__/'
  --exclude='dist/'
  --exclude='*.pyc'
)

# NTFS viaDrvFs: avoid preserving broken owner/perms; normalize on ext4.
RSYNC_BASE=(rsync -aH --info=progress2)
RSYNC_BASE+=(--no-owner --no-group --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r)

if [[ "$DRY_RUN" -eq 1 ]]; then
  RSYNC_BASE+=(--dry-run)
fi

pick_source_root() {
  local mode="$1"
  local s_win="$WIN_REPO/workspace"
  local s_shadow="$SHADOW/workspace"

  if [[ "$mode" == "shadow" ]]; then
    echo "$s_shadow"
    return
  fi
  if [[ "$mode" == "c" || "$mode" == "win" || "$mode" == "windows" ]]; then
    echo "$s_win"
    return
  fi

  # auto: prefer shadow if it exists and has input dir with files
  if [[ -d "$s_shadow/input" ]]; then
    local n
    n="$(find "$s_shadow/input" -type f 2>/dev/null | wc -l)"
    if [[ "$n" -gt 0 ]]; then
      echo "$s_shadow"
      return
    fi
  fi
  echo "$s_win"
}

SRC_BASE="$(pick_source_root "$SOURCE_MODE")"

if [[ ! -d "$SRC_BASE" ]]; then
  echo "ERROR: source workspace not found: $SRC_BASE"
  echo "Set WIN_REPO or COMFYUI_SHADOW_ROOT, or pass --source shadow|c"
  exit 1
fi

for sub in input output comfyui_user; do
  if [[ ! -d "$SRC_BASE/$sub" ]]; then
    echo "WARN: missing $SRC_BASE/$sub (skipping that subdir)"
  fi
done

mkdir -p "$DATA_ROOT/input" "$DATA_ROOT/output" "$DATA_ROOT/comfyui_user"

echo "== wsl_sync_workspace_from_windows =="
echo "SOURCE_MODE=$SOURCE_MODE"
echo "SRC_BASE=$SRC_BASE"
echo "DST_ROOT=$DATA_ROOT"
echo "DRY_RUN=$DRY_RUN"
echo ""

sync_one() {
  local name="$1"
  local src="$SRC_BASE/$name"
  local dst="$DATA_ROOT/$name"
  if [[ ! -d "$src" ]]; then
    echo "Skip (no source dir): $src"
    return 0
  fi
  echo "--- rsync $name ---"
  "${RSYNC_BASE[@]}" "${RSYNC_EXCLUDES[@]}" "$src/" "$dst/"
}

sync_one input
sync_one output
sync_one comfyui_user

echo ""
echo "OK: sync finished. Next:"
echo "  ./scripts/wsl_setup_ext4_data.sh --write-env   # point .env COMFYUI_BIND_* at $DATA_ROOT"
echo "  ./scripts/wsl_dev_check.sh && docker compose config"
echo "  ./scripts/compare_workspace_input.sh    # optional parity check vs Windows"
