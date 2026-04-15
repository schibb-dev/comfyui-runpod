Param(
  # Host dev: experiments_ui_server on 8791 (Docker uses 8790 -> container; see vite.config.ts).
  [string]$Backend = "http://127.0.0.1:8791",
  [int]$Port = 5178,
  [switch]$NoOpen,
  [switch]$EnsureContainer,
  # Listen on 0.0.0.0 + set HMR host for WebSocket when opening the app from iPhone via Tailscale.
  [switch]$Tailscale
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

# Vite reads this in vite.config.ts (loadEnv).
$env:EXPERIMENTS_UI_PROXY_TARGET = $Backend

$viteHost = "127.0.0.1"
$openUrl = "http://127.0.0.1:$Port"

if (-not $Tailscale) {
  Remove-Item Env:EXPERIMENTS_UI_HMR_HOST -ErrorAction SilentlyContinue
}
$env:EXPERIMENTS_UI_DEV_PORT = "$Port"

if ($Tailscale) {
  $viteHost = "0.0.0.0"
  $tsIp = $null
  if (Get-Command tailscale -ErrorAction SilentlyContinue) {
    try {
      $lines = & tailscale ip -4 2>$null
      if ($lines) { $tsIp = ($lines | Select-Object -First 1).ToString().Trim() }
    } catch { }
  }
  if (-not $tsIp) {
    $hmrEnv = $env:EXPERIMENTS_UI_HMR_HOST
    if ($hmrEnv) { $tsIp = $hmrEnv.Trim() }
  }
  if ($tsIp) {
    $env:EXPERIMENTS_UI_HMR_HOST = $tsIp
    Write-Host "EXPERIMENTS_UI_HMR_HOST=$tsIp (HMR WebSocket for remote devices)" -ForegroundColor DarkGray
  } else {
    Write-Host "Could not detect Tailscale IPv4. Set EXPERIMENTS_UI_HMR_HOST to your tailnet IP, e.g.:" -ForegroundColor Yellow
    Write-Host '  $env:EXPERIMENTS_UI_HMR_HOST = "100.x.y.z"' -ForegroundColor Yellow
    Write-Host "Then re-run with -Tailscale. HMR may not work on iPhone until this is set." -ForegroundColor Yellow
  }
  Write-Host "Tailscale mode: Vite listening on 0.0.0.0:$Port (reachable on tailnet)" -ForegroundColor Green
  if ($tsIp) {
    $openUrl = "http://${tsIp}:$Port"
    Write-Host "On iPhone (Tailscale VPN on): open $openUrl" -ForegroundColor Cyan
  } else {
    Write-Host "On iPhone: open http://<your-tailscale-ip>:$Port" -ForegroundColor Cyan
  }
} else {
  Write-Host "Starting Vite dev server (HMR) on http://127.0.0.1:$Port" -ForegroundColor Green
}

Write-Host "Proxying /api and /files to $Backend" -ForegroundColor DarkGray

if (-not $NoOpen) {
  Start-Process $openUrl
}

# NOTE: 127.0.0.1 avoids IPv6 localhost issues on desktop; 0.0.0.0 for Tailscale/LAN.
# Port comes from EXPERIMENTS_UI_DEV_PORT (see vite.config.ts) so HMR clientPort matches.
npm run dev -- --host $viteHost
