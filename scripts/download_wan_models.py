#!/usr/bin/env python3
"""
WAN Model Downloader
Downloads all required WAN 2.1 and WAN 2.2 models from Hugging Face repositories
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
    parser = argparse.ArgumentParser(description='Download WAN models from Hugging Face repositories')
    parser.add_argument('--token', help='Hugging Face token for private repositories')
    parser.add_argument('--output-dir', default='/ComfyUI/models', help='Output directory for models')
    parser.add_argument('--skip-existing', action='store_true', help='Skip files that already exist')
    args = parser.parse_args()
    
    # Get token from environment if not provided
    token = args.token or os.getenv('HUGGINGFACE_TOKEN')
    
    # Create output directories
    base_dir = Path(args.output_dir)
    diffusion_dir = base_dir / "diffusion_models"
    text_encoder_dir = base_dir / "text_encoders"
    clip_vision_dir = base_dir / "clip_vision"
    vae_dir = base_dir / "vae"
    clip_gguf_dir = base_dir / "clip_gguf"
    
    for dir_path in [diffusion_dir, text_encoder_dir, clip_vision_dir, vae_dir, clip_gguf_dir]:
        dir_path.mkdir(parents=True, exist_ok=True)
    
    # Register clip_gguf folder with ComfyUI if not already registered
    try:
        import sys
        sys.path.append('/ComfyUI')
        import folder_paths
        if 'clip_gguf' not in folder_paths.folder_names_and_paths:
            folder_paths.folder_names_and_paths['clip_gguf'] = ([str(clip_gguf_dir)], {'.gguf'})
            print("✅ Registered clip_gguf folder with ComfyUI")
    except Exception as e:
        print(f"⚠️  Could not register clip_gguf folder: {e}")
    
    # Define models to download
    models = [
        # WAN 2.1 Models
        {
            "name": "wan_2.1_vae.safetensors",
            "url": "https://huggingface.co/SimonJoz/wan-2.1/resolve/main/vae/wan_2.1_vae.safetensors",
            "destination": vae_dir / "wan_2.1_vae.safetensors",
            "description": "WAN 2.1 VAE Model"
        },
        {
            "name": "umt5_xxl_fp16.safetensors",
            "url": "https://huggingface.co/SimonJoz/wan-2.1/resolve/main/text_encoders/umt5_xxl_fp16.safetensors",
            "destination": text_encoder_dir / "umt5_xxl_fp16.safetensors",
            "description": "WAN 2.1 Text Encoder (UMT5 XXL)"
        },
        {
            "name": "clip_vision_h.safetensors",
            "url": "https://huggingface.co/SimonJoz/wan-2.1/resolve/main/clip_vision/clip_vision_h.safetensors",
            "destination": clip_vision_dir / "clip_vision_h.safetensors",
            "description": "WAN 2.1 CLIP Vision Model"
        },
        # Additional WAN 2.1 models (if available)
        {
            "name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
            "url": "https://huggingface.co/SimonJoz/wan-2.1/resolve/main/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
            "destination": text_encoder_dir / "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
            "description": "WAN 2.1 Text Encoder (FP8 Optimized)"
        },
        # GGUF Models (create symlinks to existing files)
        {
            "name": "umt5-xxl-encoder-Q5_K_M.gguf",
            "url": "symlink",  # Special marker for symlink creation
            "destination": clip_gguf_dir / "umt5-xxl-encoder-Q5_K_M.gguf",
            "source": base_dir / "LLM" / "umt5-xxl-encoder-Q5_K_M.gguf",
            "description": "WAN 2.1 Text Encoder (GGUF)",
            "optional": True
        },
        # WAN 2.2 Models (if available)
        {
            "name": "wan2.2_vae.safetensors",
            "url": "https://huggingface.co/SimonJoz/wan-2.2/resolve/main/vae/wan2.2_vae.safetensors",
            "destination": vae_dir / "wan2.2_vae.safetensors",
            "description": "WAN 2.2 VAE Model",
            "optional": True
        }
    ]
    
    print("🚀 Starting WAN models download...")
    print(f"📁 Output directory: {args.output_dir}")
    if token:
        print("🔑 Using Hugging Face token for authentication")
    else:
        print("⚠️  No token provided - some models may not be accessible")
    
    success_count = 0
    total_count = len(models)
    
    for model in models:
        if args.skip_existing and model["destination"].exists():
            print(f"✅ {model['name']} already exists, skipping...")
            success_count += 1
            continue
            
        print(f"\n📦 Downloading {model['description']}...")
        try:
            if model["url"] == "symlink":
                # Create symlink instead of downloading
                source_path = model["source"]
                if source_path.exists():
                    if model["destination"].exists() or model["destination"].is_symlink():
                        model["destination"].unlink()
                    model["destination"].symlink_to(source_path)
                    print(f"✅ Successfully created symlink for {model['name']}")
                    success_count += 1
                else:
                    print(f"⚠️  Source file not found: {source_path}")
                    if not model.get("optional", False):
                        print("This is a required model - symlink creation failed!")
            else:
                download_file(model["url"], model["destination"], token)
                print(f"✅ Successfully downloaded {model['name']}")
                success_count += 1
        except Exception as e:
            if model.get("optional", False):
                print(f"⚠️  Optional model {model['name']} failed to download: {e}")
            else:
                print(f"❌ Failed to download {model['name']}: {e}")
                if not model.get("optional", False):
                    print("This is a required model - download failed!")
    
    print(f"\n🎉 Download Results: {success_count}/{total_count} models downloaded")
    
    # Display downloaded models with sizes
    print("\n📋 Downloaded models:")
    for model in models:
        if model["destination"].exists():
            size_mb = model["destination"].stat().st_size / (1024 * 1024)
            print(f"  • {model['name']} ({size_mb:.1f} MB)")
    
    # Verify ComfyUI can see the models
    print("\n🔍 Verifying ComfyUI model detection...")
    try:
        import sys
        sys.path.append('/ComfyUI')
        from folder_paths import get_filename_list
        
        print(f"  • VAE models: {len(get_filename_list('vae'))}")
        print(f"  • Text encoders: {len(get_filename_list('text_encoders'))}")
        print(f"  • CLIP vision: {len(get_filename_list('clip_vision'))}")
        print(f"  • Diffusion models: {len(get_filename_list('diffusion_models'))}")
        
    except Exception as e:
        print(f"⚠️  Could not verify ComfyUI detection: {e}")
    
    return success_count == total_count

if __name__ == "__main__":
    exit(0 if main() else 1)
