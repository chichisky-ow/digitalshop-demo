# -*- coding: utf-8 -*-
import asyncio
import logging
import os
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime
from html import escape
from io import BytesIO
from urllib.parse import quote

import uvicorn
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message
from fastapi import FastAPI, HTTPException, Request

from broadcast_tools import BroadcastDelivery, recall_deliveries, send_media_with_retry
from config import (
    ADMIN_IDS,
    BANK_ACCOUNT,
    BANK_CODE,
    BANK_NAME,
    BOT_TOKEN,
    DB_PATH,
    SEPAY_WEBHOOK_SECRET,
    WEBHOOK_HOST,
    WEBHOOK_PORT,
)
from db import (
    add_key,
    cancel_all_old_pending_orders,
    cancel_pending_order,
    clear_all_keys,
    clear_keys_for_group,
    clear_keys_for_product,
    create_order,
    delete_setting,
    delete_unused_key,
    find_pending_order_by_content,
    find_order,
    fix_composite_unused_keys,
    fix_paid_order_composite_key,
    fulfill_order,
    get_admin_stats,
    get_effective_price,
    get_order,
    get_all_users,
    get_low_stock,
    get_pending_order_by_user,
    get_pending_order_by_code,
    get_latest_order_by_user,
    get_order_with_key,
    get_pending_orders,
    get_product,
    get_products,
    get_recent_orders,
    get_revenue_stats,
    get_reseller_prices,
    get_sepay_logs,
    get_setting,
    get_stock,
    get_stock_count,
    get_stock_detail,
    get_user,
    get_user_keys,
    get_user_order_history,
    init_db,
    record_sepay_transaction,
    remove_reseller_price,
    set_reseller_price,
    set_setting,
    set_product_active,
    update_order_status,
    update_product_fields,
    upsert_product,
    upsert_user,
)
from products import GROUPS, PRODUCTS
from payment_intake import (
    SepayPayloadError,
    SepaySignatureError,
    parse_sepay_webhook_payload,
    verify_sepay_hmac,
)
from security_policy import group_event_policy
from stock_texts import format_stock_detail_text, stock_counts_by_product
from texts import group_text, paid_text, payment_text, product_detail_text, start_text, support_text
from ui import admin_order_menu, confirm_buy_menu, main_menu, money, payment_menu, product_menu, start_menu

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
app = FastAPI()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("telegram-sales-bot")

SPAM_WINDOW_SECONDS = 20
SPAM_MAX_ACTIONS = 8
SPAM_BLOCK_SECONDS = 300
ORDER_AUTO_CANCEL_SECONDS = 10 * 60
ORDER_CREATE_COOLDOWN_SECONDS = 5 * 60
AFTER_KEY_MESSAGE_SETTING = "after_key_message"
USER_ACTIONS = defaultdict(deque)
USER_BLOCKED_UNTIL = {}
USER_BOT_MESSAGES = defaultdict(deque)
USER_BOT_MESSAGE_LIMIT = 60
ADMIN_WIZARDS = {}
BROADCAST_HISTORY = {}
BROADCAST_HISTORY_LIMIT = 20
RECALL_DELETE_DELAY_SECONDS = 0.08
MAX_KEY_FILE_BYTES = 512 * 1024


async def remember_user_bot_message(sent_message):
    if not sent_message:
        return sent_message

    try:
        if sent_message.chat.type != "private":
            return sent_message
        chat_id = sent_message.chat.id
        message_id = sent_message.message_id
    except Exception:
        return sent_message

    ids = USER_BOT_MESSAGES[chat_id]
    if message_id not in ids:
        ids.append(message_id)
    while len(ids) > USER_BOT_MESSAGE_LIMIT:
        ids.popleft()
    return sent_message


async def clear_user_bot_messages(chat_id: int) -> None:
    message_ids = list(USER_BOT_MESSAGES.pop(chat_id, deque()))
    for message_id in message_ids:
        try:
            await bot.delete_message(chat_id, message_id)
        except Exception:
            pass


async def user_answer(message: Message, *args, cleanup: bool = False, **kwargs):
    if cleanup:
        await clear_user_bot_messages(message.chat.id)
    sent = await message.answer(*args, **kwargs)
    return await remember_user_bot_message(sent)


async def user_answer_photo(message: Message, *args, cleanup: bool = False, **kwargs):
    if cleanup:
        await clear_user_bot_messages(message.chat.id)
    sent = await message.answer_photo(*args, **kwargs)
    return await remember_user_bot_message(sent)


async def user_send_message(chat_id: int, *args, cleanup: bool = False, **kwargs):
    if cleanup:
        await clear_user_bot_messages(chat_id)
    sent = await bot.send_message(chat_id, *args, **kwargs)
    return await remember_user_bot_message(sent)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def check_spam_guard(user_id: int) -> tuple[bool, int]:
    if is_admin(user_id):
        return True, 0

    now = time.time()
    blocked_until = USER_BLOCKED_UNTIL.get(user_id, 0)
    if blocked_until > now:
        return False, int(blocked_until - now)

    actions = USER_ACTIONS[user_id]
    while actions and now - actions[0] > SPAM_WINDOW_SECONDS:
        actions.popleft()

    actions.append(now)
    if len(actions) > SPAM_MAX_ACTIONS:
        USER_BLOCKED_UNTIL[user_id] = now + SPAM_BLOCK_SECONDS
        actions.clear()
        return False, SPAM_BLOCK_SECONDS

    return True, 0


def spam_block_text(seconds_left: int) -> str:
    minutes = max(1, (seconds_left + 59) // 60)
    return (
        "Bạn thao tác quá nhanh nên bot tạm khóa chức năng trong "
        f"{minutes} phút. Vui lòng thử lại sau."
    )


class SpamGuardMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if not user:
            return await handler(event, data)

        ok, seconds_left = check_spam_guard(user.id)
        if ok:
            return await handler(event, data)

        text = spam_block_text(seconds_left)
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        elif isinstance(event, Message):
            await event.answer(text)
        return None


class GroupPrivacyMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        chat = getattr(event, "chat", None)
        if isinstance(event, CallbackQuery):
            chat = event.message.chat if event.message else None

        if not user or not chat:
            return await handler(event, data)

        policy = group_event_policy(getattr(chat, "type", ""), user.id, set(ADMIN_IDS))
        if policy == "allow":
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            if policy == "redirect_admin_private":
                await send_group_admin_private_notice(user.id)
                await event.answer("Bot đã gửi hướng dẫn riêng cho admin.", show_alert=True)
            else:
                await event.answer("Chỉ admin được thao tác bot trong nhóm.", show_alert=True)
            return None

        if isinstance(event, Message) and policy == "redirect_admin_private":
            await send_group_admin_private_notice(user.id)
            try:
                await event.answer("Đã gửi hướng dẫn riêng cho admin.")
            except Exception:
                pass
        return None


async def send_group_admin_private_notice(admin_id: int) -> None:
    text = (
        "Để tránh lộ key và thông tin nhạy cảm, hãy thao tác bot trong chat riêng.\n\n"
        "Gõ /admin tại đây để mở admin panel."
    )
    try:
        await bot.send_message(admin_id, text, reply_markup=admin_panel_menu())
    except Exception as exc:
        log.warning("Không thể gửi tin nhắn riêng cho admin %s: %s", admin_id, exc)


dp.message.middleware(GroupPrivacyMiddleware())
dp.callback_query.middleware(GroupPrivacyMiddleware())
dp.message.middleware(SpamGuardMiddleware())
dp.callback_query.middleware(SpamGuardMiddleware())


def make_payment_code() -> str:
    return "MUA " + secrets.token_hex(4).upper()


def status_label(status: str) -> str:
    labels = {
        "pending": "Đang chờ thanh toán",
        "paid": "Đã thanh toán",
        "cancelled": "Đã hủy",
    }
    return labels.get(status, status)


def parse_license_keys(raw: str) -> list[str]:
    keys = []
    seen = set()
    raw = (raw or "").strip()
    if not raw:
        return keys

    if re.search(r"\bUser\s*:", raw, re.IGNORECASE) and re.search(r"\bPass\s*:", raw, re.IGNORECASE):
        candidates = re.split(r"(?=\bUser\s*:)", raw.replace("\r\n", "\n").replace("\r", "\n"))
    elif "\n" in raw or "\r" in raw:
        candidates = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    elif "," in raw or ";" in raw:
        candidates = re.split(r"[,;]+", raw)
    else:
        candidates = raw.split()

    for value in candidates:
        license_key = " ".join(value.strip().split())
        if license_key and license_key not in seen:
            keys.append(license_key)
            seen.add(license_key)

    return keys


async def read_key_text_document(message: Message) -> str:
    document = message.document
    if not document:
        return ""

    filename = (document.file_name or "").lower()
    mime_type = (document.mime_type or "").lower()
    allowed_mimes = {"text/plain", "text/csv", "application/octet-stream"}
    if not filename.endswith(".txt") and mime_type not in allowed_mimes:
        raise ValueError("Chỉ hỗ trợ file .txt chứa danh sách key.")

    if document.file_size and document.file_size > MAX_KEY_FILE_BYTES:
        raise ValueError("File key quá lớn. Giới hạn hiện tại là 512KB.")

    buffer = BytesIO()
    await bot.download(document, destination=buffer)
    raw = buffer.getvalue()
    for encoding in ("utf-8-sig", "utf-16", "cp1258", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


async def extract_key_text(message: Message, inline_text: str = "") -> str:
    if inline_text:
        return inline_text

    if message.document:
        return await read_key_text_document(message)

    if message.reply_to_message:
        if message.reply_to_message.text:
            return message.reply_to_message.text
        if message.reply_to_message.document:
            return await read_key_text_document(message.reply_to_message)

    return ""


def admin_panel_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Thêm sản phẩm", callback_data="admin_action:add_product"),
                InlineKeyboardButton(text="⚡ Thêm danh mục nhanh", callback_data="admin_action:add_group_fast"),
            ],
            [
                InlineKeyboardButton(text="🔑 Thêm key", callback_data="admin_action:add_key"),
                InlineKeyboardButton(text="🗑 Xóa 1 key", callback_data="admin_action:delete_key"),
            ],
            [
                InlineKeyboardButton(text="🧹 Xóa all key", callback_data="admin_action:clear_all_keys"),
                InlineKeyboardButton(text="🧽 Xóa key danh mục", callback_data="admin_action:clear_key_scope"),
            ],
            [
                InlineKeyboardButton(text="📋 Sản phẩm", callback_data="admin_action:products"),
                InlineKeyboardButton(text="📦 Tồn kho", callback_data="admin_action:stock"),
            ],
            [
                InlineKeyboardButton(text="⚠️ Sắp hết key", callback_data="admin_action:lowstock"),
            ],
            [
                InlineKeyboardButton(text="💰 Sửa giá chung", callback_data="admin_action:set_price"),
                InlineKeyboardButton(text="🏷 Giá reseller", callback_data="admin_action:reseller_price"),
            ],
            [
                InlineKeyboardButton(text="🗑 Xóa giá reseller", callback_data="admin_action:remove_reseller_price"),
                InlineKeyboardButton(text="📃 DS reseller", callback_data="admin_action:reseller_prices"),
            ],
            [
                InlineKeyboardButton(text="🗓 Sửa ngày", callback_data="admin_action:set_days"),
                InlineKeyboardButton(text="✏️ Sửa sản phẩm", callback_data="admin_action:set_product"),
            ],
            [
                InlineKeyboardButton(text="🙈 Ẩn sản phẩm", callback_data="admin_action:hide_product"),
                InlineKeyboardButton(text="👁 Hiện sản phẩm", callback_data="admin_action:restore_product"),
            ],
            [
                InlineKeyboardButton(text="⏳ Đơn chờ", callback_data="admin_action:pending"),
                InlineKeyboardButton(text="🧾 Đơn gần nhất", callback_data="admin_action:orders"),
            ],
            [
                InlineKeyboardButton(text="🔎 Tìm đơn", callback_data="admin_action:find_order"),
                InlineKeyboardButton(text="✅ Xác nhận đơn", callback_data="admin_action:confirm_order"),
            ],
            [
                InlineKeyboardButton(text="🚫 Hủy đơn", callback_data="admin_action:cancel_order"),
                InlineKeyboardButton(text="📨 Gửi lại key", callback_data="admin_action:resend_order"),
            ],
            [
                InlineKeyboardButton(text="👤 Xem user", callback_data="admin_action:user_info"),
                InlineKeyboardButton(text="📜 Lịch sử user", callback_data="admin_action:history"),
            ],
            [
                InlineKeyboardButton(text="💵 Doanh thu", callback_data="admin_action:revenue"),
                InlineKeyboardButton(text="🧾 Log SePay", callback_data="admin_action:sepaylog"),
            ],
            [
                InlineKeyboardButton(text="📝 Lời nhắn sau key", callback_data="admin_action:afterkey_message"),
            ],
            [
                InlineKeyboardButton(text="📣 Gửi 1 user", callback_data="admin_action:notice"),
                InlineKeyboardButton(text="📢 Broadcast", callback_data="admin_action:broadcast"),
            ],
            [
                InlineKeyboardButton(text="🖼 Broadcast ảnh", callback_data="admin_action:broadcastphoto_help"),
                InlineKeyboardButton(text="🎥 Broadcast video", callback_data="admin_action:broadcastvideo_help"),
            ],
            [
                InlineKeyboardButton(text="🎭 Broadcast sticker", callback_data="admin_action:broadcaststicker_help"),
            ],
            [
                InlineKeyboardButton(text="🛠 Sửa key gộp", callback_data="admin_action:fixkeys"),
            ],
            [
                InlineKeyboardButton(text="💾 Backup DB", callback_data="admin_action:backup"),
                InlineKeyboardButton(text="📖 Hướng dẫn lệnh", callback_data="admin_action:help"),
            ],
            [
                InlineKeyboardButton(text="❌ Hủy thao tác", callback_data="admin_action:cancel"),
            ],
        ]
    )


def admin_cancel_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Hủy thao tác", callback_data="admin_action:cancel")],
        ]
    )


def remember_broadcast(kind: str, deliveries: list[BroadcastDelivery]) -> str:
    broadcast_id = secrets.token_hex(4)
    BROADCAST_HISTORY[broadcast_id] = {
        "kind": kind,
        "deliveries": deliveries,
        "created_at": time.time(),
        "recalled": False,
        "recalling": False,
    }
    while len(BROADCAST_HISTORY) > BROADCAST_HISTORY_LIMIT:
        oldest_id = min(BROADCAST_HISTORY, key=lambda item: BROADCAST_HISTORY[item]["created_at"])
        BROADCAST_HISTORY.pop(oldest_id, None)
    return broadcast_id


def recall_broadcast_menu(broadcast_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="↩️ Thu hồi broadcast",
                    callback_data=f"recall_broadcast:{broadcast_id}",
                )
            ]
        ]
    )


def normalize_id(value: str) -> str:
    return (value or "").strip().lower()


async def start_admin_wizard(call: CallbackQuery, flow: str, step: str, prompt: str, data: dict | None = None) -> None:
    ADMIN_WIZARDS[call.from_user.id] = {"flow": flow, "step": step, "data": data or {}}
    await call.message.answer(prompt + "\n\nGõ <code>hủy</code> để thoát.", reply_markup=admin_cancel_menu())
    await call.answer()


