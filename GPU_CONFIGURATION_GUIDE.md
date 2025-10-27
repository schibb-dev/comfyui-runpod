# GPU Configuration Implementation Guide

## Files Created:
- `xorg.conf.new` - X.org configuration for mixed GPU setup
- `blacklist-nvidia-gt710.conf` - Blacklist NVIDIA driver for GT710
- `nvidia-compute-setup.service` - Systemd service for compute configuration
- `configure_gpus.sh` - Main installation script
- `verify_gpu_config.sh` - Verification script

## Implementation Steps:

### 1. Run the Configuration Script
```bash
sudo bash /home/yuji/Code/comfyui-runpod/configure_gpus.sh
```

This script will:
- Backup current X.org configuration
- Install Nouveau driver for GT710
- Create NVIDIA driver blacklist for GT710
- Install new X.org configuration
- Set up systemd service for compute configuration
- Configure RTX 5060 Ti cards for compute-only mode
- Update initramfs

### 2. Reboot the System
```bash
sudo reboot
```

### 3. Verify Configuration
```bash
bash /home/yuji/Code/comfyui-runpod/verify_gpu_config.sh
```

## Expected Results After Configuration:

### GT710 (Display GPU):
- Uses Nouveau driver
- Drives your monitor
- Not visible in `nvidia-smi`
- Visible in `lspci` as GK208B

### RTX 5060 Ti Cards (Compute GPUs):
- Use NVIDIA driver
- In `EXCLUSIVE_PROCESS` mode
- Visible in `nvidia-smi`
- Available for CUDA/OpenCL compute
- Not used for display

## For ComfyUI Usage:

### Use Both RTX Cards:
```bash
CUDA_VISIBLE_DEVICES=0,1 python main.py
```

### Use Specific RTX Card:
```bash
CUDA_VISIBLE_DEVICES=0 python main.py  # Use first RTX 5060 Ti
CUDA_VISIBLE_DEVICES=1 python main.py  # Use second RTX 5060 Ti
```

## Troubleshooting:

### If GT710 doesn't work for display:
1. Check monitor connection to GT710
2. Verify Nouveau driver installation: `lsmod | grep nouveau`
3. Check X.org logs: `cat /var/log/Xorg.0.log | grep -i error`

### If RTX cards don't appear in nvidia-smi:
1. Check NVIDIA driver: `lsmod | grep nvidia`
2. Verify compute mode: `nvidia-smi -i 0 --query-gpu=compute_mode`
3. Check systemd service: `systemctl status nvidia-compute-setup.service`

### If you need to revert:
```bash
sudo cp /etc/X11/xorg.conf.backup /etc/X11/xorg.conf
sudo rm /etc/modprobe.d/blacklist-nvidia-gt710.conf
sudo systemctl disable nvidia-compute-setup.service
sudo rm /etc/systemd/system/nvidia-compute-setup.service
sudo update-initramfs -u
sudo reboot
```




