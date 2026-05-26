param(
    [string]$HostName = "103.218.123.24",
    [string]$User = "root",
    [string]$Service = "telegram-bot",
    [int]$Port = 9000
)

$ErrorActionPreference = "Stop"
$Target = "$User@$HostName"

$RemoteCommand = @"
set -e
echo '===PORT_LISTENER_BEFORE==='
ss -ltnp "sport = :$Port" || true

PIDS=`$(ss -ltnp "sport = :$Port" 2>/dev/null | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | sort -u | tr '\n' ' ')
if [ -n "`$PIDS" ]; then
  echo '===KILLING_PORT_9000_PIDS==='
  for PID in `$PIDS; do
    ps -fp "`$PID" || true
  done
  kill `$PIDS || true
  sleep 2
  for PID in `$PIDS; do
    if kill -0 "`$PID" 2>/dev/null; then
      kill -9 "`$PID" || true
    fi
  done
else
  echo 'No process is listening on port $Port.'
fi

echo '===PORT_LISTENER_AFTER_KILL==='
ss -ltnp "sport = :$Port" || true

echo '===RESTART_SERVICE==='
systemctl reset-failed '$Service' || true
systemctl restart '$Service'
sleep 4

echo '===STATUS==='
systemctl status '$Service' --no-pager | head -80

echo '===PORT_LISTENER_AFTER_RESTART==='
ss -ltnp "sport = :$Port" || true

echo '===HEALTH==='
curl -fsS "http://127.0.0.1:$Port/" || true
echo

echo '===RECENT_LOGS==='
journalctl -u '$Service' -n 40 --no-pager
"@

ssh $Target $RemoteCommand