def parse_positive_int(value: str) -> int | None:
    cleaned = (value or "").replace(".", "").replace(",", "").strip()
    if not cleaned.isdigit() or int(cleaned) <= 0:
        return None
    return int(cleaned)


def parse_price_int(value: str) -> int | None:
    cleaned = (value or "").replace(".", "").replace(",", "").strip()
    if not cleaned.isdigit():
        return None
    return int(cleaned)


def parse_int_list(value: str) -> list[int]:
    items = re.split(r"[\s,;]+", (value or "").strip())
    numbers = []
    for item in items:
        cleaned = item.replace(".", "").replace(",", "").strip()
        if not cleaned:
            continue
        if not cleaned.isdigit() or int(cleaned) <= 0:
            return []
        numbers.append(int(cleaned))
    return numbers


def wants_keep_current(value: str) -> bool:
    return normalize_id(value) in {"giu", "giữ", "bo qua", "bỏ qua", "skip", "keep", ".", "-"}


async def product_picker_text(title: str, instruction: str) -> str:
    rows = await get_products(active_only=False)
    text = f"{title}\n\n{instruction}\n\n<b>Danh sách sản phẩm:</b>\n"
    current_group = None
    for row in rows:
        if row["group_id"] != current_group:
            current_group = row["group_id"]
            text += f"\n<b>{row['group_title']}</b> (<code>{row['group_id']}</code>)\n"
        status = "đang bán" if int(row["active"] or 0) == 1 else "đang ẩn"
        text += (
            f"- <code>{row['product_id']}</code> | {row['emoji']} {row['name']} | "
            f"{int(row['days'] or 0)} ngày | <b>{money(row['price'])}</b> | {status}\n"
        )
    return text[:3900]


async def refresh_product_cache() -> None:
    rows = await get_products(active_only=True)
    PRODUCTS.clear()
    GROUPS.clear()

    for row in rows:
        product_id = row["product_id"]
        group_id = row["group_id"]
        PRODUCTS[product_id] = {
            "group": group_id,
            "emoji": row["emoji"] or "",
            "name": row["name"],
            "price": int(row["price"]),
            "days": int(row["days"] or 0),
        }

        if group_id not in GROUPS:
            GROUPS[group_id] = {
                "title": row["group_title"] or group_id,
                "product_ids": [],
            }
        GROUPS[group_id]["product_ids"].append(product_id)


def vietqr_url(amount: int, payment_code: str) -> str:
    return (
        f"https://img.vietqr.io/image/{BANK_CODE}-{BANK_ACCOUNT}-compact2.png"
        f"?amount={amount}&addInfo={quote(payment_code)}&accountName={quote(BANK_NAME)}"
    )


async def notify_admins(order_id: int) -> None:
    order = await get_order(order_id)
    if not order:
        return

    product = PRODUCTS[order["product_id"]]
    username = order["username"] or "không có"
    text = (
        "🆕 <b>ĐƠN HÀNG MỚI</b>\n\n"
        f"Đơn: <b>#{order['id']}</b>\n"
        f"ID khách: <code>{order['user_id']}</code>\n"
        f"Tên Telegram: {('@' + username) if username != 'không có' else username}\n"
        f"Gói: <b>{product['emoji']} {product['name']}</b>\n"
        f"Số tiền: <b>{money(order['amount'])}</b>\n"
        f"Nội dung CK: <code>{order['payment_code']}</code>\n\n"
        "Bấm nút xác nhận khi đã nhận tiền."
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, reply_markup=admin_order_menu(order_id))
        except Exception:
            pass


def pending_order_text(order) -> str:
    product = PRODUCTS.get(order["product_id"], {"emoji": "", "name": order["product_id"]})
    return (
        "Bạn đang có giao dịch chưa hoàn tất.\n\n"
        f"Đơn: <b>#{order['id']}</b>\n"
        f"Gói: <b>{product['emoji']} {product['name']}</b>\n"
        f"Số tiền: <b>{money(order['amount'])}</b>\n"
        f"Ngân hàng: <b>{BANK_CODE}</b>\n"
        f"Số tài khoản: <code>{BANK_ACCOUNT}</code>\n"
        f"Chủ tài khoản: <b>{BANK_NAME}</b>\n"
        f"Nội dung CK: <code>{order['payment_code']}</code>\n\n"
        "Vui lòng thanh toán đúng số tiền và đúng nội dung, hoặc hủy giao dịch để tạo đơn mới."
    )


async def send_pending_order(message: Message, user_id: int) -> bool:
    order = await get_pending_order_by_user(user_id)
    if not order:
        return False
    await user_answer(message, pending_order_text(order), reply_markup=payment_menu(order["id"]))
    return True


async def send_fulfilled_order(result, source: str) -> None:
    if not result.get("ok") or result.get("already"):
        return

    order = result["order"]
    key = result["key"]
    after_key_message = await get_setting(AFTER_KEY_MESSAGE_SETTING, "")
    await user_send_message(order["user_id"], paid_text(order, key["license_key"], after_key_message), cleanup=True)

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"Đã giao key cho đơn #{order['id']} qua {source}.",
            )
        except Exception:
            pass

    await notify_low_stock(order["product_id"])


def order_cooldown_remaining_seconds(last_order, now: float | None = None) -> int:
    if not last_order or not last_order["created_at"]:
        return 0

    created_at = str(last_order["created_at"]).replace("T", " ").split(".", 1)[0]
    try:
        created_time = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return 0

    elapsed = int((time.time() if now is None else now) - created_time)
    return max(0, ORDER_CREATE_COOLDOWN_SECONDS - elapsed)


