# -*- coding: utf-8 -*-
from config import BANK_ACCOUNT, BANK_CODE, BANK_NAME, SHOP_NAME, SUPPORT_USERNAME
from products import PRODUCTS
from ui import money


def start_text(user) -> str:
    name = user.first_name or user.username or "bạn"
    text = (
        f"👋 Xin chào <b>{name}</b>!\n\n"
        f"Chào mừng bạn đến với <b>{SHOP_NAME}</b>.\n"
        "Bot hỗ trợ mua key tự động bằng chuyển khoản ngân hàng.\n\n"
        "<b>Cách mua:</b>\n"
        "1. Bấm <b>Tất cả menu</b>.\n"
        "2. Chọn danh mục và gói muốn mua.\n"
        "3. Chuyển khoản đúng số tiền và đúng nội dung bot cung cấp.\n"
        "4. Khi SePay xác nhận giao dịch, bot sẽ tự gửi key.\n\n"
        "<b>Lệnh bạn có thể dùng:</b>\n"
        "/start - xem hướng dẫn\n"
        "/shop - xem danh sách sản phẩm\n"
        "/order - xem đơn đang chờ thanh toán\n"
        "/mykeys - xem lại key đã mua\n"
        "/vip - xem cấp VIP\n\n"
        "Bấm nút bên dưới để mở toàn bộ menu."
    )


    return text


def group_text(group_name: str) -> str:
    return (
        f"📋 <b>BẢNG GIÁ {group_name}</b>\n\n"
        "Chọn gói bạn muốn mua. Bot sẽ tạo mã thanh toán riêng cho từng đơn hàng."
    )


def product_detail_text(product_id: str, stock: int, price: int | None = None) -> str:
    product = PRODUCTS[product_id]
    display_price = product["price"] if price is None else price
    return (
        "🧾 <b>CHI TIẾT SẢN PHẨM</b>\n\n"
        f"Gói: <b>{product['emoji']} {product['name']}</b>\n"
        f"Giá: <b>{money(display_price)}</b>\n"
        f"Tồn kho: <b>{stock}</b> key\n\n"
        "Bấm nút bên dưới để tạo mã thanh toán."
    )


def payment_text(product_id: str, amount: int, payment_code: str) -> str:
    product = PRODUCTS[product_id]
    return (
        "🧾 <b>THÔNG TIN THANH TOÁN</b>\n\n"
        f"Gói: <b>{product['emoji']} {product['name']}</b>\n"
        f"Số tiền: <b>{money(amount)}</b>\n\n"
        f"Ngân hàng: <b>{BANK_CODE}</b>\n"
        f"Số tài khoản: <code>{BANK_ACCOUNT}</code>\n"
        f"Chủ tài khoản: <b>{BANK_NAME}</b>\n"
        f"Nội dung chuyển khoản: <code>{payment_code}</code>\n\n"
        "Vui lòng chuyển đúng số tiền và đúng nội dung. "
        "Admin sẽ xác nhận và bot sẽ tự động gửi key cho bạn."
    )


def paid_text(order, license_key: str, after_key_message: str = "") -> str:
    product = PRODUCTS[order["product_id"]]
    text = (
        "✅ <b>THANH TOÁN THÀNH CÔNG</b>\n\n"
        f"Đơn: <b>#{order['id']}</b>\n"
        f"Gói: <b>{product['emoji']} {product['name']}</b>\n"
        f"Số tiền: <b>{money(order['amount'])}</b>\n\n"
        "Key của bạn:\n"
        f"<code>{license_key}</code>\n\n"
        "Dùng /mykeys để xem lại key đã mua."
    )


    after_key_message = (after_key_message or "").strip()
    if after_key_message:
        text += "\n\n<b>Ghi chú:</b>\n" + after_key_message
    return text


def support_text() -> str:
    return f"📞 Hỗ trợ: {SUPPORT_USERNAME}"
