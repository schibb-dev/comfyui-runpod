#!/usr/bin/env bash
# Ensure a local venv with MkDocs deps (Debian often lacks system pip/ensurepip).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${ROOT}/.venv-docs"

ensure_venv() {
  if [[ ! -x "${VENV}/bin/python" ]]; then
    echo "[planning-docs] creating ${VENV}"
    python3 -m venv --without-pip "${VENV}"
    curl -sS https://bootstrap.pypa.io/get-pip.py | "${VENV}/bin/python" -
  fi
  "${VENV}/bin/pip" install -q -r "${ROOT}/requirements-docs.txt"
}

ensure_venv
