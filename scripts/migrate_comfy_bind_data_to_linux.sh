#!/usr/bin/env bash
# Migrate COMFYUI_BIND_* trees from Windows mounts (/mnt/e/...) to the WSL ext4 disk ($DEST_ROOT).
# Models stay on E: via COMFYUI_MODELS_DIR (unchanged).
#
# Usage (from repo root):
#   bash scripts/migrate_comfy_bind_data_to_linux.sh
#   DEST_ROOT=/custom/path bash scripts/migrate_comfy_bind_data_to_linux.sh
#
# Stops Docker Compose stacks (main + output-sftp), rsyncs data, verifies sizes, updates .env,
# writes .migration_sources.env beside DEST_ROOT for the remove script, restarts stacks.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"
DEST_ROOT="${DEST_ROOT:-$HOME/comfyui-runpod-data}"

COMPOSE_MAIN="$REPO_ROOT/docker-compose.yml"
COMPOSE_SFTP="$REPO_ROOT/docker-compose.output-sftp.yml"

log() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ -f "$ENV_FILE" ]] || die "Missing $ENV_FILE"

read_env_val() {
  local key="$1"
  local line
  line="$(grep -E "^${key}=" "$ENV_FILE" | tail -1 || true)"
  echo "${line#*=}"
}

SRC_INPUT="$(read_env_val COMFYUI_BIND_INPUT_DIR)"
SRC_OUTPUT="$(read_env_val COMFYUI_BIND_OUTPUT_DIR)"
SRC_USER="$(read_env_val COMFYUI_BIND_USER_DIR)"
SRC_CRED="$(read_env_val COMFYUI_BIND_CREDENTIALS_DIR)"

[[ -n "$SRC_INPUT" && -n "$SRC_OUTPUT" && -n "$SRC_USER" && -n "$SRC_CRED" ]] ||
  die "Set COMFYUI_BIND_INPUT_DIR, OUTPUT, USER_DIR, CREDENTIALS_DIR in $ENV_FILE"

DEST_INPUT="$DEST_ROOT/input"
DEST_OUTPUT="$DEST_ROOT/output"
DEST_USER="$DEST_ROOT/comfyui_user"
DEST_CRED="$DEST_ROOT/credentials"

verify_du_pair() {
  local label="$1" src="$2" dst="$3"
  [[ -d "$src" ]] || die "Missing source dir: $src"
  mkdir -p "$dst"
  local sb db
  sb="$(du -sb "$src" | awk '{print $1}')"
  db="$(du -sb "$dst" | awk '{print $1}')"
  if [[ "$sb" != "$db" ]]; then
    die "Size mismatch for $label: source=$sb bytes dest=$db bytes"
  fi
  log "OK verify: $label ($sb bytes)"
}

stop_stack() {
  cd "$REPO_ROOT"
  if [[ -f "$COMPOSE_SFTP" ]]; then
    docker compose -f docker-compose.yml -f docker-compose.output-sftp.yml stop
  else
    docker compose stop
  fi
}

start_stack() {
  cd "$REPO_ROOT"
  if [[ -f "$COMPOSE_SFTP" ]]; then
    docker compose -f docker-compose.yml -f docker-compose.output-sftp.yml up -d
  else
    docker compose up -d comfyui watch_queue
  fi
}

rsync_copy() {
  local src="$1" dst="$2"
  log "rsync: $src -> $dst"
  mkdir -p "$dst"
  rsync -aHAX --numeric-ids --info=progress2 "${src}/" "${dst}/"
}

log "=== Comfy bind migration → Linux ext4 ==="
log "DEST_ROOT=$DEST_ROOT"
log "Sources from $ENV_FILE:"
log "  INPUT  $SRC_INPUT"
log "  OUTPUT $SRC_OUTPUT"
log "  USER   $SRC_USER"
log "  CRED   $SRC_CRED"
log ""

log "Stopping user systemd helpers (if any)…"
systemctl --user stop comfyui-runpod-vite.service 2>/dev/null || true

log "Stopping Docker Compose (comfyui stack + output-sftp if configured)…"
stop_stack

mkdir -p "$DEST_ROOT"

rsync_copy "$SRC_INPUT" "$DEST_INPUT"
rsync_copy "$SRC_OUTPUT" "$DEST_OUTPUT"
rsync_copy "$SRC_USER" "$DEST_USER"
rsync_copy "$SRC_CRED" "$DEST_CRED"

log ""
log "Verifying byte totals (du -sb)…"
verify_du_pair "input" "$SRC_INPUT" "$DEST_INPUT"
verify_du_pair "output" "$SRC_OUTPUT" "$DEST_OUTPUT"
verify_du_pair "comfyui_user" "$SRC_USER" "$DEST_USER"
verify_du_pair "credentials" "$SRC_CRED" "$DEST_CRED"

ENV_BACKUP="$ENV_FILE.bak.migrate.$(date +%Y%m%d%H%M%S)"
cp -a "$ENV_FILE" "$ENV_BACKUP"
log "Backed up .env to $ENV_BACKUP"

update_env_line() {
  local key="$1" val="$2"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    printf '\n%s=%s\n' "$key" "$val" >>"$ENV_FILE"
  fi
}

update_env_line COMFYUI_BIND_INPUT_DIR "$DEST_INPUT"
update_env_line COMFYUI_BIND_OUTPUT_DIR "$DEST_OUTPUT"
update_env_line COMFYUI_BIND_USER_DIR "$DEST_USER"
update_env_line COMFYUI_BIND_CREDENTIALS_DIR "$DEST_CRED"
# SFTP should expose the same host output tree Comfy uses
update_env_line OUTPUT_SFTP_ROOT "$DEST_OUTPUT"

SOURCES_RECORD="$DEST_ROOT/.migration_sources.env"
cat >"$SOURCES_RECORD" <<EOF
# Written by migrate_comfy_bind_data_to_linux.sh — original paths (safe to delete after verify)
# Run: REMOVE_E_SHADOW_CONFIRM=yes bash scripts/remove_e_shadow_after_migration_verify.sh
MIGRATION_DEST_ROOT=$DEST_ROOT
SRC_INPUT=$SRC_INPUT
SRC_OUTPUT=$SRC_OUTPUT
SRC_USER=$SRC_USER
SRC_CRED=$SRC_CRED
ENV_BACKUP=$ENV_BACKUP
EOF
chmod 600 "$SOURCES_RECORD" 2>/dev/null || true
log "Recorded original paths in $SOURCES_RECORD"

log ""
log "Starting Docker Compose…"
start_stack

log "Optional: systemctl --user start comfyui-runpod-docker.service comfyui-runpod-vite.service"
systemctl --user start comfyui-runpod-docker.service 2>/dev/null || true
systemctl --user start comfyui-runpod-vite.service 2>/dev/null || true

log ""
log "Done. Next:"
log "  1. Smoke-test Comfy (8188) and experiments UI (8790)."
log "  2. When satisfied, delete E: shadow copies:"
log "     REMOVE_E_SHADOW_CONFIRM=yes bash scripts/remove_e_shadow_after_migration_verify.sh"
