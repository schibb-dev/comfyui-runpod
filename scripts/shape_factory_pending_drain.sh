#!/usr/bin/env bash
# Drain factory pending jobs onto Comfy when waiting has room.
# Keeps ``pending_hourly_min`` hourlies on the FIFO: refill if the count dips,
# generate overflow so drain can feed Comfy without emptying the floor.
# Install: bash scripts/install-shape-factory-pending-drain.sh
set -euo pipefail

REPO="${REPO:-/home/yuji/src/comfyui-runpod}"
SCRIPTS="$REPO/workspace/scripts"
HOURLY_SH="$REPO/scripts/shape_factory_hourly.sh"
LOG="${LOG:-$REPO/.data/shape_factory/pending-drain.log}"
SCHEDULE="${SCHEDULE:-$REPO/.data/shape_factory/hourly-schedule.json}"
COMFY="${COMFY:-http://127.0.0.1:8188}"
# Cap how many we try per tick; submit --pending-only also refuses while Comfy waiting is non-empty.
DRAIN_LIMIT="${DRAIN_LIMIT:-2}"
JOBS_DIR="${JOBS_DIR:-$REPO/.data/shape_factory/jobs}"

read_schedule_field() {
  cd "$SCRIPTS" && python3 - "$SCHEDULE" "$1" "$2" <<'PY'
import json, sys
from pathlib import Path
from shape_factory_hourly import load_hourly_schedule
sch = load_hourly_schedule(path=Path(sys.argv[1]))
key, default = sys.argv[2], sys.argv[3]
print(sch.get(key, default))
PY
}

if [ -z "${HOURLY_QUEUE_MAX:-}" ]; then
  HOURLY_QUEUE_MAX=$(read_schedule_field comfy_queue_max 3) || HOURLY_QUEUE_MAX=3
fi
HOURLY_QUEUE_MAX="${HOURLY_QUEUE_MAX:-3}"
HOURLY_PENDING_MIN="${HOURLY_PENDING_MIN:-$(read_schedule_field pending_hourly_min 5)}"
HOURLY_SUBMIT_MODE="${HOURLY_SUBMIT_MODE:-$(read_schedule_field submit_mode auto)}"

mkdir -p "$(dirname "$LOG")"

log() { echo "$(date -Is) $*" | tee -a "$LOG"; }

hourly_pending_count() {
  cd "$SCRIPTS" && python3 shape_factory_hourly.py hourly-pending-count --jobs-dir "$JOBS_DIR"
}

refill_hourlies() {
  local overflow="${1:-0}"
  if [ "$HOURLY_SUBMIT_MODE" = "comfy" ]; then
    return 0
  fi
  local have
  have=$(hourly_pending_count)
  if [ "$have" -ge "$HOURLY_PENDING_MIN" ] && [ "$overflow" -le 0 ]; then
    return 0
  fi
  log "refill hourlies have=$have min=$HOURLY_PENDING_MIN overflow=$overflow"
  HOURLY_REFILL_ONLY=1 HOURLY_OVERFLOW="$overflow" \
    bash "$HOURLY_SH" >> "$LOG" 2>&1 || true
}

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
  log "comfy unreachable; refill hourlies only"
  refill_hourlies 0
  exit 0
fi

# Fill toward HOURLY_QUEUE_MAX waiting slots (same steady-state as hourly).
SLOTS=$((HOURLY_QUEUE_MAX - PEND))
if [ "$SLOTS" -le 0 ]; then
  log "skip drain running=$RUN waiting=$PEND (at/above max=$HOURLY_QUEUE_MAX); refill floor only"
  refill_hourlies 0
  exit 0
fi
if [ "$SLOTS" -gt "$DRAIN_LIMIT" ]; then
  SLOTS=$DRAIN_LIMIT
fi

log "drain tick running=$RUN waiting=$PEND slots=$SLOTS hourly_min=$HOURLY_PENDING_MIN"
refill_hourlies "$SLOTS"
(
  cd "$SCRIPTS"
  # Scan the factory jobs mount; gate inside submit stops when Comfy waiting fills.
  # Hourly floor is applied in submit --pending-only (operator Queue jobs still drain).
  python3 shape_factory.py submit --pending-only --quiet \
    --jobs-dir "$JOBS_DIR" \
    --limit "$SLOTS" >> "$LOG" 2>&1 || true
)
read -r RUN2 PEND2 < <(queue_counts) || true
HAVE2=$(hourly_pending_count || true)
log "drain done running=${RUN2:-?} waiting=${PEND2:-?} hourly_pending=${HAVE2:-?} hourly_min=$HOURLY_PENDING_MIN"
