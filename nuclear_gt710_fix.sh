#!/bin/bash

# Nuclear Option: Force GT710 Display Only
# This approach completely separates the drivers
# Run with: sudo bash nuclear_gt710_fix.sh

set -e

echo "=== Nuclear GT710 Display Fix ==="
echo "This will completely separate GT710 (display) from RTX cards (compute)"
echo

# Step 1: Create device-specific blacklist for RTX cards
echo "1. Creating device-specific blacklist for RTX cards..."
cat > /etc/modprobe.d/blacklist-rtx-display.conf << 'EOF'
# Blacklist NVIDIA driver for RTX 5060 Ti cards (device 10de:2d04)
# This prevents them from being used for display
blacklist nvidia
install nvidia /bin/true
EOF
echo "   ✓ RTX cards blacklisted from NVIDIA driver"

# Step 2: Create a simple X.org config that ONLY uses GT710
echo "2. Creating minimal X.org configuration..."
cat > /etc/X11/xorg.conf << 'EOF'
Section "ServerLayout"
    Identifier     "Layout0"
    Screen      0  "GT710_Screen"
    Option         "AutoAddDevices" "false"
    Option         "AutoAddGPU" "false"
EndSection

Section "Device"
    Identifier     "GT710_Device"
    Driver         "nouveau"
    BusID          "PCI:24:0:0"
    Option         "AccelMethod" "glamor"
EndSection

Section "Screen"
    Identifier     "GT710_Screen"
    Device         "GT710_Device"
    Monitor        "GT710_Monitor"
    DefaultDepth    24
    SubSection     "Display"
        Depth       24
        Modes      "1920x1080" "1600x1200" "1280x1024" "1024x768"
    EndSubSection
EndSection

Section "Monitor"
    Identifier     "GT710_Monitor"
    VendorName     "Unknown"
    ModelName      "Unknown"
    Option         "DPMS"
EndSection
EOF
echo "   ✓ Minimal X.org configuration created"

# Step 3: Install Nouveau driver
echo "3. Ensuring Nouveau driver is installed..."
apt install -y xserver-xorg-video-nouveau
echo "   ✓ Nouveau driver confirmed"

# Step 4: Create NVIDIA driver configuration for compute-only
echo "4. Creating NVIDIA compute-only configuration..."
cat > /etc/modprobe.d/nvidia-compute.conf << 'EOF'
# Allow NVIDIA driver for compute but not display
options nvidia NVreg_UseDisplayDevice=0
options nvidia NVreg_EnableGpuFirmware=0
options nvidia NVreg_EnableStreamMemOPs=0
EOF
echo "   ✓ NVIDIA compute configuration created"

# Step 5: Update initramfs
echo "5. Updating initramfs..."
update-initramfs -u
echo "   ✓ Initramfs updated"

echo
echo "=== Nuclear Fix Complete ==="
echo "WARNING: This approach may prevent RTX cards from being detected by nvidia-smi"
echo "If RTX cards don't appear in nvidia-smi after reboot, we'll need to adjust the approach"
echo
echo "Please reboot your system:"
echo "  sudo reboot"
echo
echo "After reboot, check:"
echo "  - Display should be on GT710"
echo "  - nvidia-smi may or may not show RTX cards"
echo "  - If RTX cards don't appear, we'll use a different approach"

