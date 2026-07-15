#!/usr/bin/env bash
# Vision V1 — local dry-run orchestration: pick → sample → caption(--dry-run).
# Does not load Florence / does not need a free GPU.
#
# Usage:
#   VISION_DATA_ROOT=/home/yuji/comfyui-runpod-data/output \
#     ./workspace/scripts/vision_slice_dry_run.sh
# Optional: VISION_WORK_DIR, VISION_STATUS_DIR, VISION_LIMIT, VISION_SEED, VISION_RUN_ID
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPTS="$ROOT/workspace/scripts"
DATA_ROOT="${VISION_DATA_ROOT:-}"
if [[ -z "$DATA_ROOT" ]]; then
  echo "error: set VISION_DATA_ROOT to the output bind (directory that contains og/)" >&2
  exit 2
fi
DATA_ROOT="$(cd "$DATA_ROOT" && pwd)"
WORK_DIR="${VISION_WORK_DIR:-/tmp/vision_v1_work}"
STATUS_DIR="${VISION_STATUS_DIR:-$DATA_ROOT/_status}"
LIMIT="${VISION_LIMIT:-12}"
SEED="${VISION_SEED:-0}"
RUN_ID="${VISION_RUN_ID:-vision_v1_$(date -u +%Y%m%dT%H%M%SZ)}"
RUNNER="${VISION_RUNNER:-local}"

mkdir -p "$WORK_DIR" "$STATUS_DIR"
INPUTS="${VISION_INPUTS:-$STATUS_DIR/vision_v1_inputs.txt}"

echo "== pick ($LIMIT from $DATA_ROOT/og) =="
python3 "$SCRIPTS/vision_slice_pick_inputs.py" \
  --data-root "$DATA_ROOT" \
  --limit "$LIMIT" \
  --seed "$SEED" \
  --out "$INPUTS"

echo "== sample → $WORK_DIR =="
python3 "$SCRIPTS/vision_slice_sample.py" \
  --data-root "$DATA_ROOT" \
  --work-dir "$WORK_DIR" \
  --inputs "$INPUTS" \
  --window-sec 2

echo "== caption dry-run → $STATUS_DIR =="
python3 "$SCRIPTS/vision_slice_caption_run.py" \
  --frames-manifest "$WORK_DIR/frames_manifest.json" \
  --work-dir "$WORK_DIR" \
  --status-dir "$STATUS_DIR" \
  --run-id "$RUN_ID" \
  --runner "$RUNNER" \
  --provider dry-run \
  --dry-run

echo "ok: inputs=$INPUTS"
echo "ok: ndjson=$STATUS_DIR/vision_slice_captions.ndjson"
echo "ok: manifest=$STATUS_DIR/vision_slice_manifest.json"
echo "next: spot-check (see docs/VISION_V1_TIME_SLICE_CAPTION_SPIKE.md) or re-run caption without --dry-run on a GPU runner"
