Param(
  # Default: comfyui Experiments API on the host (docker maps 8790). Use 8791 when pairing with a local-only API.
  [string]$Backend = "http://127.0.0.1:8790",
  [int]$Port = 5178,
  [switch]$NoOpen,
  [switch]$EnsureContainer,
  # Bind Vite to 127.0.0.1 only (no remote Tailscale/LAN). Default is 0.0.0.0 for phone testing.
  [switch]$LocalOnly,
  # Optional: same remote-friendly defaults as without this switch; kept for scripts that pass -Tailscale.
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

$env:EXPERIMENTS_UI_DEV_PORT = "$Port"
$openUrl = "http://127.0.0.1:$Port"

if ($LocalOnly) {
  $viteHost = "127.0.0.1"
  Remove-Item Env:EXPERIMENTS_UI_HMR_HOST -ErrorAction SilentlyContinue
  $openUrl = "http://127.0.0.1:$Port"
  Write-Host "Vite (local only): http://127.0.0.1:$Port" -ForegroundColor Green
  if ($Tailscale) {
    Write-Host "Note: -Tailscale is ignored with -LocalOnly." -ForegroundColor Yellow
  }
} else {
  $viteHost = "0.0.0.0"
  $tsIp = $null
  $hmrEnv = $env:EXPERIMENTS_UI_HMR_HOST
  if ($hmrEnv) { $tsIp = $hmrEnv.Trim() }
  if (-not $tsIp -and (Get-Command tailscale -ErrorAction SilentlyContinue)) {
    try {
      $lines = & tailscale ip -4 2>$null
      if ($lines) { $tsIp = ($lines | Select-Object -First 1).ToString().Trim() }
    } catch { }
  }
  if ($tsIp) {
    $env:EXPERIMENTS_UI_HMR_HOST = $tsIp
    Write-Host "EXPERIMENTS_UI_HMR_HOST=$tsIp (HMR WebSocket for remote devices)" -ForegroundColor DarkGray
    $openUrl = "http://${tsIp}:$Port"
  } else {
    Remove-Item Env:EXPERIMENTS_UI_HMR_HOST -ErrorAction SilentlyContinue
    Write-Host "Set EXPERIMENTS_UI_HMR_HOST to this PC's Tailscale IP for HMR on your phone, e.g.:" -ForegroundColor Yellow
    Write-Host '  $env:EXPERIMENTS_UI_HMR_HOST = "100.x.y.z"' -ForegroundColor Yellow
  }
  $tag = if ($Tailscale) { "Tailscale" } else { "Remote-friendly" }
  Write-Host "$tag : Vite on 0.0.0.0:$Port (open http://<tailnet-or-LAN-ip>:$Port from the phone)" -ForegroundColor Green
  if ($tsIp) {
    Write-Host "Phone URL: $openUrl" -ForegroundColor Cyan
  }
}

Write-Host "Proxying /api and /files to $Backend" -ForegroundColor DarkGray

if (-not $NoOpen) {
  Start-Process $openUrl
}

# Default 0.0.0.0 for Tailscale/LAN phones; use -LocalOnly for 127.0.0.1 only.
# Port comes from EXPERIMENTS_UI_DEV_PORT (see vite.config.ts) so HMR clientPort matches.
npm run dev -- --host $viteHost
