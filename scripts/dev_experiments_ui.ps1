Param(
  [string]$Backend = "http://127.0.0.1:8790",
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

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  Write-Host "node not found in PATH." -ForegroundColor Yellow
  Write-Host "Option A (recommended): install Node.js (20.19+ or 22.12+) and re-run this script." -ForegroundColor Yellow
  Write-Host "Option B: run Vite inside the container instead:" -ForegroundColor Yellow
  Write-Host "  .\scripts\dev_experiments_ui_container.ps1 -EnsureContainer" -ForegroundColor Yellow
  exit 1
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Die "npm not found in PATH. Install Node.js (includes npm)." }

$nodeVer = (node --version) 2>$null
Write-Host "node $nodeVer" -ForegroundColor DarkGray

$webDir = Join-Path $PWD "workspace\experiments_ui\web"
if (-not (Test-Path $webDir)) { Die "Missing web dir: $webDir" }

Set-Location -Path $webDir | Out-Null

if (-not (Test-Path "node_modules")) {
  Write-Host "Installing dependencies (npm install)..." -ForegroundColor Cyan
  npm install
}

Write-Host "Starting Vite dev server (HMR) on http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "Proxying /api and /files to $Backend" -ForegroundColor DarkGray

if (-not $NoOpen) {
  Start-Process "http://127.0.0.1:$Port"
}

# NOTE: we force host/port to avoid IPv6 localhost issues.
npm run dev -- --host 127.0.0.1 --port $Port

