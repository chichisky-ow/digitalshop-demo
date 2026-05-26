# README bảo trì bot Telegram

File này dành cho người hỗ trợ tiếp tục phát triển bot và deploy lên VPS.

## 1. Vị trí source

Source local trên Windows:

```text
C:\Users\Administrator\Downloads\telegram_bot_updated_sepay\telegram_bot
```

Source đang chạy trên VPS:

```text
/root/telegram_bot_updated_sepay/telegram_bot
```

VPS:

```text
103.218.123.24
```

Service systemd:

```text
telegram-bot
```

## 2. File quan trọng

```text
bot.py              Logic chính: Telegram handlers, webhook SePay, admin commands.
db.py               SQLite helpers, schema migration, orders, keys, products, stats.
ui.py               Inline keyboard buttons.
texts.py            Nội dung tin nhắn user.
products.py         Danh sách sản phẩm mặc định để seed lần đầu.
config.py           Đọc biến môi trường từ .env.
requirements.txt    Python dependencies.
shop.db             Database SQLite.
.env                Token bot, bank config, SePay secret.
```

Không cần lưu:

```text
__pycache__/
*.pyc
venv/
.venv/
```

Lý do: cache/venv có thể tạo lại trên VPS. Venv Windows không dùng được ổn định trên Ubuntu.

## 3. Chạy kiểm tra local trước khi upload

Mở PowerShell tại thư mục local:

```powershell
cd "C:\Users\Administrator\Downloads\telegram_bot_updated_sepay\telegram_bot"
python -m py_compile bot.py db.py ui.py texts.py products.py config.py
```

Nếu máy Windows không có `python`, thử:

```powershell
python3 -m py_compile bot.py db.py ui.py texts.py products.py config.py
```

## 4. Upload file lên VPS

Sau khi sửa code, upload các file đã sửa.

Ví dụ upload các file chính:

```powershell
scp "C:\Users\Administrator\Downloads\telegram_bot_updated_sepay\telegram_bot\bot.py" root@103.218.123.24:/root/telegram_bot_updated_sepay/telegram_bot/bot.py
scp "C:\Users\Administrator\Downloads\telegram_bot_updated_sepay\telegram_bot\db.py" root@103.218.123.24:/root/telegram_bot_updated_sepay/telegram_bot/db.py
scp "C:\Users\Administrator\Downloads\telegram_bot_updated_sepay\telegram_bot\ui.py" root@103.218.123.24:/root/telegram_bot_updated_sepay/telegram_bot/ui.py
scp "C:\Users\Administrator\Downloads\telegram_bot_updated_sepay\telegram_bot\texts.py" root@103.218.123.24:/root/telegram_bot_updated_sepay/telegram_bot/texts.py
```

Nếu sửa thêm file khác thì upload thêm file đó.

## 5. Restart bot trên VPS

SSH vào VPS:

```powershell
ssh root@103.218.123.24
```

Restart:

```bash
systemctl restart telegram-bot
systemctl status telegram-bot
```

Xem log realtime:

```bash
journalctl -u telegram-bot -f
```

Thoát log:

```text
Ctrl + C
```

## 6. Test nhanh sau deploy

Trong Telegram, admin test:

```text
/start
/admin
/stats
/pending
/stock
/sepaylog
```

Nếu sửa sản phẩm:

```text
/products
```

Nếu sửa webhook/thanh toán:

```text
/orders
/find MUA XXXXXXXX
```

## 7. Webhook SePay

Endpoint bot:

```text
http://103.218.123.24:9000/sepay
```

Health check:

```text
http://103.218.123.24:9000/
```

Auth đang dùng:

```text
HMAC-SHA256
```

Biến trong `.env`:

```env
SEPAY_WEBHOOK_SECRET=...
```

Bot kiểm tra header:

```text
x-sepay-signature
x-sepay-timestamp
```

Log webhook:

