#!/bin/bash
# Run CivitAI LoRA downloader after WAN models are downloaded
# This script integrates with the WAN startup process

set -e

echo "🎭 Running CivitAI LoRA downloader..."

# Check if CivitAI token is available
if [ -z "$CIVITAI_TOKEN" ] && [ ! -f "/workspace/.civitai_token" ]; then
    echo "⚠️  No CivitAI token found. Skipping LoRA downloads."
    echo "   To enable LoRA downloads:"
    echo "   1. Run: ./scripts/setup_credentials.sh"
    echo "   2. Restart the container"
    exit 0
fi

# Wait for ComfyUI to be ready
echo "⏳ Waiting for ComfyUI to be ready..."
max_attempts=30
attempt=0

while [ $attempt -lt $max_attempts ]; do
    if curl -s http://localhost:8188/ > /dev/null 2>&1; then
        echo "✅ ComfyUI is ready!"
        break
    fi
    
    attempt=$((attempt + 1))
    echo "   Attempt $attempt/$max_attempts - waiting..."
    sleep 10
done

if [ $attempt -eq $max_attempts ]; then
    echo "❌ ComfyUI did not become ready in time. Skipping LoRA downloads."
    exit 1
fi

# Run the CivitAI downloader
echo "🚀 Starting CivitAI LoRA downloads..."
cd /workspace

# Use the CivitAI downloader script
python3 scripts/civitai_lora_downloader.py \
    --comfyui-dir /ComfyUI \
    --base-dir /workspace \
    --wan-version 2.1 \
    --modality i2v \
    --resolution 480

echo "✅ CivitAI LoRA downloader completed!"

