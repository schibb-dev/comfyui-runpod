# Docker Hub Setup Guide

## 🐳 Complete Docker Hub Configuration

### Step 1: Create Docker Hub Account

1. **Go to Docker Hub**: https://hub.docker.com/
2. **Sign Up**: Create a free account (if you don't have one)
3. **Choose Username**: Pick a username (this will be your Docker Hub username)
4. **Verify Email**: Complete email verification

### Step 2: Login to Docker Hub

```bash
cd /home/yuji/Code/Umeiart/comfyui-runpod
docker login
```

**When prompted:**
- **Username**: Your Docker Hub username
- **Password**: Your Docker Hub password (or access token)

### Step 3: Configure Your Username

**Option A: Use the helper script**
```bash
./scripts/setup_dockerhub.sh
```

**Option B: Manual configuration**
If your username is different from `yuji`, update these files:
- `scripts/build.sh` - Change `IMAGE_NAME="yuji/comfyui-runpod"`
- `scripts/push.sh` - Change `IMAGE_NAME="yuji/comfyui-runpod"`
- `docker-compose.yml` - Change `image: yuji/comfyui-runpod:latest`
- `Dockerfile` - Change `LABEL maintainer="yuji@example.com"`

### Step 4: Build and Push Your Image

```bash
# Build with version tag
./scripts/build.sh v1.0.0

# Push to Docker Hub
./scripts/push.sh v1.0.0
```

### Step 5: Verify on Docker Hub

1. Go to https://hub.docker.com/r/YOUR_USERNAME/comfyui-runpod
2. You should see your image with tags `v1.0.0` and `latest`

## 🚀 Ready for RunPod!

Once pushed, your image will be available for RunPod deployment:

**RunPod Template Image**: `YOUR_USERNAME/comfyui-runpod:latest`

## 📋 Docker Hub Best Practices

### Repository Settings
- **Visibility**: Public (free) or Private (paid)
- **Description**: "Custom ComfyUI with WAN + Florence2 + Civitai LoRA Management"
- **Tags**: Use semantic versioning (v1.0.0, v1.0.1, etc.)

### Image Management
- **Latest Tag**: Always points to the most recent stable version
- **Version Tags**: Keep specific versions for rollback capability
- **Size Optimization**: Use `.dockerignore` to exclude unnecessary files

### Security
- **Access Tokens**: Use access tokens instead of passwords for CI/CD
- **Repository Permissions**: Control who can push/pull your images

## 🔧 Troubleshooting

### "Cannot perform an interactive login"
```bash
# Use access token instead
echo "YOUR_ACCESS_TOKEN" | docker login --username YOUR_USERNAME --password-stdin
```

### "Repository does not exist"
- Make sure you've created the repository on Docker Hub
- Check your username is correct in all files

### "Access denied"
- Verify you're logged in: `docker info | grep Username`
- Check repository permissions on Docker Hub

### "Image too large"
- Free tier limit: 10GB per repository
- Use `.dockerignore` to exclude large files
- Consider multi-stage builds

## 📊 Image Information

**Current Image Details:**
- **Base**: `hearmeman/comfyui-wan-template:v10`
- **Size**: ~8-10GB (estimated)
- **Architecture**: Linux x86_64
- **CUDA**: 12.8 support
- **Python**: 3.12
- **PyTorch**: 2.10.0.dev20250924+cu128

**Included Features:**
- ✅ ComfyUI with 30+ custom nodes
- ✅ WAN Video Wrapper
- ✅ Florence2 support
- ✅ Civitai LoRA downloader
- ✅ Professional workspace structure
- ✅ Health checks and proper port exposure

## 🎯 Next Steps After Docker Hub

1. **Test Pull**: `docker pull YOUR_USERNAME/comfyui-runpod:latest`
2. **RunPod Deployment**: Use the image in RunPod templates
3. **GitHub Actions**: Set up automated builds
4. **Documentation**: Update README with Docker Hub links

---

**Ready to push to Docker Hub? Run:**
```bash
./scripts/setup_dockerhub.sh
```

