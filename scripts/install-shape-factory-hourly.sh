#!/usr/bin/env bash
# Install user systemd timer: shape-factory hourly maintenance + chain advance.
# Fires once per hour at :30 (outside the top-of-hour 5★ window).
# Re-run this script after edits to refresh ~/.config/systemd/user/shape-factory-hourly.timer.
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
Description=Run shape-factory hourly tick (once per hour at :30)

[Timer]
OnBootSec=5min
# Mid-hour so ticks stay outside the top-of-hour 5★ window (:00–:12).
OnCalendar=*-*-* *:30:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now shape-factory-hourly.timer
systemctl --user list-timers shape-factory-hourly.timer --no-pager
echo "Logs: $REPO/.data/shape_factory/hourly.log"
echo "Uninstall: systemctl --user disable --now shape-factory-hourly.timer"
