# -*- coding: utf-8 -*-
from html import escape


def stock_counts_by_product(stock_rows) -> dict:
    return dict(stock_rows)


def format_stock_detail_text(product_id: str, product: dict, detail: dict, *, shown_limit: int = 50) -> str:
    summary = detail["summary"]
    total = int(summary["total"] or 0)
    unused = int(summary["unused"] or 0)
    used = int(summary["used"] or 0)
    unused_keys = detail["unused_keys"]

    text = (
        "📦 <b>CHI TIẾT KHO KEY</b>\n\n"
        f"Sản phẩm: <code>{product_id}</code>\n"
        f"Tên: <b>{product['emoji']} {product['name']}</b>\n"
        f"Tổng key: <b>{total}</b>\n"
        f"Chưa bán: <b>{unused}</b>\n"
        f"Đã bán: <b>{used}</b>\n\n"
        f"<b>Key chưa bán gần nhất ({min(unused, shown_limit)}/{unused}):</b>\n"
    )

    for index, row in enumerate(unused_keys, start=1):
        text += f"{index}. <code>{escape(row['license_key'])}</code>\n"

    if not unused_keys:
        text += "Không còn key chưa bán.\n"
    elif unused > len(unused_keys):
        text += f"\nCòn <b>{unused - len(unused_keys)}</b> key khác. Dùng backup DB nếu cần xem toàn bộ."

    return text