async def notify_low_stock(product_id: str, threshold: int = 3) -> None:
    stock = await get_stock_count(product_id)
    if stock > threshold:
        return

    product = PRODUCTS.get(product_id, {"emoji": "", "name": product_id})
    text = (
        "⚠️ <b>CẢNH BÁO TỒN KHO THẤP</b>\n\n"
        f"Sản phẩm: <code>{product_id}</code>\n"
        f"Tên: <b>{product['emoji']} {product['name']}</b>\n"
        f"Còn lại: <b>{stock}</b> key\n\n"
        f"Thêm key bằng lệnh: <code>/addkeys {product_id} KEY1 KEY2...</code>"
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            pass


async def notify_admins_payment_issue(order, amount: int, msg: str) -> None:
    product = PRODUCTS.get(order["product_id"], {"emoji": "", "name": order["product_id"]})
    text = (
        "⚠️ <b>THANH TOÁN CẦN XỬ LÝ THỦ CÔNG</b>\n\n"
        f"Đơn: <b>#{order['id']}</b>\n"
        f"ID khách: <code>{order['user_id']}</code>\n"
        f"Gói: <b>{product['emoji']} {product['name']}</b>\n"
        f"Số tiền đơn: <b>{money(order['amount'])}</b>\n"
        f"Số tiền nhận: <b>{money(amount)}</b>\n"
        f"Nội dung CK: <code>{order['payment_code']}</code>\n"
        f"Lý do: <code>{msg}</code>\n\n"
        "Kiểm tra kho key rồi dùng /confirm nếu cần."
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, reply_markup=admin_order_menu(order["id"]))
        except Exception:
            pass


async def notify_admins_user_cancelled_order(order, user) -> None:
    product = PRODUCTS.get(order["product_id"], {"emoji": "", "name": order["product_id"]})
    username = f"@{user.username}" if getattr(user, "username", None) else (f"@{order['username']}" if order["username"] else "khong co")
    text = (
        "⚠️ <b>USER HỦY ĐƠN</b>\n\n"
        f"Đơn: <b>#{order['id']}</b>\n"
        f"ID khách: <code>{order['user_id']}</code>\n"
        f"Username: <b>{escape(username)}</b>\n"
        f"Gói: <b>{product['emoji']} {product['name']}</b>\n"
        f"Số tiền: <b>{money(order['amount'])}</b>\n"
        f"Nội dung CK: <code>{order['payment_code']}</code>\n"
        f"Tạo lúc: {order['created_at']}\n\n"
        "User đã tự hủy giao dịch từ bot."
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            pass


async def notify_admins_unmatched_payment(amount: int, payment_code: str, content: str) -> None:
    if "SEPAYTEST" in (content or "").upper():
        return

    text = (
        "⚠️ <b>WEBHOOK SEPAY KHÔNG KHỚP ĐƠN</b>\n\n"
        f"Số tiền nhận: <b>{money(amount)}</b>\n"
        f"Mã trích xuất: <code>{payment_code or 'không có'}</code>\n"
        f"Nội dung: <code>{escape(content[:800])}</code>\n\n"
        "Kiểm tra nội dung chuyển khoản hoặc dùng /orders, /find để đối chiếu."
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            pass


@app.get("/")
async def health():
    return {"ok": True, "service": "telegram-sales-bot", "webhook": "/sepay"}


@app.post("/sepay")
async def sepay_webhook(request: Request):
    raw_body = await request.body()
    if not raw_body:
        raise HTTPException(status_code=400, detail="Empty body")

    try:
        verify_sepay_hmac(raw_body, request.headers, SEPAY_WEBHOOK_SECRET)
    except SepaySignatureError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error

    try:
        payment = parse_sepay_webhook_payload(raw_body)
    except SepayPayloadError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    amount = payment.amount
    content = payment.content
    payment_code = payment.payment_code
    sepay_id = payment.transaction_id

    if not payment.is_incoming:
        log.info("Bỏ qua webhook SePay không phải tiền vào: id=%s raw=%s", sepay_id or "(none)", payment.data)
        return {"success": True, "ok": True, "msg": "ignored_non_incoming"}

    log.info(
        "SePay webhook: id=%s amount=%s code=%s content=%s raw=%s",
        sepay_id or "(none)",
        amount,
        payment_code or "(none)",
        content,
        payment.data,
    )

    is_new_transaction = await record_sepay_transaction(sepay_id, payment_code, amount, payment.raw_body_text)
    if not is_new_transaction:
        log.info("Bỏ qua webhook SePay trùng: id=%s code=%s", sepay_id, payment_code)
        return {"success": True, "ok": True, "msg": "duplicate_ignored"}

    order = await get_pending_order_by_code(payment_code) if payment_code else None
    if not order:
        order = await find_pending_order_by_content(content)
    if not order:
        log.warning("Không tìm thấy đơn pending khớp nội dung: code=%s content=%s", payment_code, content)
        await notify_admins_unmatched_payment(amount, payment_code, content)
        return {"success": True, "ok": False, "msg": "không tìm thấy đơn hàng"}

    if amount < int(order["amount"]):
        log.warning(
            "Số tiền chưa đủ cho đơn #%s: nhận=%s cần=%s",
            order["id"],
            amount,
            order["amount"],
        )
        await notify_admins_payment_issue(order, amount, "amount_not_enough")
        return {
            "success": True,
            "ok": False,
            "msg": "số tiền chưa đủ",
            "need": int(order["amount"]),
            "received": amount,
        }

    result = None
    for attempt in range(3):
        result = await fulfill_order(order["id"])
        log.info("Kết quả xử lý đơn #%s (lần %s): %s", order["id"], attempt + 1, result.get("msg"))
        if result.get("msg") != "key_race_condition_retry":
            break
        await asyncio.sleep(0.2)

    if result.get("ok") and not result.get("already"):
        await send_fulfilled_order(result, "SePay tự động")
    elif not result.get("ok"):
        await notify_admins_payment_issue(order, amount, result.get("msg", "unknown_error"))

    return {"success": True, "ok": result.get("ok"), "msg": result.get("msg")}


async def auto_cancel_order(order_id: int, user_id: int, delay_seconds: int = ORDER_AUTO_CANCEL_SECONDS) -> None:
    await asyncio.sleep(delay_seconds)
    order = await get_order(order_id)
    if order and order["status"] == "pending":
        await update_order_status(order_id, "cancelled")
        try:
            await user_send_message(
                user_id,
                f"Đơn #{order_id} đã bị hủy vì quá 10 phút chưa thanh toán.",
                reply_markup=main_menu(),
                cleanup=True,
            )
        except Exception:
            pass


@dp.message(Command("start"))
async def start_cmd(message: Message) -> None:
    await upsert_user(message.from_user)
    await user_answer(message, start_text(message.from_user), reply_markup=start_menu(), cleanup=True)


@dp.message(Command("shop"))
async def shop_cmd(message: Message) -> None:
    await upsert_user(message.from_user)
    await user_answer(message, "Chọn menu bên dưới để tiếp tục.", reply_markup=main_menu(), cleanup=True)


@dp.message(Command("mykeys"))
async def mykeys_cmd(message: Message) -> None:
    await clear_user_bot_messages(message.chat.id)
    await send_mykeys(message, message.from_user.id)


@dp.message(Command("order"))
async def order_cmd(message: Message) -> None:
    await clear_user_bot_messages(message.chat.id)
    if not await send_pending_order(message, message.from_user.id):
        await user_answer(message, "Bạn không có đơn đang chờ thanh toán.", reply_markup=main_menu())


@dp.callback_query(F.data == "home")
async def home_cb(call: CallbackQuery) -> None:
    await upsert_user(call.from_user)
    await user_answer(call.message, start_text(call.from_user), reply_markup=start_menu(), cleanup=True)
    await call.answer()


@dp.callback_query(F.data == "show_menu")
async def show_menu_cb(call: CallbackQuery) -> None:
    await upsert_user(call.from_user)
    await user_answer(call.message, "Chọn menu bên dưới để tiếp tục.", reply_markup=main_menu(), cleanup=True)
    await call.answer()


@dp.callback_query(F.data == "mykeys")
async def mykeys_cb(call: CallbackQuery) -> None:
    await clear_user_bot_messages(call.message.chat.id)
    await send_mykeys(call.message, call.from_user.id)
    await call.answer()


@dp.callback_query(F.data == "support")
async def support_cb(call: CallbackQuery) -> None:
    await user_answer(call.message, support_text(), reply_markup=main_menu(), cleanup=True)
    await call.answer()


@dp.callback_query(F.data.startswith("group:"))
async def group_cb(call: CallbackQuery) -> None:
    group_id = call.data.split(":", 1)[1]
    group = GROUPS.get(group_id)
    if not group:
        await call.answer("Danh mục không tồn tại.", show_alert=True)
        return

    await user_answer(call.message, group_text(group["title"]), reply_markup=await product_menu(group_id, call.from_user.id), cleanup=True)
    await call.answer()


@dp.callback_query(F.data.startswith("buy:"))
async def buy_cb(call: CallbackQuery) -> None:
    product_id = call.data.split(":", 1)[1]
    if product_id not in PRODUCTS:
        await call.answer("Sản phẩm không tồn tại.", show_alert=True)
        return

    stock = await get_stock_count(product_id)
    price = await get_effective_price(call.from_user.id, product_id, PRODUCTS[product_id]["price"])
    await user_answer(
        call.message,
        product_detail_text(product_id, stock, price),
        reply_markup=confirm_buy_menu(product_id),
        cleanup=True,
    )
    await call.answer()


@dp.callback_query(F.data.startswith("create_order:"))
async def create_order_cb(call: CallbackQuery) -> None:
    await upsert_user(call.from_user)
    product_id = call.data.split(":", 1)[1]

    pending_order = await get_pending_order_by_user(call.from_user.id)
    if pending_order:
        await user_answer(
            call.message,
            "Bạn đang có giao dịch chưa hoàn tất.\n\n"
            f"Đơn: <b>#{pending_order['id']}</b>\n"
            f"Số tiền: <b>{money(pending_order['amount'])}</b>\n"
            f"Nội dung CK: <code>{pending_order['payment_code']}</code>\n\n"
            "Vui lòng thanh toán hoặc hủy giao dịch này trước khi tạo đơn mới.",
            reply_markup=payment_menu(pending_order["id"]),
            cleanup=True,
        )
        await call.answer("Bạn còn giao dịch chưa hoàn tất.", show_alert=True)
        return

    latest_order = await get_latest_order_by_user(call.from_user.id)
    cooldown_seconds = order_cooldown_remaining_seconds(latest_order)
    if cooldown_seconds > 0:
        minutes = max(1, (cooldown_seconds + 59) // 60)
        await call.answer(f"Vui lòng chờ {minutes} phút nữa rồi tạo đơn mới.", show_alert=True)
        return

    if product_id not in PRODUCTS:
        await call.answer("Sản phẩm không tồn tại.", show_alert=True)
        return

    stock = await get_stock_count(product_id)
    if stock <= 0:
        await call.answer("Sản phẩm tạm hết key.", show_alert=True)
        return

    product = PRODUCTS[product_id]
    amount = await get_effective_price(call.from_user.id, product_id, product["price"])
    payment_code = make_payment_code()
    order_id = await create_order(call.from_user, product_id, amount, payment_code)

    old_cancelled = await cancel_all_old_pending_orders(call.from_user.id, order_id)
    if old_cancelled > 0:
        log.info(
            "Đã hủy %s đơn pending cũ của user %s khi tạo đơn mới #%s",
            old_cancelled,
            call.from_user.id,
            order_id,
        )

    if amount <= 0:
        result = None
        for attempt in range(3):
            result = await fulfill_order(order_id)
            log.info("Ket qua giao don mien phi #%s (lan %s): %s", order_id, attempt + 1, result.get("msg"))
            if result.get("msg") != "key_race_condition_retry":
                break
            await asyncio.sleep(0.2)

        if result.get("ok") and not result.get("already"):
            await send_fulfilled_order(result, "gia 0 dong")
            await call.answer("Đã giao hàng.")
            return

        await notify_admins_payment_issue(
            result.get("order") if result else {"id": order_id},
            amount,
            result.get("msg", "unknown_error") if result else "unknown_error",
        )
        await user_answer(
            call.message,
            "Không thể giao key tự động cho đơn miễn phí lúc này. Admin đã được thông báo, vui lòng thử lại sau.",
            reply_markup=main_menu(),
            cleanup=True,
        )
        await call.answer("Không thể giao key tự động.", show_alert=True)
        return

    qr_url = vietqr_url(amount, payment_code)

    await user_answer_photo(
        call.message,
        photo=qr_url,
        caption=payment_text(product_id, amount, payment_code),
        reply_markup=payment_menu(order_id),
        cleanup=True,
    )
    await notify_admins(order_id)
    asyncio.create_task(auto_cancel_order(order_id, call.from_user.id, ORDER_AUTO_CANCEL_SECONDS))
    await call.answer("Đã tạo mã thanh toán.")


@dp.callback_query(F.data.startswith("cancel_order:"))
async def cancel_order_cb(call: CallbackQuery) -> None:
    order_id = int(call.data.split(":", 1)[1])
    result = await cancel_pending_order(order_id, call.from_user.id)

    if result.get("ok"):
        await notify_admins_user_cancelled_order(result["order"], call.from_user)
        await user_answer(
            call.message,
            f"Đã hủy giao dịch #{order_id}. Bạn có thể tạo giao dịch mới.",
            reply_markup=main_menu(),
            cleanup=True,
        )
        await call.answer("Đã hủy giao dịch.")
        return

    msg = result.get("msg", "cancel_failed")
    if msg == "not_owner":
        await call.answer("Bạn không thể hủy giao dịch này.", show_alert=True)
    elif msg == "order_paid":
        await call.answer("Đơn này đã thanh toán, không thể hủy.", show_alert=True)
    elif msg == "order_cancelled":
        await call.answer("Đơn này đã được hủy trước đó.", show_alert=True)
    else:
        await call.answer("Không thể hủy giao dịch.", show_alert=True)


@dp.callback_query(F.data.startswith("admin_confirm:"))
async def admin_confirm_cb(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("Bạn không phải admin.", show_alert=True)
        return

    order_id = int(call.data.split(":", 1)[1])
    result = await fulfill_order(order_id)

    if result.get("ok") and not result.get("already"):
        await send_fulfilled_order(result, "admin")
        await call.answer("Đã xác nhận và gửi key.", show_alert=True)
        return

    await call.answer(result.get("msg", "Không thể xác nhận."), show_alert=True)


@dp.callback_query(F.data.startswith("stock_detail:"))
async def stock_detail_cb(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("Bạn không phải admin.", show_alert=True)
        return

    product_id = call.data.split(":", 1)[1]
    if product_id not in PRODUCTS:
        await call.answer("Sản phẩm không tồn tại.", show_alert=True)
        return

    detail = await get_stock_detail(product_id, 50)
    text = format_stock_detail_text(product_id, PRODUCTS[product_id], detail)
    await call.message.answer(text[:3900])
    await call.answer()


@dp.callback_query(F.data.startswith("recall_broadcast:"))
async def recall_broadcast_cb(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("Bạn không phải admin.", show_alert=True)
        return

    broadcast_id = call.data.split(":", 1)[1]
    record = BROADCAST_HISTORY.get(broadcast_id)
    if not record:
        await call.answer("Không tìm thấy broadcast này trong bộ nhớ.", show_alert=True)
        return
    if record.get("recalled"):
        await call.answer("Broadcast này đã được thu hồi rồi.", show_alert=True)
        return

    if record.get("recalling"):
        await call.answer("Broadcast dang duoc thu hoi. Vui long doi.", show_alert=True)
        return

    record["recalling"] = True
    await call.answer("Dang thu hoi broadcast...")
    try:
        recalled, failed = await recall_deliveries(
            record["deliveries"],
            bot.delete_message,
            delay_seconds=RECALL_DELETE_DELAY_SECONDS,
            sleep=asyncio.sleep,
        )
    except Exception as error:
        record["recalling"] = False
        await call.message.answer(f"Thu hoi broadcast gap loi: <code>{escape(str(error))}</code>")
        return
    record["recalled"] = True
    record["recalling"] = False
    await call.message.answer(
        "↩️ <b>Đã thu hồi broadcast.</b>\n\n"
        f"Đã xóa: <b>{recalled}</b>\n"
        f"Lỗi khi xóa: <b>{failed}</b>"
    )


async def send_mykeys(message: Message, user_id: int) -> None:
    rows = await get_user_keys(user_id)
    if not rows:
        await user_answer(message, "Bạn chưa có key đã mua.", reply_markup=main_menu())
        return

    text = "🔑 <b>KEY ĐÃ MUA</b>\n\n"
    for row in rows:
        product = PRODUCTS.get(row["product_id"], {"emoji": "", "name": row["product_id"]})
        text += (
            f"Đơn #{row['id']} - {product['emoji']} {product['name']}\n"
            f"Số tiền: {money(row['amount'])}\n"
            f"Key: <code>{row['license_key']}</code>\n"
            f"Thời gian: {row['paid_at']}\n\n"
        )

    await user_answer(message, text, reply_markup=main_menu())


async def send_products_summary(message: Message) -> None:
    rows = await get_products(active_only=True)
    if not rows:
        await message.answer("Chưa có sản phẩm nào.", reply_markup=None)
        return

    text = "📋 <b>DANH SÁCH SẢN PHẨM</b>\n\n"
    current_group = None
    for row in rows:
        if row["group_id"] != current_group:
            current_group = row["group_id"]
            text += f"<b>{row['group_title']}</b> (<code>{row['group_id']}</code>)\n"
        text += (
            f"- <code>{row['product_id']}</code> | {row['emoji']} {row['name']} | "
            f"{int(row['days'] or 0)} ngày | <b>{money(row['price'])}</b>\n"
        )

    await message.answer(text[:3900], reply_markup=None)


async def send_stock_summary(message: Message) -> None:
    rows = stock_counts_by_product(await get_stock())
    text = "📦 <b>KHO KEY</b>\n\n"
    buttons = []
    for product_id, product in PRODUCTS.items():
        text += f"<code>{product_id}</code>: <b>{rows.get(product_id, 0)}</b> key - {product['name']}\n"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{product['emoji']} {product_id} ({rows.get(product_id, 0)})",
                    callback_data=f"stock_detail:{product_id}",
                )
            ]
        )
    text += "\nBấm sản phẩm bên dưới hoặc gõ <code>/stock mã_sản_phẩm</code> để xem nội dung key."
    await message.answer(text[:3900], reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


async def send_pending_summary(message: Message) -> None:
    rows = await get_pending_orders(20)
    if not rows:
        await message.answer("Không có đơn nào đang chờ thanh toán.", reply_markup=None)
        return

    text = "⏳ <b>ĐƠN ĐANG CHỜ THANH TOÁN</b>\n\n"
    for order in rows:
        product = PRODUCTS.get(order["product_id"], {"emoji": "", "name": order["product_id"]})
        text += (
            f"#{order['id']} | {money(order['amount'])}\n"
            f"ID khách: <code>{order['user_id']}</code>\n"
            f"Gói: {product['emoji']} {product['name']}\n"
            f"Mã CK: <code>{order['payment_code']}</code>\n"
            f"Tạo lúc: {order['created_at']}\n\n"
        )
    await message.answer(text[:3900], reply_markup=None)


async def send_orders_summary(message: Message) -> None:
    orders = await get_recent_orders()
    if not orders:
        await message.answer("Chưa có đơn hàng.", reply_markup=None)
        return

    text = "🧾 <b>ĐƠN GẦN NHẤT</b>\n\n"
    for order in orders:
        text += (
            f"#{order['id']} | <b>{status_label(order['status'])}</b> | {money(order['amount'])}\n"
            f"ID khách: <code>{order['user_id']}</code>\n"
            f"Sản phẩm: <code>{order['product_id']}</code>\n"
            f"Nội dung: <code>{order['payment_code']}</code>\n\n"
        )

    await message.answer(text[:3900], reply_markup=None)


async def send_order_detail(message: Message, query: str) -> None:
    order = await find_order(query)
    if not order:
        await message.answer("Không tìm thấy đơn hàng.", reply_markup=None)
        return

    product = PRODUCTS.get(order["product_id"], {"emoji": "", "name": order["product_id"]})
    text = (
        "🔎 <b>THÔNG TIN ĐƠN</b>\n\n"
        f"Đơn: <b>#{order['id']}</b>\n"
        f"Trạng thái: <b>{status_label(order['status'])}</b>\n"
        f"ID khách: <code>{order['user_id']}</code>\n"
        f"Username: {('@' + order['username']) if order['username'] else 'không có'}\n"
        f"Gói: <b>{product['emoji']} {product['name']}</b>\n"
        f"Số tiền: <b>{money(order['amount'])}</b>\n"
        f"Mã CK: <code>{order['payment_code']}</code>\n"
        f"Tạo lúc: {order['created_at']}\n"
    )
    if order["paid_at"]:
        text += f"Thanh toán: {order['paid_at']}\n"
    if order["cancelled_at"]:
        text += f"Hủy lúc: {order['cancelled_at']}\n"

    await message.answer(
        text,
        reply_markup=admin_order_menu(order["id"]) if order["status"] == "pending" else None,
    )


async def send_user_summary(message: Message, user_id: int) -> None:
    user = await get_user(user_id)
    if not user:
        await message.answer("Không tìm thấy user này trong database.", reply_markup=None)
        return

    pending = await get_pending_order_by_user(user_id)
    history = await get_user_order_history(user_id, limit=5)
    text = (
        "👤 <b>THÔNG TIN USER</b>\n\n"
        f"ID: <code>{user['user_id']}</code>\n"
        f"Username: {('@' + user['username']) if user['username'] else 'không có'}\n"
        f"Tên: {escape(user['first_name'] or '')}\n"
        f"Tổng chi tiêu: <b>{money(user['total_spent'] or 0)}</b>\n"
        f"Ngày tạo: {user['created_at']}\n\n"
    )
    if pending:
        text += (
            "<b>Đơn đang chờ:</b>\n"
            f"#{pending['id']} | {money(pending['amount'])} | <code>{pending['payment_code']}</code>\n\n"
        )
    text += "<b>Lịch sử gần nhất:</b>\n"
    if history:
        for row in history:
            text += f"#{row['id']} | {status_label(row['status'])} | {money(row['amount'])} | <code>{row['payment_code']}</code>\n"
    else:
        text += "Chưa có đơn.\n"

    await message.answer(text[:3900], reply_markup=None)


async def send_history_summary(message: Message, user_id: int) -> None:
    rows = await get_user_order_history(user_id)
    if not rows:
        await message.answer(f"Không có lịch sử mua cho user <code>{user_id}</code>.", reply_markup=None)
        return

    text = f"📜 <b>LỊCH SỬ USER {user_id}</b>\n\n"
    for row in rows:
        product = PRODUCTS.get(row["product_id"], {"emoji": "", "name": row["product_id"]})
        text += (
            f"#{row['id']} | <b>{status_label(row['status'])}</b>\n"
            f"Gói: {product['emoji']} {product['name']}\n"
            f"Số tiền: <b>{money(row['amount'])}</b>\n"
            f"Mã CK: <code>{row['payment_code']}</code>\n"
        )
        if row["paid_at"]:
            text += f"Thanh toán: {row['paid_at']}\n"
        if row["license_key"]:
            text += f"Key: <code>{row['license_key']}</code>\n"
        text += "\n"

    await message.answer(text[:3900], reply_markup=None)


async def send_revenue_summary(message: Message, period: str) -> None:
    stats = await get_revenue_stats(period)
    summary = stats["summary"]
    labels = {
        "today": "hôm nay",
        "7d": "7 ngày gần nhất",
        "month": "tháng này",
        "all": "toàn bộ",
    }
    text = (
        f"💰 <b>DOANH THU {labels[period].upper()}</b>\n\n"
        f"Số đơn paid: <b>{summary['orders']}</b>\n"
        f"Doanh thu: <b>{money(summary['revenue'])}</b>\n\n"
        "<b>Theo sản phẩm:</b>\n"
    )
    for row in stats["by_product"]:
        product = PRODUCTS.get(row["product_id"], {"emoji": "", "name": row["product_id"]})
        text += f"{product['emoji']} {product['name']}: {row['orders']} đơn | <b>{money(row['revenue'])}</b>\n"

    await message.answer(text[:3900], reply_markup=None)


async def send_reseller_prices_summary(message: Message) -> None:
    rows = await get_reseller_prices(80)
    if not rows:
        await message.answer("Chưa có giá reseller nào.", reply_markup=None)
        return

    text = "📃 <b>DANH SÁCH GIÁ RESELLER</b>\n\n"
    for row in rows:
        user_label = f"@{row['username']}" if row["username"] else (row["first_name"] or "không có tên")
        text += (
            f"User: <code>{row['user_id']}</code> ({escape(user_label)})\n"
            f"Sản phẩm: <code>{row['product_id']}</code> - {escape(row['product_name'] or '')}\n"
            f"Giá reseller: <b>{money(row['price'])}</b>\n\n"
        )
    await message.answer(text[:3900], reply_markup=None)


async def reseller_prices_text(limit: int = 30) -> str:
    rows = await get_reseller_prices(limit)
    text = "🏷 <b>Thiết lập giá reseller</b>\n\n"
    if not rows:
        text += "Chưa có giá reseller nào.\n"
    else:
        text += "<b>Giá reseller đang có:</b>\n"
        for row in rows:
            user_label = f"@{row['username']}" if row["username"] else (row["first_name"] or "không có tên")
            text += (
                f"- <code>{row['user_id']}</code> ({escape(user_label)}) | "
                f"<code>{row['product_id']}</code> | <b>{money(row['price'])}</b>\n"
            )
    text += "\nBước 1/3: gửi Telegram user id của reseller."
    return text[:3900]


@dp.message(Command("admin"))
async def admin_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    await message.answer(
        "⚙️ <b>BẢNG ĐIỀU KHIỂN ADMIN</b>\n\n"
        "Dùng các nút bên dưới để quản lý shop. Nếu cần lệnh gõ tay, bấm <b>Hướng dẫn lệnh</b>.",
        reply_markup=admin_panel_menu(),
    )


async def send_admin_help(message: Message) -> None:
    product_lines = "\n".join(
        f"<code>{product_id}</code> - {product['name']} - {money(product['price'])}"
        for product_id, product in PRODUCTS.items()
    )
    await message.answer(
        "📖 <b>HƯỚNG DẪN LỆNH ADMIN</b>\n\n"
        "<b>Kho key</b>\n"
        "/addkey mã_sản_phẩm KEY\n"
        "/addkeys mã_sản_phẩm KEY1 KEY2...\n"
        "/fixkeys - tách key bị dán/gộp trong kho chưa bán\n"
        "/fixorderkey mã_đơn - sửa đơn đã lỡ giao key gộp\n"
        "/delkey KEY - xóa key chưa bán\n"
        "/stock - xem tồn kho\n"
        "/stock mã_sản_phẩm - xem key còn của sản phẩm\n\n"
        "<b>Sản phẩm</b>\n"
        "/products - xem danh sách sản phẩm\n"
        "/addproduct mã_sản_phẩm mã_nhóm tên_nhóm số_ngày giá tên_sản_phẩm\n"
        "/setprice mã_sản_phẩm giá_mới\n"
        "/setdays mã_sản_phẩm số_ngày\n"
        "/setproduct mã_sản_phẩm số_ngày giá_mới tên_mới\n\n"
        "/delproduct mã_sản_phẩm - ẩn sản phẩm khỏi menu\n"
        "/restoreproduct mã_sản_phẩm - hiện lại sản phẩm\n\n"
        "<b>Reseller</b>\n"
        "/reseller user_id mã_sản_phẩm giá - thêm/sửa giá reseller\n"
        "/delreseller user_id mã_sản_phẩm - xóa giá reseller\n"
        "/resellers - xem danh sách giá reseller\n\n"
        "<b>Đơn hàng</b>\n"
        "/stats - xem tổng quan shop\n"
        "/pending - xem đơn đang chờ\n"
        "/orders - xem đơn gần nhất\n"
        "/find mã_đơn hoặc mã_ck - tra cứu đơn\n"
        "/user id_người_dùng - xem thông tin user\n"
        "/history id_người_dùng - xem lịch sử mua\n"
        "/confirm mã_đơn - xác nhận thủ công\n\n"
        "/resend mã_đơn - gửi lại key cho khách\n"
        "/cancel mã_đơn - hủy đơn đang chờ\n"
        "/revenue today|7d|month|all - xem doanh thu\n"
        "/lowstock - xem sản phẩm sắp hết key\n"
        "/backup - tải file database\n\n"
        "/sepaylog - xem webhook SePay gần nhất\n\n"
        "<b>Thông báo</b>\n"
        "/afterkeymsg - xem lời nhắn sau khi giao key\n"
        "/setafterkey nội_dung - sửa lời nhắn sau khi giao key\n"
        "/clearafterkey - xóa lời nhắn sau khi giao key\n"
        "/notice id_người_dùng nội_dung - gửi cho một user\n"
        "/broadcast nội_dung - gửi cho toàn bộ user\n\n"
        "/broadcastphoto nội_dung - gửi ảnh cho toàn bộ user\n\n"
        "/broadcastvideo nội_dung - gửi video cho toàn bộ user\n\n"
        "/broadcaststicker - reply sticker để gửi cho toàn bộ user\n\n"
        "<b>Danh sách Product ID:</b>\n"
        f"{product_lines}",
        reply_markup=None,
    )


@dp.message(Command("afterkeymsg", "keymessage"))
async def afterkeymsg_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    current = await get_setting(AFTER_KEY_MESSAGE_SETTING, "")
    if not current.strip():
        await message.answer(
            "Chưa có lời nhắn sau khi giao key.\n\n"
            "Thiết lập bằng: <code>/setafterkey nội_dung</code>"
        )
        return

    await message.answer(
        "<b>Lời nhắn sau khi giao key hiện tại:</b>\n\n"
        f"{current}\n\n"
        "Xóa bằng: <code>/clearafterkey</code>"
    )


@dp.message(Command("setafterkey", "setkeymessage"))
async def setafterkey_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        await message.answer(
            "<b>Cú pháp:</b>\n"
            "<code>/setafterkey nội_dung_muốn_gửi_sau_key</code>\n\n"
            "Ví dụ:\n"
            "<code>/setafterkey Sau khi nhận key, vui lòng liên hệ support nếu cần hỗ trợ.</code>"
        )
        return

    content = escape(parts[1].strip())[:1800]
    await set_setting(AFTER_KEY_MESSAGE_SETTING, content)
    await message.answer("<b>Đã lưu lời nhắn sau khi giao key:</b>\n\n" + content)


@dp.message(Command("clearafterkey", "clearkeymessage"))
async def clearafterkey_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    await delete_setting(AFTER_KEY_MESSAGE_SETTING)
    await message.answer("Đã xóa lời nhắn sau khi giao key.")


@dp.callback_query(F.data.startswith("admin_action:"))
async def admin_action_cb(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("Bạn không phải admin.", show_alert=True)
        return

    action = call.data.split(":", 1)[1]
    admin_id = call.from_user.id

    if action == "cancel":
        ADMIN_WIZARDS.pop(admin_id, None)
        await call.message.answer("Đã hủy thao tác admin.", reply_markup=None)
        await call.answer()
        return

    if action == "help":
        await send_admin_help(call.message)
        await call.answer()
        return

    if action == "add_product":
        await start_admin_wizard(
            call,
            "add_product",
            "product_id",
            "➕ <b>Thêm sản phẩm mới</b>\n\n"
            "Bước 1/6: gửi <b>mã sản phẩm</b>.\n"
            "Ví dụ: <code>aurax_7</code>",
        )
        return

    if action == "add_group_fast":
        await start_admin_wizard(
            call,
            "add_group_fast",
            "group_id",
            "⚡ <b>Thêm danh mục nhanh</b>\n\n"
            "Bot sẽ tạo nhiều gói trong một lần, ví dụ: <code>aurax_1</code>, <code>aurax_3</code>, <code>aurax_7</code>...\n\n"
            "Bước 1/5: gửi <b>mã danh mục</b>. Ví dụ: <code>aurax</code>",
        )
        return

    if action == "add_key":
        await start_admin_wizard(
            call,
            "add_key",
            "product_id",
            "🔑 <b>Thêm key vào kho</b>\n\n"
            "Bước 1/2: gửi <b>mã sản phẩm</b>.\n"
            "Ví dụ: <code>aurax_1</code>",
        )
        return

    if action == "fixkeys":
        result = await fix_composite_unused_keys()
        await call.message.answer(
            "<b>Đã quét kho key.</b>\n\n"
            f"Dòng key gộp đã tách: <b>{result['fixed_rows']}</b>\n"
            f"Key mới được thêm lại vào kho: <b>{result['inserted_keys']}</b>\n"
            f"Dòng đã bán bị bỏ qua: <b>{result['skipped_used']}</b>",
            reply_markup=None,
        )
        await call.answer()
        return

    if action == "clear_all_keys":
        await start_admin_wizard(
            call,
            "clear_all_keys",
            "confirm",
            "🧹 <b>Xóa toàn bộ kho key</b>\n\n"
            "Thao tác này sẽ xóa tất cả key trong bảng kho, gồm cả key đã bán nếu có.\n"
            "Gõ <code>XOA ALL KEY</code> để xác nhận.",
        )
        return

    if action == "clear_key_scope":
        prompt = await product_picker_text(
            "🧽 <b>Xóa key theo danh mục/sản phẩm</b>",
            "Gửi mã danh mục như <code>ngotran</code>, <code>aurax</code> hoặc mã sản phẩm như <code>aurax_1</code>.",
        )
        await start_admin_wizard(call, "clear_key_scope", "scope", prompt)
        return

    if action == "products":
        await send_products_summary(call.message)
        await call.answer()
        return

    if action == "stock":
        await send_stock_summary(call.message)
        await call.answer()
        return

    if action == "pending":
        await send_pending_summary(call.message)
        await call.answer()
        return

    if action == "orders":
        await send_orders_summary(call.message)
        await call.answer()
        return

    product_picker_actions = {
        "set_price": ("set_price", "product_id", "💰 <b>Sửa giá</b>", "Bước 1/2: gửi mã sản phẩm cần sửa giá. Ví dụ: <code>aurax_1</code>"),
        "set_days": ("set_days", "product_id", "🗓 <b>Sửa số ngày</b>", "Bước 1/2: gửi mã sản phẩm cần sửa ngày. Ví dụ: <code>aurax_1</code>"),
        "set_product": ("set_product", "product_id", "✏️ <b>Sửa sản phẩm</b>", "Bước 1/4: gửi mã sản phẩm cần sửa. Ví dụ: <code>aurax_1</code>"),
        "hide_product": ("toggle_product", "product_id", "🙈 <b>Ẩn sản phẩm khỏi menu user</b>", "Gửi mã sản phẩm cần ẩn.", {"active": 0}),
        "restore_product": ("toggle_product", "product_id", "👁 <b>Hiện lại sản phẩm</b>", "Gửi mã sản phẩm cần hiện lại.", {"active": 1}),
        "remove_reseller_price": ("remove_reseller_price", "user_id", "🗑 <b>Xóa giá reseller</b>", "Bước 1/2: gửi Telegram user id của reseller."),
    }
    if action in product_picker_actions:
        flow, step, title, instruction, *rest = product_picker_actions[action]
        prompt = await product_picker_text(title, instruction)
        await start_admin_wizard(call, flow, step, prompt, rest[0] if rest else None)
        return

    if action == "reseller_price":
        await start_admin_wizard(call, "reseller_price", "user_id", await reseller_prices_text())
        return

    if action == "reseller_prices":
        await send_reseller_prices_summary(call.message)
        await call.answer()
        return

    if action == "afterkey_message":
        current = (await get_setting(AFTER_KEY_MESSAGE_SETTING, "")).strip()
        current_block = current if current else "Chưa có lời nhắn sau key."
        await start_admin_wizard(
            call,
            "afterkey_message",
            "content",
            "📝 <b>Chỉnh lời nhắn sau khi giao key</b>\n\n"
            "<b>Hiện tại:</b>\n"
            f"{current_block}\n\n"
            "Gửi nội dung mới để bot tự đính kèm dưới phần key.\n"
            "Gõ <code>xóa</code> để xóa lời nhắn hiện tại.",
        )
        return

    wizard_prompts = {
        "find_order": ("find_order", "query", "🔎 <b>Tìm đơn</b>\n\nGửi mã đơn hoặc mã chuyển khoản. Ví dụ: <code>24</code> hoặc <code>MUA ABCD1234</code>"),
        "confirm_order": ("confirm_order", "order_id", "✅ <b>Xác nhận đơn thủ công</b>\n\nGửi mã đơn cần xác nhận. Ví dụ: <code>24</code>"),
        "cancel_order": ("cancel_order", "order_id", "🚫 <b>Hủy đơn đang chờ</b>\n\nGửi mã đơn cần hủy. Ví dụ: <code>24</code>"),
        "resend_order": ("resend_order", "order_id", "📨 <b>Gửi lại key</b>\n\nGửi mã đơn đã paid cần gửi lại key. Ví dụ: <code>24</code>"),
        "user_info": ("user_info", "user_id", "👤 <b>Xem user</b>\n\nGửi Telegram user id."),
        "history": ("history", "user_id", "📜 <b>Lịch sử user</b>\n\nGửi Telegram user id."),
        "revenue": ("revenue", "period", "💵 <b>Xem doanh thu</b>\n\nGửi một trong các giá trị: <code>today</code>, <code>7d</code>, <code>month</code>, <code>all</code>."),
        "notice": ("notice", "user_id", "📣 <b>Gửi thông báo cho 1 user</b>\n\nBước 1/2: gửi Telegram user id."),
        "broadcast": ("broadcast", "content", "📢 <b>Broadcast toàn bộ user</b>\n\nGửi nội dung cần gửi cho tất cả user."),
    }
    wizard_prompts["delete_key"] = (
        "delete_key",
        "license_key",
        "🗑 <b>Xóa 1 key</b>\n\nGửi chính xác key cần xóa. Bot chỉ xóa key chưa bán.",
    )

    if action in wizard_prompts:
        flow, step, prompt, *rest = wizard_prompts[action]
        await start_admin_wizard(call, flow, step, prompt, rest[0] if rest else None)
        return

    if action == "lowstock":
        rows = await get_low_stock(3)
        if not rows:
            await call.message.answer("Không có sản phẩm nào tồn kho thấp.", reply_markup=None)
        else:
            text = "⚠️ <b>SẢN PHẨM SẮP HẾT KEY</b>\n\n"
            for row in rows:
                text += f"<code>{row['product_id']}</code> - {row['name']}: <b>{row['stock']}</b> key\n"
            await call.message.answer(text, reply_markup=None)
        await call.answer()
        return

    if action == "sepaylog":
        rows = await get_sepay_logs(10)
        if not rows:
            await call.message.answer("Chưa có log webhook SePay nào.", reply_markup=None)
        else:
            text = "🧾 <b>WEBHOOK SEPAY GẦN NHẤT</b>\n\n"
            for row in rows:
                text += (
                    f"ID: <code>{escape(str(row['sepay_id']))}</code>\n"
                    f"Mã CK: <code>{escape(row['payment_code'] or 'không có')}</code>\n"
                    f"Số tiền: <b>{money(row['amount'] or 0)}</b>\n"
                    f"Lúc: {row['created_at']}\n\n"
                )
            await call.message.answer(text[:3900], reply_markup=None)
        await call.answer()
        return

    if action == "backup":
        if not os.path.exists(DB_PATH):
            await call.message.answer("Không tìm thấy file database.", reply_markup=None)
        else:
            await call.message.answer_document(FSInputFile(DB_PATH, filename="shop.db"), caption="Backup database hiện tại.")
        await call.answer()
        return

    if action == "broadcastphoto_help":
        await call.message.answer(
            "<b>Cách broadcast ảnh</b>\n\n"
            "Telegram cần bạn gửi ảnh thật, nên thao tác này dùng lệnh cũ:\n\n"
            "Cách 1: gửi ảnh kèm caption:\n"
            "<code>/broadcastphoto Nội dung thông báo</code>\n\n"
            "Cách 2: reply vào một ảnh bằng:\n"
            "<code>/broadcastphoto Nội dung thông báo</code>",
            reply_markup=None,
        )
        await call.answer()
        return

    if action == "broadcastvideo_help":
        await call.message.answer(
            "<b>Cách broadcast video</b>\n\n"
            "Cách 1: gửi video kèm caption:\n"
            "<code>/broadcastvideo Nội dung thông báo</code>\n\n"
            "Cách 2: reply vào một video bằng:\n"
            "<code>/broadcastvideo Nội dung thông báo</code>",
            reply_markup=None,
        )
        await call.answer()
        return

    if action == "broadcaststicker_help":
        await call.message.answer(
            "<b>Cách broadcast sticker</b>\n\n"
            "Gửi sticker vào chat admin, sau đó reply vào sticker bằng:\n"
            "<code>/broadcaststicker</code>\n\n"
            "Bot sẽ thử gửi lại riêng user thất bại tối đa 3 lần và cho phép thu hồi nếu gửi nhầm.",
            reply_markup=None,
        )
        await call.answer()
        return

    await call.answer("Chức năng chưa hỗ trợ.", show_alert=True)


@dp.message(lambda message: bool(message.from_user and message.from_user.id in ADMIN_WIZARDS))
async def admin_wizard_message(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    state = ADMIN_WIZARDS.get(message.from_user.id)
    if not state:
        return

    value = (message.text or "").strip()
    if value.lower() in {"hủy", "huy", "cancel", "thoát", "thoat"}:
        ADMIN_WIZARDS.pop(message.from_user.id, None)
        await message.answer("Đã hủy thao tác admin.", reply_markup=None)
        return

    if value.startswith("/"):
        await message.answer("Bạn đang trong wizard admin. Gõ <code>hủy</code> để thoát trước khi dùng lệnh khác.")
        return

    if state["flow"] == "add_product":
        await handle_add_product_wizard(message, state, value)
        return

    if state["flow"] == "add_group_fast":
        await handle_add_group_fast_wizard(message, state, value)
        return

    if state["flow"] == "add_key":
        await handle_add_key_wizard(message, state, value)
        return

    await handle_admin_simple_wizard(message, state, value)


async def handle_add_product_wizard(message: Message, state: dict, value: str) -> None:
    data = state["data"]
    step = state["step"]

    if step == "product_id":
        product_id = normalize_id(value)
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,48}", product_id):
            await message.answer("Mã sản phẩm không hợp lệ. Ví dụ đúng: <code>aurax_7</code>")
            return
        data["product_id"] = product_id
        state["step"] = "group_id"
        await message.answer("Bước 2/6: gửi <b>mã nhóm</b>. Ví dụ: <code>aurax</code>", reply_markup=admin_cancel_menu())
        return

    if step == "group_id":
        group_id = normalize_id(value)
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,48}", group_id):
            await message.answer("Mã nhóm không hợp lệ. Ví dụ đúng: <code>aurax</code>")
            return
        data["group_id"] = group_id
        state["step"] = "group_title"
        await message.answer("Bước 3/6: gửi <b>tên nhóm hiển thị</b>. Ví dụ: <code>AuraX</code>", reply_markup=admin_cancel_menu())
        return

    if step == "group_title":
        if not value:
            await message.answer("Tên nhóm không được để trống.")
            return
        data["group_title"] = value
        state["step"] = "days"
        await message.answer("Bước 4/6: gửi <b>số ngày</b>. Ví dụ: <code>7</code>", reply_markup=admin_cancel_menu())
        return

    if step == "days":
        if not value.isdigit() or int(value) <= 0:
            await message.answer("Số ngày phải là số nguyên dương. Ví dụ: <code>7</code>")
            return
        data["days"] = int(value)
        state["step"] = "price"
        await message.answer("Bước 5/6: gửi <b>giá</b>. Ví dụ: <code>70000</code>", reply_markup=admin_cancel_menu())
        return

    if step == "price":
        cleaned = value.replace(".", "").replace(",", "")
        if not cleaned.isdigit() or int(cleaned) <= 0:
            await message.answer("Giá phải là số nguyên dương. Ví dụ: <code>70000</code>")
            return
        data["price"] = int(cleaned)
        state["step"] = "name"
        await message.answer("Bước 6/6: gửi <b>tên sản phẩm hiển thị</b>. Ví dụ: <code>AuraX - 7 ngày</code>", reply_markup=admin_cancel_menu())
        return

    if step == "name":
        if not value:
            await message.answer("Tên sản phẩm không được để trống.")
            return

        data["name"] = value
        await upsert_product(
            data["product_id"],
            data["group_id"],
            data["group_title"],
            "â­",
            data["name"],
            data["price"],
            data["days"],
            1,
        )
        await refresh_product_cache()
        ADMIN_WIZARDS.pop(message.from_user.id, None)
        await message.answer(
            "✅ <b>Đã thêm/cập nhật sản phẩm.</b>\n\n"
            f"Mã sản phẩm: <code>{data['product_id']}</code>\n"
            f"Nhóm: <b>{escape(data['group_title'])}</b> (<code>{data['group_id']}</code>)\n"
            f"Tên: <b>{escape(data['name'])}</b>\n"
            f"Số ngày: <b>{data['days']}</b>\n"
            f"Giá: <b>{money(data['price'])}</b>\n\n"
            "Tiếp theo hãy thêm key bằng nút <b>Thêm key</b> hoặc lệnh /addkeys.",
            reply_markup=None,
        )


async def handle_add_group_fast_wizard(message: Message, state: dict, value: str) -> None:
    data = state["data"]
    step = state["step"]

    if step == "group_id":
        group_id = normalize_id(value)
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,48}", group_id):
            await message.answer("Mã danh mục không hợp lệ. Ví dụ đúng: <code>aurax</code>")
            return
        data["group_id"] = group_id
        state["step"] = "group_title"
        await message.answer("Bước 2/5: gửi <b>tên danh mục hiển thị</b>. Ví dụ: <code>AuraX</code>", reply_markup=admin_cancel_menu())
        return

    if step == "group_title":
        if not value:
            await message.answer("Tên danh mục không được để trống.")
            return
        data["group_title"] = value
        state["step"] = "days"
        await message.answer(
            "Bước 3/5: gửi danh sách ngày cần tạo.\n\n"
            "Ví dụ: <code>1 3 7 15 30</code>",
            reply_markup=admin_cancel_menu(),
        )
        return

    if step == "days":
        days = parse_int_list(value)
        if not days:
            await message.answer("Danh sách ngày không hợp lệ. Ví dụ: <code>1 3 7 15 30</code>")
            return
        if len(days) != len(set(days)):
            await message.answer("Danh sách ngày bị trùng. Hãy gửi lại, ví dụ: <code>1 3 7 15 30</code>")
            return
        data["days"] = days
        state["step"] = "prices"
        await message.answer(
            "Bước 4/5: gửi danh sách giá tương ứng theo đúng thứ tự ngày.\n\n"
            f"Ngày: <code>{' '.join(str(day) for day in days)}</code>\n"
            "Ví dụ: <code>25000 50000 80000 120000 180000</code>",
            reply_markup=admin_cancel_menu(),
        )
        return

    if step == "prices":
        prices = parse_int_list(value)
        if not prices:
            await message.answer("Danh sách giá không hợp lệ. Ví dụ: <code>25000 50000 80000 120000 180000</code>")
            return
        if len(prices) != len(data["days"]):
            await message.answer(
                f"Số lượng giá phải bằng số lượng ngày. Bạn đã gửi {len(prices)} giá cho {len(data['days'])} gói."
            )
            return
        data["prices"] = prices
        state["step"] = "emoji"
        await message.answer(
            "Bước 5/5: gửi emoji cho sản phẩm. Ví dụ: <code>⭐</code> hoặc <code>🔥</code>.\n"
            "Gõ <code>giữ</code> để dùng <code>⭐</code>.",
            reply_markup=admin_cancel_menu(),
        )
        return

    if step == "emoji":
        emoji = "â­" if wants_keep_current(value) else value.strip()
        if not emoji:
            emoji = "â­"

        created = []
        for days, price in zip(data["days"], data["prices"]):
            product_id = f"{data['group_id']}_{days}"
            name = f"{data['group_title']} - {days} ngày"
            await upsert_product(
                product_id,
                data["group_id"],
                data["group_title"],
                emoji,
                name,
                price,
                days,
                1,
            )
            created.append((product_id, name, days, price))

        await refresh_product_cache()
        ADMIN_WIZARDS.pop(message.from_user.id, None)

        text = "✅ <b>Đã tạo/cập nhật danh mục nhanh.</b>\n\n"
        text += f"Danh mục: <b>{escape(data['group_title'])}</b> (<code>{data['group_id']}</code>)\n\n"
        for product_id, name, days, price in created:
            text += f"- <code>{product_id}</code> | {escape(name)} | {days} ngày | <b>{money(price)}</b>\n"
        text += "\nTiếp theo hãy thêm key bằng nút <b>Thêm key</b>."
        await message.answer(text[:3900], reply_markup=None)


async def handle_add_key_wizard(message: Message, state: dict, value: str) -> None:
    data = state["data"]
    step = state["step"]

    if step == "product_id":
        product_id = normalize_id(value)
        if product_id not in PRODUCTS:
            await message.answer("Mã sản phẩm không tồn tại. Dùng /products để xem danh sách.")
            return
        data["product_id"] = product_id
        state["step"] = "keys"
        await message.answer(
            "Bước 2/2: gửi danh sách key.\n\n"
            "Có thể paste mỗi key một dòng hoặc cách nhau bằng khoảng trắng/dấu phẩy.",
            reply_markup=admin_cancel_menu(),
        )
        return

    if step == "keys":
        try:
            raw_keys = await extract_key_text(message, value)
        except ValueError as error:
            await message.answer(str(error))
            return

        license_keys = parse_license_keys(raw_keys)
        if not license_keys:
            await message.answer("Chưa đọc được key nào. Hãy gửi lại danh sách key.")
            return

        count = 0
        for license_key in license_keys:
            if await add_key(data["product_id"], license_key):
                count += 1

        ADMIN_WIZARDS.pop(message.from_user.id, None)
        skipped = len(license_keys) - count
        await message.answer(
            f"✅ Đã thêm <b>{count}</b>/{len(license_keys)} key cho <code>{data['product_id']}</code>."
            + (f"\nBỏ qua <b>{skipped}</b> key trùng/không hợp lệ." if skipped else ""),
            reply_markup=None,
        )


async def handle_admin_simple_wizard(message: Message, state: dict, value: str) -> None:
    data = state["data"]
    flow = state["flow"]
    step = state["step"]
    admin_id = message.from_user.id

    if flow == "delete_key":
        ADMIN_WIZARDS.pop(admin_id, None)
        result = await delete_unused_key(value)
        msg = result.get("msg")
        if result.get("ok"):
            await message.answer(f"✅ Đã xóa key chưa bán:\n<code>{escape(value)}</code>", reply_markup=None)
        elif msg == "key_already_used":
            await message.answer("Key này đã bán/giao cho khách, không thể xóa bằng chức năng này.", reply_markup=None)
        else:
            await message.answer("Không tìm thấy key này trong kho.", reply_markup=None)
        return

    if flow == "clear_all_keys":
        if normalize_id(value) not in {"xoa all key", "xóa all key"}:
            await message.answer("Chưa xóa. Gõ đúng <code>XOA ALL KEY</code> để xác nhận, hoặc gõ <code>hủy</code>.")
            return

        result = await clear_all_keys()
        ADMIN_WIZARDS.pop(admin_id, None)
        await message.answer(
            "✅ <b>Đã xóa toàn bộ kho key.</b>\n\n"
            f"Số key đã xóa: <b>{result['deleted']}</b>\n"
            f"Trong đó key đã dùng: <b>{result['used_deleted']}</b>",
            reply_markup=None,
        )
        return

    if flow == "clear_key_scope":
        if step == "scope":
            scope = normalize_id(value)
            rows = await get_products(active_only=False)
            product_ids = {row["product_id"] for row in rows}
            groups = {row["group_id"]: row["group_title"] for row in rows}

            if scope in product_ids:
                data["scope_type"] = "product"
                data["scope_id"] = scope
                state["step"] = "confirm"
                await message.answer(
                    "Bạn sắp xóa toàn bộ key của sản phẩm:\n"
                    f"<code>{scope}</code>\n\n"
                    "Gõ <code>XOA</code> để xác nhận.",
                    reply_markup=admin_cancel_menu(),
                )
                return

            if scope in groups:
                data["scope_type"] = "group"
                data["scope_id"] = scope
                state["step"] = "confirm"
                await message.answer(
                    "Bạn sắp xóa toàn bộ key của danh mục:\n"
                    f"<b>{escape(groups[scope] or scope)}</b> (<code>{scope}</code>)\n\n"
                    "Gõ <code>XOA</code> để xác nhận.",
                    reply_markup=admin_cancel_menu(),
                )
                return

            await message.answer("Không tìm thấy mã danh mục/sản phẩm này. Ví dụ đúng: <code>ngotran</code> hoặc <code>ngotran_1</code>.")
            return

        if step == "confirm":
            if normalize_id(value) not in {"xoa", "xóa"}:
                await message.answer("Chưa xóa. Gõ <code>XOA</code> để xác nhận, hoặc gõ <code>hủy</code>.")
                return

            if data["scope_type"] == "product":
                result = await clear_keys_for_product(data["scope_id"])
                label = f"sản phẩm <code>{data['scope_id']}</code>"
            else:
                result = await clear_keys_for_group(data["scope_id"])
                label = f"danh mục <code>{data['scope_id']}</code>"

            ADMIN_WIZARDS.pop(admin_id, None)
            await message.answer(
                f"✅ <b>Đã xóa key của {label}.</b>\n\n"
                f"Số key đã xóa: <b>{result.get('deleted', 0)}</b>\n"
                f"Trong đó key đã dùng: <b>{result.get('used_deleted', 0)}</b>"
                if result.get("ok") else f"Không xóa được: <code>{result.get('msg')}</code>",
                reply_markup=None,
            )
            return

    if flow in {"set_price", "set_days", "set_product", "toggle_product"} and step == "product_id":
        product_id = normalize_id(value)
        product_row = await get_product(product_id, active_only=False)
        if not product_row:
            await message.answer("Mã sản phẩm không tồn tại. Dùng nút Sản phẩm hoặc lệnh /products để xem danh sách.")
            return
        data["product_id"] = product_id
        data["current_days"] = int(product_row["days"] or 0)
        data["current_price"] = int(product_row["price"])
        data["current_name"] = product_row["name"]

        if flow == "set_price":
            state["step"] = "price"
            await message.answer("Bước 2/2: gửi giá mới. Ví dụ: <code>0</code> hoặc <code>70000</code>", reply_markup=admin_cancel_menu())
            return

        if flow == "set_days":
            state["step"] = "days"
            await message.answer("Bước 2/2: gửi số ngày mới. Ví dụ: <code>7</code>", reply_markup=admin_cancel_menu())
            return

        if flow == "set_product":
            state["step"] = "days"
            await message.answer(
                "Bước 2/4: gửi số ngày mới.\n\n"
                f"Hiện tại: <b>{data['current_days']}</b> ngày\n"
                "Gõ <code>giữ</code> nếu không đổi.",
                reply_markup=admin_cancel_menu(),
            )
            return

        active = int(data["active"])
        result = await set_product_active(product_id, active)
        await refresh_product_cache()
        ADMIN_WIZARDS.pop(admin_id, None)
        if not result.get("ok"):
            await message.answer("Mã sản phẩm không tồn tại.", reply_markup=None)
            return
        await message.answer(
            ("Đã hiện lại" if active else "Đã ẩn")
            + f" sản phẩm <code>{product_id}</code>.",
            reply_markup=None,
        )
        return

    if flow in {"reseller_price", "remove_reseller_price"}:
        if step == "user_id":
            if not value.isdigit():
                await message.answer("User id phải là số.")
                return
            data["user_id"] = int(value)
            state["step"] = "product_id"
            prompt = await product_picker_text(
                "🏷 <b>Giá reseller</b>",
                "Bước 2/3: gửi mã sản phẩm cần áp dụng/xóa giá reseller.",
            )
            await message.answer(prompt, reply_markup=admin_cancel_menu())
            return

        if step == "product_id":
            product_id = normalize_id(value)
            product_row = await get_product(product_id, active_only=False)
            if not product_row:
                await message.answer("Mã sản phẩm không tồn tại.")
                return
            data["product_id"] = product_id
            if flow == "remove_reseller_price":
                result = await remove_reseller_price(data["user_id"], data["product_id"])
                ADMIN_WIZARDS.pop(admin_id, None)
                await message.answer(
                    f"Đã xóa giá reseller cho user <code>{data['user_id']}</code> / <code>{data['product_id']}</code>."
                    if result.get("ok") else "Không tìm thấy giá reseller đang active cho user/product này.",
                    reply_markup=None,
                )
                return
            state["step"] = "price"
            await message.answer("Bước 3/3: gửi giá reseller. Ví dụ: <code>15000</code>", reply_markup=admin_cancel_menu())
            return

        if step == "price":
            price = parse_positive_int(value)
            if price is None:
                await message.answer("Giá reseller phải là số nguyên dương. Ví dụ: <code>15000</code>")
                return

            user = await get_user(data["user_id"])
            if not user:
                await message.answer(
                    "User này chưa có trong database. Khách cần bấm /start trong bot trước.",
                    reply_markup=None,
                )
                ADMIN_WIZARDS.pop(admin_id, None)
                return

            result = await set_reseller_price(data["user_id"], data["product_id"], price)
            ADMIN_WIZARDS.pop(admin_id, None)
            await message.answer(
                "✅ <b>Đã lưu giá reseller.</b>\n\n"
                f"User: <code>{user['user_id']}</code>\n"
                f"Sản phẩm: <code>{data['product_id']}</code>\n"
                f"Giá reseller: <b>{money(price)}</b>\n\n"
                "Từ giờ user này mua sản phẩm đó sẽ tự dùng giá reseller."
                if result.get("ok") else "Không lưu được giá reseller.",
                reply_markup=None,
            )
            return

    if flow == "set_price" and step == "price":
        price = parse_price_int(value)
        if price is None:
            await message.answer("Giá phải là số nguyên không âm. Ví dụ: <code>0</code> hoặc <code>70000</code>")
            return
        result = await update_product_fields(data["product_id"], price=price)
        await refresh_product_cache()
        ADMIN_WIZARDS.pop(admin_id, None)
        await message.answer(
            f"Đã cập nhật giá <code>{data['product_id']}</code> thành <b>{money(price)}</b>."
            if result.get("ok") else "Mã sản phẩm không tồn tại.",
            reply_markup=None,
        )
        return

    if flow == "set_days" and step == "days":
        days = parse_positive_int(value)
        if days is None:
            await message.answer("Số ngày phải là số nguyên dương. Ví dụ: <code>7</code>")
            return
        product = PRODUCTS.get(data["product_id"])
        name = None
        if product:
            base_name = product["name"].split(" - ", 1)[0]
            name = f"{base_name} - {days} ngày"
        result = await update_product_fields(data["product_id"], days=days, name=name)
        await refresh_product_cache()
        ADMIN_WIZARDS.pop(admin_id, None)
        await message.answer(
            f"Đã cập nhật <code>{data['product_id']}</code> thành <b>{days}</b> ngày."
            if result.get("ok") else "Mã sản phẩm không tồn tại.",
            reply_markup=None,
        )
        return

    if flow == "set_product":
        if step == "days":
            if wants_keep_current(value):
                days = int(data.get("current_days") or 0)
            else:
                days = parse_positive_int(value)
                if days is None:
                    await message.answer("Số ngày phải là số nguyên dương. Ví dụ: <code>7</code>. Hoặc gõ <code>giữ</code> để không đổi.")
                    return
            data["days"] = days
            state["step"] = "price"
            await message.answer(
                "Bước 3/4: gửi giá mới.\n\n"
                f"Hiện tại: <b>{money(data['current_price'])}</b>\n"
                "Gửi <code>0</code> nếu muốn miễn phí, hoặc gõ <code>giữ</code> nếu không đổi.",
                reply_markup=admin_cancel_menu(),
            )
            return
        if step == "price":
            if wants_keep_current(value):
                price = int(data["current_price"])
            else:
                price = parse_price_int(value)
                if price is None:
                    await message.answer("Giá phải là số nguyên không âm. Ví dụ: <code>0</code> hoặc <code>70000</code>. Hoặc gõ <code>giữ</code> để không đổi.")
                    return
            data["price"] = price
            state["step"] = "name"
            await message.answer(
                "Bước 4/4: gửi tên hiển thị mới.\n\n"
                f"Hiện tại: <b>{escape(data['current_name'])}</b>\n"
                "Gõ <code>giữ</code> nếu không đổi.",
                reply_markup=admin_cancel_menu(),
            )
            return
        if step == "name":
            if wants_keep_current(value):
                value = data["current_name"]
            elif not value:
                await message.answer("Tên sản phẩm không được để trống.")
                return
            result = await update_product_fields(data["product_id"], price=data["price"], days=data["days"], name=value)
            await refresh_product_cache()
            ADMIN_WIZARDS.pop(admin_id, None)
            await message.answer(
                f"Đã cập nhật <code>{data['product_id']}</code>:\n"
                f"Tên: <b>{escape(value)}</b>\n"
                f"Ngày: <b>{data['days']}</b>\n"
                f"Giá: <b>{money(data['price'])}</b>"
                if result.get("ok") else "Mã sản phẩm không tồn tại.",
                reply_markup=None,
            )
            return

    if flow == "find_order":
        ADMIN_WIZARDS.pop(admin_id, None)
        await send_order_detail(message, value)
        return

    if flow in {"confirm_order", "cancel_order", "resend_order"}:
        if not value.isdigit():
            await message.answer("Mã đơn phải là số. Ví dụ: <code>24</code>")
            return
        order_id = int(value)
        ADMIN_WIZARDS.pop(admin_id, None)

        if flow == "confirm_order":
            result = await fulfill_order(order_id)
            if result.get("ok") and not result.get("already"):
                await send_fulfilled_order(result, "admin")
                await message.answer(f"Đã xác nhận và gửi key cho đơn #{order_id}.", reply_markup=None)
            else:
                await message.answer(f"Không thể xác nhận đơn #{order_id}: <code>{result.get('msg')}</code>", reply_markup=None)
            return

        if flow == "cancel_order":
            result = await cancel_pending_order(order_id)
            await message.answer(
                f"Đã hủy đơn #{order_id}." if result.get("ok") else f"Không thể hủy đơn #{order_id}: <code>{result.get('msg')}</code>",
                reply_markup=None,
            )
            return

        order = await get_order_with_key(order_id)
        if not order:
            await message.answer("Không tìm thấy đơn hàng.", reply_markup=None)
            return
        if order["status"] != "paid" or not order["license_key"]:
            await message.answer("Đơn này chưa paid hoặc chưa có key để gửi lại.", reply_markup=None)
            return
        after_key_message = await get_setting(AFTER_KEY_MESSAGE_SETTING, "")
        await bot.send_message(order["user_id"], paid_text(order, order["license_key"], after_key_message))
        await message.answer(f"Đã gửi lại key cho đơn #{order_id}.", reply_markup=None)
        return

    if flow in {"user_info", "history"}:
        if not value.isdigit():
            await message.answer("User id phải là số.")
            return
        ADMIN_WIZARDS.pop(admin_id, None)
        if flow == "user_info":
            await send_user_summary(message, int(value))
        else:
            await send_history_summary(message, int(value))
        return

    if flow == "afterkey_message":
        content = value.strip()
        if not content:
            await message.answer("Nội dung lời nhắn sau key không được để trống.")
            return

        ADMIN_WIZARDS.pop(admin_id, None)
        if normalize_id(content) in {"xoa", "xóa", "clear", "delete"}:
            await delete_setting(AFTER_KEY_MESSAGE_SETTING)
            await message.answer("Đã xóa lời nhắn sau khi giao key.", reply_markup=None)
            return

        content = escape(content)[:1800]
        await set_setting(AFTER_KEY_MESSAGE_SETTING, content)
        await message.answer("<b>Đã lưu lời nhắn sau khi giao key:</b>\n\n" + content, reply_markup=None)
        return

    if flow == "revenue":
        period = value.lower()
        if period not in {"today", "7d", "month", "all"}:
            await message.answer("Chỉ nhận: <code>today</code>, <code>7d</code>, <code>month</code>, <code>all</code>.")
            return
        ADMIN_WIZARDS.pop(admin_id, None)
        await send_revenue_summary(message, period)
        return

    if flow == "notice":
        if step == "user_id":
            if not value.isdigit():
                await message.answer("User id phải là số.")
                return
            data["user_id"] = int(value)
            state["step"] = "content"
            await message.answer(
                "Bước 2/2: gửi nội dung thông báo.\nSau khi gửi xong bot sẽ trả về nút thu hồi.",
                reply_markup=admin_cancel_menu(),
            )
            return
        if step == "content":
            if not value:
                await message.answer("Nội dung thông báo không được để trống.")
                return
            ADMIN_WIZARDS.pop(admin_id, None)
            try:
                sent_message = await bot.send_message(data["user_id"], f"📢 <b>THÔNG BÁO TỪ SHOP</b>\n\n{escape(value)}")
            except Exception as error:
                await message.answer(f"Không gửi được thông báo: <code>{escape(str(error))}</code>", reply_markup=None)
                return
            broadcast_id = remember_broadcast(
                "notice",
                [BroadcastDelivery(user_id=data["user_id"], message_id=int(sent_message.message_id))],
            )
            await message.answer(
                f"Đã gửi thông báo đến user <code>{data['user_id']}</code>.",
                reply_markup=recall_broadcast_menu(broadcast_id),
            )
            return

    if flow == "broadcast":
        if not value:
            await message.answer("Nội dung broadcast không được để trống.")
            return
        ADMIN_WIZARDS.pop(admin_id, None)
        users = await get_all_users()
        await message.answer(f"Đang gửi thông báo đến {len(users)} user...")
        deliveries = []
        sent = 0
        failed = 0
        for user in users:
            try:
                sent_message = await bot.send_message(user["user_id"], f"📢 <b>THÔNG BÁO TỪ SHOP</b>\n\n{escape(value)}")
                sent += 1
                deliveries.append(BroadcastDelivery(user_id=int(user["user_id"]), message_id=int(sent_message.message_id)))
                await asyncio.sleep(0.05)
            except Exception:
                failed += 1
        await message.answer(
            f"Broadcast xong.\nThành công: <b>{sent}</b>\nLỗi: <b>{failed}</b>",
            reply_markup=recall_broadcast_menu(remember_broadcast("broadcast", deliveries)) if deliveries else None,
        )
        return

    await message.answer("Wizard chưa hỗ trợ bước này. Gõ <code>hủy</code> để thoát.")


@dp.message(Command("addkey", "addkeys"))
async def addkey_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 2:
        await message.answer(
            "Cú pháp:\n"
            "<code>/addkey ngotran_1 KEY-ABC-123</code>\n"
            "<code>/addkeys ngotran_1 KEY-001 KEY-002 KEY-003</code>\n\n"
            "Hoặc reply vào tin nhắn chứa danh sách key bằng:\n"
            "<code>/addkeys ngotran_1</code>"
        )
        return

    product_id = parts[1]
    if product_id not in PRODUCTS:
        await message.answer("Product ID không tồn tại.")
        return

    try:
        raw_keys = await extract_key_text(message, parts[2] if len(parts) >= 3 else "")
    except ValueError as error:
        await message.answer(str(error))
        return

    license_keys = parse_license_keys(raw_keys)
    if not license_keys:
        await message.answer("Chưa có key để thêm.")
        return

    count = 0
    for license_key in license_keys:
        if license_key and await add_key(product_id, license_key):
            count += 1

    skipped = len(license_keys) - count
    await message.answer(
        f"Đã thêm <b>{count}</b>/{len(license_keys)} key cho <code>{product_id}</code>."
        + (f"\nBỏ qua <b>{skipped}</b> key trùng/không hợp lệ." if skipped else "")
    )


@dp.message(Command("delkey", "removekey"))
async def delkey_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        await message.answer("Cú pháp: <code>/delkey KEY-ABC-123</code>")
        return

    license_key = parts[1].strip()
    result = await delete_unused_key(license_key)
    msg = result.get("msg")
    if result.get("ok"):
        await message.answer(f"Đã xóa key chưa bán: <code>{escape(license_key)}</code>")
    elif msg == "key_already_used":
        await message.answer("Key này đã bán/giao cho khách, không thể xóa.")
    else:
        await message.answer("Không tìm thấy key này.")


@dp.message(Command("fixkeys"))
async def fixkeys_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    result = await fix_composite_unused_keys()
    await message.answer(
        "<b>Đã quét kho key.</b>\n\n"
        f"Dòng key gộp đã tách: <b>{result['fixed_rows']}</b>\n"
        f"Key mới được thêm lại vào kho: <b>{result['inserted_keys']}</b>\n"
        f"Dòng đã bán bị bỏ qua: <b>{result['skipped_used']}</b>"
    )


@dp.message(Command("fixorderkey"))
async def fixorderkey_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip().isdigit():
        await message.answer("Cú pháp: <code>/fixorderkey 24</code>")
        return

    result = await fix_paid_order_composite_key(int(parts[1]))
    if not result.get("ok"):
        await message.answer(f"Không sửa được đơn: <code>{result.get('msg')}</code>")
        return

    await message.answer(
        "<b>Đã xử lý key gộp của đơn.</b>\n\n"
        f"Key giữ lại cho đơn: <code>{escape(str(result.get('kept_key')))}</code>\n"
        f"Số key còn lại đã đánh dấu không bán lại: <b>{result.get('blocked_keys')}</b>\n"
        f"Trạng thái: <code>{result.get('msg')}</code>"
    )


@dp.message(Command("products"))
async def products_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    rows = await get_products(active_only=True)
    if not rows:
        await message.answer("Chưa có sản phẩm nào.")
        return

    text = "📋 <b>DANH SÁCH SẢN PHẨM</b>\n\n"
    current_group = None
    for row in rows:
        if row["group_id"] != current_group:
            current_group = row["group_id"]
            text += f"<b>{row['group_title']}</b> (<code>{row['group_id']}</code>)\n"
        text += (
            f"- <code>{row['product_id']}</code> | {row['emoji']} {row['name']} | "
            f"{int(row['days'] or 0)} ngày | <b>{money(row['price'])}</b>\n"
        )

    await message.answer(text[:3900])


@dp.message(Command("addproduct"))
async def addproduct_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=6)
    if len(parts) < 7 or not parts[4].isdigit() or not parts[5].isdigit():
        await message.answer(
            "<b>Cú pháp thêm sản phẩm:</b>\n"
            "<code>/addproduct mã_sản_phẩm mã_nhóm tên_nhóm số_ngày giá tên_sản_phẩm</code>\n\n"
            "<b>Chú thích:</b>\n"
            "- <code>mã_sản_phẩm</code>: mã nội bộ, không dấu, không khoảng trắng. Ví dụ: <code>ngotran_60</code>\n"
            "- <code>mã_nhóm</code>: nhóm sản phẩm. Ví dụ: <code>ngotran</code>\n"
            "- <code>tên_nhóm</code>: tên nhóm hiển thị. Ví dụ: <code>NgoTran</code>\n"
            "- <code>số_ngày</code>: thời hạn sử dụng, chỉ nhập số. Ví dụ: <code>60</code>\n"
            "- <code>giá</code>: giá tiền, chỉ nhập số. Ví dụ: <code>250000</code>\n"
            "- <code>tên_sản_phẩm</code>: tên khách nhìn thấy.\n\n"
            "Ví dụ:\n"
            "<code>/addproduct ngotran_60 ngotran NgoTran 60 250000 NgoTran - 60 ngày</code>"
        )
        return

    product_id = parts[1].strip()
    group_id = parts[2].strip()
    group_title = parts[3].strip()
    days = int(parts[4])
    price = int(parts[5])
    name = parts[6].strip()
    emoji = PRODUCTS.get(next((pid for pid, p in PRODUCTS.items() if p["group"] == group_id), ""), {}).get("emoji", "â­")

    await upsert_product(product_id, group_id, group_title, emoji, name, price, days)
    await refresh_product_cache()
    await message.answer(
        f"Đã thêm/cập nhật sản phẩm <code>{product_id}</code>:\n"
        f"{emoji} <b>{name}</b>\n"
        f"Ngày: <b>{days}</b>\n"
        f"Giá: <b>{money(price)}</b>"
    )


@dp.message(Command("setprice"))
async def setprice_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) != 3 or not parts[2].strip().isdigit():
        await message.answer(
            "<b>Cú pháp sửa giá:</b>\n"
            "<code>/setprice mã_sản_phẩm giá_mới</code>\n\n"
            "Ví dụ:\n"
            "<code>/setprice ngotran_30 160000</code>"
        )
        return

    product_id = parts[1].strip()
    price = int(parts[2].strip())
    result = await update_product_fields(product_id, price=price)
    if not result.get("ok"):
        await message.answer("Mã sản phẩm không tồn tại.")
        return

    await refresh_product_cache()
    await message.answer(f"Đã cập nhật giá <code>{product_id}</code> thành <b>{money(price)}</b>.")


@dp.message(Command("setdays"))
async def setdays_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) != 3 or not parts[2].strip().isdigit():
        await message.answer(
            "<b>Cú pháp sửa số ngày:</b>\n"
            "<code>/setdays mã_sản_phẩm số_ngày</code>\n\n"
            "Ví dụ:\n"
            "<code>/setdays ngotran_30 30</code>"
        )
        return

    product_id = parts[1].strip()
    days = int(parts[2].strip())
    product = PRODUCTS.get(product_id)
    name = None
    if product:
        base_name = product["name"].split(" - ", 1)[0]
        name = f"{base_name} - {days} ngày"

    result = await update_product_fields(product_id, days=days, name=name)
    if not result.get("ok"):
        await message.answer("Mã sản phẩm không tồn tại.")
        return

    await refresh_product_cache()
    await message.answer(f"Đã cập nhật <code>{product_id}</code> thành <b>{days}</b> ngày.")


@dp.message(Command("setproduct"))
async def setproduct_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=4)
    if len(parts) < 4 or not parts[2].isdigit() or not parts[3].isdigit():
        await message.answer(
            "<b>Cú pháp sửa sản phẩm:</b>\n"
            "<code>/setproduct mã_sản_phẩm số_ngày giá_mới tên_mới</code>\n\n"
            "<b>Chú thích:</b>\n"
            "- <code>mã_sản_phẩm</code>: mã sản phẩm cần sửa.\n"
            "- <code>số_ngày</code>: thời hạn mới, chỉ nhập số.\n"
            "- <code>giá_mới</code>: giá mới, chỉ nhập số.\n"
            "- <code>tên_mới</code>: tên mới muốn hiển thị cho khách.\n\n"
            "Ví dụ:\n"
            "<code>/setproduct ngotran_30 30 160000 NgoTran - 30 ngày</code>"
        )
        return

    product_id = parts[1].strip()
    days = int(parts[2])
    price = int(parts[3])
    name = parts[4].strip() if len(parts) == 5 else None

    result = await update_product_fields(product_id, price=price, days=days, name=name)
    if not result.get("ok"):
        await message.answer("Mã sản phẩm không tồn tại.")
        return

    await refresh_product_cache()
    await message.answer(
        f"Đã cập nhật <code>{product_id}</code>:\n"
        f"Ngày: <b>{days}</b>\n"
        f"Giá: <b>{money(price)}</b>"
    )


