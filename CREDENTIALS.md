# Credentials Management

This project supports automatic model downloads from both Hugging Face and CivitAI. The credentials are managed securely and work for both local development and RunPod deployment.

## Quick Setup

Run the credentials setup script:

```bash
./scripts/setup_credentials.sh
```

This will prompt you for:
- **Hugging Face Token**: Get from https://huggingface.co/settings/tokens
- **CivitAI Token**: Get from https://civitai.com/user/account?tab=apiTokens

## How It Works

### Local Development

1. **Credentials Storage**: Tokens are stored in `credentials/` directory
2. **Environment Variables**: Loaded via `.env` file and Docker Compose
3. **Automatic Downloads**: Both WAN models and CivitAI LoRAs are downloaded automatically

### RunPod Deployment

1. **Upload Credentials**: Upload the `credentials/` directory to your RunPod volume
2. **Environment Variables**: Set in RunPod template:
   - `HUGGINGFACE_TOKEN=your_token_here`
   - `CIVITAI_TOKEN=your_token_here`
3. **Automatic Downloads**: Same automatic download process works on RunPod

## File Structure

```
comfyui-runpod/
├── credentials/
│   ├── huggingface_token    # Hugging Face API token
│   └── civitai_token        # CivitAI API token
├── .env                     # Environment variables
└── scripts/
    ├── setup_credentials.sh # Credentials setup script
    └── run_civitai_downloader.sh # CivitAI downloader runner
```

## Automatic Downloads

### WAN Models (via Hugging Face)
- **480p Native Models**: `wan2.1_i2v_480p_14B_bf16.safetensors`, etc.
- **720p Native Models**: `wan2.1_i2v_720p_14B_bf16.safetensors`, etc.
- **WAN 2.2 Models**: Various WAN 2.2 variants
- **VACE Models**: VACE enhancement modules
- **WAN Animate**: Animation-specific models

### CivitAI LoRAs
- **WAN Thiccum v3**: Enhanced body features
- **WAN Dr34mj0b**: Dream job LoRA
- **BounceV**: Bouncing animations
- **WAN Cumshot**: Cumshot effects
- **NSFW Enhancement**: NSFW improvements

## Environment Variables

The following environment variables control the download behavior:

```bash
# WAN Model Downloads
download_480p_native_models=true
download_720p_native_models=true
download_wan_fun_and_sdxl_helper=true
download_wan22=true
download_vace=true
download_wan_animate=true

# Debug Models (optional)
debug_models=false
download_vace_debug=false

# Credentials
HUGGINGFACE_TOKEN=your_token_here
CIVITAI_TOKEN=your_token_here
civitai_token=your_token_here  # Legacy format for WAN script
```

## Troubleshooting

### Missing Models
If you get "model not found" errors:

1. **Check Credentials**: Ensure tokens are valid and not expired
2. **Check Environment**: Verify environment variables are set
3. **Check Logs**: Look for download errors in container logs
4. **Manual Download**: Use the CivitAI downloader script manually

### Download Failures
If downloads fail:

1. **Check Space**: Ensure sufficient disk space
2. **Check Network**: Verify internet connectivity
3. **Check Tokens**: Ensure API tokens have proper permissions
4. **Check Logs**: Look for specific error messages

### RunPod Issues
If RunPod deployment fails:

1. **Upload Credentials**: Ensure `credentials/` directory is uploaded
2. **Set Environment**: Verify environment variables in RunPod template
3. **Check Volume**: Ensure volume is properly mounted
4. **Check Logs**: Use RunPod logs to debug issues

## Manual Downloads

If automatic downloads fail, you can run the downloaders manually:

```bash
# Run CivitAI downloader
docker exec comfyui-dev python3 /workspace/scripts/civitai_lora_downloader.py

# Check available models
docker exec comfyui-dev python3 /workspace/scripts/civitai_lora_downloader.py --list-loras

# Show results
docker exec comfyui-dev python3 /workspace/scripts/civitai_lora_downloader.py --show-results
```

## Security Notes

- **Token Files**: Credential files have restricted permissions (600)
- **Environment Variables**: Tokens are passed securely via environment variables
- **Volume Mounts**: Credentials directory is mounted read-only
- **Cleanup**: Tokens are not stored in Docker images

## Support

For issues with:
- **Hugging Face**: Check https://huggingface.co/settings/tokens
- **CivitAI**: Check https://civitai.com/user/account?tab=apiTokens
- **WAN Models**: Check the WAN repository for updates
- **CivitAI LoRAs**: Check the CivitAI downloader script documentation

