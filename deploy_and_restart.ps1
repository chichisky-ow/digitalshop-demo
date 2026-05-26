param(
    [string]$HostName = "103.218.123.24",
    [string]$User = "root",
    [string]$RemoteDir = "/root/telegram_bot_updated_sepay/telegram_bot",
    [string]$Service = "telegram-bot",
    [int]$Port = 9000
)

$ErrorActionPreference = "Stop"

$LocalDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Target = "$User@$HostName"
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$RemoteBackup = "/root/telegram_bot_backup_$Stamp"
$SshOptions = @(
    "-i", (Join-Path $env:USERPROFILE ".ssh\telegram_bot_vps_ed25519"),
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=accept-new"
)

function Invoke-Checked {
    param(
        [string]$Title,
        [scriptblock]$Command
    )

    Write-Host ""
    Write-Host "==> $Title"
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Title failed with exit code $LASTEXITCODE"
    }
}

Set-Location $LocalDir

Invoke-Checked "Checking passwordless SSH" {
    ssh @SshOptions $Target "echo ssh-ok"
}

Invoke-Checked "Running local syntax checks" {
        python -m py_compile bot.py db.py ui.py texts.py products.py config.py payment_intake.py stock_texts.py broadcast_tools.py security_policy.py
}

if (Test-Path (Join-Path $LocalDir "tests")) {
    Invoke-Checked "Running local tests" {
        python -m unittest discover -s tests
    }
}

Invoke-Checked "Preparing remote backup and directory" {
    ssh @SshOptions $Target "set -e; mkdir -p '$RemoteDir' '$RemoteBackup'; if [ -d '$RemoteDir' ]; then cd '$RemoteDir'; cp -a bot.py db.py ui.py texts.py products.py config.py payment_intake.py stock_texts.py broadcast_tools.py security_policy.py requirements.txt '$RemoteBackup'/ 2>/dev/null || true; if [ -f shop.db ]; then cp -a shop.db '$RemoteBackup'/shop.db; fi; if [ -f .env ]; then cp -a .env '$RemoteBackup'/.env; fi; fi"
}

$Files = @(
    "bot.py",
    "db.py",
    "ui.py",
    "texts.py",
    "products.py",
    "config.py",
    "payment_intake.py",
    "stock_texts.py",
    "broadcast_tools.py",
    "security_policy.py",
    "requirements.txt",
    "README.md",
    "README_MAINTENANCE.md"
)

$ExistingFiles = $Files | Where-Object { Test-Path (Join-Path $LocalDir $_) } | ForEach-Object { Join-Path $LocalDir $_ }

Invoke-Checked "Uploading source files" {
    scp @SshOptions $ExistingFiles "${Target}:$RemoteDir/"
}

Invoke-Checked "Installing dependencies, compiling, and restarting service" {
    ssh @SshOptions $Target "set -e; cd '$RemoteDir'; rm -f sepay_webhook.py payment_webhook.py; if [ -x ./venv/bin/python ]; then ./venv/bin/pip install -r requirements.txt; ./venv/bin/python -m py_compile bot.py db.py ui.py texts.py products.py config.py payment_intake.py stock_texts.py broadcast_tools.py security_policy.py; else python3 -m pip install -r requirements.txt; python3 -m py_compile bot.py db.py ui.py texts.py products.py config.py payment_intake.py stock_texts.py broadcast_tools.py security_policy.py; fi; systemctl restart '$Service'"
}

Invoke-Checked "Checking service status" {
    ssh @SshOptions $Target "systemctl is-active '$Service'; systemctl --no-pager --full status '$Service' | head -80"
}

Invoke-Checked "Checking local VPS health endpoint" {
    ssh @SshOptions $Target "curl -fsS 'http://127.0.0.1:$Port/'"
}

Write-Host ""
Write-Host "Deploy complete."
Write-Host "Webhook URL: http://$HostName`:$Port/sepay"
Write-Host "Recent logs command:"
Write-Host "ssh $Target `"journalctl -u $Service -n 80 --no-pager`""
