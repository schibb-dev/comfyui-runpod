Get-PSDrive C, E | ForEach-Object {
    [PSCustomObject]@{
        Drive = $_.Name + ':'
        FreeGB = [math]::Round($_.Free / 1GB, 2)
        UsedGB = [math]::Round($_.Used / 1GB, 2)
        TotalGB = [math]::Round(($_.Free + $_.Used) / 1GB, 2)
    }
} | Format-Table -AutoSize

$dvhd = Join-Path $env:LOCALAPPDATA 'Docker\wsl\disk\docker_data.vhdx'
if (Test-Path $dvhd) {
    $i = Get-Item $dvhd
    Write-Host "Docker docker_data.vhdx: $([math]::Round($i.Length/1GB,2)) GB"
    Write-Host $i.FullName
} else {
    Write-Host "docker_data.vhdx not at default path (may already be relocated)."
}
