#!/usr/bin/env bash
# Install /etc/sudoers.d/<user>-agent — passwordless sudo for planned agent/infra actions.
#
# Run from a NORMAL WSL terminal (not inside a Cursor agent shell):
#   cd ~/src/comfyui-runpod
#   bash scripts/install_agent_sudoers.sh          # preview + confirm
#   bash scripts/install_agent_sudoers.sh --dry-run
#   bash scripts/install_agent_sudoers.sh --yes
#
# See docs/CURSOR_AGENT_SUDO_ASKPASS.md

set -euo pipefail

DRY_RUN=0
YES=0

usage() {
  cat <<'EOF'
Usage: install_agent_sudoers.sh [--dry-run] [--yes]

  --dry-run   Show the sudoers file that would be installed; make no changes
  --yes       Skip confirmation prompt
  -h, --help  This help

Installs passwordless sudo rules for:
  - read-only docker storage inspection (du/ls)
  - wsl_migrate_to_docker_desktop.sh internal sudo calls
  - loginctl enable-linger (systemd user boot)
  - rm / chown / chmod / mkdir / mkdir -p (broad FS ownership cleanup)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -n | --dry-run) DRY_RUN=1 ;;
    -y | --yes) YES=1 ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [[ -n "${CURSOR_AGENT:-}" || -n "${CURSOR_ASKPASS_SOCKET:-}" ]]; then
  echo "ERROR: This looks like a Cursor agent shell (CURSOR_AGENT / CURSOR_ASKPASS_SOCKET set)." >&2
  echo "Open a normal Ubuntu / Windows Terminal tab and run this script there." >&2
  exit 1
fi

if [[ "$(id -u)" -eq 0 ]]; then
  echo "ERROR: Do not run as root. Run as your user; the script will invoke sudo once to install." >&2
  exit 1
fi

USER_NAME="$(id -un)"
SUDOERS_FILE="/etc/sudoers.d/${USER_NAME}-agent"

need() {
  local var="$1"
  local path="$2"
  if [[ ! -x "$path" ]]; then
    echo "ERROR: expected executable not found: $path ($var)" >&2
    exit 1
  fi
  printf -v "$var" '%s' "$path"
}

need DU /usr/bin/du
need LS /bin/ls
need SNAP /usr/bin/snap
need SYSTEMCTL /usr/bin/systemctl
need APT_GET /usr/bin/apt-get
need LOGINCTL /usr/bin/loginctl
need RM /bin/rm
need CHOWN /bin/chown
need CHMOD /bin/chmod
need MKDIR /bin/mkdir

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

cat >"$TMP" <<EOF
# comfyui-runpod — passwordless sudo for planned agent/infra actions
# Installed by: scripts/install_agent_sudoers.sh
# Validate:     sudo visudo -c && sudo -n du -sh /var/lib/docker
#               sudo -n rm --help >/dev/null && sudo -n chown --help >/dev/null

Defaults:${USER_NAME} !requiretty

Cmnd_Alias AGENT_DOCKER_READ = \\
    ${DU} -sh /var/lib/docker, \\
    ${DU} -sh /var/lib/docker/*, \\
    ${DU} -sh /var/snap/docker/common/var-lib-docker, \\
    ${DU} -sh /var/snap/docker/common/var-lib-docker/*, \\
    ${LS} -la /var/lib/docker, \\
    ${LS} -la /var/snap/docker/common/var-lib-docker

Cmnd_Alias AGENT_DOCKER_MIGRATE = \\
    ${SNAP} stop docker, \\
    ${SNAP} remove --purge docker, \\
    ${SYSTEMCTL} stop docker.socket, \\
    ${SYSTEMCTL} stop docker, \\
    ${SYSTEMCTL} stop containerd, \\
    ${SYSTEMCTL} disable docker.socket, \\
    ${SYSTEMCTL} disable docker, \\
    ${SYSTEMCTL} disable containerd, \\
    ${APT_GET} remove --purge -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin docker-ce-rootless-extras, \\
    ${APT_GET} autoremove -y

Cmnd_Alias AGENT_SYSTEMD_BOOT = \\
    ${LOGINCTL} enable-linger ${USER_NAME}

# Broad FS cleanup — agent hits root-owned bind/scratch files often.
# rm/chown/chmod/mkdir with any args (NOPASSWD). Accept the risk for this machine.
Cmnd_Alias AGENT_FS_OWN = \\
    ${RM}, \\
    ${CHOWN}, \\
    ${CHMOD}, \\
    ${MKDIR}

${USER_NAME} ALL=(root) NOPASSWD: AGENT_DOCKER_READ, AGENT_DOCKER_MIGRATE, AGENT_SYSTEMD_BOOT, AGENT_FS_OWN
EOF

echo "=== Agent sudoers install ==="
echo "Target: $SUDOERS_FILE"
echo ""
echo "--- File contents ---"
cat "$TMP"
echo "--- end ---"
echo ""

if ! sudo visudo -cf "$TMP" >/dev/null; then
  echo "ERROR: visudo rejected the generated file:" >&2
  sudo visudo -cf "$TMP" >&2 || true
  exit 1
fi
echo "visudo -c: OK"
echo ""

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Dry run — no changes made."
  exit 0
fi

if [[ "$YES" -ne 1 ]]; then
  read -r -p "Install this file to $SUDOERS_FILE? [y/N] " ans
  case "$ans" in
    y | Y | yes | YES) ;;
    *)
      echo "Aborted."
      exit 0
      ;;
  esac
fi

if [[ -f "$SUDOERS_FILE" ]]; then
  BACKUP="${SUDOERS_FILE}.bak.$(date +%Y%m%d%H%M%S)"
  echo "Backing up existing file to $BACKUP"
  sudo cp -a "$SUDOERS_FILE" "$BACKUP"
fi

sudo install -o root -g root -m 0440 "$TMP" "$SUDOERS_FILE"
sudo visudo -c

echo ""
echo "Testing passwordless rules..."
if sudo -n du -sh /var/lib/docker >/dev/null 2>&1; then
  echo "  sudo -n du -sh /var/lib/docker  OK"
else
  echo "  sudo -n du -sh /var/lib/docker  FAILED (check paths in $SUDOERS_FILE)" >&2
  exit 1
fi

if sudo -n loginctl enable-linger "$USER_NAME" >/dev/null 2>&1; then
  echo "  sudo -n loginctl enable-linger $USER_NAME  OK"
else
  echo "  sudo -n loginctl enable-linger $USER_NAME  FAILED" >&2
  exit 1
fi

echo ""
echo "Done. Agent can now run planned infra sudo without askpass."
echo "Test in agent:  sudo -n true && sudo -n du -sh /var/lib/docker"
