param(
    [string]$HostName = "103.218.123.24",
    [string]$User = "root",
    [string]$RemoteDir = "/root/telegram_bot_updated_sepay/telegram_bot",
    [string]$Service = "telegram-bot"
)

$ErrorActionPreference = "Stop"
$Target = "$User@$HostName"

$RemoteCommand = @"
set -e
echo '===SERVICE_FILE==='
systemctl cat '$Service' --no-pager
echo '===STATUS==='
systemctl status '$Service' --no-pager | head -60
echo '===REMOTE_DIR_EXPECTED==='
cd '$RemoteDir'
pwd
ls -l bot.py payment_intake.py stock_texts.py 2>&1
echo '===CODE_MARKERS==='
grep -n 'stock_detail:\|Command("reseller"\|format_stock_detail_text\|payment_intake' bot.py stock_texts.py 2>&1 | head -40
echo '===HEALTH==='
curl -fsS http://127.0.0.1:9000/ || true
echo
echo '===LOGS==='
journalctl -u '$Service' -n 40 --no-pager
"@

ssh $Target $RemoteCommand
