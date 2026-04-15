#!/usr/bin/env bash
# Cross-platform wrapper: API + Vite (same as npm run ui:dev:all).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec node "$ROOT/scripts/experiments-ui-dev.mjs" all "$@"
