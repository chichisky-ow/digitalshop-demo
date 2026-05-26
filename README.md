# Telegram Sales Bot

Bot Telegram bán key tự động bằng Python, aiogram, FastAPI và SQLite.

## Luồng chính

1. User bấm `/start`.
2. User chọn menu, chọn gói và tạo đơn.
3. Bot gửi QR/nội dung chuyển khoản dạng `MUA XXXXXXXX`.
4. SePay gọi webhook `POST /sepay`.
5. Bot kiểm tra số tiền, mã thanh toán và tự nhả key.

Không cần user bấm xác nhận đã chuyển khoản.

## File quan trọng

```text
bot.py              Bot Telegram, webhook SePay, admin panel.
db.py               SQLite schema, đơn hàng, key, sản phẩm, reseller.
ui.py               Inline keyboard.
texts.py            Nội dung tin nhắn user.
products.py         Sản phẩm mặc định khi tạo database mới.
config.py           Đọc biến môi trường từ .env.
requirements.txt    Python dependencies.
deploy_to_vps.ps1   Deploy code lên VPS và restart systemd.
seed.py             Thêm key demo khi cần test local.
```

Không đóng gói `.env`, `shop.db`, `__pycache__`, `.ruff_cache`.

## Cấu hình `.env`

Tạo `.env` từ `.env.example` rồi điền thông tin thật:

```env
BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
ADMIN_IDS=123456789
SHOP_NAME=Key Bot Store
SUPPORT_USERNAME=@your_support
BANK_CODE=ACB
BANK_ACCOUNT=123456789
BANK_NAME=TEN CHU TAI KHOAN
DB_PATH=shop.db
WEBHOOK_HOST=0.0.0.0
WEBHOOK_PORT=9000
SEPAY_WEBHOOK_SECRET=
```

## Chạy local

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

Health check:

```text
http://127.0.0.1:9000/
```

Webhook SePay:

```text
http://IP_VPS:9000/sepay
```

## Deploy lên VPS

Trên PowerShell Windows:

```powershell
cd "C:\Users\Administrator\Downloads\New folder\telegram_bot_backup_with_maintenance_20260522_185211"
powershell.exe -ExecutionPolicy Bypass -File .\deploy_to_vps.ps1
```

Script sẽ backup file cũ, upload code, kiểm tra syntax, cài requirements và restart service `telegram-bot`.

## Kiểm tra VPS

```bash
systemctl status telegram-bot --no-pager
curl http://127.0.0.1:9000/
journalctl -u telegram-bot -n 80 --no-pager
journalctl -u telegram-bot -f
```

Thoát log realtime:

```text
Ctrl + C
```

## Lệnh user

```text
/start
/shop
/order
/mykeys
/vip
```

## Admin panel

Dùng:

```text
/admin
```

Các chức năng chính:

- Thêm/sửa/ẩn/hiện sản phẩm.
- Thêm nhanh một danh mục nhiều gói ngày trong một lần.
- Thêm key bằng paste text hoặc file `.txt`.
- Xóa toàn bộ key hoặc xóa key theo danh mục/sản phẩm.
- Xem tồn kho, đơn chờ, đơn gần nhất, doanh thu, log SePay.
- Thiết lập giá reseller theo từng user.
- Gửi thông báo text, ảnh hoặc video đến toàn bộ user.
- Backup database.

## Thêm key bằng file txt

Vào `/admin` -> `Thêm key`, nhập mã sản phẩm, sau đó gửi file `.txt`.

File txt có thể chứa mỗi key một dòng:

```text
KEY-001
KEY-002
KEY-003
```

Nếu key có dạng tài khoản mật khẩu, để mỗi dòng là một key:

```text
user1 pass1
user2 pass2
user3 pass3
```

Với file nhiều dòng, bot giữ nguyên nội dung từng dòng làm một key.
Với lệnh nhập trực tiếp một dòng như `/addkeys aurax_1 KEY1 KEY2`, bot vẫn hiểu mỗi phần cách nhau bằng khoảng trắng là một key riêng.

Giới hạn file: 512KB.

## Thêm danh mục nhanh

Vào `/admin` -> `Thêm danh mục nhanh`.

Ví dụ tạo danh mục `AuraX` đủ gói 1, 3, 7, 15, 30 ngày:

```text
Mã danh mục: aurax
Tên danh mục: AuraX
Ngày: 1 3 7 15 30
Giá: 25000 50000 80000 120000 180000
Emoji: ⭐
```

Bot sẽ tự tạo:

```text
aurax_1
aurax_3
aurax_7
aurax_15
aurax_30
```

## Broadcast media

Gửi ảnh:

```text
/broadcastphoto Nội dung thông báo
```

Gửi video:

```text
/broadcastvideo Nội dung thông báo
```

Có thể gửi media kèm caption lệnh, hoặc reply vào media bằng lệnh trên.

## Ghi chú an toàn

- Không gửi `BOT_TOKEN`, `.env`, `shop.db` thật lên nơi công khai.
- Trước khi sửa lớn nên backup bằng `/backup`.
- Bot chạy trên VPS bằng systemd nên tắt VS Code/PowerShell local không làm bot dừng.
