#!/usr/bin/env bash
# Gentle, resumable discovery lineage crawl (parent-infer → persists edges;
# descendants appear as children are crawled).
#
# Usage:
#   bash scripts/crawl_discovery_lineage_gentle.sh
#   BATCH_SIZE=20 SLEEP=1.0 bash scripts/crawl_discovery_lineage_gentle.sh
#   INFER_CHILDREN=1 bash scripts/crawl_discovery_lineage_gentle.sh   # also forward-fill
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WS="${WORKSPACE_PATH:-${COMFYUI_BIND_OUTPUT_DIR:+$(dirname "$COMFYUI_BIND_OUTPUT_DIR")}}"
WS="${WS:-/home/yuji/comfyui-runpod-data}"
# Prefer container layout when present.
if [[ -d /workspace/output/_status ]] || [[ -d /workspace/output/output/_status ]]; then
  WS=/workspace
fi

BATCH_SIZE="${BATCH_SIZE:-40}"
SLEEP="${SLEEP:-0.75}"
LOOP_SLEEP="${LOOP_SLEEP:-60}"
MAX_DEPTH="${MAX_DEPTH:-2}"
LOG="${LINEAGE_CRAWL_LOG:-/tmp/lineage_backfill.log}"

ARGS=(
  --workspace-root "$WS"
  --gentle
  --resume
  --batch-size "$BATCH_SIZE"
  --sleep "$SLEEP"
  --max-depth "$MAX_DEPTH"
)

if [[ "${LOOP:-0}" == "1" || "${LOOP:-}" == "true" ]]; then
  ARGS+=(--loop --loop-sleep "$LOOP_SLEEP")
fi

if [[ "${INFER_CHILDREN:-0}" == "1" || "${INFER_CHILDREN:-}" == "true" ]]; then
  ARGS+=(--infer-children)
fi

if [[ -n "${COMFYUI_BIND_OUTPUT_DIR:-}" ]]; then
  ARGS+=(--output-root "$COMFYUI_BIND_OUTPUT_DIR")
elif [[ -d "$WS/output" ]]; then
  ARGS+=(--output-root "$WS/output")
fi

echo "[crawl] log=$LOG"
echo "[crawl] python3 $ROOT/scripts/backfill_discovery_lineage.py ${ARGS[*]}"
# shellcheck disable=SC2086
exec python3 "$ROOT/scripts/backfill_discovery_lineage.py" "${ARGS[@]}" 2>&1 | tee -a "$LOG"
