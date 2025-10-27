# ComfyUI RunPod Setup

A complete Docker-based ComfyUI setup optimized for RunPod deployment with automatic model downloads, custom node installation, and workflow support. This project extends the base ComfyUI template with WAN video generation capabilities, GGUF model support, and automated LoRA management.

## Features

- 🐳 **Docker-based**: Complete containerization for consistent deployment
- 🚀 **RunPod Ready**: Optimized for RunPod cloud GPU deployment
- 📦 **Auto Bootstrap**: Automatic custom node installation from configuration
- 🔄 **Model Downloads**: Automatic download of WAN, GGUF, and CivitAI models
- 🎯 **Workflow Support**: Pre-configured for WAN video generation workflows
- 🔧 **Multi-GPU**: Support for distributed GPU processing
- 🎬 **WAN Video**: Full WAN 2.1/2.2 support with text encoders, VAE, and diffusion models
- 🤖 **GGUF Support**: Integrated GGUF model loading for efficient inference
- 💾 **Persistent Storage**: Workspace-based model storage with symlinks

## Quick Start

### Local Development

1. **Clone and setup:**
   ```bash
   git clone <your-repo-url>
   cd comfyui-runpod
   ```

2. **Configure credentials:**
   ```bash
   ./scripts/setup_credentials.sh
   ```

3. **Build and run:**
   ```bash
   docker compose up -d
   ```

4. **Access ComfyUI:**
   - Open http://localhost:8188 in your browser

### RunPod Deployment

1. **Build and push image:**
   ```bash
   ./scripts/build.sh
   ./scripts/push.sh
   ```

2. **Deploy on RunPod:**
   - Use the pushed Docker image: `schibbdev/comfyui-runpod:latest`
   - Configure environment variables for tokens
   - Mount persistent volumes for models

## Project Structure

```
comfyui-runpod/
├── Dockerfile                 # Main container definition
├── docker-compose.yml         # Local development setup
├── custom_nodes.yaml         # Custom node configuration
├── scripts/                  # Utility scripts
│   ├── setup_credentials.sh  # Token setup
│   ├── bootstrap_nodes.py    # Node installation
│   ├── download_gguf_models.py # GGUF model downloader
│   ├── build.sh             # Build Docker image
│   └── push.sh              # Push to registry
├── workspace/               # Persistent data
│   ├── ComfyUI/            # ComfyUI installation
│   ├── workflows/          # Workflow files
│   └── setup_tokens.sh     # Token loader
└── credentials/            # Token storage (gitignored)
```

## Configuration

### Custom Nodes

Edit `custom_nodes.yaml` to add or remove custom nodes:

```yaml
nodes:
  essential:
    - name: ComfyUI-GGUF
      repo: https://github.com/city96/ComfyUI-GGUF.git
      branch: main
      required: true
```

### Environment Variables

- `HUGGINGFACE_TOKEN`: For downloading GGUF models
- `CIVITAI_TOKEN`: For downloading CivitAI models
- `download_480p_native_models`: Enable 480p model downloads
- `download_720p_native_models`: Enable 720p model downloads

## Models

The setup automatically downloads:

- **WAN Models**: 480p and 720p UNET models for video generation
- **GGUF Models**: UMT5 encoder and WAN UNET models
- **CivitAI Models**: LoRA and other community models
- **VAE Models**: WAN VAE for proper video encoding

## Workflows

Pre-configured workflows are available in `workspace/workflows/`:

- `FaceBlastA.json`: Face-focused video generation
- Additional workflows can be added as needed

## Development

### Adding Custom Nodes

1. Add to `custom_nodes.yaml`
2. Rebuild container: `docker compose build --no-cache`
3. Restart: `docker compose restart`

### Updating Models

Models are downloaded automatically on container start. To force re-download:

```bash
docker compose exec comfyui rm -rf /ComfyUI/models/unet/*.gguf
docker compose restart
```

## Troubleshooting

### Missing Nodes
- Check `custom_nodes.yaml` configuration
- Verify bootstrap script ran: `docker compose logs comfyui`
- Rebuild container if needed

### Missing Models
- Verify tokens are set correctly
- Check download logs: `docker compose logs comfyui`
- Ensure sufficient disk space

### GPU Issues
- Verify NVIDIA Docker runtime is installed
- Check GPU availability: `nvidia-smi`
- Review container logs for CUDA errors

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test locally with Docker
5. Submit a pull request

## Recent Updates

### WAN Model Integration ✅
- Complete WAN 2.1/2.2 model support with automatic downloads
- Text encoders (FP16 and FP8 optimized)
- VAE and diffusion models
- CLIP vision models
- See `WAN_INSTALLATION_COMPLETE.md` for details

### GGUF Model Support ✅
- Fixed GGUF model loading in CLIPLoaderGGUFMultiGPU node
- Automatic folder registration before ComfyUI startup
- Symlink-based model organization
- See `GGUF_MODEL_FIX_COMPLETE.md` for details

### GPU Configuration ✅
- Multi-GPU setup support (GT710 + RTX 5060 Ti)
- Compute-only mode configuration
- Display/compute GPU separation
- See `GPU_CONFIGURATION_GUIDE.md` for details

### Script Enhancements
- Added WAN model downloader (`scripts/download_wan_models.py`)
- Added GGUF model downloader with proper folder registration
- Enhanced CivitAI LoRA downloader
- Bootstrap script for custom node installation

## Support

For issues and questions:
- Check the troubleshooting section
- Review Docker logs
- Open an issue on GitHub