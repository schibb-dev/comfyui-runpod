#!/bin/bash
# Fix xrdp dropping connection after login. Run on the remote: sudo bash fix_rdp_drops_after_login.sh
# Then: sudo reboot  (multi-user.target takes effect after reboot)

set -e

echo "=== Fixing xrdp drop-after-login ==="

# 1. Boot to multi-user (no local GUI) so xrdp can start its own session.
#    On headless this is correct; no console desktop to conflict.
systemctl set-default multi-user.target
echo "Set default target to multi-user (no local GUI on boot)."

# 2. startwm.sh: use bash (in case /etc/profile.d uses bash-isms) and start XFCE
cat > /etc/xrdp/startwm.sh << 'ENDSTARTWM'
#!/bin/bash
if [ -r /etc/default/locale ]; then
  . /etc/default/locale
  export LANG LANGUAGE
fi
exec startxfce4
ENDSTARTWM
chmod +x /etc/xrdp/startwm.sh
echo "Updated /etc/xrdp/startwm.sh (bash + startxfce4)."

# 3. .xsession for yuji
for u in yuji root; do
  if getent passwd "$u" >/dev/null 2>&1; then
    h=$(getent passwd "$u" | cut -d: -f6)
    echo "xfce4-session" > "$h/.xsession"
    chown "$u:$u" "$h/.xsession"
    chmod 644 "$h/.xsession"
    echo "Set .xsession for $u"
  fi
done

# 4. Polkit: allow remote (xrdp) sessions to get auth prompts instead of failing
mkdir -p /etc/polkit-1/rules.d
cat > /etc/polkit-1/rules.d/50-xrdp-session.rules << 'ENDPOLKIT'
polkit.addRule(function(action, subject) {
  if (subject.user && subject.local === false) {
    return polkit.Result.AUTH_ADMIN;
  }
});
ENDPOLKIT
echo "Added polkit rule for remote sessions."

# 5. Restart xrdp
systemctl restart xrdp
echo "Restarted xrdp."

echo ""
echo "Reboot the machine so multi-user.target takes effect:"
echo "  sudo reboot"
echo ""
echo "After reboot, connect again with RDP (Xorg, user yuji)."
