#!/usr/bin/env bash
# Migrate WSL Ubuntu from snap or apt dockerd to Docker Desktop's engine.
#
# BEFORE running:
#   1. Docker Desktop → Settings → Resources → WSL integration → enable your Ubuntu distro → Apply.
#   2. Stop compose stacks on THIS engine (from repo): npm run down  OR  docker compose ... down
#
# Run from WSL:
#   bash scripts/wsl_migrate_to_docker_desktop.sh
#
# AFTER script + `wsl --shutdown` (from Windows PowerShell):
#   - Start Docker Desktop if needed.
#   - Open Ubuntu; run: docker context use desktop-linux   (if default socket is wrong)
#   - cd ~/src/comfyui-runpod && npm run up   (or docker compose -f ... up -d)

set -eu

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Do not run this script as root; it will invoke sudo where needed." >&2
  exit 1
fi

echo "== Preflight: docker daemon identity (expect Ubuntu host name before migration) =="
docker info 2>/dev/null | grep -E '^ Name:|^ Operating System:' || true

if command -v snap >/dev/null 2>&1 && snap list docker >/dev/null 2>&1; then
  echo ""
  echo "Stopping snap docker..."
  sudo snap stop docker
  echo "Removing snap docker (images/volumes live in snap data; Docker Desktop will hold them after migration)..."
  sudo snap remove --purge docker
elif systemctl list-unit-files docker.service >/dev/null 2>&1; then
  echo ""
  echo "Stopping native Docker daemon services..."
  sudo systemctl stop docker.socket 2>/dev/null || true
  sudo systemctl stop docker 2>/dev/null || true
  sudo systemctl stop containerd 2>/dev/null || true

  echo ""
  echo "Disabling services so dockerd does not respawn on boot..."
  sudo systemctl disable docker.socket 2>/dev/null || true
  sudo systemctl disable docker 2>/dev/null || true
  sudo systemctl disable containerd 2>/dev/null || true

  echo ""
  echo "Removing docker-ce stack (CLI returns via Docker Desktop WSL integration after reboot)..."
  sudo apt-get remove --purge -y \
    docker-ce \
    docker-ce-cli \
    containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin \
    docker-ce-rootless-extras \
    || true

  sudo apt-get autoremove -y
else
  echo "No snap docker or docker-ce detected; assuming Docker Desktop already provides the engine."
fi

echo ""
echo "== Done removing local docker engine =="
echo "Next (Windows PowerShell, admin optional):"
echo "  wsl --shutdown"
echo "Then start Docker Desktop, reopen Ubuntu, and verify:"
echo "  docker context ls"
echo "  docker context use desktop-linux"
echo "  docker info | grep -E '^ Name:|^ Operating System:'"
echo "  (expect Name: docker-desktop and Operating System: Docker Desktop)"
echo ""
echo "Bring stack back:"
echo "  cd ~/src/comfyui-runpod && npm run up"
