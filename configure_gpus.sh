#!/bin/bash

# Script to configure GT710 for display and RTX 5060 Ti for compute-only
# Run with: sudo bash configure_gpus.sh

set -e

echo "=== GPU Configuration Script ==="
echo "Configuring GT710 for display (Nouveau) and RTX 5060 Ti for compute (NVIDIA)"
echo

# Step 1: Backup current configuration
echo "1. Backing up current X.org configuration..."
cp /etc/X11/xorg.conf /etc/X11/xorg.conf.backup
echo "   ✓ Backup created at /etc/X11/xorg.conf.backup"

# Step 2: Install Nouveau driver
echo "2. Installing Nouveau driver..."
apt update
apt install -y xserver-xorg-video-nouveau
echo "   ✓ Nouveau driver installed"

# Step 3: Create blacklist for GT710 NVIDIA driver
echo "3. Creating NVIDIA driver blacklist for GT710..."
cp /home/yuji/Code/comfyui-runpod/blacklist-nvidia-gt710.conf /etc/modprobe.d/blacklist-nvidia-gt710.conf
echo "   ✓ Blacklist created"

# Step 4: Install new X.org configuration
echo "4. Installing new X.org configuration..."
cp /home/yuji/Code/comfyui-runpod/xorg.conf.new /etc/X11/xorg.conf
echo "   ✓ X.org configuration updated"

# Step 5: Install systemd service for compute configuration
echo "5. Installing compute configuration service..."
cp /home/yuji/Code/comfyui-runpod/nvidia-compute-setup.service /etc/systemd/system/nvidia-compute-setup.service
systemctl enable nvidia-compute-setup.service
echo "   ✓ Service installed and enabled"

# Step 6: Update initramfs
echo "6. Updating initramfs..."
update-initramfs -u
echo "   ✓ Initramfs updated"

# Step 7: Configure RTX cards for compute mode
echo "7. Configuring RTX 5060 Ti cards for compute mode..."
nvidia-smi -i 0 -c EXCLUSIVE_PROCESS
nvidia-smi -i 1 -c EXCLUSIVE_PROCESS
nvidia-smi -pm 1
echo "   ✓ RTX cards configured for compute-only"

echo
echo "=== Configuration Complete ==="
echo "Please reboot your system to apply all changes:"
echo "  sudo reboot"
echo
echo "After reboot, verify with:"
echo "  nvidia-smi"
echo "  glxinfo | grep 'OpenGL renderer'"
echo




