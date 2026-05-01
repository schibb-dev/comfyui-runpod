# Minimal audit — ext4.vhdx + a few heavy paths
$ErrorActionPreference = 'SilentlyContinue'
$c = Get-PSDrive C
Write-Host ("C: Free {0:N2} GB of {1:N2} GB`n" -f ($c.Free/1GB), (($c.Free+$c.Used)/1GB))

Write-Host "=== ext4.vhdx (WSL disks) ==="
Get-ChildItem "$env:LOCALAPPDATA\Packages" -Filter ext4.vhdx -Recurse |
    ForEach-Object { '{0:N2} GB  {1}' -f ($_.Length/1GB), $_.FullName }

Write-Host "`n=== ProgramData\Docker (if exists) ==="
$d = 'C:\ProgramData\Docker'
if (Test-Path $d) {
    cmd /c "dir `"$d`" /s /-c" | Select-Object -Last 4
}

Write-Host "`n=== Local\Docker folder ==="
$ld = Join-Path $env:LOCALAPPDATA 'Docker'
if (Test-Path $ld) {
    cmd /c "dir `"$ld`" /s /-c" | Select-Object -Last 4
}

Write-Host "`n=== All .vhdx under Local AppData (WSL / VMs) ==="
Get-ChildItem $env:LOCALAPPDATA -Filter '*.vhdx' -Recurse -ErrorAction SilentlyContinue |
    ForEach-Object { '{0:N2} GB  {1}' -f ($_.Length/1GB), $_.FullName }
