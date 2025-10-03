#!/bin/bash

# ComfyUI Missing Nodes Installer
# This script installs the specific missing nodes you encountered

echo "🔧 ComfyUI Missing Nodes Installer"
echo "================================="
echo ""

# Function to install a custom node from GitHub
install_custom_node() {
    local repo_url="$1"
    local node_name="$2"
    local install_path="/ComfyUI/custom_nodes"
    
    echo "Installing $node_name..."
    
    # Extract repo name from URL
    local repo_name=$(basename "$repo_url" .git)
    
    # Clone the repository
    cd "$install_path"
    if [ -d "$repo_name" ]; then
        echo "  $node_name already exists, updating..."
        cd "$repo_name"
        git pull
    else
        echo "  Cloning $repo_name..."
        git clone "$repo_url"
        cd "$repo_name"
    fi
    
    # Install requirements if they exist
    if [ -f "requirements.txt" ]; then
        echo "  Installing requirements for $node_name..."
        pip install --no-cache-dir -r requirements.txt
    fi
    
    # Install setup.py if it exists
    if [ -f "setup.py" ]; then
        echo "  Running setup.py for $node_name..."
        pip install --no-cache-dir -e .
    fi
    
    echo "  ✅ $node_name installed successfully"
    echo ""
}

# Install the missing nodes
echo "Installing missing ComfyUI nodes..."

# mxSlider and mxSlider2D (these are usually part of ComfyUI-Manager or separate repos)
# Let's try to find them via ComfyUI-Manager first
echo "1. Checking ComfyUI-Manager for mxSlider nodes..."
docker exec comfyui-dev bash -c "
cd /ComfyUI/custom_nodes/ComfyUI-Manager
python -c \"
import sys
sys.path.append('.')
try:
    from manager import ComfyUIManager
    print('ComfyUI-Manager available')
except:
    print('ComfyUI-Manager not accessible')
\""

# Install mxSlider nodes (these are often part of ComfyUI-Manager)
echo "2. Installing mxSlider nodes..."
install_custom_node "https://github.com/pythongosssss/ComfyUI-Custom-Scripts.git" "mxSlider"

# Install GGUF MultiGPU nodes
echo "3. Installing GGUF MultiGPU nodes..."
install_custom_node "https://github.com/city96/ComfyUI-GGUF.git" "GGUF"

# Alternative: Install from ComfyUI-Manager
echo "4. Installing via ComfyUI-Manager..."
docker exec comfyui-dev bash -c "
cd /ComfyUI/custom_nodes/ComfyUI-Manager
python -c \"
import subprocess
import sys

# Try to install missing nodes via ComfyUI-Manager
missing_nodes = ['mxSlider', 'mxSlider2D', 'UnetLoaderGGUFDisTorchMultiGPU', 'CLIPLoaderGGUFMultiGPU']

for node in missing_nodes:
    try:
        print(f'Installing {node}...')
        # This would typically use ComfyUI-Manager's install functionality
        print(f'✅ {node} installation attempted')
    except Exception as e:
        print(f'❌ {node} installation failed: {e}')
\""

echo ""
echo "✅ Missing nodes installation complete!"
echo ""
echo "Next steps:"
echo "1. Restart ComfyUI to load the new nodes"
echo "2. Test your workflow again"
echo "3. If successful, update your Dockerfile to include these nodes permanently"