@dp.message(Command("reseller", "setreseller", "resellerprice"))
async def reseller_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=3)
    if len(parts) != 4 or not parts[1].strip().isdigit():
        await message.answer(
            "<b>Cú pháp thêm/sửa giá reseller:</b>\n"
            "<code>/reseller user_id mã_sản_phẩm giá</code>\n\n"
            "Ví dụ:\n"
            "<code>/reseller 5446467416 ngotran_7 45000</code>"
        )
        return

    user_id = int(parts[1].strip())
    product_id = normalize_id(parts[2])
    price = parse_positive_int(parts[3])
    if price is None:
        await message.answer("Giá reseller phải là số nguyên dương. Ví dụ: <code>45000</code>")
        return

    user = await get_user(user_id)
    if not user:
        await message.answer("User này chưa có trong database. Khách cần bấm /start trong bot trước.")
        return

    result = await set_reseller_price(user_id, product_id, price)
    if not result.get("ok"):
        await message.answer("Mã sản phẩm không tồn tại. Dùng /products để xem danh sách.")
        return

    await message.answer(
        "✅ <b>Đã lưu giá reseller.</b>\n\n"
        f"User: <code>{user_id}</code>\n"
        f"Sản phẩm: <code>{product_id}</code>\n"
        f"Giá reseller: <b>{money(price)}</b>\n\n"
        "Từ giờ user này mua sản phẩm đó sẽ tự dùng giá reseller."
    )


