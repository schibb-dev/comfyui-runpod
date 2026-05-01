param(
  [string]$Services = "refresh_run_status report_experiment_queue_status queue_incomplete_experiments ws_event_tap"
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
Push-Location $repo
try {
  $docker = (Get-Command docker -ErrorAction Stop).Source
  & $docker compose --profile ops up -d @($Services.Split(' ', [System.StringSplitOptions]::RemoveEmptyEntries))
} finally {
  Pop-Location
}

