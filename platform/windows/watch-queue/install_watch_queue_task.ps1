<#
Installs/updates the Windows startup mechanism for watch_queue.

Behavior:
- If run as Administrator: creates a Scheduled Task (SYSTEM, AtStartup, delayed).
- If not admin: creates a Startup-folder shortcut for the current user.

Logs:
- Setup: C:\Logs\watch_queue_setup.log
- Runtime: C:\Logs\watch_queue.log
#>

[CmdletBinding()]
param(
    [string]$TaskName = "ComfyUI_Watch_Queue",
    [string]$Server = "http://127.0.0.1:8188",
    [string]$ExperimentsRoot = "output/output/experiments",

    # Optional: explicitly provide a Python executable (recommended for SYSTEM task)
    [string]$PythonExe = "",

    # Optional: ComfyUI portable root folder used for auto-detecting embedded python
    [string]$ComfyUIPortablePath = "",

    [switch]$Check,
    [switch]$Remove,
    [switch]$RunNow
)

$ErrorActionPreference = "Stop"

$StartupFolder = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
$ShortcutName = "ComfyUI Watch Queue.lnk"

function Ensure-LogsDir {
    if (-not (Test-Path "C:\Logs")) { New-Item -ItemType Directory -Path "C:\Logs" -Force | Out-Null }
}

function Write-SetupLog {
    param([string]$Message, [string]$Level = "INFO")
    Ensure-LogsDir
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] [SETUP] [$Level] $Message"
    Write-Host $line
    Add-Content -Path "C:\Logs\watch_queue_setup.log" -Value $line
}

function Test-Admin {
    return ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).
        IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Resolve-StartupScript {
    $scriptPath = Join-Path $PSScriptRoot "watch_queue_startup.ps1"
    if (-not (Test-Path $scriptPath)) {
        throw "watch_queue_startup.ps1 not found at: $scriptPath"
    }
    return (Resolve-Path $scriptPath).Path
}

function Build-PowerShellArgs {
    param(
        [string]$StartupScriptPath,
        [string]$ServerIn,
        [string]$ExperimentsRootIn,
        [string]$PythonExeIn,
        [string]$PortableIn
    )

    $args = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$StartupScriptPath`"",
        "-Server", "`"$ServerIn`"",
        "-ExperimentsRoot", "`"$ExperimentsRootIn`""
    )

    if ($PythonExeIn) { $args += @("-PythonExe", "`"$PythonExeIn`"") }
    if ($PortableIn) { $args += @("-ComfyUIPortablePath", "`"$PortableIn`"") }

    return ($args -join " ")
}

function Create-ScheduledTask {
    param([string]$Task, [string]$StartupScriptArgs)

    Write-SetupLog "Creating scheduled task: $Task"
    $exe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

    $action = New-ScheduledTaskAction -Execute $exe -Argument $StartupScriptArgs -WorkingDirectory $PSScriptRoot
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $trigger.Delay = "PT1M"

    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1)

    Register-ScheduledTask `
        -TaskName $Task `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description "Start ComfyUI watch_queue after ComfyUI is up" `
        -Force | Out-Null

    Write-SetupLog "Scheduled task created successfully." "SUCCESS"
}

function Create-StartupShortcut {
    param([string]$StartupScriptArgs)

    Write-SetupLog "Creating Startup-folder shortcut (current user)."
    if (-not (Test-Path $StartupFolder)) { New-Item -ItemType Directory -Path $StartupFolder -Force | Out-Null }

    $shortcutPath = Join-Path $StartupFolder $ShortcutName
    $exe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut($shortcutPath)
    $Shortcut.TargetPath = $exe
    $Shortcut.Arguments = $StartupScriptArgs
    $Shortcut.WorkingDirectory = $PSScriptRoot
    $Shortcut.WindowStyle = 7  # minimized
    $Shortcut.Description = "Start ComfyUI watch_queue after ComfyUI is up"
    $Shortcut.Save()

    Write-SetupLog "Startup shortcut created: $shortcutPath" "SUCCESS"
}

function Remove-Install {
    Write-SetupLog "Removing watch_queue startup configuration..." "INFO"

    if (Test-Admin) {
        try {
            $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            if ($task) {
                Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
                Write-SetupLog "Scheduled task removed: $TaskName" "SUCCESS"
            }
        } catch {
            Write-SetupLog "Failed to remove scheduled task: $($_.Exception.Message)" "WARNING"
        }
    } else {
        Write-SetupLog "Not admin; skipping scheduled task removal." "WARNING"
    }

    $shortcutPath = Join-Path $StartupFolder $ShortcutName
    if (Test-Path $shortcutPath) {
        try {
            Remove-Item $shortcutPath -Force
            Write-SetupLog "Startup shortcut removed." "SUCCESS"
        } catch {
            Write-SetupLog "Failed to remove startup shortcut: $($_.Exception.Message)" "WARNING"
        }
    }

    Write-SetupLog "Removal complete." "SUCCESS"
}

function Check-Install {
    $taskExists = $false
    if (Test-Admin) {
        try {
            $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            $taskExists = $null -ne $task
        } catch {
            # ignore
        }
    }

    $shortcutExists = Test-Path (Join-Path $StartupFolder $ShortcutName)

    Write-SetupLog "Configuration status:" "INFO"
    Write-SetupLog "  Scheduled Task ($TaskName): $(if ($taskExists) { 'EXISTS [OK]' } else { 'NOT FOUND' })" "INFO"
    Write-SetupLog "  Startup Shortcut: $(if ($shortcutExists) { 'EXISTS [OK]' } else { 'NOT FOUND' })" "INFO"
}

if ($Remove) {
    Remove-Install
    exit 0
}

if ($Check) {
    Check-Install
    exit 0
}

Ensure-LogsDir

$startupScript = Resolve-StartupScript
$psArgs = Build-PowerShellArgs `
    -StartupScriptPath $startupScript `
    -ServerIn $Server `
    -ExperimentsRootIn $ExperimentsRoot `
    -PythonExeIn $PythonExe `
    -PortableIn $ComfyUIPortablePath

Write-SetupLog "Installing watch_queue startup..." "INFO"
Write-SetupLog "TaskName: $TaskName"
Write-SetupLog "StartupScript: $startupScript"
Write-SetupLog "Server: $Server"
Write-SetupLog "ExperimentsRoot: $ExperimentsRoot"
Write-SetupLog "PythonExe (optional): $PythonExe"
Write-SetupLog "ComfyUIPortablePath (optional): $ComfyUIPortablePath"

if (Test-Admin) {
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

    Create-ScheduledTask -Task $TaskName -StartupScriptArgs $psArgs
} else {
    Write-SetupLog "Not running as Administrator - using Startup-folder shortcut method." "WARNING"
    Create-StartupShortcut -StartupScriptArgs $psArgs
}

if ($RunNow) {
    if (Test-Admin) {
        try {
            Write-SetupLog "Starting scheduled task now..." "INFO"
            Start-ScheduledTask -TaskName $TaskName
            Write-SetupLog "Task started." "SUCCESS"
        } catch {
            Write-SetupLog "Failed to start task: $($_.Exception.Message)" "WARNING"
        }
    } else {
        Write-SetupLog "RunNow requested but not admin; launching startup script directly..." "INFO"
        $exe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
        Start-Process -FilePath $exe -ArgumentList $psArgs -WorkingDirectory $PSScriptRoot
    }
}

Write-SetupLog "Install complete. Use -Check to verify, -Remove to uninstall." "SUCCESS"

