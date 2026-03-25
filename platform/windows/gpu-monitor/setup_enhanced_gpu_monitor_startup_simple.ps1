# Simple Setup Script for Enhanced GPU Monitor Startup
# This script sets up the enhanced GPU monitor to run automatically on system restart.
#
# Notes:
# - When run as Administrator, it will create a Scheduled Task as SYSTEM.
# - When not admin, it will create a Startup-folder shortcut for the current user.
# - Paths are relative to this script’s location (portable/reusable).

param(
    [switch]$Check,
    [switch]$Remove
)

$TaskName = "ComfyUI_Enhanced_GPU_Monitor"
$RootPath = Split-Path -Parent $PSScriptRoot
$MonitorScript = Join-Path $PSScriptRoot "enhanced_gpu_monitor.py"
$StartupFolder = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
$ShortcutName = "Enhanced GPU Monitor.lnk"

function Write-SetupLog {
    param([string]$Message, [string]$Level = "INFO")
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logEntry = "[$timestamp] [SETUP] [$Level] $Message"
    Write-Host $logEntry
    if (Test-Path "C:\Logs") {
        Add-Content -Path "C:\Logs\gpu_monitor_setup.log" -Value $logEntry
    }
}

function Test-Admin {
    $currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-PythonPath {
    $embedded = Join-Path $RootPath "python_embeded\python.exe"
    if (Test-Path $embedded) { return $embedded }
    return "python.exe"
}

function Create-ScheduledTask {
    Write-SetupLog "Creating scheduled task: $TaskName"

    if (!(Test-Path $MonitorScript)) {
        Write-SetupLog "Monitor script not found: $MonitorScript" "ERROR"
        return $false
    }

    try {
        $pythonPath = Get-PythonPath
        $action = New-ScheduledTaskAction -Execute $pythonPath -Argument "-u `"$MonitorScript`"" -WorkingDirectory $RootPath

        $trigger = New-ScheduledTaskTrigger -AtStartup
        $trigger.Delay = "PT1M"

        $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Enhanced GPU Monitor - Monitors GPU status and detects failures" -Force
        Write-SetupLog "Scheduled task created successfully" "SUCCESS"
        return $true
    } catch {
        Write-SetupLog "Failed to create scheduled task: $($_.Exception.Message)" "ERROR"
        return $false
    }
}

function Create-StartupShortcut {
    Write-SetupLog "Creating startup shortcut"

    if (!(Test-Path $MonitorScript)) {
        Write-SetupLog "Monitor script not found: $MonitorScript" "ERROR"
        return $false
    }

    try {
        $pythonPath = Get-PythonPath
        $shortcutPath = Join-Path $StartupFolder $ShortcutName
        $WshShell = New-Object -ComObject WScript.Shell
        $Shortcut = $WshShell.CreateShortcut($shortcutPath)
        $Shortcut.TargetPath = $pythonPath
        $Shortcut.Arguments = "-u `"$MonitorScript`""
        $Shortcut.WorkingDirectory = $RootPath
        $Shortcut.WindowStyle = 1
        $Shortcut.Description = "Enhanced GPU Monitor"
        $Shortcut.Save()

        Write-SetupLog "Startup shortcut created: $shortcutPath" "SUCCESS"
        return $true
    } catch {
        Write-SetupLog "Failed to create startup shortcut: $($_.Exception.Message)" "ERROR"
        return $false
    }
}

function Check-Configuration {
    Write-SetupLog "Checking GPU monitor configuration..." "INFO"

    $taskExists = $false
    $shortcutExists = Test-Path (Join-Path $StartupFolder $ShortcutName)

    if (Test-Admin) {
        try {
            $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            $taskExists = $null -ne $task
        } catch {
            Write-SetupLog "Could not check scheduled tasks (may need admin)" "WARNING"
        }
    }

    Write-SetupLog "`nGPU Monitor Configuration Status:" "INFO"
    Write-SetupLog "  Scheduled Task: $(if ($taskExists) { 'EXISTS [OK]' } else { 'NOT FOUND' })" "INFO"
    Write-SetupLog "  Startup Shortcut: $(if ($shortcutExists) { 'EXISTS [OK]' } else { 'NOT FOUND' })" "INFO"

    if (!$taskExists -and !$shortcutExists) {
        Write-SetupLog "`n[WARNING] Enhanced GPU monitor is not configured for startup!" "WARNING"
        Write-SetupLog "Run this script without -Check to set it up" "INFO"
    } else {
        Write-SetupLog "`n[OK] Enhanced GPU monitor is configured for startup" "SUCCESS"
    }
}

if ($Check) {
    Check-Configuration
    exit 0
}

if ($Remove) {
    Write-SetupLog "Removing enhanced GPU monitor startup configuration..." "INFO"

    if (Test-Admin) {
        try {
            $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            if ($task) {
                Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
                Write-SetupLog "Scheduled task removed" "SUCCESS"
            }
        } catch {
            Write-SetupLog "Error removing scheduled task: $($_.Exception.Message)" "WARNING"
        }
    }

    $shortcutPath = Join-Path $StartupFolder $ShortcutName
    if (Test-Path $shortcutPath) {
        Remove-Item $shortcutPath -Force
        Write-SetupLog "Startup shortcut removed" "SUCCESS"
    }

    Write-SetupLog "Enhanced GPU monitor startup configuration removed" "SUCCESS"
    exit 0
}

Write-SetupLog "Setting up Enhanced GPU Monitor startup configuration..." "INFO"

$taskCreated = $false
$shortcutCreated = $false

if (Test-Admin) {
    $taskCreated = Create-ScheduledTask
} else {
    Write-SetupLog "Not running as Administrator - will use startup folder method" "WARNING"
    Write-SetupLog "For better reliability, run as Administrator to use scheduled task" "INFO"
}

$shortcutCreated = Create-StartupShortcut

Write-SetupLog "`n=== Setup Summary ===" "INFO"
if ($taskCreated) {
    Write-SetupLog "[OK] Enhanced GPU monitor scheduled task created" "SUCCESS"
} elseif (Test-Admin) {
    Write-SetupLog "[FAIL] Failed to create scheduled task" "ERROR"
} else {
    Write-SetupLog "[WARN] Scheduled task not created (requires Administrator)" "WARNING"
}

if ($shortcutCreated) {
    Write-SetupLog "[OK] Enhanced GPU monitor startup shortcut created" "SUCCESS"
} else {
    Write-SetupLog "[FAIL] Failed to create startup shortcut" "ERROR"
}

if ($taskCreated -or $shortcutCreated) {
    Write-SetupLog "`nEnhanced GPU Monitor will start automatically on boot" "SUCCESS"
    Write-SetupLog "To verify, run: .\setup_enhanced_gpu_monitor_startup_simple.ps1 -Check" "INFO"
} else {
    Write-SetupLog "`nSetup failed - Enhanced GPU monitor will not start automatically" "ERROR"
    exit 1
}

