Param(
  # Host port in the URL (default 51780; must match `EXPERIMENTS_UI_VITE_HOST_PORT` in `.env` / docker-compose).
  [int]$HostPort = 0,
  [switch]$NoOpen,
  [switch]$EnsureContainer
)

$ErrorActionPreference = "Stop"

function Die($msg) {
  Write-Host $msg -ForegroundColor Red
  exit 1
}

Set-Location -Path (Split-Path -Parent $MyInvocation.MyCommand.Path) | Out-Null
Set-Location -Path ".." | Out-Null  # repo root

if ($EnsureContainer) {
  Write-Host "Ensuring container is up (docker compose up -d)..." -ForegroundColor Cyan
  docker compose up -d | Out-Null
}

# Host URL port: explicit -HostPort wins; else ask compose for published mapping (picks up .env); else env; else default.
$resolvedHostPort = if ($HostPort -gt 0) {
  $HostPort
} else {
  $fromCompose = $null
  try {
    $pl = docker compose port comfyui 5178 2>$null
    if ($pl -match ':(\d+)\s*$') { $fromCompose = [int]$Matches[1] }
  } catch { }
  if ($null -ne $fromCompose) { $fromCompose }
  elseif (($env:EXPERIMENTS_UI_VITE_HOST_PORT -as [string]) -match '^\s*\d+\s*$') { [int]$env:EXPERIMENTS_UI_VITE_HOST_PORT.Trim() }
  else { 51780 }
}

if (-not $NoOpen) {
  Start-Process "http://127.0.0.1:$resolvedHostPort"
}

Write-Host "Starting Vite *inside* the comfyui container (listens on container port 5178)." -ForegroundColor Green
Write-Host "From the host open: http://127.0.0.1:$resolvedHostPort/ (docker maps host $resolvedHostPort -> container 5178)." -ForegroundColor Cyan
Write-Host "This is useful when Node.js is not installed on the host." -ForegroundColor DarkGray
Write-Host "If file watching is flaky on Windows mounts, we enable CHOKIDAR_USEPOLLING=true." -ForegroundColor DarkGray

# Long-running foreground process. EXPERIMENTS_UI_DEV_PORT must stay 5178 to match the container side of the compose port mapping.
docker compose exec comfyui bash -lc "cd /workspace/experiments_ui/web && npm install && export EXPERIMENTS_UI_PROXY_TARGET=http://127.0.0.1:8790 && export EXPERIMENTS_UI_DEV_PORT=5178 && CHOKIDAR_USEPOLLING=true npm run dev -- --host 0.0.0.0"

