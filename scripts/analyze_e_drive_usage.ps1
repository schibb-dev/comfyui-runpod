# Summarize E:\ top-level folder sizes (robocopy /L; same caveats as analyze_c_drive_usage.ps1).
# Run: powershell -ExecutionPolicy Bypass -File .\scripts\analyze_e_drive_usage.ps1
param(
    [string]$Root = 'E:\'
)
$ErrorActionPreference = 'SilentlyContinue'

function Get-FolderSizeGB([string]$path) {
    if (-not (Test-Path $path)) { return $null }
    $raw = cmd /c "robocopy `"$path`" `"$path`" /L /S /NJH /BYTES /NP /NDL /NFL /NC /NS 2>&1" | Out-String
    foreach ($line in ($raw -split "`n")) {
        if ($line -match '^\s+Bytes\s*:\s+(\d+)') {
            $num = [long]$Matches[1]
            if ($num -gt 0) { return [math]::Round($num / 1GB, 2) }
        }
    }
    return $null
}

$letter = [string]$Root[0]
$d = Get-PSDrive $letter
Write-Host "=== $Root summary ==="
if ($d) {
    Write-Host ("Free {0:N2} GB | Used {1:N2} GB | Total ~{2:N2} GB`n" -f ($d.Free/1GB), ($d.Used/1GB), (($d.Free+$d.Used)/1GB))
}

Write-Host "=== Top-level folders on $Root (GB, descending) ==="
if (-not (Test-Path $Root)) { Write-Host "Path missing: $Root"; exit 1 }

Get-ChildItem $Root -Force -ErrorAction SilentlyContinue | Where-Object { $_.PSIsContainer } | ForEach-Object {
    $gb = Get-FolderSizeGB $_.FullName
    if ($null -ne $gb) {
        [PSCustomObject]@{ GB = $gb; Folder = $_.Name; FullPath = $_.FullName }
    }
} | Sort-Object GB -Descending | Format-Table -AutoSize

Write-Host ""
Write-Host "Known comfy-related paths (check for overlap with Linux / C: clone):"
Write-Host "  E:\models                - model weights (often largest)"
Write-Host "  E:\comfyui-runpod-shadow - shadow workspace copy"
Write-Host "  E:\output                - top-level output tree if present"
Write-Host "Done."
