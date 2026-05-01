# Base Image Analysis: hearmeman/comfyui-wan-template:v10

## Key Details
- PyTorch version: 2.10.0.dev20250924+cu128
- CUDA version: 12.8
- Base path: / (root)
- ComfyUI location: /ComfyUI
- Startup: /start_script.sh (clones WAN wrapper and runs start.sh)

## Pre-installed Custom Nodes
- ComfyUI-Florence2 (already installed!)
- ComfyUI-WanVideoWrapper (WAN video support)
- ComfyUI-Easy-Use
- ComfyUI-Manager
- ComfyUI-VideoHelperSuite
- ComfyUI-Impact-Pack
- ComfyUI-KJNodes
- ComfyUI-Logic
- ComfyUI-RMBG
- ComfyUI-TeaCache
- ComfyUI-segment-anything-2
- ComfyUI_Comfyroll_CustomNodes
- ComfyUI_JPS-Nodes
- ComfyUI_LayerStyle
- ComfyUI_LayerStyle_Advance
- ComfyUI_UltimateSDUpscale
- ComfyUI_essentials
- RES4LYF
- cg-image-picker
- cg-use-everywhere
- comfy-plasma
- comfyui_controlnet_aux
- masquerade-nodes-comfyui
- mikey_nodes
- rgthree-comfy
- was-node-suite-comfyui
- websocket_image_save.py

## Packages of Interest
- torch: 2.10.0.dev20250924+cu128
- torchvision: 0.25.0.dev20250924+cu128
- torchaudio: 2.8.0.dev20250924+cu128
- triton: 3.4.0
- pytorch-triton: 3.5.0+gitbbb06c03
- cupy-cuda12x: 12.3.0
- open_clip_torch: 3.2.0

## RunPod Compatible
- nginx: Unknown (need to check)
- openssh-server: Unknown (need to check)
- jupyter: Unknown (need to check)

## Notes
- Florence2 is already installed - no need to add it
- WAN video wrapper is present
- Uses development PyTorch build
- CUDA 12.8 support
- Has extensive custom node collection

