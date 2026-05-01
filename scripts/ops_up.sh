#!/usr/bin/env bash
set -euo pipefail

services="${*:-refresh_run_status report_experiment_queue_status queue_incomplete_experiments ws_event_tap}"

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

docker compose --profile ops up -d ${services}

