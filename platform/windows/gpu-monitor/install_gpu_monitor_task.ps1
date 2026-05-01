<# 
Installs/updates the Windows Scheduled Task for the Enhanced GPU Monitor.

Goal:
- Ensure the task name stays consistent: ComfyUI_Enhanced_GPU_Monitor
- Point the task to THIS runpod repo copy of enhanced_gpu_monitor.py
- Remove/overwrite any older task that points to the portable folder

This script does NOT delete any portable files.
#>

[CmdletBinding()]
param(
    [string]$TaskName = "ComfyUI_Enhanced_GPU_Monitor",

    # Optional: explicitly provide a Python executable.
    # Recommended: point at your portable embedded python.exe so it works even if system Python isn't installed.
    [string]$PythonExe = "",

    # Optional: ComfyUI portable root folder used for auto-detecting embedded python.
    [string]$ComfyUIPortablePath = "",

    [switch]$RunNow
)

function Require-Admin {
    $isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).
        IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) {
        throw "This script must be run as Administrator (it creates a Scheduled Task that runs as SYSTEM)."
    }
}

function Ensure-LogsDir {
    if (-not (Test-Path "C:\Logs")) { New-Item -ItemType Directory -Path "C:\Logs" -Force | Out-Null }
}

function Write-SetupLog {
    param([string]$Message, [string]$Level = "INFO")
    Ensure-LogsDir
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] [SETUP] [$Level] $Message"
    Write-Host $line
    Add-Content -Path "C:\Logs\gpu_monitor_setup.log" -Value $line
}

function Resolve-PythonExe {
    param([string]$PythonExeIn, [string]$PortableRoot)

    if ($PythonExeIn -and (Test-Path $PythonExeIn)) { return (Resolve-Path $PythonExeIn).Path }

    $candidates = @()
    if ($PortableRoot) {
        $candidates += (Join-Path $PortableRoot "python_embeded\python.exe")
    }
    if ($env:COMFYUI_PORTABLE_PATH) {
        $candidates += (Join-Path $env:COMFYUI_PORTABLE_PATH "python_embeded\python.exe")
    }
    if ($env:USERPROFILE) {
        # Common layout on this machine: %USERPROFILE%\UmeAiRT\ComfyUI_windows_portable
        $candidates += (Join-Path $env:USERPROFILE "UmeAiRT\ComfyUI_windows_portable\python_embeded\python.exe")
    }
    # Last-resort historical path (safe to try; may not exist on other machines)
    $candidates += "C:\Users\yuji\UmeAiRT\ComfyUI_windows_portable\python_embeded\python.exe"

    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c)) { return (Resolve-Path $c).Path }
    }

    # If python.exe is on PATH this can still work (but task runs as SYSTEM, so PATH may differ)
    return "python.exe"
}

Require-Admin

$gpuMonitorDir = $PSScriptRoot
$monitorScript = Join-Path $gpuMonitorDir "enhanced_gpu_monitor.py"
if (-not (Test-Path $monitorScript)) {
    throw "enhanced_gpu_monitor.py not found at: $monitorScript"
}

$python = Resolve-PythonExe -PythonExeIn $PythonExe -PortableRoot $ComfyUIPortablePath

Write-SetupLog "Installing/Updating scheduled task '$TaskName' to run runpod GPU monitor."
Write-SetupLog "MonitorScript: $monitorScript"
Write-SetupLog "WorkingDirectory: $gpuMonitorDir"
Write-SetupLog "PythonExe: $python"

# Remove existing task if present (this is how we migrate away from older/portable tasks)
try {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Write-SetupLog "Existing task found; removing before install." "WARNING"
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Start-Sleep -Seconds 1
    }
} catch {
    Write-SetupLog "Could not query/remove existing task: $($_.Exception.Message)" "WARNING"
}

# Action: run python -u <runpod>\enhanced_gpu_monitor.py
$args = "-u `"$monitorScript`""
$action = New-ScheduledTaskAction -Execute $python -Argument $args -WorkingDirectory $gpuMonitorDir

# Trigger: startup (with short delay to let drivers/services settle)
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = "PT1M"

# Principal: SYSTEM with highest privileges
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

# Settings: restart on failure
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Runpod Enhanced GPU Monitor (runs from comfyui-runpod repo)" `
    -Force | Out-Null

Write-SetupLog "Scheduled task installed successfully." "SUCCESS"

if ($RunNow) {
    try {
        Write-SetupLog "Starting task now..." "INFO"
        Start-ScheduledTask -TaskName $TaskName
        Write-SetupLog "Task started." "SUCCESS"
    } catch {
        Write-SetupLog "Failed to start task: $($_.Exception.Message)" "WARNING"
    }
}

