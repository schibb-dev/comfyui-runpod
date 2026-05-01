# Restart Docker stack in dependency order: stop pollers/WS tap -> restart ComfyUI -> bring satellites back.
# Run from repo root:  powershell -NoProfile -File .\scripts\restart_stack.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path (Split-Path $MyInvocation.MyCommand.Path))

# Match package.json / Makefile (project includes output-sftp compose overlay).
$dc = @("compose", "-f", "docker-compose.yml", "-f", "docker-compose.output-sftp.yml")
$ops = @(
  "ws_event_tap",
  "refresh_run_status",
  "report_experiment_queue_status",
  "queue_incomplete_experiments",
  "queue_ledger"
)

Write-Host "=== 1/4 Stopping satellite services ===" -ForegroundColor Cyan
docker @dc stop watch_queue @ops

Write-Host "=== 2/4 Restarting ComfyUI ===" -ForegroundColor Cyan
docker @dc restart comfyui

Write-Host "Waiting for ComfyUI http://127.0.0.1:8188/queue (up to 120s)..." -ForegroundColor Yellow
$ok = $false
for ($i = 0; $i -lt 40; $i++) {
  Start-Sleep -Seconds 3
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:8188/queue" -UseBasicParsing -TimeoutSec 5
    if ($r.StatusCode -eq 200) { $ok = $true; break }
  } catch { }
  Write-Host "  ... attempt $($i + 1)/40"
}

if (-not $ok) {
  Write-Host "WARNING: /queue not ready; check: docker compose -f docker-compose.yml -f docker-compose.output-sftp.yml logs comfyui --tail 100" -ForegroundColor Yellow
}

Write-Host "=== 3/4 Starting watch_queue ===" -ForegroundColor Cyan
docker @dc start watch_queue

Write-Host "=== 4/4 Starting ops services ===" -ForegroundColor Cyan
docker @dc start @ops

Write-Host "=== Status ===" -ForegroundColor Green
docker @dc ps -a --format "table {{.Name}}\t{{.Status}}\t{{.Service}}"
