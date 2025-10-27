#!/bin/bash

# Script to verify GPU configuration after reboot
# Run with: bash verify_gpu_config.sh

echo "=== GPU Configuration Verification ==="
echo

echo "1. Checking NVIDIA GPU detection:"
nvidia-smi
echo

echo "2. Checking PCI GPU detection:"
lspci | grep -i vga
echo

echo "3. Checking display driver:"
echo "DISPLAY variable: $DISPLAY"
echo "OpenGL renderer:"
glxinfo | grep "OpenGL renderer" 2>/dev/null || echo "   (glxinfo not available - install with: sudo apt install mesa-utils)"
echo

echo "4. Checking compute mode for RTX cards:"
echo "RTX 5060 Ti #0 compute mode:"
nvidia-smi -i 0 --query-gpu=compute_mode --format=csv,noheader,nounits 2>/dev/null || echo "   (GPU 0 not accessible)"
echo "RTX 5060 Ti #1 compute mode:"
nvidia-smi -i 1 --query-gpu=compute_mode --format=csv,noheader,nounits 2>/dev/null || echo "   (GPU 1 not accessible)"
echo

echo "5. Checking persistent mode:"
nvidia-smi --query-gpu=persistence_mode --format=csv,noheader,nounits 2>/dev/null || echo "   (Persistent mode check failed)"
echo

echo "6. Testing CUDA visibility:"
echo "CUDA_VISIBLE_DEVICES=0,1 nvidia-smi" 
CUDA_VISIBLE_DEVICES=0,1 nvidia-smi 2>/dev/null || echo "   (CUDA test failed)"
echo

echo "=== Verification Complete ==="
echo "Expected results:"
echo "  - nvidia-smi should show only RTX 5060 Ti cards"
echo "  - Display should be driven by GT710 via Nouveau"
echo "  - RTX cards should be in EXCLUSIVE_PROCESS mode"
echo "  - Persistent mode should be enabled"




