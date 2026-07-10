#!/usr/bin/env bash
set -euo pipefail
LOG="${LOG:-/home/yuji/src/comfyui-runpod/.data/shape_factory/queue-soak.log}"
SCRIPTS=/home/yuji/src/comfyui-runpod/workspace/scripts
BINDS=/home/yuji/src/comfyui-runpod/.data/pipelines/binds/facial-from-latest-gex2.yaml
SUBMITTED_PENDING=0

echo "=== queue soak started $(date -Is) ===" >> "$LOG"
while true; do
  if [ "$SUBMITTED_PENDING" = "0" ]; then
    echo "$(date -Is) submit pending shape-factory jobs" >> "$LOG"
    (cd "$SCRIPTS" && python3 shape_factory.py submit --family FB9_GEX2 >> "$LOG" 2>&1) || true
    SUBMITTED_PENDING=1
  fi

  QFILE=$(mktemp)
  curl -s http://127.0.0.1:8188/queue > "$QFILE"
  read -r RUN PEND < <(python3 - "$QFILE" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    q = json.load(f)
print(len(q.get("queue_running", [])), len(q.get("queue_pending", [])))
PY
)
  rm -f "$QFILE"
  echo "$(date -Is) running=$RUN pending=$PEND" >> "$LOG"
  (cd "$SCRIPTS" && python3 shape_factory.py status --family FB9_GEX2 >> "$LOG" 2>&1) || true
  (cd "$SCRIPTS" && python3 shape_factory.py status --family FB9_GEX_FACIAL >> "$LOG" 2>&1) || true

  if [ "$RUN" = "0" ] && [ "$PEND" = "0" ]; then
    echo "$(date -Is) queue empty — deposit + april03 step-2 facial chain" >> "$LOG"
    (cd "$SCRIPTS" && python3 shape_factory.py deposit --family FB9_GEX2 >> "$LOG" 2>&1) || true
    (cd "$SCRIPTS" && python3 shape_factory.py deposit --family FB9_GEX_FACIAL >> "$LOG" 2>&1) || true
    (cd "$SCRIPTS" && python3 shape_factory.py generate \
      --shape ../../.data/shapes/FB9_GEX_FACIAL.shape.yaml \
      --pools ../../.data/pools/FB9_GEX_FACIAL/pools.yaml \
      --binds-override "$BINDS" \
      --pick zip --limit 1 --dev >> "$LOG" 2>&1) || true
    (cd "$SCRIPTS" && python3 shape_factory.py submit --family FB9_GEX_FACIAL --limit 1 >> "$LOG" 2>&1) || true
    (cd "$SCRIPTS" && python3 shape_factory.py status --family FB9_GEX_FACIAL --wait --deposit --timeout 7200 >> "$LOG" 2>&1) || true
    echo "=== queue soak finished $(date -Is) ===" >> "$LOG"
    break
  fi
  sleep 30
done
