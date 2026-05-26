param(
    [string]$HostName = "103.218.123.24",
    [string]$User = "root",
    [string]$RemoteDir = "/root/telegram_bot_updated_sepay/telegram_bot",
    [string]$Service = "telegram-bot"
)

$ErrorActionPreference = "Stop"

$localDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$target = "$User@$HostName"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$remoteBackup = "/root/telegram_bot_backup_$stamp"

Write-Host "Deploying Telegram bot to ${target}:$RemoteDir"
Write-Host "Remote backup: $remoteBackup"

ssh $target "set -e; mkdir -p '$remoteBackup'; cd '$RemoteDir'; cp -a bot.py db.py ui.py texts.py config.py payment_intake.py stock_texts.py broadcast_tools.py security_policy.py requirements.txt '$remoteBackup'/ 2>/dev/null || true; if [ -f shop.db ]; then cp -a shop.db '$remoteBackup'/shop.db; fi"

scp `
    (Join-Path $localDir "bot.py") `
    (Join-Path $localDir "db.py") `
    (Join-Path $localDir "ui.py") `
    (Join-Path $localDir "texts.py") `
    (Join-Path $localDir "config.py") `
    (Join-Path $localDir "payment_intake.py") `
    (Join-Path $localDir "stock_texts.py") `
    (Join-Path $localDir "broadcast_tools.py") `
    (Join-Path $localDir "security_policy.py") `
    (Join-Path $localDir "requirements.txt") `
    "${target}:$RemoteDir/"

ssh $target "set -e; cd '$RemoteDir'; rm -f sepay_webhook.py payment_webhook.py; if [ -x ./venv/bin/python ]; then ./venv/bin/python -m py_compile bot.py db.py ui.py texts.py config.py payment_intake.py stock_texts.py broadcast_tools.py security_policy.py; ./venv/bin/pip install -r requirements.txt; else python3 -m py_compile bot.py db.py ui.py texts.py config.py payment_intake.py stock_texts.py broadcast_tools.py security_policy.py; fi; systemctl stop '$Service' || true; PIDS=\$(ss -ltnp 'sport = :9000' 2>/dev/null | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | sort -u | tr '\n' ' '); if [ -n \"\$PIDS\" ]; then kill \$PIDS || true; sleep 2; fi; systemctl restart '$Service'; systemctl --no-pager --full status '$Service' | head -80"

Write-Host ""
Write-Host "Deploy complete."
Write-Host "Webhook URL: http://$HostName:9000/sepay"
Write-Host "Logs: ssh $target `"journalctl -u $Service -f`""
