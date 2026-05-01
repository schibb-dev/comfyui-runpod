# Windows GPU monitor (portable watchdog)

This folder contains the **Windows-side** GPU watchdog that can **restart Windows** when `nvidia-smi` reports **"GPU is lost"**.

This is **not used inside the Docker container**. It runs on the **Windows host** and can be used to recover a **Docker-based ComfyUI** stack when the GPU goes away.

## What installs/sets it up

There are two supported setup flows:

- `setup_gpu_monitor_task_scheduler.ps1` (**recommended**): creates a scheduled task `ComfyUI_Enhanced_GPU_Monitor` that runs at **system startup** as **SYSTEM** with highest privileges and launches `start_enhanced_gpu_monitor_elevated.bat`.
- `setup_enhanced_gpu_monitor_startup_simple.ps1` (more flexible): can create either a scheduled task (when admin) or a Startup-folder shortcut (when not).

### Migrating from the portable install (recommended if you have competing versions)

If you previously installed the monitor from `ComfyUI_windows_portable`, your Scheduled Task may still be pointing at:
`ComfyUI_windows_portable\scripts\automation\start_enhanced_gpu_monitor_elevated.bat`.

This repo includes migration scripts that:
- Remove the existing `ComfyUI_Enhanced_GPU_Monitor` task (regardless of where it points)
- Recreate it to run **this repo's** `enhanced_gpu_monitor.py`

Run **as Administrator** from this folder:

```powershell
.\migrate_gpu_monitor_from_portable.ps1 -RunNow
```

If you want to keep using the portable embedded Python (recommended), pass the portable root (or python exe) explicitly:

```powershell
.\migrate_gpu_monitor_from_portable.ps1 -ComfyUIPortablePath "C:\Users\yuji\UmeAiRT\ComfyUI_windows_portable" -RunNow
```

To remove the legacy Startup-folder shortcut created by older installers:

```powershell
.\migrate_gpu_monitor_from_portable.ps1 -RemoveStartupShortcut -RunNow
```

To additionally delete the *legacy portable monitor files* (only the monitor script/launchers, not the whole portable install):

```powershell
.\migrate_gpu_monitor_from_portable.ps1 -DeletePortableFiles -RunNow
```

### Clean uninstall

Run **as Administrator**:

```powershell
.\uninstall_gpu_monitor_task.ps1
```

## Configuration (controls reboot behavior)

The monitor reads config in this order:

1. `C:\Logs\gpu_monitor_config.json`
2. `.\gpu_monitor_config.json` (same folder as `enhanced_gpu_monitor.py`)
3. Defaults embedded in the script

**Important**: if `"report_only": false`, the monitor can take disruptive actions (including rebooting Windows).

### Recovery ladder (try things before reboot)

When `nvidia-smi` reports **"GPU is lost"**, the monitor can try a sequence of recovery steps before rebooting.
Configure these in `gpu_monitor_config.json`:

- `recovery_enabled`: enable/disable the recovery ladder (default true)
- `recovery_steps`: ordered list. Supported steps:
  - `restart_docker_containers` (restarts containers listed in `recovery_docker_containers`)
  - `wsl_shutdown` (runs `wsl.exe --shutdown`)
  - `restart_docker_service` (restarts Windows service(s) listed in `recovery_docker_services`, typically `com.docker.service`; may require admin)
  - `pnputil_restart_display` (optional; uses `pnputil /restart-device` for the display adapter; may require admin)
  - `restart_comfyui` (legacy: restarts a **portable** Windows ComfyUI `main.py` process)
  - `reboot` (final fallback)
- `recovery_fallback_to_reboot`: if true, reboot if all steps fail (when `report_only=false`)

`shutdown /r /t 30 /c "GPU is lost - system reboot required"`

To keep logging but stop reboots, set `"report_only": true`.

## Logs

By default logs go to `C:\Logs\`:

- `C:\Logs\gpu_crash.log`
- `C:\Logs\gpu_crash-YYYY-MM-DD.csv`
- `C:\Logs\gpu_crash-failures-YYYY-MM-DD.json`
- `C:\Logs\gpu_crash-failures-YYYY-MM-DD.log`

