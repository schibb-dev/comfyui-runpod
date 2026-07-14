#!/usr/bin/env bash
# Install user systemd timer: shape-factory hourly maintenance + chain advance.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_SYSTEMD="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$USER_SYSTEMD"

cat > "$USER_SYSTEMD/shape-factory-hourly.service" <<EOF
[Unit]
Description=ComfyUI shape factory hourly tick (deposit, submit pending, chain advance)
After=network-online.target

[Service]
Type=oneshot
Environment=REPO=$REPO
Environment=ADVANCE_CHAIN=1
Environment=DEV_CHAIN=0
Environment=HOURLY_QUEUE_MIN=1
Environment=HOURLY_QUEUE_MAX=2
Environment=HOURLY_PREDICTED_SHARE=0.35
ExecStart=$REPO/scripts/shape_factory_hourly.sh
EOF

cat > "$USER_SYSTEMD/shape-factory-hourly.timer" <<EOF
[Unit]
Description=Run shape-factory hourly tick

[Timer]
OnBootSec=5min
OnUnitActiveSec=30min
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now shape-factory-hourly.timer
systemctl --user list-timers shape-factory-hourly.timer --no-pager
echo "Logs: $REPO/.data/shape_factory/hourly.log"
echo "Uninstall: systemctl --user disable --now shape-factory-hourly.timer"
