# Start from the ComfyUI template
FROM schibbdev/comfyui-runpod:v1.2.0

# Metadata
LABEL maintainer="schibbdev@example.com"
LABEL description="Custom ComfyUI with WAN + Florence2 + Civitai LoRA Management"
LABEL version="1.0"

# Set working directory
WORKDIR /workspace

# Update system packages
RUN apt-get update && apt-get install -y \
    vim \
    git \
    wget \
    curl \
    aria2 \
    && rm -rf /var/lib/apt/lists/*

# Install additional Python packages for our customizations
RUN pip install --no-cache-dir \
    requests \
    pathlib \
    huggingface_hub \
    safetensors \
    sageattention

# Copy our Civitai LoRA downloader script and other scripts
COPY scripts/ /workspace/scripts/

# Create credentials directory and setup token handling
RUN mkdir -p /workspace/credentials && \
    echo '#!/bin/bash' > /workspace/setup_tokens.sh && \
    echo 'if [ -f "/workspace/credentials/huggingface_token" ]; then' >> /workspace/setup_tokens.sh && \
    echo '  export HUGGINGFACE_TOKEN=$(cat /workspace/credentials/huggingface_token)' >> /workspace/setup_tokens.sh && \
    echo '  echo "✅ Hugging Face token loaded"' >> /workspace/setup_tokens.sh && \
    echo 'fi' >> /workspace/setup_tokens.sh && \
    echo 'if [ -f "/workspace/credentials/civitai_token" ]; then' >> /workspace/setup_tokens.sh && \
    echo '  export CIVITAI_TOKEN=$(cat /workspace/credentials/civitai_token)' >> /workspace/setup_tokens.sh && \
    echo '  export civitai_token=$(cat /workspace/credentials/civitai_token)' >> /workspace/setup_tokens.sh && \
    echo '  echo "✅ CivitAI token loaded"' >> /workspace/setup_tokens.sh && \
    echo 'fi' >> /workspace/setup_tokens.sh && \
    chmod +x /workspace/setup_tokens.sh

# Install additional ComfyUI custom nodes
RUN cd /ComfyUI/custom_nodes && \
    git clone https://github.com/Smirnov75/ComfyUI-mxToolkit.git && \
    git clone https://github.com/city96/ComfyUI-GGUF.git && \
    git clone https://github.com/Nuitari/ComfyUI-MultiGPU-XPU.git && \
    git clone https://github.com/pythongosssss/ComfyUI-Custom-Scripts.git && \
    cd ComfyUI-GGUF && \
    pip install --no-cache-dir -r requirements.txt || echo "No requirements.txt found"

# Create directories for models and our workspace
RUN mkdir -p /workspace/{workflows,models,output,input,scripts} \
    && mkdir -p /workspace/models/{checkpoints,loras,vae,upscale_models} \
    && mkdir -p /ComfyUI/models/{checkpoints,loras,vae,upscale_models}

# Create symlinks from ComfyUI models to workspace for persistent storage
RUN ln -sf /workspace/models/loras /ComfyUI/models/loras \
    && ln -sf /workspace/models/checkpoints /ComfyUI/models/checkpoints \
    && ln -sf /workspace/models/vae /ComfyUI/models/vae \
    && ln -sf /workspace/models/upscale_models /ComfyUI/models/upscale_models

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV COMFYUI_PATH=/ComfyUI
ENV HF_HOME=/workspace/.cache/huggingface
ENV WORKSPACE_PATH=/workspace

# Expose ports
EXPOSE 8188 22 8888

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8188/ || exit 1

# Create a custom startup script that handles the WAN wrapper properly
RUN echo '#!/bin/bash' > /custom_start.sh && \
    echo '# Load credentials' >> /custom_start.sh && \
    echo 'source /workspace/setup_tokens.sh' >> /custom_start.sh && \
    echo '' >> /custom_start.sh && \
    echo '# Create symlink for ComfyUI input directory' >> /custom_start.sh && \
    echo 'if [ ! -L "/ComfyUI/input" ]; then' >> /custom_start.sh && \
    echo '  rm -rf /ComfyUI/input' >> /custom_start.sh && \
    echo '  ln -sf /workspace/input /ComfyUI/input' >> /custom_start.sh && \
    echo '  echo "✅ ComfyUI input symlink created"' >> /custom_start.sh && \
    echo 'fi' >> /custom_start.sh && \
    echo '' >> /custom_start.sh && \
    echo '# Create symlinks for ComfyUI model directories' >> /custom_start.sh && \
    echo 'for model_dir in loras checkpoints vae upscale_models; do' >> /custom_start.sh && \
    echo '  if [ ! -L "/ComfyUI/models/$model_dir" ]; then' >> /custom_start.sh && \
    echo '    rm -rf "/ComfyUI/models/$model_dir"' >> /custom_start.sh && \
    echo '    ln -sf "/workspace/models/$model_dir" "/ComfyUI/models/$model_dir"' >> /custom_start.sh && \
    echo '    echo "✅ ComfyUI $model_dir symlink created"' >> /custom_start.sh && \
    echo '  fi' >> /custom_start.sh && \
    echo 'done' >> /custom_start.sh && \
    echo '' >> /custom_start.sh && \
    echo '# Setup CivitAI token file for the downloader' >> /custom_start.sh && \
    echo 'if [ -n "$CIVITAI_TOKEN" ]; then' >> /custom_start.sh && \
    echo '  echo "{\\"civitai_token\\": \\"$CIVITAI_TOKEN\\"}" > /workspace/.civitai_token' >> /custom_start.sh && \
    echo '  echo "✅ CivitAI token file created"' >> /custom_start.sh && \
    echo 'fi' >> /custom_start.sh && \
    echo '' >> /custom_start.sh && \
    echo '# Bootstrap custom nodes from config' >> /custom_start.sh && \
    echo 'if [ -f "/workspace/custom_nodes.yaml" ]; then' >> /custom_start.sh && \
    echo '  echo "🚀 Bootstrapping custom nodes..."' >> /custom_start.sh && \
    echo '  python3 /workspace/scripts/bootstrap_nodes.py' >> /custom_start.sh && \
    echo '  echo "✅ Custom nodes bootstrap completed"' >> /custom_start.sh && \
    echo 'else' >> /custom_start.sh && \
    echo '  echo "⚠️  No custom_nodes.yaml found, skipping bootstrap"' >> /custom_start.sh && \
    echo 'fi' >> /custom_start.sh && \
    echo '' >> /custom_start.sh && \
    echo '# Download WAN models using our custom script' >> /custom_start.sh && \
    echo 'echo "🚀 Starting WAN model downloads..."' >> /custom_start.sh && \
    echo '/workspace/scripts/run_wan_downloader.sh' >> /custom_start.sh && \
    echo 'echo "✅ WAN model downloads completed"' >> /custom_start.sh && \
    echo '' >> /custom_start.sh && \
    echo '# Copy GGUF model to text_encoders folder so CLIPLoaderGGUFMultiGPU can find it' >> /custom_start.sh && \
    echo 'if [ -f "/ComfyUI/models/clip_gguf/umt5-xxl-encoder-Q5_K_M.gguf" ]; then' >> /custom_start.sh && \
    echo '  cp /ComfyUI/models/clip_gguf/umt5-xxl-encoder-Q5_K_M.gguf /ComfyUI/models/text_encoders/' >> /custom_start.sh && \
    echo '  echo "✅ Copied GGUF model to text_encoders folder"' >> /custom_start.sh && \
    echo 'else' >> /custom_start.sh && \
    echo '  echo "⚠️  GGUF model not found, skipping copy"' >> /custom_start.sh && \
    echo 'fi' >> /custom_start.sh && \
    echo '' >> /custom_start.sh && \
    echo '# Run CivitAI downloader' >> /custom_start.sh && \
    echo '/workspace/scripts/run_civitai_downloader.sh &' >> /custom_start.sh && \
    echo '' >> /custom_start.sh && \
    echo '# Start ComfyUI' >> /custom_start.sh && \
    echo 'echo "🎭 Starting ComfyUI..."' >> /custom_start.sh && \
    echo 'cd /ComfyUI' >> /custom_start.sh && \
    echo 'python main.py --listen --port 8188' >> /custom_start.sh && \
    chmod +x /custom_start.sh

# Use our custom startup script
CMD ["/custom_start.sh"]
