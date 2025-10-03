#!/usr/bin/env python3
"""
GGUF Models Downloader
Downloads required GGUF models from city96 Hugging Face repositories
"""

import os
import requests
import argparse
from pathlib import Path
from tqdm import tqdm

def download_file(url, destination, token=None):
    """Download a file with progress bar"""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    response = requests.get(url, headers=headers, stream=True)
    response.raise_for_status()
    
    total_size = int(response.headers.get('content-length', 0))
    
    with open(destination, 'wb') as f:
        with tqdm(total=total_size, unit='B', unit_scale=True, desc=destination.name) as pbar:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    pbar.update(len(chunk))

def main():
    parser = argparse.ArgumentParser(description='Download GGUF models from city96 repositories')
    parser.add_argument('--token', help='Hugging Face token for private repositories')
    parser.add_argument('--output-dir', default='/ComfyUI/models', help='Output directory for models')
    args = parser.parse_args()
    
    # Get token from environment if not provided
    token = args.token or os.getenv('HUGGINGFACE_TOKEN')
    
    # Create output directories
    llm_dir = Path(args.output_dir) / "LLM"
    unet_dir = Path(args.output_dir) / "unet"
    llm_dir.mkdir(parents=True, exist_ok=True)
    unet_dir.mkdir(parents=True, exist_ok=True)
    
    # Define models to download
    models = [
        {
            "name": "umt5-xxl-encoder-Q5_K_M.gguf",
            "url": "https://huggingface.co/city96/umt5-xxl-encoder-gguf/resolve/main/umt5-xxl-encoder-Q5_K_M.gguf",
            "destination": llm_dir / "umt5-xxl-encoder-Q5_K_M.gguf",
            "description": "UMT5 XXL Text Encoder GGUF (CLIP)"
        },
        {
            "name": "wan2.1-i2v-14b-480p-Q5_K_M.gguf", 
            "url": "https://huggingface.co/city96/Wan2.1-I2V-14B-480P-gguf/resolve/main/wan2.1-i2v-14b-480p-Q5_K_M.gguf",
            "destination": unet_dir / "wan2.1-i2v-14b-480p-Q5_K_M.gguf",
            "description": "WAN2.1 Image-to-Video 480p GGUF (UNET)"
        }
    ]
    
    print("🚀 Starting GGUF models download...")
    print(f"📁 Output directory: {args.output_dir}")
    
    for model in models:
        if model["destination"].exists():
            print(f"✅ {model['name']} already exists, skipping...")
            continue
            
        print(f"\n📦 Downloading {model['description']}...")
        try:
            download_file(model["url"], model["destination"], token)
            print(f"✅ Successfully downloaded {model['name']}")
        except Exception as e:
            print(f"❌ Failed to download {model['name']}: {e}")
            return 1
    
    print("\n🎉 All GGUF models downloaded successfully!")
    print("\n📋 Downloaded models:")
    for model in models:
        if model["destination"].exists():
            size_mb = model["destination"].stat().st_size / (1024 * 1024)
            print(f"  • {model['name']} ({size_mb:.1f} MB)")
    
    return 0

if __name__ == "__main__":
    exit(main())
