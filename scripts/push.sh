#!/bin/bash
set -e

IMAGE_NAME="schibbdev/comfyui-runpod"
VERSION="${1:-latest}"

echo "🚀 Pushing ${IMAGE_NAME}:${VERSION} to Docker Hub..."

# Check if logged in
if ! docker info | grep -q "Username"; then
    echo "⚠️  Not logged in to Docker Hub"
    echo "Run: docker login"
    exit 1
fi

docker push ${IMAGE_NAME}:${VERSION}

if [ "$VERSION" != "latest" ]; then
    echo "🚀 Also pushing ${IMAGE_NAME}:latest..."
    docker push ${IMAGE_NAME}:latest
fi

echo "✅ Push complete!"
echo ""
echo "Image available at:"
echo "  docker pull ${IMAGE_NAME}:${VERSION}"
echo ""
echo "RunPod template image: ${IMAGE_NAME}:${VERSION}"

