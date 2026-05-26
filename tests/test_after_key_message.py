import unittest
from unittest.mock import AsyncMock, patch

import bot
from texts import paid_text


class AfterKeyMessageTextTests(unittest.TestCase):
    def test_paid_text_appends_optional_after_key_message(self):
        order = {"id": 10, "product_id": "demo", "amount": 0}

        with patch.dict("texts.PRODUCTS", {"demo": {"emoji": "D", "name": "Demo"}}, clear=True):
            text = paid_text(order, "KEY-001", "Vao nhom ho tro sau khi kich hoat.")

        self.assertIn("<code>KEY-001</code>", text)
        self.assertIn("<b>Ghi chú:</b>", text)
        self.assertIn("Vao nhom ho tro sau khi kich hoat.", text)

    def test_paid_text_omits_blank_after_key_message(self):
        order = {"id": 10, "product_id": "demo", "amount": 0}

        with patch.dict("texts.PRODUCTS", {"demo": {"emoji": "D", "name": "Demo"}}, clear=True):
            text = paid_text(order, "KEY-001", "   ")

        self.assertNotIn("<b>Ghi chú", text)


class AfterKeyMessageDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_fulfilled_order_uses_configured_after_key_message(self):
        result = {
            "ok": True,
            "already": False,
            "order": {"id": 10, "user_id": 123, "product_id": "demo", "amount": 0},
            "key": {"license_key": "KEY-001"},
        }
        user_send_message = AsyncMock()

        with (
            patch.dict(bot.PRODUCTS, {"demo": {"emoji": "D", "name": "Demo"}}, clear=True),
            patch.object(bot, "get_setting", new=AsyncMock(return_value="Sau khi nhan key, lien he support neu can.")),
            patch.object(bot, "user_send_message", new=user_send_message),
            patch.object(bot, "notify_low_stock", new=AsyncMock()),
            patch.object(bot.bot, "send_message", new=AsyncMock()),
        ):
            await bot.send_fulfilled_order(result, "test")

        sent_text = user_send_message.await_args.args[1]
        self.assertIn("KEY-001", sent_text)
        self.assertIn("Sau khi nhan key, lien he support neu can.", sent_text)


if __name__ == "__main__":
    unittest.main()
