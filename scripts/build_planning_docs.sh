#!/usr/bin/env bash
# Build static planning docs site to site/
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck source=planning_docs_venv.sh
source "${ROOT}/scripts/planning_docs_venv.sh"

"${VENV}/bin/python" -m mkdocs build
echo "[planning-docs] wrote ${ROOT}/site/"
