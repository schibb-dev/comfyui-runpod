#!/usr/bin/env bash
# Create persistent SSH host keys for the output-sftp (atmoz/sftp) container so the host fingerprint
# stays stable across container recreations. Safe to run repeatedly (no-op if keys already exist).
#
# Default key directory: <repo>/.data/output-sftp-ssh-hostkeys
# Override: OUTPUT_SFTP_SSH_HOSTKEYS_DIR=/absolute/path
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KEYDIR="${OUTPUT_SFTP_SSH_HOSTKEYS_DIR:-$REPO_ROOT/.data/output-sftp-ssh-hostkeys}"

if ! command -v ssh-keygen >/dev/null 2>&1; then
  echo "ssh-keygen not found; install OpenSSH client tools." >&2
  exit 1
fi

mkdir -p "$KEYDIR"
chmod 700 "$KEYDIR"

if [[ ! -f "$KEYDIR/ssh_host_ed25519_key" ]]; then
  ssh-keygen -t ed25519 -f "$KEYDIR/ssh_host_ed25519_key" -N ''
fi
if [[ ! -f "$KEYDIR/ssh_host_rsa_key" ]]; then
  ssh-keygen -t rsa -b 4096 -f "$KEYDIR/ssh_host_rsa_key" -N ''
fi

chmod 600 "$KEYDIR"/ssh_host_ed25519_key "$KEYDIR"/ssh_host_rsa_key 2>/dev/null || true
chmod 644 "$KEYDIR"/*.pub 2>/dev/null || true

echo "SFTP SSH host keys: $KEYDIR"
