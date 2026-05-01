#!/usr/bin/env bash
set -euo pipefail

# Build React UI (dist) inside the running comfyui container.
# This avoids rebuilding the whole image.

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

echo "Building Experiments UI inside container..."
docker compose exec -T comfyui bash -lc 'cd /workspace/experiments_ui/web && npm install && npm run build'

echo "Restarting container to pick up new dist..."
docker compose restart comfyui

echo "OK. Open: http://127.0.0.1:8790/"

