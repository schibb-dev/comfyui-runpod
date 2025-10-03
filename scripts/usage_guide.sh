#!/bin/bash
# Civitai LoRA Downloader - Quick Usage Guide

echo "🎭 Civitai LoRA Downloader - Quick Usage"
echo "========================================"

echo ""
echo "🚀 DEFAULT USAGE (WAN 2.1 I2V 480p):"
echo "python3 civitai_lora_downloader.py"

echo ""
echo "🛡️  SAFE OPTIONS (No Downloads):"
echo "python3 civitai_lora_downloader.py --list-loras"
echo "python3 civitai_lora_downloader.py --dry-run"
echo "python3 civitai_lora_downloader.py --show-results"

echo ""
echo "🎯 FILTERING OPTIONS:"
echo "python3 civitai_lora_downloader.py --wan-version 2.2 --modality t2v"
echo "python3 civitai_lora_downloader.py --resolution 720"
echo "python3 civitai_lora_downloader.py --noise-level low"

echo ""
echo "📁 ADVANCED OPTIONS:"
echo "python3 civitai_lora_downloader.py --comfyui-dir /path/to/ComfyUI"
echo "python3 civitai_lora_downloader.py --disable-all --dry-run"

echo ""
echo "⚙️  CONFIGURATION MANAGEMENT:"
echo "python3 civitai_lora_downloader.py --show-defaults"
echo "python3 civitai_lora_downloader.py --set-defaults wan_version=2.2 modality=t2v"
echo "python3 civitai_lora_downloader.py --reset-defaults"

echo ""
echo "✅ Defaults: WAN 2.1, I2V, 480p, any noise"
echo "🔄 Noise level only applies to WAN 2.2 models"
echo "💾 Custom defaults saved to ~/.civitai_lora_defaults.json"
echo "💡 Use --dry-run to test without downloading"