@dp.message(Command("delreseller", "removereseller"))
async def delreseller_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) != 3 or not parts[1].strip().isdigit():
        await message.answer(
            "<b>Cú pháp xóa giá reseller:</b>\n"
            "<code>/delreseller user_id mã_sản_phẩm</code>\n\n"
            "Ví dụ:\n"
            "<code>/delreseller 5446467416 ngotran_7</code>"
        )
        return

    user_id = int(parts[1].strip())
    product_id = normalize_id(parts[2])
    result = await remove_reseller_price(user_id, product_id)
    await message.answer(
        f"Đã xóa giá reseller cho user <code>{user_id}</code> / <code>{product_id}</code>."
        if result.get("ok") else "Không tìm thấy giá reseller đang active cho user/product này."
    )


@dp.message(Command("resellers"))
async def resellers_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    await send_reseller_prices_summary(message)


@dp.message(Command("delproduct", "deleteproduct"))
async def delproduct_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer(
            "<b>Cú pháp ẩn sản phẩm:</b>\n"
            "<code>/delproduct mã_sản_phẩm</code>\n\n"
            "Ví dụ:\n"
            "<code>/delproduct ngotran_60</code>\n\n"
            "Lưu ý: lệnh này chỉ ẩn sản phẩm khỏi menu mua, không xóa lịch sử đơn hàng."
        )
        return

    product_id = parts[1].strip()
    result = await set_product_active(product_id, 0)
    if not result.get("ok"):
        await message.answer("Mã sản phẩm không tồn tại.")
        return

    await refresh_product_cache()
    await message.answer(
        f"Đã ẩn sản phẩm <code>{product_id}</code> khỏi menu mua.\n"
        "Có thể hiện lại bằng lệnh <code>/restoreproduct mã_sản_phẩm</code>."
    )


