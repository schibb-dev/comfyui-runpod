#!/bin/bash
set -e

IMAGE_NAME="schibbdev/comfyui-runpod"
VERSION="${1:-latest}"

# Honor .env build-time knobs (same as docker compose build via docker-compose.yml args).
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source ./.env
  set +a
fi

echo "🔨 Building ${IMAGE_NAME}:${VERSION}..."
if [[ "${INSTALL_KRITA_BACKEND_NODES:-false}" == "true" ]]; then
  echo "   INSTALL_KRITA_BACKEND_NODES=true (baking Acly/Krita backend nodes)"
fi

docker build \
  --build-arg "COMFYUI_REF=${COMFYUI_REF:-38d049382533c6662d815b08ca3395e96cca9f57}" \
  --build-arg "INSTALL_KRITA_BACKEND_NODES=${INSTALL_KRITA_BACKEND_NODES:-false}" \
  -t "${IMAGE_NAME}:${VERSION}" .

if [ "$VERSION" != "latest" ]; then
    echo "🏷️  Tagging as latest..."
    docker tag ${IMAGE_NAME}:${VERSION} ${IMAGE_NAME}:latest
fi

echo "✅ Build complete!"
echo ""
echo "Image: ${IMAGE_NAME}:${VERSION}"
echo ""
echo "Next steps:"
echo "  Test locally:  docker-compose up -d"
echo "  Push to hub:   ./scripts/push.sh ${VERSION}"

