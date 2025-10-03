#!/bin/bash

# ComfyUI Package Manager Script
# This script helps identify and install missing ComfyUI packages

echo "🔍 ComfyUI Package Manager"
echo "========================="
echo ""

# Function to check for common missing packages
check_missing_packages() {
    echo "Checking for common missing packages..."
    
    # Check if we can import common packages
    python3 -c "
import sys
missing = []

packages_to_check = [
    'cv2', 'opencv-python',
    'PIL', 'Pillow', 
    'numpy',
    'torch',
    'torchvision',
    'transformers',
    'diffusers',
    'accelerate',
    'xformers',
    'controlnet_aux',
    'ultralytics',
    'segment_anything',
    'insightface',
    'onnxruntime',
    'scipy',
    'scikit-image',
    'matplotlib',
    'tqdm',
    'psutil',
    'requests',
    'aiohttp',
    'websockets',
    'flask',
    'gradio'
]

for pkg in packages_to_check:
    try:
        if pkg == 'cv2':
            import cv2
        elif pkg == 'PIL':
            import PIL
        elif pkg == 'opencv-python':
            import cv2
        else:
            __import__(pkg)
        print(f'✅ {pkg}')
    except ImportError:
        print(f'❌ {pkg} - MISSING')
        missing.append(pkg)

if missing:
    print(f'\\nMissing packages: {missing}')
    return missing
else:
    print('\\n✅ All common packages are available!')
    return []
"
}

# Function to install missing packages
install_packages() {
    local packages="$1"
    echo "Installing missing packages: $packages"
    
    # Common package mappings
    declare -A package_map=(
        ["cv2"]="opencv-python"
        ["PIL"]="Pillow"
        ["opencv-python"]="opencv-python"
        ["controlnet_aux"]="controlnet-aux"
        ["segment_anything"]="segment-anything"
        ["insightface"]="insightface"
        ["onnxruntime"]="onnxruntime-gpu"
        ["scikit-image"]="scikit-image"
        ["aiohttp"]="aiohttp"
        ["websockets"]="websockets"
        ["flask"]="flask"
        ["gradio"]="gradio"
    )
    
    for pkg in $packages; do
        install_name=${package_map[$pkg]:-$pkg}
        echo "Installing $install_name..."
        pip install --no-cache-dir "$install_name" || echo "Failed to install $install_name"
    done
}

# Function to check ComfyUI custom node requirements
check_custom_node_requirements() {
    echo ""
    echo "Checking ComfyUI custom node requirements..."
    
    # Check requirements files in custom nodes
    find /ComfyUI/custom_nodes -name "requirements.txt" -exec echo "Found requirements: {}" \; -exec cat {} \;
    
    echo ""
    echo "Installing custom node requirements..."
    find /ComfyUI/custom_nodes -name "requirements.txt" -exec pip install --no-cache-dir -r {} \;
}

# Main execution
echo "1. Checking for missing packages..."
missing=$(check_missing_packages)

if [ ! -z "$missing" ]; then
    echo ""
    echo "2. Installing missing packages..."
    install_packages "$missing"
fi

echo ""
echo "3. Checking custom node requirements..."
check_custom_node_requirements

echo ""
echo "✅ Package check complete!"
echo ""
echo "To add packages to your Docker image permanently:"
echo "1. Add them to the Dockerfile RUN pip install section"
echo "2. Rebuild the image: ./scripts/build.sh v1.0.2"
echo "3. Push to Docker Hub: ./scripts/push.sh v1.0.2"

