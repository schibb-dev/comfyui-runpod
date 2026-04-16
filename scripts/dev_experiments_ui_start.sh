#!/usr/bin/env bash
# Cross-platform wrapper: watched API + Vite (same as npm run ui:dev:start).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec node "$ROOT/scripts/experiments-ui-dev.mjs" start "$@"
