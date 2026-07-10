#!/usr/bin/env bash
# Replay early-April chained renders: FB9_GEX2 → deposit → FB9_GEX_FACIAL per zip index.
# Pattern from 2026-04-03 og/ (~20 GEX2 + ~16 FACIAL same day).
set -euo pipefail

SCRIPTS=/home/yuji/src/comfyui-runpod/workspace/scripts
PIPELINE=/home/yuji/src/comfyui-runpod/.data/pipelines/fb9-gex2-to-facial.pipeline.yaml
LOG="${LOG:-/home/yuji/src/comfyui-runpod/.data/shape_factory/april03-chain.log}"
COUNT="${COUNT:-3}"
DEV="${DEV:-1}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-7200}"

dev_args=()
if [ "$DEV" = "1" ]; then dev_args+=(--dev); fi

echo "=== april03 chain batch started $(date -Is) count=$COUNT ===" | tee -a "$LOG"

for i in $(seq 0 $((COUNT - 1))); do
  echo "--- chain pick_index=$i $(date -Is) ---" | tee -a "$LOG"
  (
    cd "$SCRIPTS"
    python3 shape_factory.py generate \
      --shape ../../.data/shapes/FB9_GEX2.shape.yaml \
      --pools ../../.data/pools/FB9_GEX2/pools.yaml \
      --pick zip --limit 1 --pick-index "$i" \
      "${dev_args[@]}" >> "$LOG" 2>&1
    python3 shape_factory.py submit --family FB9_GEX2 --limit 1 >> "$LOG" 2>&1
    python3 shape_factory.py status --family FB9_GEX2 --wait --deposit --timeout "$WAIT_TIMEOUT" >> "$LOG" 2>&1
    python3 shape_factory.py generate \
      --shape ../../.data/shapes/FB9_GEX_FACIAL.shape.yaml \
      --pools ../../.data/pools/FB9_GEX_FACIAL/pools.yaml \
      --binds-override ../../.data/pipelines/binds/facial-from-latest-gex2.yaml \
      --pick zip --limit 1 \
      "${dev_args[@]}" >> "$LOG" 2>&1
    python3 shape_factory.py submit --family FB9_GEX_FACIAL --limit 1 >> "$LOG" 2>&1
    python3 shape_factory.py status --family FB9_GEX_FACIAL --wait --deposit --timeout "$WAIT_TIMEOUT" >> "$LOG" 2>&1
  ) || echo "chain $i failed (see $LOG)" | tee -a "$LOG"
done

echo "=== april03 chain batch finished $(date -Is) ===" | tee -a "$LOG"
