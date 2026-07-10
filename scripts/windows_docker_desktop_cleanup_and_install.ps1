# Clean up a broken/partial Docker Desktop install and reinstall to E:.
#
# Run from Windows PowerShell (Admin recommended):
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_docker_desktop_cleanup_and_install.ps1
#
# Options:
#   -CleanupOnly     Remove leftovers only; do not download/install
#   -SkipCleanup     Install only (assumes cleanup already done)
#   -InstallerPath   Use an existing Docker Desktop Installer.exe
#
# After install: open Docker Desktop → Settings → Resources → WSL integration → enable Ubuntu → Apply.
# Then in Ubuntu: bash ~/src/comfyui-runpod/scripts/wsl_migrate_to_docker_desktop.sh

param(
    [switch]$CleanupOnly,
    [switch]$SkipCleanup,
    [string]$InstallerPath = "",
    [string]$InstallDir = "C:\Program Files\Docker\Docker",
    [string]$WslDataRoot = "E:\DockerDesktop\wsl",
    [string]$DownloadDir = "$env:LOCALAPPDATA\Temp\DockerDesktopInstallers"
)

$ErrorActionPreference = "Stop"
$InstallerUrl = "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Stop-DockerDesktop {
    Write-Host "Stopping Docker Desktop processes (if any)..."
    Get-Process -Name "Docker Desktop", "com.docker.backend", "com.docker.service", "com.docker.build" -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

function Remove-DockerWslDistros {
    Write-Host "Unregistering docker-desktop WSL distros (if present)..."
    foreach ($name in @("docker-desktop", "docker-desktop-data")) {
        $listed = wsl -l -v 2>$null | Out-String
        if ($listed -match "(?m)^\s*\*?\s*$name\s") {
            Write-Host "  wsl --unregister $name"
            wsl --unregister $name 2>$null
        }
    }
}

function Invoke-Cleanup {
    Stop-DockerDesktop
    Remove-DockerWslDistros

    $paths = @(
        "$env:LOCALAPPDATA\Docker",
        "$env:APPDATA\Docker",
        "$env:APPDATA\Docker Desktop",
        "C:\Program Files\Docker",
        "C:\ProgramData\DockerDesktop",
        "E:\DockerDesktop",
        "E:\DockerDesktopSAV"
    )

    foreach ($p in $paths) {
        if (Test-Path $p) {
            Write-Host "Removing $p ..."
            Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    Write-Host "Cleanup pass complete."
}

function Get-Installer {
    param([string]$PathOverride)
    if ($PathOverride -and (Test-Path $PathOverride)) {
        return (Resolve-Path $PathOverride).Path
    }
    New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null
    $dest = Join-Path $DownloadDir "Docker Desktop Installer.exe"
    if (-not (Test-Path $dest)) {
        Write-Host "Downloading Docker Desktop installer..."
        Invoke-WebRequest -Uri $InstallerUrl -OutFile $dest -UseBasicParsing
    } else {
        Write-Host "Using cached installer: $dest"
    }
    return $dest
}

function Invoke-Install {
    param([string]$Installer)
    New-Item -ItemType Directory -Force -Path $WslDataRoot | Out-Null
    New-Item -ItemType Directory -Force -Path (Split-Path $InstallDir -Parent) -ErrorAction SilentlyContinue | Out-Null

    $args = @(
        "install",
        "--quiet",
        "--accept-license",
        "--backend=wsl-2",
        "--installation-dir=$InstallDir",
        "--wsl-default-data-root=$WslDataRoot"
    )

    Write-Host "Installing Docker Desktop..."
    Write-Host "  installation-dir: $InstallDir"
    Write-Host "  wsl-default-data-root: $WslDataRoot"

    $proc = Start-Process -FilePath $Installer -ArgumentList $args -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
        throw "Docker Desktop installer exited with code $($proc.ExitCode). Check $env:LOCALAPPDATA\Docker\install-log.txt"
    }

    $desktopExe = Join-Path $InstallDir "Docker Desktop.exe"
    if (-not (Test-Path $desktopExe)) {
        $desktopExe = Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\Docker Desktop.exe"
    }
    if (-not (Test-Path $desktopExe)) {
        throw "Install finished but Docker Desktop.exe not found (checked Program Files and per-user path)."
    }

    Write-Host "Starting Docker Desktop..."
    Start-Process $desktopExe
    Write-Host ""
    Write-Host "NEXT STEPS (manual):"
    Write-Host "  1. Docker Desktop → Settings → Resources → WSL integration → enable Ubuntu → Apply"
    Write-Host "  2. In Ubuntu: bash ~/src/comfyui-runpod/scripts/wsl_migrate_to_docker_desktop.sh"
    Write-Host "  3. Windows PowerShell: wsl --shutdown, then restart Docker Desktop"
    Write-Host "  4. In Ubuntu: docker context use desktop-linux && cd ~/src/comfyui-runpod && npm run up"
}

if (-not $SkipCleanup) {
    if (-not (Test-IsAdmin)) {
        Write-Warning "Not running as Administrator. Cleanup of Program Files may fail; re-run elevated if needed."
    }
    Invoke-Cleanup
}

if ($CleanupOnly) {
    Write-Host "CleanupOnly set; exiting."
    exit 0
}

$installer = Get-Installer -PathOverride $InstallerPath
Invoke-Install -Installer $installer
