#!/bin/bash

# Force GT710 Display Configuration
# Run with: sudo bash force_gt710_display.sh

set -e

echo "=== Forcing GT710 Display Configuration ==="
echo

# Step 1: Install the aggressive X.org configuration
echo "1. Installing aggressive X.org configuration..."
cp /home/yuji/Code/comfyui-runpod/xorg.conf.force_gt710 /etc/X11/xorg.conf
echo "   ✓ X.org configuration updated"

# Step 2: Create NVIDIA driver configuration to disable display
echo "2. Creating NVIDIA driver configuration..."
cat > /etc/modprobe.d/nvidia-compute-only.conf << 'EOF'
# Configure NVIDIA driver for compute-only use
# This prevents NVIDIA driver from taking display control
options nvidia NVreg_UseDisplayDevice=0
options nvidia NVreg_EnableGpuFirmware=0
EOF
echo "   ✓ NVIDIA compute-only configuration created"

# Step 3: Ensure Nouveau driver is available
echo "3. Ensuring Nouveau driver is installed..."
apt install -y xserver-xorg-video-nouveau
echo "   ✓ Nouveau driver confirmed"

# Step 4: Update initramfs
echo "4. Updating initramfs..."
update-initramfs -u
echo "   ✓ Initramfs updated"

# Step 5: Show current configuration
echo "5. Current configuration status:"
echo "   X.org config:"
grep -E "(Driver|BusID)" /etc/X11/xorg.conf | head -5
echo "   NVIDIA modules:"
lsmod | grep nvidia | head -3 || echo "   (No NVIDIA modules loaded)"
echo "   Nouveau modules:"
lsmod | grep nouveau | head -3 || echo "   (No Nouveau modules loaded)"

echo
echo "=== Configuration Complete ==="
echo "Please reboot your system:"
echo "  sudo reboot"
echo
echo "After reboot:"
echo "  - GT710 should be the ONLY display device"
echo "  - RTX 5060 Ti cards should be compute-only"
echo "  - Check with: nvidia-smi"
echo "  - Verify display: glxinfo | grep 'OpenGL renderer'"


