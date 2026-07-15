#!/usr/bin/env bash
# Vision V1 — optional sync helper for remote runners (runpod / any SSH host).
# Local and Docker runners do not need this; same Python jobs run everywhere.
#
# Usage:
#   VISION_REMOTE=user@host VISION_REMOTE_DIR=/workspace/vision_v1 \
#     ./workspace/scripts/vision_slice_sync.sh push /path/to/work_dir
#   ./workspace/scripts/vision_slice_sync.sh pull /path/to/local_status_dir
#
# push: rsync work_dir (frames + frames_manifest) → remote
# pull: rsync remote status NDJSON/manifest → local status dir
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MODE="${1:-}"
LOCAL_PATH="${2:-}"

if [[ -z "$MODE" || -z "$LOCAL_PATH" ]]; then
  echo "usage: $0 push <work_dir> | pull <status_dir>" >&2
  exit 2
fi

REMOTE="${VISION_REMOTE:-}"
REMOTE_DIR="${VISION_REMOTE_DIR:-/workspace/vision_v1}"
RSYNC_RSH="${VISION_RSYNC_RSH:-ssh}"

if [[ -z "$REMOTE" ]]; then
  echo "error: set VISION_REMOTE=user@host (optional VISION_REMOTE_DIR, VISION_RSYNC_RSH)" >&2
  exit 2
fi

if ! command -v rsync >/dev/null 2>&1; then
  echo "error: rsync required for remote sync" >&2
  exit 1
fi

case "$MODE" in
  push)
    if [[ ! -d "$LOCAL_PATH" ]]; then
      echo "error: work_dir not found: $LOCAL_PATH" >&2
      exit 1
    fi
    echo "push $LOCAL_PATH/ → ${REMOTE}:${REMOTE_DIR}/work/"
    rsync -az -e "$RSYNC_RSH" --delete \
      "$LOCAL_PATH/" \
      "${REMOTE}:${REMOTE_DIR}/work/"
    # Also copy caption script so remote need not have full repo
    rsync -az -e "$RSYNC_RSH" \
      "$ROOT/workspace/scripts/vision_slice_caption_run.py" \
      "${REMOTE}:${REMOTE_DIR}/vision_slice_caption_run.py"
    echo "on remote, e.g.:"
    echo "  python3 ${REMOTE_DIR}/vision_slice_caption_run.py \\"
    echo "    --frames-manifest ${REMOTE_DIR}/work/frames_manifest.json \\"
    echo "    --work-dir ${REMOTE_DIR}/work \\"
    echo "    --status-dir ${REMOTE_DIR}/status \\"
    echo "    --run-id vision_v1_\$(date -u +%Y%m%d) --runner runpod"
    ;;
  pull)
    mkdir -p "$LOCAL_PATH"
    echo "pull ${REMOTE}:${REMOTE_DIR}/status/ → $LOCAL_PATH/"
    rsync -az -e "$RSYNC_RSH" \
      "${REMOTE}:${REMOTE_DIR}/status/" \
      "$LOCAL_PATH/"
    ;;
  *)
    echo "error: mode must be push or pull" >&2
    exit 2
    ;;
esac
