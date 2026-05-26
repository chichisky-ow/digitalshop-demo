import unittest
from unittest.mock import AsyncMock, Mock, patch

import bot


class OrderTimingTests(unittest.IsolatedAsyncioTestCase):
    def test_order_cooldown_remaining_seconds_uses_five_minutes(self):
        last_order = {"created_at": "2026-05-25 10:00:00"}

        self.assertEqual(
            bot.order_cooldown_remaining_seconds(last_order, now=bot.datetime(2026, 5, 25, 10, 3, 0).timestamp()),
            120,
        )
        self.assertEqual(
            bot.order_cooldown_remaining_seconds(last_order, now=bot.datetime(2026, 5, 25, 10, 5, 0).timestamp()),
            0,
        )

    async def test_recent_order_blocks_new_order_creation(self):
        call = FakeCallback("create_order:paid_product")
        create_order = AsyncMock()

        with (
            patch.dict(bot.PRODUCTS, {"paid_product": {"price": 10000, "name": "Paid Product", "emoji": "P", "days": 1}}, clear=True),
            patch.object(bot, "upsert_user", new=AsyncMock()),
            patch.object(bot, "get_pending_order_by_user", new=AsyncMock(return_value=None)),
            patch.object(bot, "get_latest_order_by_user", new=AsyncMock(return_value={"created_at": "2026-05-25 10:00:00"})),
            patch.object(bot, "order_cooldown_remaining_seconds", return_value=120),
            patch.object(bot, "create_order", new=create_order),
        ):
            await bot.create_order_cb(call)

        create_order.assert_not_awaited()
        self.assertEqual(call.answers, [("Vui lòng chờ 2 phút nữa rồi tạo đơn mới.", {"show_alert": True})])

    async def test_paid_order_schedules_auto_cancel_after_ten_minutes(self):
        call = FakeCallback("create_order:paid_product")
        auto_cancel_marker = Mock()

        with (
            patch.dict(bot.PRODUCTS, {"paid_product": {"price": 10000, "name": "Paid Product", "emoji": "P", "days": 1}}, clear=True),
            patch.object(bot, "upsert_user", new=AsyncMock()),
            patch.object(bot, "get_pending_order_by_user", new=AsyncMock(return_value=None)),
            patch.object(bot, "get_latest_order_by_user", new=AsyncMock(return_value=None)),
            patch.object(bot, "get_stock_count", new=AsyncMock(return_value=3)),
            patch.object(bot, "get_effective_price", new=AsyncMock(return_value=10000)),
            patch.object(bot, "make_payment_code", return_value="MUA PAID0001"),
            patch.object(bot, "create_order", new=AsyncMock(return_value=77)),
            patch.object(bot, "cancel_all_old_pending_orders", new=AsyncMock(return_value=0)),
            patch.object(bot, "vietqr_url", return_value="https://qr.example"),
            patch.object(bot, "user_answer_photo", new=AsyncMock()),
            patch.object(bot, "notify_admins", new=AsyncMock()),
            patch.object(bot, "auto_cancel_order", new=Mock(return_value=auto_cancel_marker)) as auto_cancel_order,
            patch.object(bot.asyncio, "create_task") as create_task,
        ):
            await bot.create_order_cb(call)

        auto_cancel_order.assert_called_once_with(77, 123, bot.ORDER_AUTO_CANCEL_SECONDS)
        create_task.assert_called_once_with(auto_cancel_marker)
        self.assertEqual(bot.ORDER_AUTO_CANCEL_SECONDS, 600)
        self.assertEqual(call.answers, [("Đã tạo mã thanh toán.", {})])


class FakeUser:
    id = 123
    username = "buyer"
    first_name = "Buyer"


class FakeMessage:
    chat = Mock(id=123, type="private")


class FakeCallback:
    def __init__(self, data: str):
        self.data = data
        self.from_user = FakeUser()
        self.message = FakeMessage()
        self.answers = []

    async def answer(self, text: str = "", **kwargs):
        self.answers.append((text, kwargs))


if __name__ == "__main__":
    unittest.main()