@dp.message(Command("restoreproduct"))
async def restoreproduct_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer(
            "<b>Cú pháp hiện lại sản phẩm:</b>\n"
            "<code>/restoreproduct mã_sản_phẩm</code>\n\n"
            "Ví dụ:\n"
            "<code>/restoreproduct ngotran_60</code>"
        )
        return

    product_id = parts[1].strip()
    result = await set_product_active(product_id, 1)
    if not result.get("ok"):
        await message.answer("Mã sản phẩm không tồn tại.")
        return

    await refresh_product_cache()
    await message.answer(f"Đã hiện lại sản phẩm <code>{product_id}</code> trong menu mua.")


@dp.message(Command("confirm"))
async def confirm_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip().isdigit():
        await message.answer("Cú pháp: <code>/confirm 123</code>")
        return

    order_id = int(parts[1].strip())
    result = await fulfill_order(order_id)

    if result.get("ok") and not result.get("already"):
        await send_fulfilled_order(result, "admin")
        await message.answer(f"Đã xác nhận và gửi key cho đơn #{order_id}.")
        return

    await message.answer(f"Không thể xác nhận đơn #{order_id}: {result.get('msg')}")


@dp.message(Command("resend"))
async def resend_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip().isdigit():
        await message.answer("Cú pháp: <code>/resend 123</code>")
        return

    order_id = int(parts[1].strip())
    order = await get_order_with_key(order_id)
    if not order:
        await message.answer("Không tìm thấy đơn hàng.")
        return
    if order["status"] != "paid" or not order["license_key"]:
        await message.answer("Đơn này chưa paid hoặc chưa có key để gửi lại.")
        return

    after_key_message = await get_setting(AFTER_KEY_MESSAGE_SETTING, "")
    await bot.send_message(order["user_id"], paid_text(order, order["license_key"], after_key_message))
    await message.answer(f"Đã gửi lại key cho đơn #{order_id}.")


