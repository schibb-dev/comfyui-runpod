#!/bin/bash

# Fix script for GPU configuration
# Run with: sudo bash fix_gpu_config.sh

set -e

echo "=== Fixing GPU Configuration ==="
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

# Step 4: Load Nouveau driver
echo "4. Loading Nouveau driver..."
modprobe nouveau
echo "   ✓ Nouveau driver loaded"

# Step 5: Update initramfs
echo "5. Updating initramfs..."
update-initramfs -u
echo "   ✓ Initramfs updated"

echo
echo "=== Fix Complete ==="
echo "Please reboot your system:"
echo "  sudo reboot"
echo
echo "After reboot, the GT710 should drive the display via Nouveau,"
echo "and RTX 5060 Ti cards should be available for compute only."



