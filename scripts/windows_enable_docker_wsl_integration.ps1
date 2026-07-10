# Enable Docker Desktop WSL integration for Ubuntu and restart Docker Desktop.
#
# Run from Windows PowerShell (normal user is fine):
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_enable_docker_wsl_integration.ps1
#
# After restart, in Ubuntu:
#   docker context use default
#   docker info | grep -E 'Name:|Operating System:|Runtimes:'
#   (expect Name: docker-desktop, nvidia runtime listed)

param(
    [string]$Distro = "Ubuntu",
    [string]$SettingsPath = "$env:APPDATA\Docker\settings-store.json"
)

$ErrorActionPreference = "Stop"

function Stop-DockerDesktop {
    Write-Host "Stopping Docker Desktop..."
    Get-Process -Name "Docker Desktop", "com.docker.backend", "com.docker.build", "com.docker.service" -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
}

function Update-Settings {
    if (-not (Test-Path $SettingsPath)) {
        throw "Settings not found: $SettingsPath"
    }
    $raw = Get-Content $SettingsPath -Raw
    $settings = $raw | ConvertFrom-Json

    # Docker Desktop 4.x settings-store.json (PascalCase keys)
    $settings | Add-Member -NotePropertyName "WslEngineEnabled" -NotePropertyValue $true -Force
    $settings | Add-Member -NotePropertyName "EnableIntegrationWithDefaultWslDistro" -NotePropertyValue $true -Force
    $settings | Add-Member -NotePropertyName "IntegratedWslDistros" -NotePropertyValue @($Distro) -Force

    ($settings | ConvertTo-Json -Depth 10) | Set-Content -Path $SettingsPath -Encoding UTF8
    Write-Host "Updated ${SettingsPath}:"
    Write-Host "  IntegratedWslDistros = [$Distro]"
    Write-Host "  EnableIntegrationWithDefaultWslDistro = true"
}

Stop-DockerDesktop
Update-Settings

Write-Host "Shutting down WSL..."
wsl --shutdown
Start-Sleep -Seconds 5

$desktopExe = Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\Docker Desktop.exe"
if (-not (Test-Path $desktopExe)) {
    $desktopExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
}
if (-not (Test-Path $desktopExe)) {
    throw "Docker Desktop.exe not found"
}

Write-Host "Starting Docker Desktop..."
Start-Process $desktopExe
Write-Host ""
Write-Host "Wait ~60s for engine + WSL integration, then in Ubuntu:"
Write-Host "  docker context use default"
Write-Host "  docker info | grep -E 'Name:|Operating System:|Runtimes:'"
Write-Host "  cd ~/src/comfyui-runpod && npm run up"
