#!/usr/bin/env bash
# OPTIONAL second phase: archive Windows-side trees and optionally delete them.
#
# Do NOT run until WSL + Docker + Experiments UI are verified against ext4 data
# (see scripts/wsl_setup_ext4_data.sh + wsl_sync_workspace_from_windows.sh).
#
# Default: prints what would happen (no writes). Archive/removal require explicit gates.
#
# Usage:
#   ./scripts/wsl_archive_windows_after_verify.sh
#   COMFYUI_I_HAVE_VERIFIED_WSL=1 ./scripts/wsl_archive_windows_after_verify.sh --archive-secrets
#   COMFYUI_I_HAVE_VERIFIED_WSL=1 ./scripts/wsl_archive_windows_after_verify.sh --archive-secrets --tarball-workspace-win
#
# Dangerous (only after backups elsewhere):
#   COMFYUI_I_HAVE_VERIFIED_WSL=1 COMFYUI_CONFIRM_DELETE_WINDOWS=1 \
#     ./scripts/wsl_archive_windows_after_verify.sh --delete-windows-sources
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

ARCHIVE_SECRETS=0
TARBALL_WIN_WS=0
DELETE_WIN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --archive-secrets) ARCHIVE_SECRETS=1 ;;
    --tarball-workspace-win) TARBALL_WIN_WS=1 ;;
    --delete-windows-sources) DELETE_WIN=1 ;;
    *) echo "Unknown option: $1"; exit 2 ;;
  esac
  shift
done

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
ARCHIVE_ROOT="${COMFYUI_ARCHIVE_ROOT:-$HOME/archive/comfyui-runpod-windows-retired-$(date +%Y%m%d)}"

verified="${COMFYUI_I_HAVE_VERIFIED_WSL:-}"
confirm_del="${COMFYUI_CONFIRM_DELETE_WINDOWS:-}"

echo "== wsl_archive_windows_after_verify (dry-run by default) =="
echo "WIN_REPO=$WIN_REPO"
echo "SHADOW=$SHADOW"
echo "ARCHIVE_ROOT=$ARCHIVE_ROOT"
echo "flags: ARCHIVE_SECRETS=$ARCHIVE_SECRETS TARBALL_WIN_WS=$TARBALL_WIN_WS DELETE_WIN=$DELETE_WIN"
echo ""

if [[ "$ARCHIVE_SECRETS" -eq 1 || "$TARBALL_WIN_WS" -eq 1 || "$DELETE_WIN" -eq 1 ]]; then
  if [[ "$verified" != "1" ]]; then
    echo "BLOCKED: set COMFYUI_I_HAVE_VERIFIED_WSL=1 after you confirm WSL stack works."
    exit 1
  fi
fi

mkdir -p "$ARCHIVE_ROOT"

if [[ "$ARCHIVE_SECRETS" -eq 1 ]]; then
  echo "Archiving non-repo secrets snapshots under $ARCHIVE_ROOT/secrets ..."
  mkdir -p "$ARCHIVE_ROOT/secrets"
  for f in .env .env.local .env.development .env.production; do
    for base in "$WIN_REPO" "$SHADOW"; do
      if [[ -f "$base/$f" ]]; then
        cp -a "$base/$f" "$ARCHIVE_ROOT/secrets/$(basename "$base")_$f" 2>/dev/null || true
      fi
    done
  done
  if [[ -d "$WIN_REPO/credentials" ]]; then
    rsync -a "$WIN_REPO/credentials/" "$ARCHIVE_ROOT/secrets/credentials-from-win-repo/"
  fi
  if [[ -d "$SHADOW/credentials" ]]; then
    rsync -a "$SHADOW/credentials/" "$ARCHIVE_ROOT/secrets/credentials-from-shadow/"
  fi
  echo "OK: secrets copied."
else
  echo "[dry-run] Would copy .env* and credentials from WIN_REPO and SHADOW -> $ARCHIVE_ROOT/secrets"
fi

if [[ "$TARBALL_WIN_WS" -eq 1 ]]; then
  mkdir -p "$ARCHIVE_ROOT/tarballs"
  out="$ARCHIVE_ROOT/tarballs/workspace-from-$(basename "$WIN_REPO" | tr '/' '-').tgz"
  echo "Creating $out (may take a long time; excludes node_modules/dist/__pycache__/.git) ..."
  tar -C "$WIN_REPO" \
    --exclude='workspace/node_modules' \
    --exclude='**/node_modules' \
    --exclude='**/dist' \
    --exclude='**/__pycache__' \
    --exclude='**/.git' \
    -czf "$out" workspace 2>/dev/null || {
    echo "WARN: tarball failed — check disk space and paths."
    exit 1
  }
  echo "OK: $out"
else
  echo "[dry-run] Would optionally tarball $WIN_REPO/workspace (pass --tarball-workspace-win)"
fi

if [[ "$DELETE_WIN" -eq 1 ]]; then
  if [[ "$confirm_del" != "1" ]]; then
    echo "BLOCKED: deleting Windows sources requires COMFYUI_CONFIRM_DELETE_WINDOWS=1 as well."
    exit 1
  fi
  echo "ERROR: automated deletion disabled — remove/rename Windows folders manually after verifying backups."
  echo "Targets you might delete manually once satisfied:"
  echo "  $WIN_REPO/workspace (or entire repo clone)"
  echo "  $SHADOW/workspace (optional; keep if still using as cold storage)"
  exit 2
else
  echo "[dry-run] No Windows deletes (pass --delete-windows-sources only records policy; use manual rm)."
fi

echo ""
echo "Done. Reminder: git-tracked code lives on GitHub; keep ~/archive on ext4 or move cold storage to E:."
