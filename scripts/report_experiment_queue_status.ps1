param(
  # Container to run the report inside (recommended).
  [string]$ContainerName = 'comfyui0-watch-queue',

  # ComfyUI server URL as seen FROM INSIDE the container.
  # (In compose, the comfyui service is reachable as http://comfyui:8188.)
  [string]$Server = 'http://comfyui:8188',

  # How many experiments to include.
  [int]$Limit = 10,

  # Append output to this log file (host path). If empty, uses a default under workspace output.
  [string]$LogPath = ''
)

$ErrorActionPreference = 'Stop'

# Wrapper to run the reporter with a stable working directory.
$repo = Split-Path -Parent $PSScriptRoot

if ([string]::IsNullOrWhiteSpace($LogPath)) {
  $LogDir = Join-Path $repo 'workspace\output\output\experiments\_status'
  New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
  $LogPath = Join-Path $LogDir 'queue_status.log'
} else {
  $parent = Split-Path -Parent $LogPath
  if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
}

$ts = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')

# Resolve docker executable (scheduled tasks may have a minimal PATH).
$docker = (Get-Command docker -ErrorAction Stop).Source

# Run the report inside the watcher container so we don't depend on host Python env.
$cmd = @(
  $docker, 'exec', $ContainerName,
  'python3', '/workspace/ws_scripts/report_experiment_queue_status.py',
  '--server', $Server,
  '--newest-first',
  '--limit', "$Limit",
  '--summary-only'
)

$out = & $cmd[0] $cmd[1..($cmd.Length-1)] 2>&1 | Out-String
$rc = $LASTEXITCODE

$header = "[$ts] rc=$rc"
Add-Content -LiteralPath $LogPath -Value $header
Add-Content -LiteralPath $LogPath -Value $out.TrimEnd()
Add-Content -LiteralPath $LogPath -Value ""

# Also echo to stdout for interactive use.
Write-Output $header
Write-Output $out.TrimEnd()

exit $rc

