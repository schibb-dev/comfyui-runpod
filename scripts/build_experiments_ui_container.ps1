Param(
  [switch]$EnsureContainer,
  [switch]$Recreate
)

$ErrorActionPreference = "Stop"

function Die($msg) {
  Write-Host $msg -ForegroundColor Red
  exit 1
}

Set-Location -Path (Split-Path -Parent $MyInvocation.MyCommand.Path) | Out-Null
Set-Location -Path ".." | Out-Null  # repo root

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  Die "docker not found in PATH."
}

if ($EnsureContainer) {
  Write-Host "Ensuring container is up (docker compose up -d)..." -ForegroundColor Cyan
  docker compose up -d | Out-Null
}

if ($Recreate) {
  Write-Host "Recreating comfyui container (volume mounts apply)..." -ForegroundColor Cyan
  docker compose up -d --force-recreate comfyui | Out-Null
}

Write-Host "Building Experiments UI *inside container*..." -ForegroundColor Green
docker compose exec -T comfyui bash -lc "cd /workspace/experiments_ui/web && if [ -f package-lock.json ]; then npm ci; else npm install; fi && npm run build"

Write-Host "Done. Output is in workspace/experiments_ui/dist" -ForegroundColor DarkGray

