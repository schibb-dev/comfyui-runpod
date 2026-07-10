#!/usr/bin/env bash
# Hourly lineage backfill progress ticks for agent notifications.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MONITOR="$ROOT/scripts/monitor_lineage_backfill.sh"
PIDFILE=/tmp/lineage_hourly_loop.pid
LOG=/tmp/lineage_hourly_loop.log
INTERVAL_SEC="${LINEAGE_NOTIFY_INTERVAL_SEC:-3600}"

stop_existing() {
  if [[ -f "$PIDFILE" ]]; then
    local old
    old="$(cat "$PIDFILE")"
    if kill -0 "$old" 2>/dev/null; then
      kill "$old" 2>/dev/null || true
      sleep 1
    fi
    rm -f "$PIDFILE"
  fi
  # Retire legacy 10-minute loop if still running.
  if [[ -f /tmp/lineage_progress_loop.pid ]]; then
    local legacy
    legacy="$(cat /tmp/lineage_progress_loop.pid)"
    if kill -0 "$legacy" 2>/dev/null; then
      kill "$legacy" 2>/dev/null || true
    fi
    rm -f /tmp/lineage_progress_loop.pid
  fi
}

case "${1:-start}" in
  start)
    stop_existing
    (
      while true; do
        sleep "$INTERVAL_SEC"
        payload="$("$MONITOR")"
        printf 'AGENT_LOOP_TICK_lineage_hourly %s\n' "$payload"
      done
    ) &
    disown
    echo $! >"$PIDFILE"
    echo "started hourly loop pid=$(cat "$PIDFILE") interval=${INTERVAL_SEC}s log=$LOG"
    ;;
  stop)
    stop_existing
    echo "stopped hourly lineage loop"
    ;;
  status)
    if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "running pid=$(cat "$PIDFILE")"
    else
      echo "not running"
    fi
    ;;
  *)
    echo "usage: $0 {start|stop|status}" >&2
    exit 1
    ;;
esac
