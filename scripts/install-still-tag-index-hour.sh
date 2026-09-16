#!/usr/bin/env bash
# Install user systemd timer: still-tag index-hour drain (Florence backlog).
# Wakes every minute; real drains are gated by .data/shape_factory/still_tag_schedule.json
# (default 03:00–05:00 local, front-of-queue). Re-run after schedule edits.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_SYSTEMD="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
DATA_ROOT="${SHAPE_FACTORY_DATA_ROOT:-$REPO/.data}"
SCHEDULE="$DATA_ROOT/shape_factory/still_tag_schedule.json"
DRAIN="$REPO/workspace/scripts/vision_still_tag_drain.py"
STATUS_DIR="${VISION_STATUS_DIR:-/home/yuji/comfyui-runpod-data/output/_status}"
COMFY_SERVER="${VISION_COMFY_SERVER:-http://127.0.0.1:8188}"

mkdir -p "$USER_SYSTEMD" "$(dirname "$SCHEDULE")"
chmod +x "$DRAIN"

if [ ! -f "$SCHEDULE" ]; then
  cat > "$SCHEDULE" <<'EOF'
{
  "schema_version": 1,
  "enabled": true,
  "timezone": "America/New_York",
  "window_start": "03:00",
  "window_duration_min": 120,
  "front": true,
  "max_inflight": 1,
  "max_items_per_tick": 96,
  "comfy_server": null,
  "auto_drain_on_enqueue": false
}
EOF
fi

cat > "$USER_SYSTEMD/still-tag-index-hour.service" <<EOF
[Unit]
Description=Still-tag index-hour drain (Florence backlog, schedule-gated)
After=network-online.target

[Service]
Type=oneshot
Environment=SHAPE_FACTORY_DATA_ROOT=$DATA_ROOT
Environment=VISION_STATUS_DIR=$STATUS_DIR
Environment=VISION_COMFY_SERVER=$COMFY_SERVER
ExecStart=/usr/bin/python3 $DRAIN --respect-schedule --data-root $DATA_ROOT --status-dir $STATUS_DIR --comfy-server $COMFY_SERVER
EOF

cat > "$USER_SYSTEMD/still-tag-index-hour.timer" <<EOF
[Unit]
Description=Wake still-tag index-hour drain every minute

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
AccuracySec=15s
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now still-tag-index-hour.timer
systemctl --user list-timers still-tag-index-hour.timer --no-pager
echo "Schedule: $SCHEDULE"
echo "Uninstall: systemctl --user disable --now still-tag-index-hour.timer"
echo "Force drain now: python3 $DRAIN --force --front --max-items 96 --data-root $DATA_ROOT"
