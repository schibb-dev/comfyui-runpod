#!/usr/bin/env bash
# Install user systemd units so Docker (ComfyUI stack + output SFTP) and host Vite start on login/boot
# (user session). WSL2: enable lingering for units without an interactive login:
#   sudo loginctl enable-linger "$USER"
#
# Uninstall: scripts/uninstall-systemd-boot.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_SYSTEMD="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
DOCKER_BIN="$(command -v docker)"
if [[ -z "$DOCKER_BIN" ]]; then
  echo "docker not found on PATH; install Docker CLI first." >&2
  exit 1
fi
QDOCKER="$(printf '%q' "$DOCKER_BIN")"
QREPO="$(printf '%q' "$REPO_ROOT")"

# systemd runs bash non-interactively; typical ~/.bashrc exits unless interactive, so fnm/nvm never load.
# Prefer stable paths (fnm default alias, nvm default, system npm) — not /run/user/.../fnm_multishells (ephemeral).
resolve_boot_npm() {
  local p
  p="${HOME}/.local/share/fnm/aliases/default/bin/npm"
  if [[ -x "$p" ]]; then
    printf '%s' "$p"
    return 0
  fi
  if [[ -L "${HOME}/.nvm/versions/node/default" ]]; then
    p="$(readlink -f "${HOME}/.nvm/versions/node/default")/bin/npm"
    if [[ -x "$p" ]]; then
      printf '%s' "$p"
      return 0
    fi
  fi
  if [[ -x /usr/bin/npm ]]; then
    printf '%s' /usr/bin/npm
    return 0
  fi
  local w
  w="$(command -v npm 2>/dev/null || true)"
  if [[ -n "$w" && "$w" != /run/user/* ]]; then
    printf '%s' "$w"
    return 0
  fi
  return 1
}

NPM_BIN=""
if ! NPM_BIN="$(resolve_boot_npm)"; then
  echo "Could not find npm for systemd (tried fnm ~/.local/share/fnm/aliases/default/bin/npm, nvm default, /usr/bin/npm). Set a default: fnm default <ver>" >&2
  exit 1
fi
QNPM="$(printf '%q' "$NPM_BIN")"
# experiments-ui-dev.mjs spawns `npm` for the Vite child — inherit a PATH that includes that npm's bin/.
NPM_DIR="${NPM_BIN%/*}"
VITE_SERVICE_PATH="${NPM_DIR}:/usr/local/bin:/usr/bin:/bin"

# WSL2 + Docker Desktop: ~/.docker/config.json often uses credsStore "desktop", which runs
# docker-credential-desktop.exe. systemd's default PATH does not include that binary — pulls then fail.
compose_service_path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
for d in "/mnt/c/Program Files/Docker/Docker/resources/bin" "/mnt/c/Program Files (x86)/Docker/Docker/resources/bin"; do
  if [[ -x "$d/docker-credential-desktop.exe" || -x "$d/docker-credential-wsl.exe" ]]; then
    compose_service_path="$d:$compose_service_path"
    break
  fi
done

mkdir -p "$USER_SYSTEMD"

DOCKER_UNIT="$USER_SYSTEMD/comfyui-runpod-docker.service"
VITE_UNIT="$USER_SYSTEMD/comfyui-runpod-vite.service"

cat >"$DOCKER_UNIT" <<EOF
[Unit]
Description=ComfyUI RunPod (docker compose + output SFTP)
Documentation=file://$REPO_ROOT/scripts/install-systemd-boot.sh
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$REPO_ROOT
Environment="PATH=$compose_service_path"
# Relative -f paths resolve against WorkingDirectory.
ExecStart=/bin/bash -lc "exec $QDOCKER compose -f docker-compose.yml -f docker-compose.output-sftp.yml up -d"
ExecStop=/bin/bash -lc "exec $QDOCKER compose -f docker-compose.yml -f docker-compose.output-sftp.yml stop"

[Install]
WantedBy=default.target
EOF

cat >"$VITE_UNIT" <<EOF
[Unit]
Description=Experiments UI Vite (npm run ui:dev:all → comfyui :8790)
Documentation=file://$REPO_ROOT/scripts/install-systemd-boot.sh
After=comfyui-runpod-docker.service network-online.target
Wants=comfyui-runpod-docker.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$REPO_ROOT
Environment="PATH=$VITE_SERVICE_PATH"
# Non-interactive: skip .bashrc (fnm is often behind an interactive-only guard). Use absolute npm: $NPM_BIN
ExecStart=/bin/bash --noprofile --norc -lc "cd $QREPO && exec $QNPM run ui:dev:all -- --no-open"
# Wait (best-effort) for Experiments UI inside the container; do not block forever.
ExecStartPre=/bin/bash -lc 'for i in \$(seq 1 90); do curl -fsS -m 2 http://127.0.0.1:8790/ >/dev/null 2>&1 && exit 0; sleep 2; done; exit 0'
Restart=on-failure
RestartSec=15

[Install]
WantedBy=default.target
EOF

ENABLED=0
if systemctl --user daemon-reload 2>/dev/null && systemctl --user enable comfyui-runpod-docker.service comfyui-runpod-vite.service 2>/dev/null; then
  ENABLED=1
else
  echo "Note: systemctl --user enable failed (no user D-Bus session?). Units were written; from a logged-in session run: systemctl --user daemon-reload && systemctl --user enable comfyui-runpod-docker.service comfyui-runpod-vite.service" >&2
fi

echo "Installed:"
echo "  $DOCKER_UNIT"
echo "  $VITE_UNIT"
echo ""
if [[ "$ENABLED" -eq 1 ]]; then
  echo "Enabled user units: comfyui-runpod-docker.service, comfyui-runpod-vite.service"
  echo "Start now:  systemctl --user start comfyui-runpod-docker.service && systemctl --user start comfyui-runpod-vite.service"
else
  echo "Enable manually when D-Bus is available (see note above)."
fi
echo "Status:     systemctl --user status comfyui-runpod-docker.service comfyui-runpod-vite.service"
echo ""
echo "WSL2 / headless user session:  sudo loginctl enable-linger \"$USER\""
echo "Requires: docker CLI on PATH, OUTPUT_SFTP_* in $REPO_ROOT/.env, Node/npm for Vite."
