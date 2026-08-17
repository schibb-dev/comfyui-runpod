#!/usr/bin/env bash
# Gentle crawl to build discovery_lineage_citations.sqlite (inverted forward-fill index).
#
# Usage:
#   bash scripts/crawl_discovery_citations_gentle.sh
#   BATCH_SIZE=50 LOOP=1 bash scripts/crawl_discovery_citations_gentle.sh
#   FORCE=1 bash scripts/crawl_discovery_citations_gentle.sh
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WS="${WORKSPACE_PATH:-${COMFYUI_BIND_OUTPUT_DIR:+$(dirname "$COMFYUI_BIND_OUTPUT_DIR")}}"
WS="${WS:-/home/yuji/comfyui-runpod-data}"
if [[ -d /workspace/output/_status ]] || [[ -d /workspace/output/output/_status ]]; then
  WS=/workspace
fi

BATCH_SIZE="${BATCH_SIZE:-80}"
SLEEP="${SLEEP:-0.5}"
LOOP_SLEEP="${LOOP_SLEEP:-60}"
LOG="${CITATIONS_CRAWL_LOG:-/tmp/lineage_citations_backfill.log}"

ARGS=(
  --workspace-root "$WS"
  --gentle
  --resume
  --batch-size "$BATCH_SIZE"
  --sleep "$SLEEP"
)

if [[ "${LOOP:-0}" == "1" || "${LOOP:-}" == "true" ]]; then
  ARGS+=(--loop --loop-sleep "$LOOP_SLEEP")
fi
if [[ "${FORCE:-0}" == "1" || "${FORCE:-}" == "true" ]]; then
  ARGS+=(--force)
fi
if [[ -n "${COMFYUI_BIND_OUTPUT_DIR:-}" ]]; then
  ARGS+=(--output-root "$COMFYUI_BIND_OUTPUT_DIR")
elif [[ -d "$WS/output" ]]; then
  ARGS+=(--output-root "$WS/output")
fi

echo "[citations-crawl] log=$LOG"
echo "[citations-crawl] python3 $ROOT/scripts/backfill_discovery_citations.py ${ARGS[*]}"
exec python3 "$ROOT/scripts/backfill_discovery_citations.py" "${ARGS[@]}" 2>&1 | tee -a "$LOG"
