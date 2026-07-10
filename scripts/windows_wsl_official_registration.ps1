# Convert WSL Ubuntu junction workaround → official BasePath on E:\WSL\Ubuntu.
#
# Run from Windows PowerShell (NOT inside WSL). Closes WSL — run when you can disconnect.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_wsl_official_registration.ps1
#
# Disk note: E: may not have room for export+old vhdx together (~380 GB peak).
# This script exports to C:\WSL\backups by default (C: had ~214 GB free as of 2026-07-02).
#
# Options:
#   -ExportTar      Override export path
#   -Distro         Default: Ubuntu
#   -ImportDir      Default: E:\WSL\Ubuntu
#   -JunctionGuid   Default: b7fa9724-762b-4f5c-9a5a-0d53a75bab60
#   -DryRun         Print steps only

param(
    [string]$Distro = "Ubuntu",
    [string]$ExportTar = "",
    [string]$ImportDir = "E:\WSL\Ubuntu",
    [string]$JunctionGuid = "b7fa9724-762b-4f5c-9a5a-0d53a75bab60",
    [string]$DefaultUser = "yuji",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$JunctionPath = "C:\Users\yuji\AppData\Local\wsl\{$JunctionGuid}"
$BackupRoot = "C:\WSL\backups"
$LogFile = "E:\WSL\move.log"

if (-not $ExportTar) {
    $stamp = Get-Date -Format "yyyyMMdd"
    $ExportTar = Join-Path $BackupRoot "ubuntu-$stamp.tar"
}

function Write-Log([string]$Message) {
    $line = "$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssK') $Message"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -ErrorAction SilentlyContinue
}

function Assert-FreeSpace {
    $exportDrive = (Split-Path $ExportTar -Qualifier)
    $importDrive = (Split-Path $ImportDir -Qualifier)
    $cFree = (Get-PSDrive ($exportDrive.TrimEnd(':')).Name).Free / 1GB
    $eFree = (Get-PSDrive ($importDrive.TrimEnd(':')).Name).Free / 1GB
    Write-Host "Free space: ${exportDrive} $([math]::Round($cFree,1)) GB, ${importDrive} $([math]::Round($eFree,1)) GB"
    if ($cFree -lt 120) {
        Write-Warning "${exportDrive} has less than 120 GB free; export may fail for a ~190 GB distro."
    }
    if ($eFree -lt 100) {
        Write-Warning "${importDrive} has less than 100 GB free; import may fail until old vhdx is removed."
    }
}

if ($DryRun) {
    Write-Host @"
DRY RUN — would execute:
  1. wsl --shutdown
  2. wsl --export $Distro $ExportTar
  3. wsl --unregister $Distro
  4. rmdir junction $JunctionPath
  5. Rename-Item $ImportDir -> E:\WSL\Ubuntu.pre-junction-era
  6. wsl --import $Distro $ImportDir $ExportTar --version 2
  7. wsl --set-default $Distro; ubuntu.exe config --default-user $DefaultUser
  8. Verify BasePath in registry
"@
    exit 0
}

Assert-FreeSpace
New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
Write-Log "=== Official WSL registration start: distro=$Distro import=$ImportDir export=$ExportTar ==="

Write-Log "Shutting down WSL..."
wsl --shutdown
Start-Sleep -Seconds 5

Write-Log "Exporting $Distro to $ExportTar (long-running)..."
wsl --export $Distro $ExportTar
$gb = (Get-Item $ExportTar).Length / 1GB
Write-Log "Export size: $([math]::Round($gb,2)) GB"

Write-Log "Unregistering $Distro..."
wsl --unregister $Distro

if (Test-Path $JunctionPath) {
    Write-Log "Removing junction $JunctionPath"
    cmd /c rmdir $JunctionPath
}

$legacy = "$ImportDir.pre-junction-era"
if (Test-Path $ImportDir) {
    Write-Log "Renaming $ImportDir -> $legacy"
    if (Test-Path $legacy) {
        throw "Legacy folder already exists: $legacy (delete or rename manually first)"
    }
    Rename-Item $ImportDir $legacy
}

Write-Log "Importing to $ImportDir..."
wsl --import $Distro $ImportDir $ExportTar --version 2

wsl --set-default $Distro
$ubuntuExe = Get-Command ubuntu.exe -ErrorAction SilentlyContinue
if ($ubuntuExe) {
    & ubuntu.exe config --default-user $DefaultUser
} else {
    Write-Warning "ubuntu.exe not on PATH; set default user in /etc/wsl.conf if needed."
}

$basePath = Get-ChildItem HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss |
    ForEach-Object {
        if ($_.GetValue('DistributionName') -eq $Distro) { $_.GetValue('BasePath') }
    } | Select-Object -First 1

Write-Log "Registry BasePath: $basePath"
if ($basePath -notlike 'E:\WSL\Ubuntu*') {
    Write-Warning "BasePath is not E:\WSL\Ubuntu — verify manually before deleting backups."
}

Write-Log "Boot test..."
wsl -d $Distro -e bash -lc "echo OK; whoami; df -h /; test -f ~/src/comfyui-runpod/.env && echo repo_ok"

Write-Host ""
Write-Host "SUCCESS (pending your verification)."
Write-Host "After 24-48h normal use, reclaim space:"
Write-Host "  Remove-Item -Recurse -Force '$legacy'"
Write-Host "  Remove-Item '$ExportTar'"
Write-Log "=== Official registration complete ==="
