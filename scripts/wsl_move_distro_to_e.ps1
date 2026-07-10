# Move the default WSL distro (Ubuntu) from C: to E:\WSL\Ubuntu.
#
# RUN FROM WINDOWS POWERSHELL — NOT from inside WSL/Cursor's Ubuntu terminal.
# This shuts down all WSL distros; any in-WSL session will disconnect.
#
# Usage (Windows PowerShell):
#   cd \\wsl.localhost\Ubuntu\home\yuji\src\comfyui-runpod
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\wsl_move_distro_to_e.ps1
#
# Or with a custom target:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\wsl_move_distro_to_e.ps1 -TargetDir E:\WSL\Ubuntu -Distro Ubuntu

param(
    [string]$Distro = "Ubuntu",
    [string]$TargetDir = "E:\WSL\Ubuntu",
    [string]$LogFile = "E:\WSL\move.log"
)

$ErrorActionPreference = "Stop"

function Write-Log([string]$Message) {
    $line = "$(Get-Date -Format o) $Message"
    $parent = Split-Path -Parent $LogFile
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    Add-Content -Path $LogFile -Value $line
    Write-Host $line
}

Write-Log "=== WSL move start: distro=$Distro target=$TargetDir ==="

$targetParent = Split-Path -Parent $TargetDir
if (-not (Test-Path $targetParent)) {
    New-Item -ItemType Directory -Path $targetParent -Force | Out-Null
    Write-Log "Created $targetParent"
}

# Preflight: target must not already contain a distro unless user is re-running after failure.
if (Test-Path $TargetDir) {
    $existing = Get-ChildItem -Path $TargetDir -Force -ErrorAction SilentlyContinue
    if ($existing -and $existing.Count -gt 0) {
        Write-Log "ERROR: Target already exists and is not empty: $TargetDir"
        Write-Log "If a prior move succeeded, skip this script. If not, remove or rename that folder first."
        exit 1
    }
}

Write-Log "Distros before:"
wsl.exe --list -v 2>&1 | ForEach-Object { Write-Log $_ }

Write-Log "Shutting down WSL (all distros stop)..."
wsl.exe --shutdown
Start-Sleep -Seconds 8

Write-Log "Moving $Distro to $TargetDir (may take 10-30+ min for ~190 GB)..."
$moveOut = wsl.exe --manage $Distro --move $TargetDir 2>&1
$moveOut | ForEach-Object { Write-Log $_ }
if ($LASTEXITCODE -ne 0) {
    Write-Log "ERROR: wsl --manage --move failed with exit $LASTEXITCODE"
    exit $LASTEXITCODE
}

Write-Log "Enabling sparse VHD (helps reclaim deleted space later)..."
wsl.exe --manage $Distro --set-sparse true 2>&1 | ForEach-Object { Write-Log $_ }

Write-Log "Starting $Distro to verify..."
$verify = wsl.exe -d $Distro -u yuji -e bash -lc "echo WSL_OK; df -h /; du -sh /mnt/c/Users/yuji/AppData/Local/wsl 2>/dev/null || true; ls -la /mnt/e/WSL/" 2>&1
$verify | ForEach-Object { Write-Log $_ }

Write-Log "=== WSL move finished ==="
Write-Log "Next: reopen Cursor via Remote WSL, then check C: free space in Windows."
Write-Log "Optional: move swap off C: — see scripts/wslconfig-move-swap.example in repo."
