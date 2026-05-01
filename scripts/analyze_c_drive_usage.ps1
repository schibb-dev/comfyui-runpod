# C: space audit — fast-ish via robocopy listing mode for folder totals.
# Run: powershell -ExecutionPolicy Bypass -File .\scripts\analyze_c_drive_usage.ps1
$ErrorActionPreference = 'SilentlyContinue'

function Get-FolderSizeGB([string]$path) {
    if (-not (Test-Path $path)) { return $null }
    # robocopy same source/dest = metadata only; /BYTES totals files
    $raw = cmd /c "robocopy `"$path`" `"$path`" /L /S /NJH /BYTES /NP /NDL /NFL /NC /NS 2>&1" | Out-String
    # Must use ONLY the first integer on the Bytes summary line (total bytes). Concatenating
    # all digit columns produced bogus terabyte-scale numbers.
    foreach ($line in ($raw -split "`n")) {
        if ($line -match '^\s+Bytes\s*:\s+(\d+)') {
            $num = [long]$Matches[1]
            if ($num -gt 0) { return [math]::Round($num / 1GB, 2) }
        }
    }
    return $null
}

$c = Get-PSDrive C
if (-not $c) { Write-Host 'No C: drive'; exit 1 }
$totalGB = [math]::Round(($c.Free + $c.Used) / 1GB, 2)
$freeGB = [math]::Round($c.Free / 1GB, 2)
$usedGB = [math]::Round($c.Used / 1GB, 2)
Write-Host "=== C: summary ==="
Write-Host ("Total ~ {0} GB | Used ~ {1} GB | Free ~ {2} GB ({3}% free)" -f $totalGB, $usedGB, $freeGB, [math]::Round(100 * $c.Free / ($c.Free + $c.Used), 1))

Write-Host "`n=== Top folders under C:\Users\$env:USERNAME (GB, robocopy) ==="
$userRoot = "C:\Users\$env:USERNAME"
if (Test-Path $userRoot) {
    Get-ChildItem $userRoot -Directory -Force | ForEach-Object {
        $gb = Get-FolderSizeGB $_.FullName
        if ($null -ne $gb) {
            [PSCustomObject]@{ GB = $gb; Folder = $_.Name }
        }
    } | Sort-Object GB -Descending | Format-Table -AutoSize
}

Write-Host "=== AppData\Local one-offs (GB) ==="
$la = "$env:LOCALAPPDATA"
@(
    @{ Name = 'Docker'; Path = Join-Path $la 'Docker' },
    @{ Name = 'npm-cache'; Path = Join-Path $la 'npm-cache' },
    @{ Name = 'pnpm'; Path = Join-Path $la 'pnpm' },
    @{ Name = 'Temp'; Path = $env:TEMP }
) | ForEach-Object {
    $gb = Get-FolderSizeGB $_.Path
    if ($null -ne $gb -and $gb -gt 0.01) {
        [PSCustomObject]@{ GB = $gb; Path = $_.Name }
    }
} | Sort-Object GB -Descending | Format-Table -AutoSize

Write-Host "=== WSL ext4.vhdx under Packages (Store installs only) ==="
Write-Host "(Docker/WSL2 disks may live under Local\Docker or Local\wsl — run scripts/quick_c_audit.ps1)"
Get-ChildItem -Path "$env:LOCALAPPDATA\Packages" -Filter 'ext4.vhdx' -Recurse -ErrorAction SilentlyContinue |
    ForEach-Object {
        [PSCustomObject]@{
            GB   = [math]::Round($_.Length / 1GB, 2)
            File = $_.FullName
        }
    } | Sort-Object GB -Descending | Format-Table -Wrap

Write-Host "=== Largest Microsoft Store package folders (top 15, GB) ==="
$pkgRoot = Join-Path $env:LOCALAPPDATA 'Packages'
if (Test-Path $pkgRoot) {
    Get-ChildItem $pkgRoot -Directory | ForEach-Object {
        $gb = Get-FolderSizeGB $_.FullName
        if ($null -ne $gb) {
            [PSCustomObject]@{ GB = $gb; Package = $_.Name }
        }
    } | Sort-Object GB -Descending | Select-Object -First 15 | Format-Table -AutoSize
}

Write-Host "`nTip: Packages growth is often WSL (ext4.vhdx) or games; Docker Desktop uses Local\Docker."
Write-Host "Done."
