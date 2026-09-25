#!/usr/bin/env bash
# Point Tailscale Serve HTTPS at Vite (live HMR) instead of static :8790.
# Pair with: EXPERIMENTS_UI_HMR_HOST=<MagicDNS> EXPERIMENTS_UI_HMR_PROTOCOL=wss
#            EXPERIMENTS_UI_HMR_CLIENT_PORT=443  (see systemd vite unit / ui:dev:vite:tailscale)
# Restore static UI: ./scripts/tailscale_serve_experiments_ui.sh
set -euo pipefail

TS="${TAILSCALE_EXE:-/mnt/c/Program Files/Tailscale/tailscale.exe}"
if [[ ! -x "$TS" ]]; then
  TS="$(command -v tailscale || true)"
fi
if [[ -z "${TS}" || ! -e "$TS" ]]; then
  echo "tailscale CLI not found (Windows: C:\\Program Files\\Tailscale\\tailscale.exe)" >&2
  exit 1
fi

PORT="${EXPERIMENTS_UI_VITE_PORT:-5179}"
if ! curl -sf -o /dev/null --max-time 3 "http://127.0.0.1:${PORT}/"; then
  echo "Vite is not answering on 127.0.0.1:${PORT}" >&2
  echo "Start it: systemctl --user restart comfyui-runpod-vite.service" >&2
  echo "  or: npm run ui:dev:vite:tailscale -- --no-open --port ${PORT}" >&2
  exit 1
fi

"$TS" serve --bg --yes "${PORT}"
echo
"$TS" serve status
DNS="$("$TS" status --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("Self",{}).get("DNSName","").rstrip("."))')"
echo
echo "Phone (live Vite): https://${DNS}/"
echo "Restore static :8790 later: ./scripts/tailscale_serve_experiments_ui.sh"
