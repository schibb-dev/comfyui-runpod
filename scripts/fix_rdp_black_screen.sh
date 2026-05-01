#!/bin/bash
# Fix xrdp black screen: ensure XFCE starts. Run on the remote: sudo bash fix_rdp_black_screen.sh

set -e

echo "=== Fixing xrdp session for XFCE ==="

# 1. Set .xsession for yuji (and root in case)
for u in yuji root; do
  if getent passwd "$u" >/dev/null 2>&1; then
    h=$(getent passwd "$u" | cut -d: -f6)
    echo "xfce4-session" > "$h/.xsession"
    chown "$u:$u" "$h/.xsession"
    chmod 644 "$h/.xsession"
    echo "Set .xsession for $u"
  fi
done

# 2. Force startwm.sh to run XFCE: replace the script so it only starts xfce
cat > /etc/xrdp/startwm.sh << 'EOF'
#!/bin/sh
if [ -r /etc/default/locale ]; then
  . /etc/default/locale
  export LANG LANGUAGE
fi
exec startxfce4
EOF
chmod +x /etc/xrdp/startwm.sh
echo "Updated /etc/xrdp/startwm.sh"

# 3. Restart xrdp
systemctl restart xrdp
echo "Restarted xrdp"

echo ""
echo "Disconnect RDP and connect again. If there is a session dropdown, choose 'Xorg'."
echo "Login as yuji."