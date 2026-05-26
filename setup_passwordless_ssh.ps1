param(
    [string]$HostName = "103.218.123.24",
    [string]$User = "root"
)

$ErrorActionPreference = "Stop"

$Target = "$User@$HostName"
$SshDir = Join-Path $env:USERPROFILE ".ssh"
$KeyPath = Join-Path $SshDir "telegram_bot_vps_ed25519"
$PubKeyPath = "$KeyPath.pub"

if (!(Test-Path $SshDir)) {
    New-Item -ItemType Directory -Path $SshDir | Out-Null
}

if (!(Test-Path $KeyPath)) {
    Write-Host "Creating SSH key: $KeyPath"
    cmd.exe /c "ssh-keygen -t ed25519 -f `"$KeyPath`" -N `"`" -C telegram-bot-deploy"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create SSH key"
    }
} else {
    Write-Host "SSH key already exists: $KeyPath"
}

$PublicKey = Get-Content -Raw -Path $PubKeyPath
$EscapedPublicKey = $PublicKey.Trim().Replace("'", "'\''")

Write-Host ""
Write-Host "Installing public key on $Target"
Write-Host "This setup step may ask for the VPS password once. Deploys after this should not ask."

ssh $Target "set -e; mkdir -p ~/.ssh; chmod 700 ~/.ssh; touch ~/.ssh/authorized_keys; grep -qxF '$EscapedPublicKey' ~/.ssh/authorized_keys || echo '$EscapedPublicKey' >> ~/.ssh/authorized_keys; chmod 600 ~/.ssh/authorized_keys"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install SSH key"
}

Write-Host ""
Write-Host "Testing passwordless SSH"
ssh -i $KeyPath -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new $Target "echo ssh-ok"
if ($LASTEXITCODE -ne 0) {
    throw "Passwordless SSH test failed"
}

Write-Host ""
Write-Host "Passwordless SSH is ready."
Write-Host "Use this key with: ssh -i `"$KeyPath`" $Target"
