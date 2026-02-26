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

# Optional: upgrade/pin ComfyUI at build time.
# Set COMFYUI_REF to a commit hash, tag, branch name, or remote ref (e.g. origin/master).
ARG COMFYUI_REF=origin/master
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
    onnxruntime

# Pin NumPy to <2 so OpenCV (cv2) and other binary extensions built for NumPy 1.x work.
# Otherwise: "numpy.core.multiarray failed to import" / "_ARRAY_API not found" when custom nodes import cv2.
RUN pip install --no-cache-dir "numpy<2"

# ComfyUI-Crystools and other nodes may need these at runtime; install once in image.
RUN pip install --no-cache-dir deepdiff

# Optional: pre-install and build ComfyUI Mobile Frontend in the image.
ARG INSTALL_COMFYUI_MOBILE_FRONTEND=true
ARG COMFYUI_MOBILE_FRONTEND_REF=main
RUN if [ "${INSTALL_COMFYUI_MOBILE_FRONTEND}" = "true" ]; then \
      mkdir -p /ComfyUI/custom_nodes && \
      if [ ! -d "/ComfyUI/custom_nodes/comfyui-mobile-frontend" ]; then \
        git clone --depth 1 --branch "${COMFYUI_MOBILE_FRONTEND_REF}" https://github.com/cosmicbuffalo/comfyui-mobile-frontend.git /ComfyUI/custom_nodes/comfyui-mobile-frontend; \
      fi && \
      cd /ComfyUI/custom_nodes/comfyui-mobile-frontend && \
      if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi && \
      npm run build; \
    fi

# Copy our scripts and entrypoint
COPY scripts/ /workspace/scripts/
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && \
    sed -i 's/\r$//' /usr/local/bin/entrypoint.sh && \
    if [ -d /workspace/scripts ]; then find /workspace/scripts -maxdepth 1 -type f -name "*.sh" -exec sed -i 's/\r$//' {} \; ; fi

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
