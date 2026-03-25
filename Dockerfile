# Start from the WAN template
FROM hearmeman/comfyui-wan-template:v11

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
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Pin ComfyUI at build time (compose passes COMFYUI_REF; default matches docker-compose).
# 38d0493… = last commit before #11632 removed module-level precompute_freqs_cis (TeaCache).
# Override: docker compose build --build-arg COMFYUI_REF=origin/master
ARG COMFYUI_REF=38d049382533c6662d815b08ca3395e96cca9f57
RUN cd /ComfyUI && \
    git fetch --all --tags && \
    git checkout "${COMFYUI_REF}" && \
    pip install --no-cache-dir -r /ComfyUI/requirements.txt

# Install additional Python packages for our customizations
RUN pip install --no-cache-dir \
    requests \
    pyyaml \
    huggingface_hub \
    safetensors \
    sageattention \
    insightface \
    onnxruntime \
    aiohttp \
    tqdm

# Pin NumPy to <2 so OpenCV (cv2) and other binary extensions built for NumPy 1.x work.
# Otherwise: "numpy.core.multiarray failed to import" / "_ARRAY_API not found" when custom nodes import cv2.
RUN pip install --no-cache-dir "numpy<2"

# OpenCV (headless) so ComfyUI-VideoHelperSuite loads and registers VHS_VideoCombine; without it the node shows "not found".
RUN pip install --no-cache-dir opencv-python-headless

# ComfyUI-Crystools and other nodes may need these at runtime; install once in image.
RUN pip install --no-cache-dir deepdiff

# Bake all custom nodes from custom_nodes.yaml into the image (avoids clone at container startup).
# When you do not mount ./custom_nodes over /ComfyUI/custom_nodes, these are used.
# When you do mount, bootstrap at startup still runs and skips existing (so no redownload).
COPY custom_nodes.yaml /workspace/custom_nodes.yaml
COPY scripts/ /workspace/scripts/
RUN python3 /workspace/scripts/bootstrap_nodes.py

# Copy our scripts and entrypoint
COPY scripts/ /workspace/scripts/
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && \
    sed -i 's/\r$//' /usr/local/bin/entrypoint.sh && \
    if [ -d /workspace/scripts ]; then find /workspace/scripts -maxdepth 1 -type f -name "*.sh" -exec sed -i 's/\r$//' {} \; ; fi

# Krita AI Diffusion: clone repo so we can run its download_models.py (wired in entrypoint).
RUN git clone --depth 1 https://github.com/Acly/krita-ai-diffusion.git /opt/krita-ai-diffusion

# Create directories for models and our workspace
RUN mkdir -p /workspace/{workflows,models,output,input,scripts} \
    && mkdir -p /workspace/models/{checkpoints,loras,vae,upscale_models} \
    && mkdir -p /ComfyUI/models/{checkpoints,loras,vae,upscale_models}

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV COMFYUI_PATH=/ComfyUI
ENV HF_HOME=/workspace/.cache/huggingface
ENV WORKSPACE_PATH=/workspace

# Expose ports
EXPOSE 8188 22 8888 8790

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8188/ || exit 1

# Entrypoint handles tokens + bootstrap then delegates to base startup
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
