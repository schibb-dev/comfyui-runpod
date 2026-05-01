#!/usr/bin/env bash
# Disk and workspace inventory for WSL cutover (read-only).
# Usage: from repo root in WSL: ./scripts/wsl_migration_inventory.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# Override if your Windows checkout or shadow live elsewhere.
pick_win_repo() {
  if [[ -n "${WIN_REPO:-}" ]]; then
    echo "$WIN_REPO"
    return
  fi
  local u
  u="$(whoami)"
  for candidate in \
    "/mnt/c/Users/${u}/Code/comfyui-runpod" \
    "/mnt/c/Users/yuji/Code/comfyui-runpod"; do
    if [[ -d "$candidate" ]]; then
      echo "$candidate"
      return
    fi
  done
  echo "/mnt/c/Users/${u}/Code/comfyui-runpod"
}
WIN_REPO="$(pick_win_repo)"
SHADOW="${COMFYUI_SHADOW_ROOT:-/mnt/e/comfyui-runpod-shadow}"
DATA_ROOT="${COMFYUI_EXT4_DATA_ROOT:-$HOME/data/comfyui-runpod}"

echo "== comfyui-runpod migration inventory =="
echo "repo:       $REPO_ROOT"
echo "WIN_REPO:   $WIN_REPO"
echo "SHADOW:     $SHADOW"
echo "EXT4 data:  $DATA_ROOT (planned / canonical hot paths)"
echo ""

echo "== df -h (WSL root, Windows mounts, ext4 data parent) =="
df -h / /mnt/c /mnt/e 2>/dev/null || true
df -h "$(dirname "$DATA_ROOT")" 2>/dev/null || true
echo ""

summarize_dir() {
  local label="$1"
  local path="$2"
  if [[ ! -d "$path" ]]; then
    printf "%-26s %s\n" "$label" "(missing)"
    return
  fi
  local cnt sz
  cnt="$(find "$path" -type f 2>/dev/null | wc -l)"
  sz="$(du -sh "$path" 2>/dev/null | awk '{print $1}')"
  printf "%-26s %8s files  %8s  %s\n" "$label" "$cnt" "$sz" "$path"
}

echo "== Directory sizes (file counts may be slow on NTFS) =="
summarize_dir "win workspace"           "$WIN_REPO/workspace"
summarize_dir "win workspace/input"     "$WIN_REPO/workspace/input"
summarize_dir "win workspace/output"    "$WIN_REPO/workspace/output"
summarize_dir "shadow workspace"        "$SHADOW/workspace"
summarize_dir "shadow input"            "$SHADOW/workspace/input"
summarize_dir "shadow output"           "$SHADOW/workspace/output"
summarize_dir "repo workspace (linux)"  "$REPO_ROOT/workspace"
summarize_dir "repo input"              "$REPO_ROOT/workspace/input"
summarize_dir "repo output"             "$REPO_ROOT/workspace/output"
summarize_dir "ext4 DATA_ROOT input"    "$DATA_ROOT/input"
summarize_dir "ext4 DATA_ROOT output"   "$DATA_ROOT/output"
echo ""

if [[ -f .env ]] && grep -q '^COMFYUI_BIND_INPUT_DIR=' .env; then
  echo "== .env COMFYUI_BIND_* (first line each) =="
  grep -E '^COMFYUI_BIND_|^COMFYUI_MODELS_DIR=' .env | head -20 || true
  echo ""
fi

echo "== done (read-only) =="
