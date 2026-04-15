Param(
  [int]$Port = 5178,
  [switch]$NoOpen,
  # Same as dev_experiments_ui.ps1: listen on 0.0.0.0 + HMR host for iPhone via Tailscale.
  [switch]$Tailscale
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$webDir = Join-Path $repoRoot "workspace\experiments_ui\web"
$apiScript = Join-Path $repoRoot "scripts\experiments_ui_server.py"
$viteScript = Join-Path $PSScriptRoot "dev_experiments_ui.ps1"

if (-not (Test-Path $webDir)) {
  throw "Web UI directory not found: $webDir"
}
if (-not (Test-Path $apiScript)) {
  throw "API server script not found: $apiScript"
}
if (-not (Test-Path $viteScript)) {
  throw "Vite helper not found: $viteScript"
}

# Use 8791 on the host so we do not collide with Docker mapping host:8790 -> container Experiments UI.
Write-Host "[ui:dev:all] starting backend API at http://127.0.0.1:8791 (Vite proxies here by default)"
$apiProc = Start-Process `
  -FilePath "python" `
  -ArgumentList "`"$apiScript`" --host 127.0.0.1 --port 8791" `
  -WorkingDirectory $repoRoot `
  -PassThru

try {
  Start-Sleep -Seconds 1
  if ($apiProc.HasExited) {
    throw "Backend API exited early with code $($apiProc.ExitCode)"
  }

  Write-Host "[ui:dev:all] starting Vite via $viteScript" -ForegroundColor Green
  if ($Tailscale) {
    & $viteScript -Port $Port -Backend "http://127.0.0.1:8791" -NoOpen:$NoOpen -Tailscale
  } else {
    & $viteScript -Port $Port -Backend "http://127.0.0.1:8791" -NoOpen:$NoOpen
  }
}
finally {
  if ($apiProc -and -not $apiProc.HasExited) {
    Write-Host "[ui:dev:all] stopping backend API (pid=$($apiProc.Id))"
    Stop-Process -Id $apiProc.Id -Force -ErrorAction SilentlyContinue
  }
}
