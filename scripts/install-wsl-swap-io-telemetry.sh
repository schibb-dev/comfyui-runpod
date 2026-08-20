#!/usr/bin/env bash
# Install user systemd service: WSL swap + disk I/O telemetry (baseline + stress sampling).
# Re-run after edits to refresh the unit file.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_SYSTEMD="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
TELEMETRY_SCRIPT="$REPO/scripts/wsl_swap_io_telemetry.sh"
LOG_DIR="$REPO/.data/telemetry"
OUT_FILE="${TELEMETRY_OUT:-$LOG_DIR/swap_io_telemetry.jsonl}"
BASELINE_INTERVAL="${TELEMETRY_BASELINE_INTERVAL:-60}"
STRESS_INTERVAL="${TELEMETRY_STRESS_INTERVAL:-2}"
HOLD_STRESS="${TELEMETRY_HOLD_STRESS:-90}"

mkdir -p "$USER_SYSTEMD" "$LOG_DIR"
chmod +x "$TELEMETRY_SCRIPT"

cat >"$USER_SYSTEMD/wsl-swap-io-telemetry.service" <<EOF
[Unit]
Description=WSL swap + disk I/O telemetry (ComfyUI / Claude perf correlation)
Documentation=file://$REPO/scripts/install-wsl-swap-io-telemetry.sh
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$REPO
Environment=REPO=$REPO
ExecStart=$TELEMETRY_SCRIPT \\
  --out $OUT_FILE \\
  --baseline-interval $BASELINE_INTERVAL \\
  --stress-interval $STRESS_INTERVAL \\
  --hold-stress $HOLD_STRESS
Restart=on-failure
RestartSec=15

[Install]
WantedBy=default.target
EOF

if systemctl --user daemon-reload 2>/dev/null; then
  systemctl --user enable --now wsl-swap-io-telemetry.service
  echo "Enabled: wsl-swap-io-telemetry.service"
  systemctl --user status wsl-swap-io-telemetry.service --no-pager -l | head -20 || true
else
  echo "Note: systemctl --user failed (no user D-Bus?). Unit written to:" >&2
  echo "  $USER_SYSTEMD/wsl-swap-io-telemetry.service" >&2
  echo "Enable manually: systemctl --user daemon-reload && systemctl --user enable --now wsl-swap-io-telemetry.service" >&2
fi

echo ""
echo "Telemetry log: $OUT_FILE"
echo "Stop:    systemctl --user stop wsl-swap-io-telemetry.service"
echo "Disable: systemctl --user disable --now wsl-swap-io-telemetry.service"
echo "Tail:    tail -f $OUT_FILE"
