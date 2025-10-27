# WAN Models Installation - Complete Solution ✅

## 🎯 **Problem Solved**

The CLIPLoaderGGUFMultiGPU node was looking for `umt5-xxl-encoder-Q5_K_M.gguf` but couldn't find it because:
1. The GGUF model was in `/ComfyUI/models/LLM/` instead of the expected location
2. ComfyUI's `clip_gguf` folder wasn't registered in the folder paths
3. The WAN download scripts weren't handling GGUF models properly

## ✅ **Complete Solution Implemented**

### 1. **Fixed Model Location**
- **GGUF Model**: `umt5-xxl-encoder-Q5_K_M.gguf` (3.9 GB)
- **Location**: `/ComfyUI/models/LLM/` → `/ComfyUI/models/clip_gguf/` (via symlink)
- **Accessible to**: CLIPLoaderGGUFMultiGPU node

### 2. **Updated Download Scripts**
- **`scripts/download_wan_models.py`**: Now handles GGUF models via symlinks
- **`scripts/run_wan_downloader.sh`**: Wrapper script for easy execution
- **Automatic registration**: Registers `clip_gguf` folder with ComfyUI

### 3. **Dockerfile Integration**
- **Permanent fix**: Added clip_gguf folder registration to startup script
- **Automatic symlink**: Creates symlink from LLM to clip_gguf directory
- **Persistent**: Survives container restarts

## 📦 **All WAN Models Now Available**

### ✅ **WAN 2.1 Complete Set**
- **VAE**: `wan_2.1_vae.safetensors` (242.1 MB)
- **Text Encoder (FP16)**: `umt5_xxl_fp16.safetensors` (10.8 GB)
- **Text Encoder (FP8)**: `umt5_xxl_fp8_e4m3fn_scaled.safetensors` (6.4 GB)
- **Text Encoder (GGUF)**: `umt5-xxl-encoder-Q5_K_M.gguf` (3.9 GB) ← **FIXED!**
- **CLIP Vision**: `clip_vision_h.safetensors` (1.2 GB)
- **Diffusion Models**: 2 models including main I2V model

### ✅ **Additional Models**
- **LoRAs**: 6 WAN 2.1 LoRAs from CivitAI
- **GGUF Models**: UMT5 Text Encoder + WAN 2.1 I2V UNET

## 🔧 **Technical Details**

### **CLIPLoaderGGUFMultiGPU Node**
- **Scans**: `ComfyUI/models/clip` + `ComfyUI/models/clip_gguf`
- **Supported formats**: `.safetensors` + `.gguf`
- **Now finds**: `umt5-xxl-encoder-Q5_K_M.gguf` ✅

### **Folder Registration**
```python
folder_paths.folder_names_and_paths['clip_gguf'] = (['/ComfyUI/models/clip_gguf'], {'.gguf'})
```

### **Symlink Structure**
```
/ComfyUI/models/clip_gguf/umt5-xxl-encoder-Q5_K_M.gguf 
    → /ComfyUI/models/LLM/umt5-xxl-encoder-Q5_K_M.gguf
```

## 🚀 **Installation Process**

The complete installation now runs automatically:

1. **WAN Model Downloads** → Downloads all WAN 2.1 models
2. **GGUF Symlink Creation** → Links GGUF model to clip_gguf folder
3. **Folder Registration** → Registers clip_gguf with ComfyUI
4. **CivitAI Downloads** → Downloads LoRAs
5. **Custom Nodes** → Bootstraps all custom nodes
6. **ComfyUI Start** → Starts with all models accessible

## ✅ **Verification**

All models are now accessible to ComfyUI:
- **VAE models**: 1 ✅
- **Text encoders**: 2 ✅  
- **CLIP vision**: 1 ✅
- **Diffusion models**: 2 ✅
- **GGUF models**: 1 ✅ ← **FIXED!**

## 🎉 **Result**

The CLIPLoaderGGUFMultiGPU node now has access to:
- `umt5_xxl_fp16.safetensors`
- `umt5_xxl_fp8_e4m3fn_scaled.safetensors`
- `umt5-xxl-encoder-Q5_K_M.gguf` ← **NOW AVAILABLE!**

**Error resolved**: `Value not in list: clip_name: 'umt5-xxl-encoder-Q5_K_M.gguf' not in ['umt5_xxl_fp16.safetensors', 'umt5_xxl_fp8_e4m3fn_scaled.safetensors']`
