Param(
  [int]$Port = 5178,
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

if (-not $NoOpen) {
  Start-Process "http://127.0.0.1:$Port"
}

Write-Host "Starting Vite dev server *inside container* on http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "This is useful when Node.js is not installed on the host." -ForegroundColor DarkGray
Write-Host "If file watching is flaky on Windows mounts, we enable CHOKIDAR_USEPOLLING=true." -ForegroundColor DarkGray

# Long-running foreground process.
docker compose exec comfyui bash -lc "cd /workspace/experiments_ui/web && npm install && export EXPERIMENTS_UI_PROXY_TARGET=http://127.0.0.1:8790 && export EXPERIMENTS_UI_DEV_PORT=$Port && CHOKIDAR_USEPOLLING=true npm run dev -- --host 0.0.0.0"

