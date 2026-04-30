Param(
  [int]$Port = 5178,
  [switch]$NoOpen,
  # Same as dev_experiments_ui.ps1: listen on 0.0.0.0 + HMR host for iPhone via Tailscale.
  [switch]$Tailscale,
  [switch]$EnsureContainer
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $repoRoot
try {
  $nodeArgs = @("scripts/experiments-ui-dev.mjs", "all")
  if ($Tailscale) { $nodeArgs += "--tailscale" }
  if ($NoOpen) { $nodeArgs += "--no-open" }
  if ($EnsureContainer) { $nodeArgs += "--ensure-container" }
  if ($Port -ne 5178) {
    $nodeArgs += "--port"
    $nodeArgs += "$Port"
  }
  & node @nodeArgs
  exit $LASTEXITCODE
}
finally {
  Pop-Location
}
