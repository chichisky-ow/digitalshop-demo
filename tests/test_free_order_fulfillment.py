import unittest
from unittest.mock import AsyncMock, Mock, patch

import bot


class FreeOrderFulfillmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_zero_price_product_is_fulfilled_without_payment_qr(self):
        call = FakeCallback("create_order:free_product")
        paid_result = {
            "ok": True,
            "msg": "paid",
            "already": False,
            "order": {
                "id": 99,
                "user_id": 123,
                "product_id": "free_product",
                "amount": 0,
            },
            "key": {"license_key": "FREE-KEY"},
        }
        create_order = AsyncMock(return_value=99)
        cancel_all_old_pending_orders = AsyncMock(return_value=0)
        fulfill_order = AsyncMock(return_value=paid_result)
        send_fulfilled_order = AsyncMock()
        user_answer_photo = AsyncMock()
        notify_admins = AsyncMock()

        with (
            patch.dict(bot.PRODUCTS, {"free_product": {"price": 0, "name": "Free Product", "emoji": "F", "days": 1}}, clear=True),
            patch.object(bot, "upsert_user", new=AsyncMock()),
            patch.object(bot, "get_pending_order_by_user", new=AsyncMock(return_value=None)),
            patch.object(bot, "get_latest_order_by_user", new=AsyncMock(return_value=None)),
            patch.object(bot, "get_stock_count", new=AsyncMock(return_value=1)),
            patch.object(bot, "get_effective_price", new=AsyncMock(return_value=0)),
            patch.object(bot, "make_payment_code", return_value="MUA FREE0001"),
            patch.object(bot, "create_order", new=create_order),
            patch.object(bot, "cancel_all_old_pending_orders", new=cancel_all_old_pending_orders),
            patch.object(bot, "fulfill_order", new=fulfill_order),
            patch.object(bot, "send_fulfilled_order", new=send_fulfilled_order),
            patch.object(bot, "auto_cancel_order", new=Mock(return_value=Mock())),
            patch.object(bot, "user_answer_photo", new=user_answer_photo),
            patch.object(bot, "notify_admins", new=notify_admins),
            patch.object(bot.asyncio, "create_task") as create_task,
        ):
            await bot.create_order_cb(call)

        create_order.assert_awaited_once_with(call.from_user, "free_product", 0, "MUA FREE0001")
        fulfill_order.assert_awaited_once_with(99)
        send_fulfilled_order.assert_awaited_once_with(paid_result, "gia 0 dong")
        user_answer_photo.assert_not_awaited()
        notify_admins.assert_not_awaited()
        create_task.assert_not_called()
        self.assertEqual(len(call.answers), 1)
        self.assertEqual(call.answers[0][1], {})


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
