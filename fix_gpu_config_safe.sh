#!/bin/bash

# Fix script for GPU configuration - SAFER VERSION
# Run with: sudo bash fix_gpu_config.sh

set -e

echo "=== Fixing GPU Configuration (Safer Version) ==="
echo

# Step 1: Remove the problematic blacklist
echo "1. Removing problematic NVIDIA blacklist..."
rm -f /etc/modprobe.d/blacklist-nvidia-gt710.conf
echo "   ✓ Blacklist removed"

# Step 2: Install fixed X.org configuration
echo "2. Installing fixed X.org configuration..."
cp /home/yuji/Code/comfyui-runpod/xorg.conf.fixed /etc/X11/xorg.conf
echo "   ✓ Fixed X.org configuration installed"

# Step 3: Ensure Nouveau driver is installed
echo "3. Ensuring Nouveau driver is installed..."
apt install -y xserver-xorg-video-nouveau
echo "   ✓ Nouveau driver confirmed"

# Step 4: Try to load Nouveau driver safely
echo "4. Attempting to load Nouveau driver..."
if modprobe nouveau 2>/dev/null; then
    echo "   ✓ Nouveau driver loaded successfully"
else
    echo "   ⚠ Nouveau driver load failed (this is normal if NVIDIA driver is active)"
    echo "   ✓ Driver will be loaded automatically on reboot"
fi

# Step 5: Update initramfs
echo "5. Updating initramfs..."
update-initramfs -u
echo "   ✓ Initramfs updated"

# Step 6: Check current driver status
echo "6. Checking current driver status..."
echo "   NVIDIA modules loaded:"
lsmod | grep nvidia | head -3 || echo "   (No NVIDIA modules currently loaded)"
echo "   Nouveau modules loaded:"
lsmod | grep nouveau | head -3 || echo "   (No Nouveau modules currently loaded)"

echo
echo "=== Fix Complete ==="
echo "The configuration has been applied. Please reboot your system:"
echo "  sudo reboot"
echo
echo "After reboot:"
echo "  - GT710 should drive the display via Nouveau driver"
echo "  - RTX 5060 Ti cards should be available for compute only"
echo "  - Check with: nvidia-smi"
echo "  - Verify display with: glxinfo | grep 'OpenGL renderer'"



