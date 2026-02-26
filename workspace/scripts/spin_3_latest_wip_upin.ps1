$ErrorActionPreference = 'Stop'

$wipRoot = 'workspace/output/output/wip'
if (-not (Test-Path $wipRoot)) {
  $wipRoot = 'output/output/wip'
}
if (-not (Test-Path $wipRoot)) {
  throw "No wip root found at output/output/wip or workspace/output/output/wip"
}

Write-Host ("WIP_ROOT: {0}" -f (Resolve-Path $wipRoot))

# Pick the latest 3 date directories by LastWriteTime
$latestDirs = Get-ChildItem -Path $wipRoot -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 3

if (-not $latestDirs -or $latestDirs.Count -lt 3) {
  throw "Need at least 3 wip subdirectories under $wipRoot"
}

$picks = @()
foreach ($d in $latestDirs) {
  $upins = Get-ChildItem -Path $d.FullName -File -Filter '*UPIN*.mp4' -ErrorAction SilentlyContinue
  if (-not $upins -or $upins.Count -lt 1) {
    throw ("No UPIN mp4 files found under {0}" -f $d.FullName)
  }
  $pick = $upins | Get-Random -Count 1
  $picks += [pscustomobject]@{
    wip_dir = $d.FullName
    mp4 = $pick.FullName
  }
}

Write-Host "PICKS:"
$picks | Format-Table -AutoSize

# Generate 3 experiment sweeps (core defaults) into workspace/output/output/experiments
$outRoot = 'workspace/output/output/experiments'
New-Item -ItemType Directory -Force -Path $outRoot | Out-Null

$expDirs = @()
foreach ($p in $picks) {
  $seed = Get-Random -Minimum 100000000 -Maximum 2000000000
  $args = @(
    'workspace/scripts/comfy_tool.py', 'tune-sweep', '--',
    $p.mp4,
    '--out-root', $outRoot,
    '--exp-id', ('spin_' + [System.IO.Path]::GetFileNameWithoutExtension($p.mp4) + '_' + (Get-Date -Format 'yyyyMMdd-HHmmss')),
    '--seed', $seed,
    '--duration', '2.0',
    '--defaults', 'core'
  )
  Write-Host ("GEN: python {0}" -f ($args -join ' '))
  $exp = & python @args
  if ($LASTEXITCODE -ne 0) { throw "generate failed for $($p.mp4)" }
  $expDirs += $exp.Trim()
}

Write-Host "EXPERIMENT_DIRS:"
$expDirs | ForEach-Object { Write-Host $_ }

# Submit runs asynchronously and collect histories (will run until done)
# Limit to newest 3 experiments to avoid touching older ones.
Write-Host "WATCH_QUEUE: submitting & collecting histories"
python workspace/scripts/watch_queue.py $outRoot --limit-experiments 3 --max-inflight 12 --poll 2.0

