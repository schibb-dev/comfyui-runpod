# GPU Selection Guide for ComfyUI 🎯

## 🎮 **Your GPU Setup**
- **GPU 0**: NVIDIA GeForce RTX 5060 Ti (15.5 GB) - Currently in use
- **GPU 1**: NVIDIA GeForce RTX 5060 Ti (15.5 GB) - Available and idle

## 🎯 **Method 1: Global GPU Selection (Environment Variable)**

### Option A: Use Only GPU 1
```yaml
# In docker-compose.yml
environment:
  - NVIDIA_VISIBLE_DEVICES=1
```

### Option B: Use Only GPU 0  
```yaml
# In docker-compose.yml
environment:
  - NVIDIA_VISIBLE_DEVICES=0
```

### Option C: Use Both GPUs (Current)
```yaml
# In docker-compose.yml
environment:
  - NVIDIA_VISIBLE_DEVICES=all
```

## 🎯 **Method 2: Per-Node GPU Selection (Recommended)**

With ComfyUI-MultiGPU custom node, you can control GPU usage per node:

### **Available MultiGPU Nodes:**
- `CLIPLoaderMultiGPU` - Text encoder with device selection
- `CLIPLoaderGGUFMultiGPU` - GGUF text encoder with device selection
- `UnetLoaderMultiGPU` - UNet with device selection
- `UnetLoaderGGUFMultiGPU` - GGUF UNet with device selection
- `CheckpointLoaderMultiGPU` - Checkpoint loader with device selection
- `VAELoaderMultiGPU` - VAE loader with device selection

### **Device Selection Options:**
- `cuda:0` - Use GPU 0
- `cuda:1` - Use GPU 1  
- `cpu` - Use CPU
- `cuda:0,cuda:1` - Split across both GPUs

## 🎯 **Method 3: Advanced Multi-GPU Distribution**

### **DisTorch2 Advanced Allocation:**
For complex multi-GPU setups, use expert mode allocations:

#### **Bytes Mode (Recommended):**
```
cuda:1,8gb;cuda:0,4gb;cpu,*
```
- 8GB on GPU 1
- 4GB on GPU 0  
- Rest on CPU

#### **Ratio Mode:**
```
cuda:1,60%;cuda:0,30%;cpu,10%
```
- 60% on GPU 1
- 30% on GPU 0
- 10% on CPU

#### **Fraction Mode:**
```
cuda:1,0.5;cuda:0,0.3;cpu,0.2
```
- 50% of GPU 1's VRAM
- 30% of GPU 0's VRAM
- 20% of CPU RAM

## 🚀 **Quick Start: Use GPU 1**

### **For WAN Models:**
1. Use `CLIPLoaderGGUFMultiGPU` node
2. Set `device` to `cuda:1`
3. Select `umt5-xxl-encoder-Q5_K_M.gguf`

### **For Diffusion Models:**
1. Use `UnetLoaderMultiGPU` or `UnetLoaderGGUFMultiGPU`
2. Set `device` to `cuda:1`
3. Select your WAN model

### **For VAE:**
1. Use `VAELoaderMultiGPU`
2. Set `device` to `cuda:1`
3. Select `wan_2.1_vae.safetensors`

## 🔧 **Current Configuration**

Your setup is currently configured to:
- ✅ **Access both GPUs** (`NVIDIA_VISIBLE_DEVICES=all`)
- ✅ **Use MultiGPU nodes** for fine-grained control
- ✅ **Allow per-node device selection**

## 💡 **Recommendations**

1. **Keep current setup** (`NVIDIA_VISIBLE_DEVICES=all`) for maximum flexibility
2. **Use MultiGPU nodes** to control which GPU each model uses
3. **Use GPU 1** for large models (WAN 2.1) since it's currently idle
4. **Use GPU 0** for smaller models or when GPU 1 is busy
5. **Split large models** across both GPUs for maximum performance

## 🎯 **Example Workflow**

```
CLIPLoaderGGUFMultiGPU (device: cuda:1) → umt5-xxl-encoder-Q5_K_M.gguf
UnetLoaderMultiGPU (device: cuda:1) → wan2.1_diffusion_model.safetensors  
VAELoaderMultiGPU (device: cuda:0) → wan_2.1_vae.safetensors
```

This gives you complete control over which GPU each component uses!
