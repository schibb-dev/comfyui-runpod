# Stop Python processes running sort_unsorted_models.py (PowerShell).
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ForEach-Object {
  $cl = $_.CommandLine
  if ($cl -and ($cl -match 'sort_unsorted_models')) {
    Write-Host "Stopping PID $($_.ProcessId)"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
}
