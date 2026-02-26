param(
  [string]$TaskName = 'ComfyUI-RefreshRunStatus',
  [int]$Minutes = 1,
  [string]$Server = 'http://127.0.0.1:8188'
)

$ErrorActionPreference = 'Stop'

if ($Minutes -lt 1) { throw "Minutes must be >= 1" }

$repo = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $repo 'scripts\refresh_run_status.ps1'
if (!(Test-Path -LiteralPath $runner)) {
  throw "Missing runner script: $runner"
}

$ps = (Get-Command powershell).Source
$tr = "`"$ps`" -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runner`" --server `"$Server`""

Write-Host "Creating/Updating scheduled task:"
Write-Host "  TaskName: $TaskName"
Write-Host "  Every:    $Minutes minute(s)"
Write-Host "  Command:  $tr"

schtasks /Create /F /SC MINUTE /MO $Minutes /TN $TaskName /TR $tr | Out-Host
Write-Host "OK"

