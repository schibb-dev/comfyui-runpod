param(
  [string]$TaskName = 'ComfyUI-QueueIncompleteExperiments',
  [int]$Minutes = 10,
  [string]$Server = 'http://127.0.0.1:8188'
)

$ErrorActionPreference = 'Stop'

if ($Minutes -lt 1) { throw "Minutes must be >= 1" }

$repo = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $repo 'scripts\queue_incomplete_experiments.ps1'
if (!(Test-Path -LiteralPath $runner)) {
  throw "Missing runner script: $runner"
}

# Build an schtasks command that:
# - runs as current user
# - starts in repo root via the wrapper
# - runs every N minutes
$ps = (Get-Command powershell).Source
$tr = "`"$ps`" -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runner`" --server `"$Server`""

Write-Host "Creating/Updating scheduled task:"
Write-Host "  TaskName: $TaskName"
Write-Host "  Every:    $Minutes minute(s)"
Write-Host "  Command:  $tr"

schtasks /Create /F /SC MINUTE /MO $Minutes /TN $TaskName /TR $tr | Out-Host
Write-Host "OK"

