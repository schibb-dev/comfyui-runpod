#!/bin/bash
# WAN Model Downloader Runner
# Runs the WAN model downloader script with proper environment setup

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "🎭 WAN Model Downloader"
echo "========================"

# Check if we're running in a container
if [ -f "/.dockerenv" ]; then
    echo "🐳 Running in Docker container"
    COMFYUI_DIR="/ComfyUI"
else
    echo "🖥️  Running on host system"
    COMFYUI_DIR="${PROJECT_ROOT}/ComfyUI"
fi

# Check if ComfyUI directory exists
if [ ! -d "$COMFYUI_DIR" ]; then
    echo "❌ ComfyUI directory not found: $COMFYUI_DIR"
    echo "Please ensure ComfyUI is properly installed"
    exit 1
fi

# Check for Hugging Face token
if [ -f "/workspace/credentials/huggingface_token" ]; then
    echo "🔑 Found Hugging Face token in credentials"
    export HUGGINGFACE_TOKEN=$(cat /workspace/credentials/huggingface_token)
elif [ -n "$HUGGINGFACE_TOKEN" ]; then
    echo "🔑 Using Hugging Face token from environment"
else
    echo "⚠️  No Hugging Face token found"
    echo "Some models may not be accessible without authentication"
fi

# Run the WAN model downloader
echo "🚀 Starting WAN model downloads..."
python3 "${SCRIPT_DIR}/download_wan_models.py" \
    --output-dir "${COMFYUI_DIR}/models" \
    --skip-existing

echo ""
echo "✅ WAN model download process completed!"
echo ""
echo "📁 Models are located in: ${COMFYUI_DIR}/models/"
echo "  • VAE models: ${COMFYUI_DIR}/models/vae/"
echo "  • Text encoders: ${COMFYUI_DIR}/models/text_encoders/"
echo "  • CLIP vision: ${COMFYUI_DIR}/models/clip_vision/"
echo "  • Diffusion models: ${COMFYUI_DIR}/models/diffusion_models/"
echo ""
echo "🎯 Your ComfyUI setup now has all required WAN models!"
