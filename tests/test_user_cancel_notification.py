import unittest
from unittest.mock import AsyncMock, Mock, patch

import bot


class UserCancelNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_cancel_order_notifies_admins(self):
        call = FakeCallback("cancel_order:42")
        cancelled_order = {
            "id": 42,
            "user_id": 123,
            "username": "buyer",
            "product_id": "demo",
            "amount": 50000,
            "payment_code": "MUA ABCD1234",
            "created_at": "2026-05-25 10:00:00",
        }
        notify_admins_user_cancelled_order = AsyncMock()

        with (
            patch.object(bot, "cancel_pending_order", new=AsyncMock(return_value={"ok": True, "order": cancelled_order})),
            patch.object(bot, "notify_admins_user_cancelled_order", new=notify_admins_user_cancelled_order),
            patch.object(bot, "user_answer", new=AsyncMock()),
        ):
            await bot.cancel_order_cb(call)

        notify_admins_user_cancelled_order.assert_awaited_once_with(cancelled_order, call.from_user)

    async def test_admin_notification_contains_order_and_user_details(self):
        order = {
            "id": 42,
            "user_id": 123,
            "username": "buyer",
            "product_id": "demo",
            "amount": 50000,
            "payment_code": "MUA ABCD1234",
            "created_at": "2026-05-25 10:00:00",
        }
        send_message = AsyncMock()

        with (
            patch.object(bot, "ADMIN_IDS", [111, 222]),
            patch.dict(bot.PRODUCTS, {"demo": {"emoji": "D", "name": "Demo Product"}}, clear=True),
            patch.object(bot.bot, "send_message", new=send_message),
        ):
            await bot.notify_admins_user_cancelled_order(order, FakeUser())

        self.assertEqual(send_message.await_count, 2)
        sent_text = send_message.await_args_list[0].args[1]
        self.assertIn("#42", sent_text)
        self.assertIn("123", sent_text)
        self.assertIn("@buyer", sent_text)
        self.assertIn("Demo Product", sent_text)
        self.assertIn("MUA ABCD1234", sent_text)


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
