#!/bin/bash

# Targeted Fix: Use NVIDIA's built-in compute-only mode
# This approach uses NVIDIA's own mechanisms to disable display
# Run with: sudo bash targeted_gt710_fix.sh

set -e

echo "=== Targeted GT710 Display Fix ==="
echo "Using NVIDIA's built-in compute-only mode"
echo

# Step 1: Create NVIDIA driver configuration to disable display on RTX cards
echo "1. Creating NVIDIA driver configuration..."
cat > /etc/modprobe.d/nvidia-disable-display.conf << 'EOF'
# Disable display functionality on NVIDIA driver
# This forces RTX cards to be compute-only
options nvidia NVreg_UseDisplayDevice=0
options nvidia NVreg_EnableGpuFirmware=0
options nvidia NVreg_EnableStreamMemOPs=0
options nvidia NVreg_EnableGpuFirmware=0
EOF
echo "   ✓ NVIDIA display-disabled configuration created"

# Step 2: Create X.org configuration that forces GT710
echo "2. Creating X.org configuration..."
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
    Option         "DRI" "3"
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
echo "   ✓ X.org configuration created"

# Step 3: Install Nouveau driver
echo "3. Ensuring Nouveau driver is installed..."
apt install -y xserver-xorg-video-nouveau
echo "   ✓ Nouveau driver confirmed"

# Step 4: Update initramfs
echo "4. Updating initramfs..."
update-initramfs -u
echo "   ✓ Initramfs updated"

# Step 5: Show current status
echo "5. Current driver status:"
echo "   NVIDIA modules:"
lsmod | grep nvidia | head -3 || echo "   (No NVIDIA modules loaded)"
echo "   Nouveau modules:"
lsmod | grep nouveau | head -3 || echo "   (No Nouveau modules loaded)"

echo
echo "=== Targeted Fix Complete ==="
echo "Please reboot your system:"
echo "  sudo reboot"
echo
echo "After reboot:"
echo "  - GT710 should drive display via Nouveau"
echo "  - RTX cards should be compute-only via NVIDIA"
echo "  - Check with: nvidia-smi"
echo "  - Verify display: glxinfo | grep 'OpenGL renderer'"