```bash
journalctl -u telegram-bot --since "30 minutes ago" | grep -E "SePay webhook|POST /sepay|Kết quả xử lý|Không tìm thấy|Số tiền chưa đủ"
```

Nếu SePay test webhook vào đúng nhưng bank thật không nhả key:

1. Kiểm tra giao dịch thật có xuất hiện trong SePay không.
2. Kiểm tra nội dung chuyển khoản có chứa đúng `MUA XXXXXXXX` không.
3. Kiểm tra số tiền đủ không.
4. Kiểm tra sản phẩm còn key không: `/stock mã_sản_phẩm`.
5. Kiểm tra log: `/sepaylog`.

## 8. Database và backup

Database:

```text
/root/telegram_bot_updated_sepay/telegram_bot/shop.db
```

Backup từ Telegram:

```text
/backup
```

Backup từ VPS:

```bash
cp /root/telegram_bot_updated_sepay/telegram_bot/shop.db /root/shop_backup_$(date +%Y%m%d_%H%M%S).db
```

Trước khi sửa lớn, nên backup `shop.db`.

## 9. Cài lại dependencies trên VPS nếu cần

Nếu upload sang VPS mới:

```bash
cd /root/telegram_bot_updated_sepay/telegram_bot
python3 -m venv venv
./venv/bin/pip install -U pip
./venv/bin/pip install -r requirements.txt
```

Nếu service chưa tồn tại, tạo:

```bash
cat >/etc/systemd/system/telegram-bot.service <<'EOF'
[Unit]
Description=Telegram Sales Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/telegram_bot_updated_sepay/telegram_bot
ExecStart=/root/telegram_bot_updated_sepay/telegram_bot/venv/bin/python /root/telegram_bot_updated_sepay/telegram_bot/bot.py
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable telegram-bot
systemctl start telegram-bot
```

## 10. Quy tắc khi thêm chức năng

- Sửa local trước, không sửa trực tiếp trên VPS nếu có thể.
- Sau khi sửa luôn chạy `python -m py_compile ...`.
- Nếu thêm bảng/cột SQLite, viết migration trong `init_db()` của `db.py`.
- Nếu thêm admin command, cập nhật menu `/admin`.
- Nếu thêm user command, cập nhật hướng dẫn trong `texts.py` tại `start_text()`.
- Nếu ảnh hưởng thanh toán, test bằng đơn mới và xem log webhook.
- Không xóa cứng sản phẩm/đơn/key đã bán vì sẽ làm hỏng lịch sử.

## 11. Lưu ý bảo mật

- Không gửi `BOT_TOKEN` hoặc `SEPAY_WEBHOOK_SECRET` vào chat công khai.
- Nếu token/secret bị lộ, đổi ngay:
  - Bot token: BotFather.
  - SePay secret: tạo/cập nhật webhook secret trong dashboard SePay.
- File `.env` không nên đưa cho người không tin cậy.

## 12. Các lệnh admin hiện có

```text
/admin
/stats
/pending
/orders
/find mã_đơn hoặc mã_ck
/user id_người_dùng
/history id_người_dùng
/confirm mã_đơn
/cancel mã_đơn
/resend mã_đơn
/stock
/stock mã_sản_phẩm
/lowstock
/addkey mã_sản_phẩm KEY
/addkeys mã_sản_phẩm KEY1 KEY2...
/delkey KEY
/products
/addproduct mã_sản_phẩm mã_nhóm tên_nhóm số_ngày giá tên_sản_phẩm
/setprice mã_sản_phẩm giá_mới
/setdays mã_sản_phẩm số_ngày
/setproduct mã_sản_phẩm số_ngày giá_mới tên_mới
/delproduct mã_sản_phẩm
/restoreproduct mã_sản_phẩm
/revenue today|7d|month|all
/sepaylog
/backup
/notice id_người_dùng nội_dung
/broadcast nội_dung
/broadcastphoto nội_dung
```

## 13. Các lệnh user hiện có

```text
/start
/shop
/order
/mykeys
/vip
```
