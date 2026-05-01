# Copy your SSH public key to aritomo-thinkstation-p920 so you can log in without a password.
# Run from PowerShell: .\scripts\install_remote_ssh_key.ps1
# Or: pwsh -File scripts\install_remote_ssh_key.ps1

$ErrorActionPreference = "Stop"
$keyPath = "$env:USERPROFILE\.ssh\id_ed25519.pub"
$remote = "yuji@aritomo-thinkstation-p920"

if (-not (Test-Path $keyPath)) {
    $keyPath = "$env:USERPROFILE\.ssh\id_rsa.pub"
}
if (-not (Test-Path $keyPath)) {
    Write-Error "No public key found. Expected id_ed25519.pub or id_rsa.pub in $env:USERPROFILE\.ssh"
}

Write-Host "Using key: $keyPath"
Write-Host "Installing to $remote (you may be prompted for your Linux password once)..."
Get-Content $keyPath | & "$env:WINDIR\System32\OpenSSH\ssh.exe" $remote "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
Write-Host "Done. Test with: ssh $remote"
