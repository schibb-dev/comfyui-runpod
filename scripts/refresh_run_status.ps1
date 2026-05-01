$ErrorActionPreference = 'Stop'

# Wrapper to run the refresher with a stable working directory.
$repo = Split-Path -Parent $PSScriptRoot
$py = Join-Path $repo 'workspace\scripts\refresh_run_status.py'

if (!(Test-Path -LiteralPath $py)) {
  throw "Missing script: $py"
}

Push-Location $repo
try {
  python $py @args
} finally {
  Pop-Location
}

