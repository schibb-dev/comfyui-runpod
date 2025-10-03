#!/bin/bash

# Docker Hub Setup Helper Script
# This script helps you configure your Docker Hub username

echo "🐳 Docker Hub Setup Helper"
echo "========================="
echo ""

# Check if already logged in
if docker info | grep -q "Username"; then
    CURRENT_USER=$(docker info | grep "Username" | awk '{print $2}')
    echo "✅ Already logged in as: $CURRENT_USER"
    echo ""
    echo "Current image name: $CURRENT_USER/comfyui-runpod"
    echo ""
    read -p "Do you want to use this username? (y/n): " use_current
    if [[ $use_current == "y" || $use_current == "Y" ]]; then
        USERNAME=$CURRENT_USER
    else
        read -p "Enter your Docker Hub username: " USERNAME
    fi
else
    echo "❌ Not logged in to Docker Hub"
    echo ""
    echo "Please run: docker login"
    echo "Then enter your Docker Hub username and password"
    echo ""
    read -p "Enter your Docker Hub username: " USERNAME
fi

if [[ -n "$USERNAME" ]]; then
    echo ""
    echo "🔄 Updating files to use username: $USERNAME"
    
    # Update build script
    sed -i "s/IMAGE_NAME=\"yuji\/comfyui-runpod\"/IMAGE_NAME=\"$USERNAME\/comfyui-runpod\"/g" scripts/build.sh
    
    # Update push script
    sed -i "s/IMAGE_NAME=\"yuji\/comfyui-runpod\"/IMAGE_NAME=\"$USERNAME\/comfyui-runpod\"/g" scripts/push.sh
    
    # Update docker-compose.yml
    sed -i "s/image: yuji\/comfyui-runpod:latest/image: $USERNAME\/comfyui-runpod:latest/g" docker-compose.yml
    
    # Update Dockerfile maintainer
    sed -i "s/LABEL maintainer=\"yuji@example.com\"/LABEL maintainer=\"$USERNAME@example.com\"/g" Dockerfile
    
    echo "✅ Updated all files to use username: $USERNAME"
    echo ""
    echo "Next steps:"
    echo "1. Make sure you're logged in: docker login"
    echo "2. Build the image: ./scripts/build.sh v1.0.0"
    echo "3. Push to Docker Hub: ./scripts/push.sh v1.0.0"
    echo ""
    echo "Your image will be available at:"
    echo "  docker pull $USERNAME/comfyui-runpod:v1.0.0"
    echo "  docker pull $USERNAME/comfyui-runpod:latest"
else
    echo "❌ No username provided. Exiting."
    exit 1
fi