@dp.message(Command("cancel"))
async def admin_cancel_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip().isdigit():
        await message.answer("Cú pháp: <code>/cancel 123</code>")
        return

    order_id = int(parts[1].strip())
    result = await cancel_pending_order(order_id)
    if result.get("ok"):
        await message.answer(f"Đã hủy đơn #{order_id}.")
        return
    await message.answer(f"Không thể hủy đơn #{order_id}: <code>{result.get('msg')}</code>")


@dp.message(Command("find"))
async def find_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Cú pháp: <code>/find 123</code> hoặc <code>/find MUA ABCD1234</code>")
        return

    order = await find_order(parts[1])
    if not order:
        await message.answer("Không tìm thấy đơn hàng.")
        return

    product = PRODUCTS.get(order["product_id"], {"emoji": "", "name": order["product_id"]})
    text = (
        "🔎 <b>THÔNG TIN ĐƠN</b>\n\n"
        f"Đơn: <b>#{order['id']}</b>\n"
        f"Trạng thái: <b>{status_label(order['status'])}</b>\n"
        f"ID khách: <code>{order['user_id']}</code>\n"
        f"Username: {('@' + order['username']) if order['username'] else 'không có'}\n"
        f"Gói: <b>{product['emoji']} {product['name']}</b>\n"
        f"Số tiền: <b>{money(order['amount'])}</b>\n"
        f"Mã CK: <code>{order['payment_code']}</code>\n"
        f"Tạo lúc: {order['created_at']}\n"
    )
    if order["paid_at"]:
        text += f"Thanh toán: {order['paid_at']}\n"
    if order["cancelled_at"]:
        text += f"Hủy lúc: {order['cancelled_at']}\n"

    await message.answer(text, reply_markup=admin_order_menu(order["id"]) if order["status"] == "pending" else None)


@dp.message(Command("user"))
async def user_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip().isdigit():
        await message.answer("Cú pháp: <code>/user 5446467416</code>")
        return

    user_id = int(parts[1].strip())
    user = await get_user(user_id)
    pending = await get_pending_order_by_user(user_id)
    history = await get_user_order_history(user_id, limit=5)

    if not user and not history:
        await message.answer("Không tìm thấy user này trong database.")
        return

    text = (
        "👤 <b>THÔNG TIN USER</b>\n\n"
        f"User ID: <code>{user_id}</code>\n"
        f"Tên: <b>{escape(user['first_name'] if user and user['first_name'] else 'Không rõ')}</b>\n"
        f"Username: <b>{('@' + escape(user['username'])) if user and user['username'] else 'Không có'}</b>\n"
        f"Tổng chi tiêu: <b>{money(user['total_spent'] if user else 0)}</b>\n\n"
    )
    if pending:
        text += (
            "<b>Đơn đang chờ:</b>\n"
            f"#{pending['id']} | {money(pending['amount'])} | <code>{pending['payment_code']}</code>\n\n"
        )
    text += "<b>5 đơn gần nhất:</b>\n"
    for row in history:
        text += f"#{row['id']} | {status_label(row['status'])} | {money(row['amount'])} | <code>{row['payment_code']}</code>\n"

    await message.answer(text)


