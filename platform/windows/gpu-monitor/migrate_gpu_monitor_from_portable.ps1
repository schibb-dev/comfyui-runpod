<#
Migrates the active GPU monitor from the portable repo to the runpod repo.

What it does:
- Removes the existing Scheduled Task (same task name) which currently points to the portable launcher
- Installs a new Scheduled Task pointing to THIS runpod repo copy of enhanced_gpu_monitor.py
- Optionally removes the old Startup shortcut and/or portable launcher files

This script does not run anything unless you execute it as Administrator.
#>

[CmdletBinding()]
param(
    [string]$TaskName = "ComfyUI_Enhanced_GPU_Monitor",
    [string]$PythonExe = "",
    [string]$ComfyUIPortablePath = "",
    [switch]$RemoveStartupShortcut,
    [switch]$DeletePortableFiles,
    [switch]$RunNow
)

$here = $PSScriptRoot
$uninstall = Join-Path $here "uninstall_gpu_monitor_task.ps1"
$install = Join-Path $here "install_gpu_monitor_task.ps1"

if (-not (Test-Path $uninstall)) { throw "Missing: $uninstall" }
if (-not (Test-Path $install)) { throw "Missing: $install" }

& $uninstall -TaskName $TaskName -RemoveStartupShortcut:$RemoveStartupShortcut -DeletePortableFiles:$DeletePortableFiles
& $install -TaskName $TaskName -PythonExe $PythonExe -ComfyUIPortablePath $ComfyUIPortablePath -RunNow:$RunNow

