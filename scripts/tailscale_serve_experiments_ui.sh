#!/usr/bin/env bash
# Persist Tailscale Serve → Experiments UI (container :8790 on the Windows host).
# Requires: Tailscale app on Windows, Serve enabled for this tailnet, UI listening on 127.0.0.1:8790.
set -euo pipefail

TS="${TAILSCALE_EXE:-/mnt/c/Program Files/Tailscale/tailscale.exe}"
if [[ ! -x "$TS" ]]; then
  TS="$(command -v tailscale || true)"
fi
if [[ -z "${TS}" || ! -e "$TS" ]]; then
  echo "tailscale CLI not found (Windows: C:\\Program Files\\Tailscale\\tailscale.exe)" >&2
  exit 1
fi

PORT="${EXPERIMENTS_UI_PORT:-8790}"
if ! curl -sf -o /dev/null --max-time 3 "http://127.0.0.1:${PORT}/"; then
  echo "Experiments UI is not answering on 127.0.0.1:${PORT}" >&2
  exit 1
fi

"$TS" serve --bg --yes "${PORT}"
echo
"$TS" serve status
echo
echo "On the tailnet: https://$("$TS" status --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("Self",{}).get("DNSName","").rstrip("."))')"
