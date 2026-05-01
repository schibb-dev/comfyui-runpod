param(
  [string]$Services = "refresh_run_status report_experiment_queue_status queue_incomplete_experiments ws_event_tap",
  [switch]$Remove
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
Push-Location $repo
try {
  $docker = (Get-Command docker -ErrorAction Stop).Source
  $svc = @($Services.Split(' ', [System.StringSplitOptions]::RemoveEmptyEntries))
  if ($Remove) {
    & $docker compose --profile ops rm -fsv @svc
  } else {
    & $docker compose --profile ops stop @svc
  }
} finally {
  Pop-Location
}

