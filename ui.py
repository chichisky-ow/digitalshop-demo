# -*- coding: utf-8 -*-
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db import get_effective_price, get_stock_count
from products import GROUPS, PRODUCTS


def money(value: int) -> str:
    return f"{int(value):,}".replace(",", ".") + "đ"


def main_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=group["title"], callback_data=f"group:{group_id}")]
        for group_id, group in GROUPS.items()
        if group.get("product_ids")
    ]
    rows.extend(
        [
            [InlineKeyboardButton(text="🔑 Key đã mua", callback_data="mykeys")],
            [InlineKeyboardButton(text="📞 Hỗ trợ", callback_data="support")],
        ]
    )
    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def start_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Tất cả menu", callback_data="show_menu")],
        ]
    )


async def product_menu(group_id: str, user_id: int | None = None) -> InlineKeyboardMarkup:
    rows = []
    group = GROUPS.get(group_id)
    if not group:
        return main_menu()

    for product_id in group["product_ids"]:
        product = PRODUCTS[product_id]
        stock = await get_stock_count(product_id)
        price = (
            await get_effective_price(user_id, product_id, product["price"])
            if user_id is not None
            else product["price"]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{product['emoji']} {product['name']} - "
                        f"{money(price)} | Còn {stock}"
                    ),
                    callback_data=f"buy:{product_id}",
                )
            ]
        )

    rows.append([InlineKeyboardButton(text="⬅️ Quay lại", callback_data="show_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_menu(order_id: int | None = None) -> InlineKeyboardMarkup:
    rows = []
    if order_id is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Hủy giao dịch",
                    callback_data=f"cancel_order:{order_id}",
                )
            ]
        )

    rows.extend(
        [
            [InlineKeyboardButton(text="⬅️ Về menu", callback_data="show_menu")],
            [InlineKeyboardButton(text="🔑 Key đã mua", callback_data="mykeys")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_order_menu(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Xác nhận thanh toán",
                    callback_data=f"admin_confirm:{order_id}",
                )
            ]
        ]
    )


def confirm_buy_menu(product_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Tạo mã thanh toán",
                    callback_data=f"create_order:{product_id}",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Quay lại", callback_data="show_menu")],
        ]
    )
