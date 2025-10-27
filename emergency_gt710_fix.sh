#!/bin/bash

# Emergency GT710 Fix - Simple Working Configuration
# This creates a minimal working X.org config for GT710
# Run with: sudo bash emergency_gt710_fix.sh

set -e

echo "=== Emergency GT710 Fix ==="
echo "Creating minimal working X.org configuration"
echo

# Step 1: Backup current config
echo "1. Backing up current configuration..."
cp /etc/X11/xorg.conf /etc/X11/xorg.conf.backup.$(date +%Y%m%d_%H%M%S)
echo "   ✓ Backup created"

# Step 2: Create minimal working X.org config
echo "2. Creating minimal X.org configuration..."
cat > /etc/X11/xorg.conf << 'EOF'
Section "ServerLayout"
    Identifier     "Layout0"
    Screen      0  "Screen0"
EndSection

Section "Device"
    Identifier     "Device0"
    Driver         "nouveau"
    BusID          "PCI:24:0:0"
EndSection

Section "Screen"
    Identifier     "Screen0"
    Device         "Device0"
    Monitor        "Monitor0"
    DefaultDepth    24
    SubSection     "Display"
        Depth       24
        Modes      "1920x1080"
    EndSubSection
EndSection

Section "Monitor"
    Identifier     "Monitor0"
    VendorName     "Unknown"
    ModelName      "Unknown"
EndSection
EOF
echo "   ✓ Minimal X.org configuration created"

# Step 3: Ensure Nouveau driver is installed
echo "3. Ensuring Nouveau driver is installed..."
apt install -y xserver-xorg-video-nouveau
echo "   ✓ Nouveau driver confirmed"

# Step 4: Remove conflicting NVIDIA configurations
echo "4. Removing conflicting NVIDIA configurations..."
rm -f /etc/modprobe.d/nvidia-compute-only.conf
rm -f /etc/modprobe.d/nvidia-disable-display.conf
rm -f /etc/modprobe.d/nvidia-compute.conf
echo "   ✓ Conflicting configurations removed"

# Step 5: Update initramfs
echo "5. Updating initramfs..."
update-initramfs -u
echo "   ✓ Initramfs updated"

echo
echo "=== Emergency Fix Complete ==="
echo "This creates a minimal working configuration for GT710"
echo "Please reboot your system:"
echo "  sudo reboot"
echo
echo "After reboot:"
echo "  - GT710 should show display"
echo "  - RTX cards will still be available for compute"
echo "  - We can then configure RTX cards for compute-only"

