<#
Uninstalls the Windows Scheduled Task for the Enhanced GPU Monitor.

By default, this only removes the Scheduled Task.
Optionally, it can also remove the legacy Startup-folder shortcut created by older installers.

It does NOT delete any Python scripts unless you explicitly pass -DeletePortableFiles.
#>

[CmdletBinding(SupportsShouldProcess=$true)]
param(
    [string]$TaskName = "ComfyUI_Enhanced_GPU_Monitor",
    [switch]$RemoveStartupShortcut,
    [switch]$DeletePortableFiles
)

function Require-Admin {
    $isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).
        IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) {
        throw "This script must be run as Administrator (it removes a Scheduled Task that may run as SYSTEM)."
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

Require-Admin

Write-SetupLog "Uninstalling scheduled task '$TaskName'..."
try {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch { }
        if ($PSCmdlet.ShouldProcess($TaskName, "Unregister-ScheduledTask")) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Write-SetupLog "Scheduled task removed." "SUCCESS"
        }
    } else {
        Write-SetupLog "Scheduled task not found; nothing to remove." "INFO"
    }
} catch {
    Write-SetupLog "Failed to remove scheduled task: $($_.Exception.Message)" "WARNING"
}

if ($RemoveStartupShortcut) {
    $startupFolder = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
    $shortcutPath = Join-Path $startupFolder "Enhanced GPU Monitor.lnk"
    if (Test-Path $shortcutPath) {
        if ($PSCmdlet.ShouldProcess($shortcutPath, "Remove-Item")) {
            Remove-Item $shortcutPath -Force
            Write-SetupLog "Startup shortcut removed: $shortcutPath" "SUCCESS"
        }
    } else {
        Write-SetupLog "Startup shortcut not found; skipping." "INFO"
    }
}

if ($DeletePortableFiles) {
    # Optional cleanup of the known legacy portable monitor launcher & script.
    # NOTE: This does NOT remove the portable ComfyUI install; it only targets monitor files.
    $portableRootCandidates = @(
        $env:COMFYUI_PORTABLE_PATH,
        (Join-Path $env:USERPROFILE "UmeAiRT\ComfyUI_windows_portable"),
        "C:\Users\yuji\UmeAiRT\ComfyUI_windows_portable"
    ) | Where-Object { $_ -and $_.Trim() -ne "" }

    foreach ($root in $portableRootCandidates) {
        $rootPath = $root
        if (-not (Test-Path $rootPath)) { continue }

        $targets = @(
            (Join-Path $rootPath "scripts\automation\enhanced_gpu_monitor.py"),
            (Join-Path $rootPath "scripts\automation\start_enhanced_gpu_monitor.bat"),
            (Join-Path $rootPath "scripts\automation\start_enhanced_gpu_monitor_elevated.bat")
        )

        foreach ($t in $targets) {
            if (Test-Path $t) {
                if ($PSCmdlet.ShouldProcess($t, "Remove-Item")) {
                    try {
                        Remove-Item $t -Force
                        Write-SetupLog "Deleted legacy portable monitor file: $t" "SUCCESS"
                    } catch {
                        Write-SetupLog "Failed to delete $t : $($_.Exception.Message)" "WARNING"
                    }
                }
            }
        }
    }
}

