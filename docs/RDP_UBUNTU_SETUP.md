# RDP on Ubuntu (headless)

Set up xrdp so you can connect from Windows Remote Desktop to your Ubuntu machine over SSH, Tailscale, or directly.

## Tailscale (if already running)

If **Tailscale is already running** on the Ubuntu machine and on your Windows PC:

- **Connect using the Tailscale IP:** In Remote Desktop (mstsc), use the Ubuntu machine’s **Tailscale IP** (e.g. `100.x.x.x`). You can find it on the Ubuntu box with `tailscale ip -4` or in the Tailscale admin console.
- **No need to open 3389 on the public firewall** — traffic stays on the Tailscale VPN. You still need xrdp listening on 3389 (default); no extra config for Tailscale.
- **No SSH tunnel required** — Tailscale already provides encrypted access between your devices.

So: install xrdp + desktop as below, then RDP to the machine’s Tailscale IP.

**Example host:** `aritomo-thinkstation-p920` (Tailscale MagicDNS). SSH: `yuji@aritomo-thinkstation-p920`. RDP to that hostname or its Tailscale IP (e.g. `100.x.x.x`).

---

## 1. Install xrdp and a desktop (if headless)

```bash
# Update and install xrdp + lightweight desktop (XFCE is a good choice for headless)
sudo apt update
sudo apt install -y xrdp xorgxrdp

# If the machine has no desktop yet, install one (pick one)
sudo apt install -y xfce4 xfce4-goodies   # lightweight
# OR
# sudo apt install -y ubuntu-desktop       # full Ubuntu desktop (heavier)
```

## 2. Use the right session for xrdp

Tell xrdp to start your desktop:

```bash
# For XFCE
echo "xfce4-session" | tee ~/.xsession
# Make sure xrdp can read it
chmod 644 ~/.xsession

# If you chose ubuntu-desktop (GNOME), create/edit:
# echo "gnome-session" | tee ~/.xsession
```

For **Ubuntu 20.04+ with GNOME**, you may need:

```bash
sudo sed -i 's/^test -x \/etc\/X11\/Xsession/test -x \/usr\/bin\/startxfce4 \&\& exec \/usr\/bin\/startxfce4\ntest -x \/etc\/X11\/Xsession/' /etc/xrdp/startwm.sh
```

Or manually set the session in `/etc/xrdp/startwm.sh` so the last line is:

```bash
exec startxfce4
# or: exec gnome-session
```

## 3. Start and enable xrdp

```bash
sudo systemctl enable xrdp
sudo systemctl start xrdp
sudo systemctl status xrdp
```

## 4. Open the RDP port (if using a firewall)

Only needed if you connect **without** Tailscale (e.g. direct LAN or SSH tunnel).

```bash
sudo ufw allow 3389/tcp
sudo ufw reload
# Or: sudo ufw allow from YOUR_IP to any port 3389
```

**With Tailscale:** You usually don’t need to allow 3389 on `ufw` for Tailscale; Tailscale traffic uses its own interface. If RDP to the Tailscale IP fails, try: `sudo ufw allow in on tailscale0` or allow 3389 as above.

## 5. Connect from Windows

- **Tailscale (recommended if both machines are on Tailscale):** Open Remote Desktop, connect to the Ubuntu machine’s **Tailscale IP** (e.g. `100.x.x.x`). No tunnel or port forwarding needed.
- **Direct LAN:** Use the machine’s LAN IP in Remote Desktop.
- **Via SSH tunnel:** `ssh -L 3389:localhost:3389 yuji@aritomo-thinkstation-p920`, then RDP to **localhost**.

## 6. Optional: Restrict xrdp to localhost (use only over SSH)

If you only want RDP over the SSH tunnel:

```bash
# Edit xrdp to listen only on 127.0.0.1
sudo sed -i 's/^port=3389/port=3389\naddress=127.0.0.1/' /etc/xrdp/xrdp.ini
# Or add under [Globals]: address=127.0.0.1
sudo systemctl restart xrdp
```

Then always connect via the SSH tunnel; no need to open 3389 in the firewall.

## Quick setup (yuji @ aritomo-thinkstation-p920)

From PowerShell on your Windows machine:

```powershell
# Copy SSH key to remote (one-time)
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh yuji@aritomo-thinkstation-p920 "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"

# Copy and run RDP setup script
scp "c:\Users\yuji\Code\comfyui-runpod\scripts\setup_rdp_ubuntu.sh" yuji@aritomo-thinkstation-p920:~/
ssh yuji@aritomo-thinkstation-p920 "sudo bash ~/setup_rdp_ubuntu.sh"
```

Then open Remote Desktop and connect to **aritomo-thinkstation-p920** (or its Tailscale IP). Log in as **yuji** with your Linux password.

### Add another SSH key to the remote

When you have a second public key (e.g. from another machine or a new key), add it to the ThinkStation’s `authorized_keys`:

**Option A – from Windows (with the new key’s public file):**
```powershell
type PATH_TO_NEW_PUB_KEY | ssh yuji@aritomo-thinkstation-p920 "cat >> ~/.ssh/authorized_keys"
```
Example if the key is at `$env:USERPROFILE\.ssh\id_rsa.pub`:  
`type $env:USERPROFILE\.ssh\id_rsa.pub | ssh yuji@aritomo-thinkstation-p920 "cat >> ~/.ssh/authorized_keys"`

**Option B – from the remote (paste the one-line public key):**
```bash
ssh yuji@aritomo-thinkstation-p920
echo "ssh-ed25519 AAAA... or ssh-rsa AAAA... paste the full line" >> ~/.ssh/authorized_keys
```

## Troubleshooting

- **Black screen after login:** Usually wrong session. Fix `~/.xsession` or `/etc/xrdp/startwm.sh` to `startxfce4` or `gnome-session`. Use `scripts/fix_rdp_black_screen.sh` on the remote.
- **Connection drops right after entering credentials (Windows):** Often the machine is at `graphical.target` and the session conflicts. On the remote run `scripts/fix_rdp_drops_after_login.sh` then **reboot**. That script sets `multi-user.target`, fixes `startwm.sh`, and adds a polkit rule. After reboot, RDP should stay connected.
- **macOS RDP client drops before login screen:** Try Microsoft Remote Desktop from the Mac App Store; ensure the host is **aritomo-thinkstation-p920** or the Tailscale IP. If it still drops, try disabling “Use TLS for client authentication” or similar in the Mac client if present, or use the Windows RDP client (e.g. from a Windows VM) until the server-side fix above is applied and the machine is rebooted.
- **“Connection refused”:** Check `sudo systemctl status xrdp` and that 3389 is allowed (or you’re using the tunnel).
- **Reconnect and session is gone:** In xrdp, use “Sesman - Xorg” session type; avoid “Xvnc” unless you installed and want VNC.