@dp.message(Command("history"))
async def history_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip().isdigit():
        await message.answer("Cú pháp: <code>/history 5446467416</code>")
        return

    user_id = int(parts[1].strip())
    user = await get_user(user_id)
    rows = await get_user_order_history(user_id)

    if not rows:
        await message.answer(f"Không có lịch sử mua cho user <code>{user_id}</code>.")
        return

    username = user["username"] if user and user["username"] else ""
    first_name = user["first_name"] if user and user["first_name"] else ""
    total_spent = user["total_spent"] if user else 0

    text = (
        "📜 <b>LỊCH SỬ MUA</b>\n\n"
        f"User ID: <code>{user_id}</code>\n"
        f"Tên: <b>{escape(first_name or 'Không rõ')}</b>\n"
        f"Username: <b>{('@' + escape(username)) if username else 'Không có'}</b>\n"
        f"Tổng chi tiêu: <b>{money(total_spent)}</b>\n\n"
    )

    for row in rows:
        product = PRODUCTS.get(row["product_id"], {"emoji": "", "name": row["product_id"]})
        text += (
            f"#{row['id']} | <b>{status_label(row['status'])}</b> | {money(row['amount'])}\n"
            f"Gói: {product['emoji']} {product['name']}\n"
            f"Mã CK: <code>{row['payment_code']}</code>\n"
            f"Tạo lúc: {row['created_at']}\n"
        )
        if row["paid_at"]:
            text += f"Thanh toán: {row['paid_at']}\n"
        if row["cancelled_at"]:
            text += f"Hủy lúc: {row['cancelled_at']}\n"
        if row["license_key"]:
            text += f"Key: <code>{row['license_key']}</code>\n"
        text += "\n"

    if len(text) > 3900:
        text = text[:3900] + "\n... Đã rút gọn, chỉ hiển thị một phần lịch sử."

    await message.answer(text)


@dp.message(Command("notice"))
async def notice_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        await message.answer(
            "<b>Cú pháp gửi thông báo cho một user:</b>\n"
            "<code>/notice id_người_dùng nội_dung</code>\n\n"
            "Ví dụ:\n"
            "<code>/notice 5446467416 Shop đã xử lý đơn của bạn.</code>"
        )
        return

    user_id = int(parts[1])
    content = parts[2].strip()
    if not content:
        await message.answer("Nội dung thông báo không được để trống.")
        return

    try:
        await bot.send_message(
            user_id,
            "📢 <b>THÔNG BÁO TỪ ADMIN</b>\n\n" + escape(content),
        )
    except Exception as error:
        await message.answer(f"Không gửi được thông báo: <code>{escape(str(error))}</code>")
        return

    await message.answer(f"Đã gửi thông báo đến user <code>{user_id}</code>.")


@dp.message(Command("broadcast"))
async def broadcast_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "<b>Cú pháp gửi thông báo toàn bộ user:</b>\n"
            "<code>/broadcast nội_dung</code>\n\n"
            "Ví dụ:\n"
            "<code>/broadcast Shop bảo trì lúc 23:00 hôm nay.</code>"
        )
        return

    content = parts[1].strip()
    users = await get_all_users()
    sent = 0
    failed = 0

    await message.answer(f"Đang gửi thông báo đến {len(users)} user...")

    for user in users:
        try:
            await bot.send_message(
                user["user_id"],
                "📢 <b>THÔNG BÁO TỪ ADMIN</b>\n\n" + escape(content),
            )
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await message.answer(
        "Đã gửi broadcast xong.\n"
        f"Thành công: <b>{sent}</b>\n"
        f"Thất bại: <b>{failed}</b>"
    )


@dp.message(Command("broadcastphoto"))
@dp.message(F.photo, F.caption.startswith("/broadcastphoto"))
async def broadcastphoto_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    photo_message = message
    command_text = message.caption or message.text or ""
    if not message.photo and message.reply_to_message and message.reply_to_message.photo:
        photo_message = message.reply_to_message

    if not photo_message.photo:
        await message.answer(
            "<b>Cách gửi ảnh cho toàn bộ user:</b>\n\n"
            "Cách 1: Gửi ảnh kèm caption:\n"
            "<code>/broadcastphoto Nội dung thông báo</code>\n\n"
            "Cách 2: Reply vào một ảnh bằng lệnh:\n"
            "<code>/broadcastphoto Nội dung thông báo</code>"
        )
        return

    content = command_text.split(maxsplit=1)[1].strip() if len(command_text.split(maxsplit=1)) == 2 else ""
    caption = "📢 <b>THÔNG BÁO SẢN PHẨM MỚI</b>"
    if content:
        caption += "\n\n" + escape(content)

    photo_id = photo_message.photo[-1].file_id
    users = await get_all_users()
    user_ids = [int(user["user_id"]) for user in users]

    await message.answer(f"Đang gửi ảnh thông báo đến {len(users)} user...")

    async def send_photo_to_user(user_id: int):
        sent_message = await bot.send_photo(user_id, photo=photo_id, caption=caption)
        await asyncio.sleep(0.07)
        return sent_message

    result = await send_media_with_retry(
        user_ids,
        send_photo_to_user,
        max_attempts=3,
        delay_seconds=0.5,
        sleep=asyncio.sleep,
    )
    broadcast_id = remember_broadcast("photo", result.sent) if result.sent else ""

    await message.answer(
        "Đã gửi broadcast ảnh xong.\n"
        f"Thành công: <b>{result.sent_count}</b>\n"
        f"Thất bại sau 3 lần thử: <b>{result.failed_count}</b>",
        reply_markup=recall_broadcast_menu(broadcast_id) if broadcast_id else None,
    )


@dp.message(Command("broadcastvideo"))
@dp.message(F.video, F.caption.startswith("/broadcastvideo"))
async def broadcastvideo_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    video_message = message
    command_text = message.caption or message.text or ""
    if not message.video and message.reply_to_message and message.reply_to_message.video:
        video_message = message.reply_to_message

    if not video_message.video:
        await message.answer(
            "<b>Cách gửi video cho toàn bộ user:</b>\n\n"
            "Cách 1: Gửi video kèm caption:\n"
            "<code>/broadcastvideo Nội dung thông báo</code>\n\n"
            "Cách 2: Reply vào một video bằng lệnh:\n"
            "<code>/broadcastvideo Nội dung thông báo</code>"
        )
        return

    content = command_text.split(maxsplit=1)[1].strip() if len(command_text.split(maxsplit=1)) == 2 else ""
    caption = "📢 <b>THÔNG BÁO TỪ SHOP</b>"
    if content:
        caption += "\n\n" + escape(content)

    video_id = video_message.video.file_id
    users = await get_all_users()
    user_ids = [int(user["user_id"]) for user in users]

    await message.answer(f"Đang gửi video thông báo đến {len(users)} user...")

    async def send_video_to_user(user_id: int):
        sent_message = await bot.send_video(user_id, video=video_id, caption=caption)
        await asyncio.sleep(0.09)
        return sent_message

    result = await send_media_with_retry(
        user_ids,
        send_video_to_user,
        max_attempts=3,
        delay_seconds=0.5,
        sleep=asyncio.sleep,
    )
    broadcast_id = remember_broadcast("video", result.sent) if result.sent else ""

    await message.answer(
        "Đã gửi broadcast video xong.\n"
        f"Thành công: <b>{result.sent_count}</b>\n"
        f"Thất bại sau 3 lần thử: <b>{result.failed_count}</b>",
        reply_markup=recall_broadcast_menu(broadcast_id) if broadcast_id else None,
    )


@dp.message(Command("broadcaststicker"))
@dp.message(F.sticker, F.caption.startswith("/broadcaststicker"))
async def broadcaststicker_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    sticker_message = message
    if not message.sticker and message.reply_to_message and message.reply_to_message.sticker:
        sticker_message = message.reply_to_message

    if not sticker_message.sticker:
        await message.answer(
            "<b>Cách gửi sticker cho toàn bộ user:</b>\n\n"
            "Gửi sticker vào chat admin, sau đó reply vào sticker bằng:\n"
            "<code>/broadcaststicker</code>"
        )
        return

    sticker_id = sticker_message.sticker.file_id
    users = await get_all_users()
    user_ids = [int(user["user_id"]) for user in users]

    await message.answer(f"Đang gửi sticker đến {len(users)} user...")

    async def send_sticker_to_user(user_id: int):
        sent_message = await bot.send_sticker(user_id, sticker=sticker_id)
        await asyncio.sleep(0.05)
        return sent_message

    result = await send_media_with_retry(
        user_ids,
        send_sticker_to_user,
        max_attempts=3,
        delay_seconds=0.5,
        sleep=asyncio.sleep,
    )
    broadcast_id = remember_broadcast("sticker", result.sent) if result.sent else ""

    await message.answer(
        "Đã gửi broadcast sticker xong.\n"
        f"Thành công: <b>{result.sent_count}</b>\n"
        f"Thất bại sau 3 lần thử: <b>{result.failed_count}</b>",
        reply_markup=recall_broadcast_menu(broadcast_id) if broadcast_id else None,
    )


@dp.message(Command("stats"))
async def stats_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    stats = await get_admin_stats()
    orders = stats["orders"]
    users = stats["users"]
    keys = stats["keys"]
    low_stock = await get_low_stock(3)

    text = (
        "📊 <b>TỔNG QUAN SHOP</b>\n\n"
        f"User đã dùng bot: <b>{users['users']}</b>\n"
        f"Đơn đang chờ: <b>{orders['pending_orders'] or 0}</b>\n"
        f"Đơn đã paid: <b>{orders['paid_orders'] or 0}</b>\n"
        f"Đơn đã hủy: <b>{orders['cancelled_orders'] or 0}</b>\n"
        f"Key chưa bán: <b>{keys['unused_keys'] or 0}</b>\n"
        f"Doanh thu hôm nay: <b>{money(orders['today_revenue'] or 0)}</b>\n"
        f"Tổng doanh thu: <b>{money(orders['total_revenue'] or 0)}</b>\n"
        f"Sản phẩm tồn kho thấp: <b>{len(low_stock)}</b>"
    )
    await message.answer(text)


@dp.message(Command("pending"))
async def pending_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    rows = await get_pending_orders(20)
    if not rows:
        await message.answer("Không có đơn nào đang chờ thanh toán.")
        return

    text = "⏳ <b>ĐƠN ĐANG CHỜ THANH TOÁN</b>\n\n"
    for order in rows:
        product = PRODUCTS.get(order["product_id"], {"emoji": "", "name": order["product_id"]})
        text += (
            f"#{order['id']} | {money(order['amount'])}\n"
            f"ID khách: <code>{order['user_id']}</code>\n"
            f"Gói: {product['emoji']} {product['name']}\n"
            f"Mã CK: <code>{order['payment_code']}</code>\n"
            f"Tạo lúc: {order['created_at']}\n\n"
        )
    await message.answer(text[:3900])


@dp.message(Command("sepaylog"))
async def sepaylog_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    rows = await get_sepay_logs(10)
    if not rows:
        await message.answer("Chưa có log webhook SePay nào.")
        return

    text = "🧾 <b>WEBHOOK SEPAY GẦN NHẤT</b>\n\n"
    for row in rows:
        text += (
            f"ID: <code>{escape(str(row['sepay_id']))}</code>\n"
            f"Mã CK: <code>{escape(row['payment_code'] or 'không có')}</code>\n"
            f"Số tiền: <b>{money(row['amount'] or 0)}</b>\n"
            f"Lúc: {row['created_at']}\n\n"
        )
    await message.answer(text[:3900])


@dp.message(Command("revenue"))
async def revenue_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    period = parts[1].strip().lower() if len(parts) == 2 else "today"
    if period not in {"today", "7d", "month", "all"}:
        await message.answer("Cú pháp: <code>/revenue today|7d|month|all</code>")
        return

    stats = await get_revenue_stats(period)
    summary = stats["summary"]
    labels = {
        "today": "hôm nay",
        "7d": "7 ngày gần nhất",
        "month": "tháng này",
        "all": "toàn bộ",
    }

    text = (
        f"💰 <b>DOANH THU {labels[period].upper()}</b>\n\n"
        f"Số đơn paid: <b>{summary['orders']}</b>\n"
        f"Doanh thu: <b>{money(summary['revenue'])}</b>\n\n"
        "<b>Theo sản phẩm:</b>\n"
    )
    for row in stats["by_product"]:
        product = PRODUCTS.get(row["product_id"], {"emoji": "", "name": row["product_id"]})
        text += f"{product['emoji']} {product['name']}: {row['orders']} đơn | <b>{money(row['revenue'])}</b>\n"

    await message.answer(text)


@dp.message(Command("lowstock"))
async def lowstock_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    rows = await get_low_stock(3)
    if not rows:
        await message.answer("Không có sản phẩm nào tồn kho thấp.")
        return

    text = "⚠️ <b>SẢN PHẨM SẮP HẾT KEY</b>\n\n"
    for row in rows:
        text += f"<code>{row['product_id']}</code> - {row['name']}: <b>{row['stock']}</b> key\n"
    await message.answer(text)


@dp.message(Command("backup"))
async def backup_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    if not os.path.exists(DB_PATH):
        await message.answer("Không tìm thấy file database.")
        return

    await message.answer_document(
        FSInputFile(DB_PATH, filename="shop.db"),
        caption="Backup database hiện tại.",
    )


@dp.message(Command("stock"))
async def stock_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) == 2 and parts[1].strip():
        product_id = parts[1].strip()
        if product_id not in PRODUCTS:
            await message.answer("Mã sản phẩm không tồn tại.")
            return

        detail = await get_stock_detail(product_id, 50)
        product = PRODUCTS[product_id]
        text = format_stock_detail_text(product_id, product, detail)
        await message.answer(text[:3900])
        return

    rows = dict(await get_stock())
    text = "📦 <b>KHO KEY</b>\n\n"
    for product_id, product in PRODUCTS.items():
        text += f"<code>{product_id}</code>: <b>{rows.get(product_id, 0)}</b> key\n"

    await message.answer(text)


@dp.message(Command("orders"))
async def orders_cmd(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    orders = await get_recent_orders()
    if not orders:
        await message.answer("Chưa có đơn hàng.")
        return

    text = "🧾 <b>ĐƠN GẦN NHẤT</b>\n\n"
    for order in orders:
        text += (
            f"#{order['id']} | <b>{status_label(order['status'])}</b> | {money(order['amount'])}\n"
            f"ID khách: <code>{order['user_id']}</code>\n"
            f"Sản phẩm: <code>{order['product_id']}</code>\n"
            f"Nội dung: <code>{order['payment_code']}</code>\n\n"
        )

    await message.answer(text)


@dp.message(Command("vip"))
async def vip_cmd(message: Message) -> None:
    await clear_user_bot_messages(message.chat.id)
    user = await get_user(message.from_user.id)
    total = user["total_spent"] if user else 0
    level = min(total // 500000, 10)
    discount = level * 2
    await user_answer(
        message,
        "<b>VIP</b>\n\n"
        f"Tổng chi tiêu: <b>{money(total)}</b>\n"
        f"VIP hiện tại: <b>{level}</b>\n"
        f"Ưu đãi tham khảo: <b>{discount}%</b>"
    )


async def main() -> None:
    await init_db()
    await refresh_product_cache()
    log.info("Bot Telegram đang chạy.")
    log.info("Webhook SePay đang nghe tại http://%s:%s/sepay", WEBHOOK_HOST, WEBHOOK_PORT)

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=WEBHOOK_HOST,
            port=WEBHOOK_PORT,
            log_level="info",
        )
    )

    await asyncio.gather(
        dp.start_polling(bot),
        server.serve(),
    )


if __name__ == "__main__":
    asyncio.run(main())
