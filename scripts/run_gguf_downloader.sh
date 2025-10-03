#!/bin/bash
# GGUF Models Downloader Script
# Downloads required GGUF models from city96 repositories

set -e

echo "🚀 Starting GGUF models download process..."

# Check if models already exist
LLM_DIR="/ComfyUI/models/LLM"
UNET_DIR="/ComfyUI/models/unet"

if [ -f "$LLM_DIR/umt5-xxl-encoder-Q5_K_M.gguf" ] && [ -f "$UNET_DIR/wan2.1-i2v-14b-480p-Q5_K_M.gguf" ]; then
    echo "✅ All GGUF models already exist, skipping download"
    exit 0
fi

# Run the Python downloader
echo "📦 Downloading GGUF models..."
python3 /workspace/scripts/download_gguf_models.py

echo "✅ GGUF models download completed"
