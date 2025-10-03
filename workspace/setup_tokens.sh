#!/bin/bash
if [ -f "/workspace/credentials/huggingface_token" ]; then
  export HUGGINGFACE_TOKEN=$(cat /workspace/credentials/huggingface_token)
  echo "✅ Hugging Face token loaded"
fi
if [ -f "/workspace/credentials/civitai_token" ]; then
  export CIVITAI_TOKEN=$(cat /workspace/credentials/civitai_token)
  export civitai_token=$(cat /workspace/credentials/civitai_token)
  echo "✅ CivitAI token loaded"
fi

# Download GGUF models if they don't exist
if [ -f "/workspace/scripts/run_gguf_downloader.sh" ]; then
  echo "🚀 Downloading GGUF models..."
  /workspace/scripts/run_gguf_downloader.sh
  echo "✅ GGUF models download completed"
else
  echo "⚠️  GGUF downloader script not found"
fi
