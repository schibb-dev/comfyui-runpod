#!/usr/bin/env bash
# Drain factory pending jobs onto Comfy as soon as the waiting queue is empty.
# Does not generate new hourly work — only ``submit --pending-only``.
# Install: bash scripts/install-shape-factory-pending-drain.sh
set -euo pipefail

REPO="${REPO:-/home/yuji/src/comfyui-runpod}"
SCRIPTS="$REPO/workspace/scripts"
LOG="${LOG:-$REPO/.data/shape_factory/pending-drain.log}"
COMFY="${COMFY:-http://127.0.0.1:8188}"
# Cap how many we try per tick; submit --pending-only also refuses while Comfy waiting is non-empty.
DRAIN_LIMIT="${DRAIN_LIMIT:-2}"
HOURLY_QUEUE_MAX="${HOURLY_QUEUE_MAX:-2}"

mkdir -p "$(dirname "$LOG")"

log() { echo "$(date -Is) $*" | tee -a "$LOG"; }

queue_counts() {
  local qf
  qf=$(mktemp)
  if ! curl -sf --max-time 8 "$COMFY/queue" > "$qf"; then
    rm -f "$qf"
    echo "0 0"
    return 1
  fi
  python3 - "$qf" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    q = json.load(f)
print(len(q.get("queue_running", [])), len(q.get("queue_pending", [])))
PY
  rm -f "$qf"
}

if ! read -r RUN PEND < <(queue_counts); then
  log "comfy unreachable; skip drain"
  exit 0
fi

# Fill toward HOURLY_QUEUE_MAX waiting slots (same steady-state as hourly).
SLOTS=$((HOURLY_QUEUE_MAX - PEND))
if [ "$SLOTS" -le 0 ]; then
  log "skip drain running=$RUN waiting=$PEND (at/above max=$HOURLY_QUEUE_MAX)"
  exit 0
fi
if [ "$SLOTS" -gt "$DRAIN_LIMIT" ]; then
  SLOTS=$DRAIN_LIMIT
fi

log "drain tick running=$RUN waiting=$PEND slots=$SLOTS"
JOBS_DIR="${JOBS_DIR:-$REPO/.data/shape_factory/jobs}"
(
  cd "$SCRIPTS"
  # Scan the factory jobs mount; gate inside submit stops when Comfy waiting fills.
  python3 shape_factory.py submit --pending-only --quiet \
    --jobs-dir "$JOBS_DIR" \
    --limit "$SLOTS" >> "$LOG" 2>&1 || true
)
read -r RUN2 PEND2 < <(queue_counts) || true
log "drain done running=${RUN2:-?} waiting=${PEND2:-?}"
