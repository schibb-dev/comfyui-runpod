#!/usr/bin/env bash
# Park Comfy's live queue and stop feeders (drain + watch_queue).
# Usage:
#   bash scripts/suspend_comfy_queue.sh            # suspend
#   bash scripts/suspend_comfy_queue.sh resume
#   bash scripts/suspend_comfy_queue.sh status
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CMD="${1:-suspend}"
shift || true
exec python3 "$REPO/workspace/scripts/suspend_comfy_queue.py" "$CMD" "$@"
