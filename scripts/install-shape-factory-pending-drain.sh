#!/usr/bin/env bash
# Install user systemd timer: drain factory pending → Comfy when waiting is empty.
# Fires every minute (cheap no-op when nothing to submit / Comfy busy).
# Re-run after edits: bash scripts/install-shape-factory-pending-drain.sh
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_SYSTEMD="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$USER_SYSTEMD"
chmod +x "$REPO/scripts/shape_factory_pending_drain.sh"

cat > "$USER_SYSTEMD/shape-factory-pending-drain.service" <<EOF
[Unit]
Description=ComfyUI shape factory pending drain (submit --pending-only)
After=network-online.target

[Service]
Type=oneshot
Environment=REPO=$REPO
Environment=HOURLY_QUEUE_MAX=3
Environment=DRAIN_LIMIT=2
ExecStart=$REPO/scripts/shape_factory_pending_drain.sh
EOF

cat > "$USER_SYSTEMD/shape-factory-pending-drain.timer" <<EOF
[Unit]
Description=Drain factory pending jobs onto Comfy every minute

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
AccuracySec=15s
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now shape-factory-pending-drain.timer
systemctl --user list-timers shape-factory-pending-drain.timer --no-pager
echo "Logs: $REPO/.data/shape_factory/pending-drain.log"
echo "Uninstall: systemctl --user disable --now shape-factory-pending-drain.timer"
echo "One-shot now: systemctl --user start shape-factory-pending-drain.service"
