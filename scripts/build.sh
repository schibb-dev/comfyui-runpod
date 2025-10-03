#!/bin/bash
set -e

IMAGE_NAME="schibbdev/comfyui-runpod"
VERSION="${1:-latest}"

echo "🔨 Building ${IMAGE_NAME}:${VERSION}..."

docker build -t ${IMAGE_NAME}:${VERSION} .

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

