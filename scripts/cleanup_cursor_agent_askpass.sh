#!/usr/bin/env bash
# Reset stuck Cursor agent + askpass state (stale agents, sockets, hung sudo).
#
# Run from a NORMAL WSL terminal — NOT from inside a Cursor agent shell.
#   cd ~/src/comfyui-runpod
#   bash scripts/cleanup_cursor_agent_askpass.sh          # preview + confirm
#   bash scripts/cleanup_cursor_agent_askpass.sh --dry-run
#   bash scripts/cleanup_cursor_agent_askpass.sh --yes
#
# After cleanup, start a single fresh agent session (cursor agent / new chat).

set -euo pipefail

DRY_RUN=0
YES=0

usage() {
  cat <<'EOF'
Usage: cleanup_cursor_agent_askpass.sh [--dry-run] [--yes]

  --dry-run   Show what would be done; make no changes
  --yes       Skip confirmation prompt
  -h, --help  This help

Stops:
  - cursor agent / worker-server processes (all matching PIDs)
  - hung sudo waiting on cursor-askpass
  - stale /tmp/cursor-askpass-*.sock sockets (after agents are gone)
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
run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '[dry-run] '
    printf '%q ' "$@"
    printf '\n'
  else
    "$@"
  fi
}

collect_agent_pids() {
  pgrep -f '/home/yuji/\.local/bin/agent|cursor-agent/versions/.*/index\.js' 2>/dev/null || true
}

collect_worker_pids() {
  pgrep -f 'cursor-agent/versions/.*/index\.js worker-server' 2>/dev/null || true
}

collect_hung_sudo_pids() {
  # sudo blocked on cursor-askpass (parent chain often includes agent shell wrapper)
  pgrep -af 'sudo.*-A' 2>/dev/null | rg 'cursor-askpass|SUDO_ASKPASS' >/dev/null 2>&1 || true
  pgrep -f 'sudo -A' 2>/dev/null || true
}

list_sockets() {
  ls -1 /tmp/cursor-askpass-*.sock 2>/dev/null || true
}

AGENT_PIDS="$(collect_agent_pids | tr '\n' ' ' | xargs echo 2>/dev/null || true)"
WORKER_PIDS="$(collect_worker_pids | tr '\n' ' ' | xargs echo 2>/dev/null || true)"
SUDO_PIDS="$(collect_hung_sudo_pids | tr '\n' ' ' | xargs echo 2>/dev/null || true)"
SOCKETS="$(list_sockets | tr '\n' ' ' | xargs echo 2>/dev/null || true)"

log "=== Cursor agent / askpass cleanup ==="
log ""

if [[ -n "$AGENT_PIDS" ]]; then
  log "Agent processes:"
  ps -o pid,etime,cmd -p $AGENT_PIDS 2>/dev/null || true
else
  log "Agent processes: (none)"
fi
log ""

if [[ -n "$WORKER_PIDS" ]]; then
  log "Worker-server processes:"
  ps -o pid,etime,cmd -p $WORKER_PIDS 2>/dev/null || true
else
  log "Worker-server processes: (none)"
fi
log ""

if [[ -n "$SUDO_PIDS" ]]; then
  log "sudo -A processes (may be hung on askpass):"
  ps -o pid,etime,cmd -p $SUDO_PIDS 2>/dev/null || true
else
  log "sudo -A processes: (none)"
fi
log ""

if [[ -n "$SOCKETS" ]]; then
  log "Askpass sockets:"
  ls -la /tmp/cursor-askpass-*.sock 2>/dev/null || true
  if command -v ss >/dev/null 2>&1; then
    ss -lxp 2>/dev/null | rg cursor-askpass || true
  fi
else
  log "Askpass sockets: (none)"
fi
log ""

if [[ -z "$AGENT_PIDS" && -z "$WORKER_PIDS" && -z "$SUDO_PIDS" && -z "$SOCKETS" ]]; then
  log "Nothing to clean up."
  exit 0
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  log "Dry run — no changes made."
  exit 0
fi

if [[ "$YES" -ne 1 ]]; then
  read -r -p "Stop these processes and remove askpass sockets? [y/N] " ans
  case "$ans" in
    y | Y | yes | YES) ;;
    *)
      log "Aborted."
      exit 0
      ;;
  esac
fi

# Kill order: hung sudo first, then workers, then agents (TERM, then KILL)
for pid in $SUDO_PIDS; do
  if kill -0 "$pid" 2>/dev/null; then
    log "SIGTERM sudo pid $pid"
    run kill -TERM "$pid" 2>/dev/null || true
  fi
done

sleep 1

for pid in $WORKER_PIDS $AGENT_PIDS; do
  if kill -0 "$pid" 2>/dev/null; then
    log "SIGTERM pid $pid"
    run kill -TERM "$pid" 2>/dev/null || true
  fi
done

sleep 2

for pid in $SUDO_PIDS $WORKER_PIDS $AGENT_PIDS; do
  if kill -0 "$pid" 2>/dev/null; then
    log "SIGKILL pid $pid"
    run kill -KILL "$pid" 2>/dev/null || true
  fi
done

# Remove sockets only when nothing is listening (or file still exists orphaned)
for sock in /tmp/cursor-askpass-*.sock; do
  [[ -e "$sock" ]] || continue
  if command -v ss >/dev/null 2>&1 && ss -lx 2>/dev/null | rg -qF "$sock"; then
    log "SKIP socket still in use: $sock"
  else
    log "Remove socket $sock"
    run rm -f "$sock"
  fi
done

log ""
log "Done. Next:"
log "  1. Close any stuck Cursor agent UI tabs if still open."
log "  2. Start ONE fresh session:  cursor agent   (or a new chat in Cursor)."
log "  3. Test:  sudo -A true   and complete the askpass prompt once."
