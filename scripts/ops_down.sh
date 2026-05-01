#!/usr/bin/env bash
set -euo pipefail

remove="${REMOVE_OPS_CONTAINERS:-false}"
services="${*:-refresh_run_status report_experiment_queue_status queue_incomplete_experiments ws_event_tap}"

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

if [[ "$remove" == "true" ]]; then
  docker compose --profile ops rm -fsv ${services}
else
  docker compose --profile ops stop ${services}
fi

