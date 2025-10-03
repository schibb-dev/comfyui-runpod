#!/bin/bash
# Setup credentials for Hugging Face and CivitAI
# This script manages credentials for both local development and RunPod deployment

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
CREDENTIALS_DIR="$PROJECT_ROOT/credentials"

echo "🔐 Setting up credentials for Hugging Face and CivitAI"
echo "Project root: $PROJECT_ROOT"
echo "Credentials directory: $CREDENTIALS_DIR"

# Create credentials directory
mkdir -p "$CREDENTIALS_DIR"

# Function to prompt for credentials
prompt_credentials() {
    local service="$1"
    local token_file="$2"
    local env_var="$3"
    
    echo ""
    echo "=== $service Credentials ==="
    
    if [ -f "$token_file" ]; then
        echo "✅ $service token file already exists: $token_file"
        echo "Current token: $(head -c 20 "$token_file")..."
        read -p "Do you want to update it? (y/N): " update_token
        if [[ ! "$update_token" =~ ^[Yy]$ ]]; then
            return 0
        fi
    fi
    
    echo "Please enter your $service token:"
    echo "  - Hugging Face: Get from https://huggingface.co/settings/tokens"
    echo "  - CivitAI: Get from https://civitai.com/user/account?tab=apiTokens"
    echo ""
    read -p "Token: " token
    
    if [ -z "$token" ]; then
        echo "❌ No token provided, skipping $service"
        return 1
    fi
    
    # Save token to file
    echo "$token" > "$token_file"
    chmod 600 "$token_file"
    
    # Create environment file entry
    echo "$env_var=$token" >> "$PROJECT_ROOT/.env"
    
    echo "✅ $service token saved to $token_file"
    return 0
}

# Setup Hugging Face credentials
prompt_credentials "Hugging Face" "$CREDENTIALS_DIR/huggingface_token" "HUGGINGFACE_TOKEN"

# Setup CivitAI credentials
prompt_credentials "CivitAI" "$CREDENTIALS_DIR/civitai_token" "CIVITAI_TOKEN"

# Create .env file if it doesn't exist
if [ ! -f "$PROJECT_ROOT/.env" ]; then
    touch "$PROJECT_ROOT/.env"
fi

# Add other environment variables
cat >> "$PROJECT_ROOT/.env" << EOF

# ComfyUI Configuration
COMFYUI_PATH=/ComfyUI
WORKSPACE_PATH=/workspace

# WAN Model Download Configuration
download_480p_native_models=true
download_720p_native_models=true
download_wan_fun_and_sdxl_helper=true
download_wan22=true
download_vace=true
download_wan_animate=true
debug_models=false
download_vace_debug=false

# CivitAI Configuration
civitai_token=\${CIVITAI_TOKEN}

EOF

echo ""
echo "✅ Credentials setup complete!"
echo ""
echo "Files created:"
echo "  - $CREDENTIALS_DIR/huggingface_token"
echo "  - $CREDENTIALS_DIR/civitai_token"
echo "  - $PROJECT_ROOT/.env"
echo ""
echo "Next steps:"
echo "  1. Run: docker compose up -d"
echo "  2. Check logs: docker compose logs -f"
echo "  3. Access ComfyUI: http://localhost:8188"
echo ""
echo "For RunPod deployment:"
echo "  - Upload credentials/ directory to your RunPod volume"
echo "  - Set environment variables in RunPod template"

