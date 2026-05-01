#!/usr/bin/env bash
# Cross-platform wrapper: delegates to experiments-ui-dev.mjs (same as npm run ui:dev:vite).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec node "$ROOT/scripts/experiments-ui-dev.mjs" vite "$@"
