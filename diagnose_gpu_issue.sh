#!/bin/bash

# Diagnostic script to understand GPU configuration issues
# Run with: bash diagnose_gpu_issue.sh

echo "=== GPU Configuration Diagnostic ==="
echo

echo "1. PCI GPU Detection:"
lspci | grep -i vga
echo

echo "2. NVIDIA Driver Status:"
nvidia-smi 2>/dev/null || echo "   nvidia-smi failed"
echo

echo "3. Driver Modules:"
echo "   NVIDIA modules:"
lsmod | grep nvidia | head -5 || echo "   (No NVIDIA modules)"
echo "   Nouveau modules:"
lsmod | grep nouveau | head -5 || echo "   (No Nouveau modules)"
echo

echo "4. X.org Configuration:"
echo "   Current X.org config:"
grep -E "(Driver|BusID|Identifier)" /etc/X11/xorg.conf | head -10
echo

echo "5. NVIDIA Driver Configuration:"
echo "   NVIDIA modprobe configs:"
ls -la /etc/modprobe.d/nvidia* 2>/dev/null || echo "   (No NVIDIA modprobe configs)"
echo

echo "6. X.org Log Analysis:"
echo "   Recent X.org errors:"
grep -i error /var/log/Xorg.0.log | tail -5 2>/dev/null || echo "   (No recent errors)"
echo

echo "7. Display Driver Test:"
echo "   Current display driver:"
glxinfo | grep "OpenGL renderer" 2>/dev/null || echo "   (glxinfo not available - install with: sudo apt install mesa-utils)"
echo

echo "8. Process Analysis:"
echo "   Processes using GPU 0:"
nvidia-smi -i 0 --query-compute-apps=pid,process_name --format=csv,noheader,nounits 2>/dev/null || echo "   (No processes on GPU 0)"
echo "   Processes using GPU 1:"
nvidia-smi -i 1 --query-compute-apps=pid,process_name --format=csv,noheader,nounits 2>/dev/null || echo "   (No processes on GPU 1)"
echo

echo "=== Diagnostic Complete ==="
echo "Key observations:"
echo "  - GT710 should be at PCI 18:00.0"
echo "  - RTX cards should be at PCI 3b:00.0 and af:00.0"
echo "  - Only GT710 should show in display driver test"
echo "  - RTX cards should show 'Off' in Disp.A column"

