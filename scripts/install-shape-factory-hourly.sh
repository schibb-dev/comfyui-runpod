#!/usr/bin/env bash
# Install user systemd timer: shape-factory hourly maintenance + chain advance.
# Wakes every 5 minutes; real fills are gated by .data/shape_factory/hourly-schedule.json
# (default interval 30m). Re-run after edits to refresh the unit files.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_SYSTEMD="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SCHEDULE="${SCHEDULE:-$REPO/.data/shape_factory/hourly-schedule.json}"
mkdir -p "$USER_SYSTEMD" "$(dirname "$SCHEDULE")"
chmod +x "$REPO/scripts/shape_factory_hourly.sh"

# Seed schedule if missing (30m / auto / comfy max 2 / pending max 4).
if [ ! -f "$SCHEDULE" ]; then
  (
    cd "$REPO/workspace/scripts"
    python3 shape_factory_hourly.py schedule-set \
      --schedule "$SCHEDULE" \
      --minutes 30 \
      --enabled 1 \
      --submit-mode auto \
      --comfy-queue-min 1 \
      --comfy-queue-max 3 \
      --pending-queue-max 4 >/dev/null
  )
fi

cat > "$USER_SYSTEMD/shape-factory-hourly.service" <<EOF
[Unit]
Description=ComfyUI shape factory hourly tick (maintain + fill when due)
After=network-online.target

[Service]
Type=oneshot
Environment=REPO=$REPO
Environment=ADVANCE_CHAIN=1
Environment=DEV_CHAIN=0
ExecStart=$REPO/scripts/shape_factory_hourly.sh
EOF

cat > "$USER_SYSTEMD/shape-factory-hourly.timer" <<EOF
[Unit]
Description=Wake shape-factory hourly tick (schedule-gated fills)

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=30s
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now shape-factory-hourly.timer
systemctl --user list-timers shape-factory-hourly.timer --no-pager
echo "Schedule: $SCHEDULE"
echo "Logs: $REPO/.data/shape_factory/hourly.log"
echo "Uninstall: systemctl --user disable --now shape-factory-hourly.timer"
echo "One-shot now: systemctl --user start shape-factory-hourly.service"
