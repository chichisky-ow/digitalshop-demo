import unittest
from unittest.mock import AsyncMock, patch

import bot


class AdminPriceParsingTests(unittest.TestCase):
    def test_price_can_be_zero(self):
        self.assertEqual(bot.parse_price_int("0"), 0)

    def test_price_rejects_negative_or_blank_values(self):
        self.assertIsNone(bot.parse_price_int("-1"))
        self.assertIsNone(bot.parse_price_int(""))

    def test_price_accepts_group_separators(self):
        self.assertEqual(bot.parse_price_int("1.000"), 1000)
        self.assertEqual(bot.parse_price_int("1,000"), 1000)


class AdminPriceCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_setprice_command_accepts_zero_price(self):
        message = FakeMessage("/setprice demo_product 0")
        update_product_fields = AsyncMock(return_value={"ok": True})
        refresh_product_cache = AsyncMock()

        with (
            patch.object(bot, "is_admin", return_value=True),
            patch.object(bot, "update_product_fields", new=update_product_fields),
            patch.object(bot, "refresh_product_cache", new=refresh_product_cache),
        ):
            await bot.setprice_cmd(message)

        update_product_fields.assert_awaited_once_with("demo_product", price=0)
        refresh_product_cache.assert_awaited_once()
        self.assertIn("0đ", message.answers[0])

    async def test_setproduct_command_accepts_zero_price(self):
        message = FakeMessage("/setproduct demo_product 30 0 Demo Product")
        update_product_fields = AsyncMock(return_value={"ok": True})
        refresh_product_cache = AsyncMock()

        with (
            patch.object(bot, "is_admin", return_value=True),
            patch.object(bot, "update_product_fields", new=update_product_fields),
            patch.object(bot, "refresh_product_cache", new=refresh_product_cache),
        ):
            await bot.setproduct_cmd(message)

        update_product_fields.assert_awaited_once_with(
            "demo_product",
            price=0,
            days=30,
            name="Demo Product",
        )
        refresh_product_cache.assert_awaited_once()
        self.assertIn("0đ", message.answers[0])


class FakeUser:
    id = 123


class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.from_user = FakeUser()
        self.answers = []

    async def answer(self, text: str, **kwargs):
        self.answers.append(text)


if __name__ == "__main__":
    unittest.main()
