# Restart Docker stack in dependency order: stop pollers/WS tap -> restart ComfyUI -> bring satellites back.
# Run from repo root:  powershell -NoProfile -File .\scripts\restart_stack.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path (Split-Path $MyInvocation.MyCommand.Path))

Write-Host "=== 1/4 Stopping satellite services ===" -ForegroundColor Cyan
docker compose stop watch_queue ws_event_tap refresh_run_status report_experiment_queue_status queue_incomplete_experiments

Write-Host "=== 2/4 Restarting ComfyUI ===" -ForegroundColor Cyan
docker compose restart comfyui

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
  Write-Host "WARNING: /queue not ready; check: docker compose logs comfyui --tail 100" -ForegroundColor Yellow
}

Write-Host "=== 3/4 Starting watch_queue ===" -ForegroundColor Cyan
docker compose start watch_queue

Write-Host "=== 4/4 Starting ops services ===" -ForegroundColor Cyan
docker compose start ws_event_tap refresh_run_status report_experiment_queue_status queue_incomplete_experiments

Write-Host "=== Status ===" -ForegroundColor Green
docker compose ps -a --format "table {{.Name}}\t{{.Status}}\t{{.Service}}"
