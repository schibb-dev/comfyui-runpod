#!/usr/bin/env bash
# Delete original COMFYUI_BIND_* trees under /mnt/e after a successful migration.
# ONLY removes paths listed in $DEST_ROOT/.migration_sources.env and only if they are under /mnt/e/.
#
# Usage:
#   REMOVE_E_SHADOW_CONFIRM=yes bash scripts/remove_e_shadow_after_migration_verify.sh
# Optional:
#   DEST_ROOT=/home/you/comfyui-runpod-data bash scripts/remove_e_shadow_after_migration_verify.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST_ROOT="${DEST_ROOT:-$HOME/comfyui-runpod-data}"
SOURCES_RECORD="$DEST_ROOT/.migration_sources.env"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ "${REMOVE_E_SHADOW_CONFIRM:-}" == "yes" ]] ||
  die "Refusing to delete: set REMOVE_E_SHADOW_CONFIRM=yes"

[[ -f "$SOURCES_RECORD" ]] || die "Missing $SOURCES_RECORD — run migrate_comfy_bind_data_to_linux.sh first"

# shellcheck source=/dev/null
source "$SOURCES_RECORD"

log() { printf '%s\n' "$*"; }

verify_du_pair() {
  local label="$1" src="$2" dst="$3"
  [[ -d "$src" ]] || { log "skip verify (no src): $src"; return 0; }
  [[ -d "$dst" ]] || die "Missing dest: $dst"
  local sb db
  sb="$(du -sb "$src" | awk '{print $1}')"
  db="$(du -sb "$dst" | awk '{print $1}')"
  [[ "$sb" == "$db" ]] || die "Re-verify failed for $label: src=$sb dst=$db"
  printf 'OK re-verify %s (%s bytes)\n' "$label" "$sb"
}

log "Re-verifying sizes before delete…"
verify_du_pair "input" "${SRC_INPUT:?}" "${DEST_INPUT:-$DEST_ROOT/input}"
verify_du_pair "output" "${SRC_OUTPUT:?}" "${DEST_OUTPUT:-$DEST_ROOT/output}"
verify_du_pair "user" "${SRC_USER:?}" "${DEST_USER:-$DEST_ROOT/comfyui_user}"
verify_du_pair "cred" "${SRC_CRED:?}" "${DEST_CRED:-$DEST_ROOT/credentials}"

safe_rm_tree() {
  local path="$1"
  case "$path" in
    /mnt/e/*)
      if [[ -e "$path" ]]; then
        log "Removing: $path"
        rm -rf "$path"
      else
        log "Already gone: $path"
      fi
      ;;
    "")
      die "empty path"
      ;;
    *)
      die "Refusing to remove non-/mnt/e path: $path"
      ;;
  esac
}

log "Removing shadow directories on E: (only under /mnt/e/)…"
safe_rm_tree "$SRC_INPUT"
safe_rm_tree "$SRC_OUTPUT"
safe_rm_tree "$SRC_USER"
safe_rm_tree "$SRC_CRED"

# Optional: remove empty parent dirs up to shadow root
PARENT="$(dirname "$SRC_INPUT")"
while [[ "$PARENT" == /mnt/e/comfyui-runpod-shadow/* ]]; do
  if [[ -d "$PARENT" ]] && [[ -z "$(ls -A "$PARENT" 2>/dev/null)" ]]; then
    log "Removing empty dir: $PARENT"
    rmdir "$PARENT" 2>/dev/null || break
  fi
  PARENT="$(dirname "$PARENT")"
done

log "Done. Original .env backup was: ${ENV_BACKUP:-unknown}"
