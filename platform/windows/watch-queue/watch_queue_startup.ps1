<#
Starts the ComfyUI experiment watch_queue after ComfyUI is available.

Designed to be run by Task Scheduler at boot (or via Startup shortcut).
It will:
  - avoid duplicate instances
  - wait until ComfyUI responds on /queue
  - run watch_queue.py in the comfyui-runpod workspace folder
  - append logs to C:\Logs\watch_queue.log
#>

[CmdletBinding()]
param(
    [string]$Server = "http://127.0.0.1:8188",

    # Experiments root relative to the workspace working directory
    [string]$ExperimentsRoot = "output/output/experiments",

    # Optional: explicitly provide a Python executable
    [string]$PythonExe = "",

    # Optional: ComfyUI portable root folder used for auto-detecting embedded python
    [string]$ComfyUIPortablePath = "",

    # Wait for ComfyUI to respond. 0 = wait forever.
    [int]$WaitSeconds = 0,

    # Poll interval while waiting for ComfyUI
    [int]$WaitPollSeconds = 2,

    # watch_queue settings
    [double]$PollSeconds = 2.0,
    [int]$MaxInflight = 16,

    # If set, run one iteration and exit (useful for debugging)
    [switch]$Once
)

$ErrorActionPreference = "Stop"

function Ensure-LogsDir {
    if (-not (Test-Path "C:\Logs")) { New-Item -ItemType Directory -Path "C:\Logs" -Force | Out-Null }
}

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    Ensure-LogsDir
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] [WATCH_QUEUE] [$Level] $Message"
    Write-Host $line
    Add-Content -Path "C:\Logs\watch_queue.log" -Value $line
}

function Resolve-RepoRoot {
    # This script lives at: <repo>\platform\windows\watch-queue\
    return (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
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
        $candidates += (Join-Path $env:USERPROFILE "UmeAiRT\ComfyUI_windows_portable\python_embeded\python.exe")
    }
    # Historical path on this machine (safe to try; may not exist elsewhere)
    $candidates += "C:\Users\yuji\UmeAiRT\ComfyUI_windows_portable\python_embeded\python.exe"

    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c)) { return (Resolve-Path $c).Path }
    }

    return "python.exe"
}

function Test-WatchQueueAlreadyRunning {
    param([string]$WatchQueueScriptPath)

    try {
        $procs = Get-CimInstance Win32_Process | Where-Object {
            $_.CommandLine -and ($_.CommandLine -match "watch_queue\.py")
        }
        foreach ($p in $procs) {
            if ($p.CommandLine -match [Regex]::Escape($WatchQueueScriptPath)) {
                return $true
            }
        }
        # If we can't match the full path, still treat any watch_queue.py as running to avoid duplicates
        return ($procs.Count -gt 0)
    } catch {
        # If process enumeration fails, don't block startup; allow launching.
        return $false
    }
}

function Wait-ForComfyUI {
    param([string]$ServerBase, [int]$MaxWaitSeconds, [int]$PollSeconds)

    $deadline = $null
    if ($MaxWaitSeconds -gt 0) {
        $deadline = (Get-Date).AddSeconds($MaxWaitSeconds)
    }

    $url = ($ServerBase.TrimEnd("/") + "/queue")
    Write-Log "Waiting for ComfyUI to respond at $url (max wait: $(if ($MaxWaitSeconds -gt 0) { "${MaxWaitSeconds}s" } else { "infinite" }))"

    while ($true) {
        try {
            $resp = Invoke-RestMethod -Uri $url -Method GET -TimeoutSec 5
            if ($null -ne $resp) {
                Write-Log "ComfyUI is responding on /queue. Starting watch_queue."
                return $true
            }
        } catch {
            # keep waiting
        }

        if ($deadline -and (Get-Date) -ge $deadline) {
            Write-Log "Timed out waiting for ComfyUI. Exiting." "ERROR"
            return $false
        }

        Start-Sleep -Seconds ([Math]::Max(1, $PollSeconds))
    }
}

try {
    $repoRoot = Resolve-RepoRoot
    $workspaceRoot = Join-Path $repoRoot "workspace"
    $watchQueueScript = Join-Path $workspaceRoot "scripts\watch_queue.py"

    if (-not (Test-Path $watchQueueScript)) {
        Write-Log "watch_queue.py not found: $watchQueueScript" "ERROR"
        exit 1
    }
    if (-not (Test-Path $workspaceRoot)) {
        Write-Log "Workspace root not found: $workspaceRoot" "ERROR"
        exit 1
    }

    $python = Resolve-PythonExe -PythonExeIn $PythonExe -PortableRoot $ComfyUIPortablePath
    Write-Log "PythonExe: $python"
    Write-Log "WorkspaceRoot: $workspaceRoot"
    Write-Log "WatchQueueScript: $watchQueueScript"
    Write-Log "ExperimentsRoot (arg): $ExperimentsRoot"

    if (Test-WatchQueueAlreadyRunning -WatchQueueScriptPath $watchQueueScript) {
        Write-Log "watch_queue appears to already be running; exiting."
        exit 0
    }

    $ok = Wait-ForComfyUI -ServerBase $Server -MaxWaitSeconds $WaitSeconds -PollSeconds $WaitPollSeconds
    if (-not $ok) { exit 1 }

    Push-Location $workspaceRoot
    try {
        $onceArg = @()
        if ($Once) { $onceArg = @("--once") }

        $cmdArgs = @(
            "-u",
            $watchQueueScript,
            $ExperimentsRoot,
            "--server", $Server,
            "--poll", [string]$PollSeconds,
            "--max-inflight", [string]$MaxInflight
        ) + $onceArg

        Write-Log ("Launching: " + $python + " " + ($cmdArgs -join " "))

        # Run synchronously so Task Scheduler can track/restart the task.
        & $python @cmdArgs 2>&1 | ForEach-Object {
            $line = $_.ToString()
            Ensure-LogsDir
            Add-Content -Path "C:\Logs\watch_queue.log" -Value $line
            Write-Host $line
        }

        exit $LASTEXITCODE
    } finally {
        Pop-Location
    }
} catch {
    Write-Log "Fatal error: $($_.Exception.Message)" "ERROR"
    exit 1
}

