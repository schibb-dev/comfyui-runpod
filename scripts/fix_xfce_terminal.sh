#!/bin/bash
# Fix XFCE terminal not opening when clicking panel icon. Run on the remote: sudo bash fix_xfce_terminal.sh

set -e

echo "=== Ensuring XFCE terminal is installed and default ==="

# Install terminal (xfce4-goodies includes it, but may be missing on minimal install)
apt-get update -qq
apt-get install -y xfce4-terminal

# Set as default terminal for the session (for user yuji)
for u in yuji root; do
  if getent passwd "$u" >/dev/null 2>&1; then
    h=$(getent passwd "$u" | cut -d: -f6)
    mkdir -p "$h/.config/xfce4"
    # Default helper for "open terminal" / file manager "open in terminal"
    if [ -d "$h/.config/xfce4" ]; then
      echo "[Configuration]
TerminalEmulator=xfce4-terminal" > "$h/.config/xfce4/helpers.rc"
      chown -R "$u:$u" "$h/.config/xfce4"
      echo "Set default terminal for $u"
    fi
  fi
done

echo ""
echo "Done. From RDP: log out and log back in (or restart the panel), then try the terminal icon again."
echo "If it still does not show: right-click desktop -> Open Terminal, or press Alt+F2 and run: xfce4-terminal"
