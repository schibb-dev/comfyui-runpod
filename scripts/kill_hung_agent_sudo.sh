#!/usr/bin/env bash
# Kill hung Cursor agent sudo -A processes (waiting on cursor-askpass).
#
# Run from a NORMAL WSL terminal — NOT from inside a Cursor agent shell.
#   cd ~/src/comfyui-runpod
#   bash scripts/kill_hung_agent_sudo.sh          # preview + confirm
#   bash scripts/kill_hung_agent_sudo.sh --dry-run
#   bash scripts/kill_hung_agent_sudo.sh --yes
#
# For full reset (agents, workers, sockets), use:
#   bash scripts/cleanup_cursor_agent_askpass.sh

set -euo pipefail

DRY_RUN=0
YES=0

usage() {
  cat <<'EOF'
Usage: kill_hung_agent_sudo.sh [--dry-run] [--yes]

  --dry-run   Show hung sudo processes; make no changes
  --yes       Skip confirmation prompt
  -h, --help  This help

Finds and stops sudo -A processes (typically stuck on cursor-askpass).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -n | --dry-run) DRY_RUN=1 ;;
    -y | --yes) YES=1 ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [[ -n "${CURSOR_AGENT:-}" || -n "${CURSOR_ASKPASS_SOCKET:-}" ]]; then
  echo "ERROR: This looks like a Cursor agent shell (CURSOR_AGENT / CURSOR_ASKPASS_SOCKET set)." >&2
  echo "Open a normal Ubuntu / Windows Terminal tab and run this script there." >&2
  exit 1
fi

log() { printf '%s\n' "$*"; }

collect_hung_sudo_pids() {
  pgrep -f 'sudo -A' 2>/dev/null || true
}

SUDO_PIDS="$(collect_hung_sudo_pids | tr '\n' ' ' | xargs echo 2>/dev/null || true)"

log "=== Kill hung agent sudo ==="
log ""

if [[ -n "$SUDO_PIDS" ]]; then
  log "sudo -A processes:"
  ps -o pid,etime,cmd -p $SUDO_PIDS 2>/dev/null || true
else
  log "sudo -A processes: (none)"
  log ""
  log "Nothing to kill."
  exit 0
fi
log ""

if [[ "$DRY_RUN" -eq 1 ]]; then
  log "Dry run — no changes made."
  exit 0
fi

if [[ "$YES" -ne 1 ]]; then
  read -r -p "Kill these sudo processes? [y/N] " ans
  case "$ans" in
    y | Y | yes | YES) ;;
    *)
      log "Aborted."
      exit 0
      ;;
  esac
fi

for pid in $SUDO_PIDS; do
  if kill -0 "$pid" 2>/dev/null; then
    log "SIGTERM sudo pid $pid"
    kill -TERM "$pid" 2>/dev/null || true
  fi
done

sleep 1

for pid in $SUDO_PIDS; do
  if kill -0 "$pid" 2>/dev/null; then
    log "SIGKILL sudo pid $pid"
    kill -KILL "$pid" 2>/dev/null || true
  fi
done

log ""
log "Done. If the agent tab is still waiting, cancel it in Cursor."
log "If sudo keeps hanging, run:  bash scripts/cleanup_cursor_agent_askpass.sh"
