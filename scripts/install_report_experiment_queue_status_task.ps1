param(
  [string]$TaskName = 'ComfyUI-ReportExperimentQueueStatus',
  [int]$Minutes = 1,
  [string]$RunnerRelativePath = 'scripts\report_experiment_queue_status.ps1'
)

$ErrorActionPreference = 'Stop'

if ($Minutes -lt 1) { throw "Minutes must be >= 1" }

$repo = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $repo $RunnerRelativePath
if (!(Test-Path -LiteralPath $runner)) {
  throw "Missing runner script: $runner"
}

$ps = (Get-Command powershell).Source
$tr = "`"$ps`" -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runner`""

Write-Host "Creating/Updating scheduled task:"
Write-Host "  TaskName: $TaskName"
Write-Host "  Every:    $Minutes minute(s)"
Write-Host "  Command:  $tr"

schtasks /Create /F /SC MINUTE /MO $Minutes /TN $TaskName /TR $tr | Out-Host
$rc = $LASTEXITCODE
if ($rc -ne 0) {
  throw "schtasks /Create failed (exitCode=$rc)"
}
Write-Host "OK"

