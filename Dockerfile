# syntax=docker/dockerfile:1.4
# BuildKit cache mounts (--mount=type=cache) persist apt/pip downloads across rebuilds.
# Rebuild tips: stop running containers first; entrypoint-only changes can bind-mount
# ./entrypoint.sh instead of a full rebuild.
#
# Start from the WAN template
FROM hearmeman/comfyui-wan-template:v11

# Metadata
LABEL maintainer="schibbdev@example.com"
LABEL description="Custom ComfyUI with WAN + Florence2 + Civitai LoRA Management"
LABEL version="1.0"

# Set working directory
WORKDIR /workspace

# System packages (no apt nodejs/npm — that pulls ~500 deb packages and is brutal on WSL disk).
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    vim \
    git \
    wget \
    curl \
    aria2 \
    ca-certificates \
    xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Official Node tarball for optional custom-node npm builds (e.g. comfyui-mobile-frontend).
# One ~25MB download vs hours of apt unpack/configure on Docker Desktop + WSL2.
ARG NODE_VERSION=20.18.0
RUN --mount=type=cache,target=/root/.cache/node \
    curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
    | tar -xJ -C /usr/local --strip-components=1 \
    && node --version && npm --version

# Pin ComfyUI at build time (compose passes COMFYUI_REF; default matches docker-compose).
# 38d0493… = last commit before #11632 removed module-level precompute_freqs_cis (TeaCache).
# Override: docker compose build --build-arg COMFYUI_REF=origin/master
ARG COMFYUI_REF=38d049382533c6662d815b08ca3395e96cca9f57
RUN --mount=type=cache,target=/root/.cache/pip \
    cd /ComfyUI && \
    git fetch --all --tags && \
    git checkout "${COMFYUI_REF}" && \
    pip install -r /ComfyUI/requirements.txt

# Install additional Python packages for our customizations
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install \
    requests \
    pyyaml \
    huggingface_hub \
    safetensors \
    sageattention \
    insightface \
    onnxruntime \
    aiohttp \
    tqdm \
    websockets

# Pin NumPy to <2 so OpenCV (cv2) and other binary extensions built for NumPy 1.x work.
# Otherwise: "numpy.core.multiarray failed to import" / "_ARRAY_API not found" when custom nodes import cv2.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install "numpy<2"

# OpenCV (headless) so ComfyUI-VideoHelperSuite loads and registers VHS_VideoCombine; without it the node shows "not found".
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install opencv-python-headless deepdiff

# Bake all custom nodes from custom_nodes.yaml into the image (avoids clone/fetch at container startup).
# When you do not mount ./custom_nodes over /ComfyUI/custom_nodes, these baked nodes are used.
# Runtime bootstrap is opt-in via COMFYUI_BOOTSTRAP_NODES_ON_START=true (or auto when Krita nodes missing).
# Acly/Krita bridge nodes (comfyui-tooling-nodes, comfyui-inpaint-nodes) install only when true.
# Set INSTALL_KRITA_BACKEND_NODES=true in .env before `docker compose build` to bake them into the image.
ARG INSTALL_KRITA_BACKEND_NODES=false
ENV INSTALL_KRITA_BACKEND_NODES=${INSTALL_KRITA_BACKEND_NODES}
COPY custom_nodes.yaml /workspace/custom_nodes.yaml
COPY scripts/bootstrap_nodes.py /workspace/scripts/bootstrap_nodes.py
RUN --mount=type=cache,target=/root/.cache/pip \
    python3 /workspace/scripts/bootstrap_nodes.py

# Copy runtime scripts after custom-node baking so unrelated script changes do not invalidate
# the expensive custom_nodes image layer.
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
    && mkdir -p /ComfyUI/models/{checkpoints,loras,vae,upscale_models} \
    && mkdir -p /ComfyUI/web/extensions/pysssss \
    && chown -R 1000:1000 /ComfyUI/custom_nodes /ComfyUI/web

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
