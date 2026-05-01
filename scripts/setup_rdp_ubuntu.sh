#!/bin/bash
# Setup xrdp on Ubuntu for headless RDP access. Run with: sudo bash setup_rdp_ubuntu.sh

set -e

echo "=== Installing xrdp and XFCE (lightweight desktop) ==="
apt update
apt install -y xrdp xorgxrdp xfce4 xfce4-goodies

echo "=== Configuring xrdp to use XFCE ==="
# Set default session for the user who runs this script (usually root; run as target user if needed)
SUDO_USER="${SUDO_USER:-$USER}"
if [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ]; then
  USER_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
  echo "xfce4-session" > "$USER_HOME/.xsession"
  chown "$SUDO_USER:$SUDO_USER" "$USER_HOME/.xsession"
  chmod 644 "$USER_HOME/.xsession"
  echo "Set .xsession for user $SUDO_USER"
else
  echo "xfce4-session" | tee /etc/xrdp/startwm.sh.pref
  # Ensure startwm.sh ends with starting a session
  if ! grep -q "startxfce4" /etc/xrdp/startwm.sh; then
    sed -i 's/^exit 1$/exec \/usr\/bin\/startxfce4/' /etc/xrdp/startwm.sh || true
  fi
fi

# Common fix: ensure startwm.sh runs xfce
if [ -f /etc/xrdp/startwm.sh ]; then
  if ! grep -q "startxfce4" /etc/xrdp/startwm.sh; then
    echo 'exec startxfce4' >> /etc/xrdp/startwm.sh
  fi
fi

echo "=== Enabling and starting xrdp ==="
systemctl enable xrdp
systemctl start xrdp
systemctl status xrdp --no-pager || true

echo ""
echo "=== Done ==="
echo "Connect from Windows: Remote Desktop -> aritomo-thinkstation-p920 (or this machine's Tailscale IP)"
echo "Log in as user: yuji"
echo ""
echo "Or over SSH tunnel: ssh -L 3389:localhost:3389 yuji@aritomo-thinkstation-p920"
echo "Then RDP to localhost"
echo ""
echo "If you use ufw, open port: sudo ufw allow 3389/tcp && sudo ufw reload"
