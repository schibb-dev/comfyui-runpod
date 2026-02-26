$ErrorActionPreference = 'Stop'

# Wrapper to run the periodic-safe queuer with a stable working directory.
$repo = Split-Path -Parent $PSScriptRoot
$py = Join-Path $repo 'workspace\scripts\queue_incomplete_experiments.py'

if (!(Test-Path -LiteralPath $py)) {
  throw "Missing script: $py"
}

Push-Location $repo
try {
  python $py @args
} finally {
  Pop-Location
}

