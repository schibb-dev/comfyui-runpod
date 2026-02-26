# Scan custom_nodes/ for local git changes (nodes that have their own .git).
# Run from repo root: .\scripts\scan_custom_nodes_git_status.ps1
# Requires: git

$ErrorActionPreference = 'Stop'
$repoRoot = if ($PSScriptRoot) { Resolve-Path (Join-Path $PSScriptRoot '..') } else { Get-Location }
$customNodesDir = Join-Path $repoRoot 'custom_nodes'
if (-not (Test-Path $customNodesDir)) {
    Write-Output "custom_nodes/ not found at $customNodesDir"
    exit 0
}

$dirs = Get-ChildItem -LiteralPath $customNodesDir -Directory | Where-Object {
    $_.Name -notmatch '^\.' -and $_.Name -ne '__pycache__'
}

Write-Output "Scanning custom nodes (nodes with their own .git)"
Write-Output ""

foreach ($d in $dirs) {
    $gitDir = Join-Path $d.FullName '.git'
    if (-not (Test-Path $gitDir)) { continue }

    $name = $d.Name
    Push-Location $d.FullName | Out-Null
    try {
        $status = git status --short 2>&1 | Out-String
        $branch = git rev-parse --abbrev-ref HEAD 2>$null
        $upstream = git rev-parse --abbrev-ref '@{u}' 2>$null
        $ahead = git rev-list --count '@{u}..HEAD' 2>$null
        $behind = git rev-list --count 'HEAD..@{u}' 2>$null
        if (-not $upstream) { $ahead = $null; $behind = $null }

        $hasModified = $status -match '^\s*[MADRC]'
        $hasUntracked = $status -match '^\s*\?\?'
        $modifiedCount = ([regex]::Matches($status, '^\s*[MADRC]', 'Multiline')).Count
        $untrackedCount = ([regex]::Matches($status, '^\s*\?\?', 'Multiline')).Count

        $summary = "branch=$branch"
        if ($upstream) { $summary += " upstream=$upstream ahead=$ahead behind=$behind" }
        if ($hasModified) { $summary += " | MODIFIED: $modifiedCount file(s)" }
        if ($hasUntracked) { $summary += " | UNTRACKED: $untrackedCount" }
        if (-not $hasModified -and -not $hasUntracked) { $summary += " | clean" }

        Write-Output "=== $name ==="
        Write-Output "  $summary"
        if ($hasModified -and $modifiedCount -le 20) {
            git status --short 2>$null | Where-Object { $_ -match '^\s*[MADRC]' } | ForEach-Object { Write-Output "    $_" }
        } elseif ($hasModified) {
            git status --short 2>$null | Where-Object { $_ -match '^\s*[MADRC]' } | Select-Object -First 12 | ForEach-Object { Write-Output "    $_" }
            Write-Output "    ... and $($modifiedCount - 12) more"
        }
        Write-Output ""
    } finally {
        Pop-Location | Out-Null
    }
}

Write-Output "Done. Nodes without a .git (e.g. ComfyUI-Custom-Scripts) are not clones; they were/are tracked by the parent repo only."
