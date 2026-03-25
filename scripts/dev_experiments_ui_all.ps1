$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$webDir = Join-Path $repoRoot "workspace\experiments_ui\web"
$apiScript = Join-Path $repoRoot "scripts\experiments_ui_server.py"

if (-not (Test-Path $webDir)) {
  throw "Web UI directory not found: $webDir"
}
if (-not (Test-Path $apiScript)) {
  throw "API server script not found: $apiScript"
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

  Write-Host "[ui:dev:all] starting Vite dev server"
  Set-Location $webDir
  npm run dev
}
finally {
  if ($apiProc -and -not $apiProc.HasExited) {
    Write-Host "[ui:dev:all] stopping backend API (pid=$($apiProc.Id))"
    Stop-Process -Id $apiProc.Id -Force -ErrorAction SilentlyContinue
  }
}

