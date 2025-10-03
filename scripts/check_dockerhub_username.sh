#!/bin/bash

# Docker Hub Username Checker and Configuration Script

echo "🐳 Docker Hub Username Checker"
echo "=============================="
echo ""

# Function to check if a username is available
check_username() {
    local username=$1
    echo "Checking if '$username' is available on Docker Hub..."
    
    # Try to access the user's profile page
    response=$(curl -s -o /dev/null -w "%{http_code}" "https://hub.docker.com/u/$username")
    
    if [ "$response" = "404" ]; then
        echo "✅ '$username' appears to be available!"
        return 0
    elif [ "$response" = "200" ]; then
        echo "❌ '$username' is already taken"
        return 1
    else
        echo "⚠️  Could not check '$username' (HTTP $response)"
        return 2
    fi
}

# Check current configured username
CURRENT_USER="yuji"
echo "Current configured username: $CURRENT_USER"
echo ""

# Check if yuji is available
check_username "$CURRENT_USER"
yuji_available=$?

echo ""
echo "Options:"
echo "1. Keep 'yuji' (if available)"
echo "2. Choose a different username"
echo "3. Check multiple usernames"
echo ""

read -p "What would you like to do? (1/2/3): " choice

case $choice in
    1)
        if [ $yuji_available -eq 0 ]; then
            echo "✅ Using 'yuji' as Docker Hub username"
            USERNAME="yuji"
        else
            echo "❌ 'yuji' is not available. Please choose a different username."
            read -p "Enter your preferred Docker Hub username: " USERNAME
        fi
        ;;
    2)
        read -p "Enter your preferred Docker Hub username: " USERNAME
        ;;
    3)
        echo "Let's check multiple usernames..."
        for username in "yuji" "yuji-art" "yuji-ai" "yuji-comfyui" "yuji-ml"; do
            echo ""
            check_username "$username"
            if [ $? -eq 0 ]; then
                read -p "Use '$username'? (y/n): " use_this
                if [[ $use_this == "y" || $use_this == "Y" ]]; then
                    USERNAME="$username"
                    break
                fi
            fi
        done
        ;;
    *)
        echo "Invalid choice. Exiting."
        exit 1
        ;;
esac

if [[ -n "$USERNAME" ]]; then
    echo ""
    echo "🔄 Configuring Docker Hub username: $USERNAME"
    
    # Update all files
    sed -i "s/IMAGE_NAME=\"yuji\/comfyui-runpod\"/IMAGE_NAME=\"$USERNAME\/comfyui-runpod\"/g" scripts/build.sh
    sed -i "s/IMAGE_NAME=\"yuji\/comfyui-runpod\"/IMAGE_NAME=\"$USERNAME\/comfyui-runpod\"/g" scripts/push.sh
    sed -i "s/image: yuji\/comfyui-runpod:latest/image: $USERNAME\/comfyui-runpod:latest/g" docker-compose.yml
    sed -i "s/LABEL maintainer=\"yuji@example.com\"/LABEL maintainer=\"$USERNAME@example.com\"/g" Dockerfile
    
    echo "✅ Updated all files to use username: $USERNAME"
    echo ""
    echo "📋 Next steps:"
    echo "1. Create Docker Hub account with username: $USERNAME"
    echo "2. Login: docker login"
    echo "3. Build: ./scripts/build.sh v1.0.0"
    echo "4. Push: ./scripts/push.sh v1.0.0"
    echo ""
    echo "Your image will be available at:"
    echo "  https://hub.docker.com/r/$USERNAME/comfyui-runpod"
    echo "  docker pull $USERNAME/comfyui-runpod:latest"
else
    echo "❌ No username configured. Please run the script again."
    exit 1
fi

