#!/usr/bin/env bash
# Serve planning docs locally (MkDocs Material).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck source=planning_docs_venv.sh
source "${ROOT}/scripts/planning_docs_venv.sh"

HOST="${PLANNING_DOCS_HOST:-127.0.0.1}"
PORT="${PLANNING_DOCS_PORT:-8000}"

exec "${VENV}/bin/python" -m mkdocs serve -a "${HOST}:${PORT}"
