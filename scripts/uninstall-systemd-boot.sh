#!/usr/bin/env bash
# Disable and remove user systemd units installed by scripts/install-systemd-boot.sh
set -euo pipefail

USER_SYSTEMD="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
for u in wsl-swap-io-telemetry.service comfyui-runpod-keep.timer comfyui-runpod-keep.service comfyui-runpod-vite.service comfyui-runpod-docker.service; do
  systemctl --user disable --now "$u" 2>/dev/null || true
  rm -f "$USER_SYSTEMD/$u"
done
systemctl --user daemon-reload
echo "Removed comfyui-runpod-docker.service, comfyui-runpod-vite.service, and comfyui-runpod-keep.{service,timer} from $USER_SYSTEMD"
