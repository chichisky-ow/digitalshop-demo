import tempfile
import unittest
from pathlib import Path

import db
from ui import money, product_menu


class ResellerPricingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.original_db_path = db.DB_PATH
        db.DB_PATH = str(Path(self.tmpdir.name) / "shop.db")
        await db.init_db()

    async def asyncTearDown(self):
        db.DB_PATH = self.original_db_path
        self.tmpdir.cleanup()

    async def test_reseller_price_overrides_default_price(self):
        result = await db.set_reseller_price(123, "ngotran_7", 45000)

        self.assertEqual(result, {"ok": True, "msg": "updated"})
        self.assertEqual(await db.get_reseller_price(123, "ngotran_7"), 45000)
        self.assertEqual(await db.get_effective_price(123, "ngotran_7", 70000), 45000)

    async def test_missing_reseller_price_uses_default_price(self):
        self.assertIsNone(await db.get_reseller_price(123, "ngotran_7"))
        self.assertEqual(await db.get_effective_price(123, "ngotran_7", 70000), 70000)

    async def test_removing_reseller_price_restores_default_price(self):
        await db.set_reseller_price(123, "ngotran_7", 45000)

        result = await db.remove_reseller_price(123, "ngotran_7")

        self.assertEqual(result, {"ok": True, "msg": "removed"})
        self.assertIsNone(await db.get_reseller_price(123, "ngotran_7"))
        self.assertEqual(await db.get_effective_price(123, "ngotran_7", 70000), 70000)

    async def test_setting_removed_reseller_price_reactivates_it(self):
        await db.set_reseller_price(123, "ngotran_7", 45000)
        await db.remove_reseller_price(123, "ngotran_7")

        result = await db.set_reseller_price(123, "ngotran_7", 39000)

        self.assertEqual(result, {"ok": True, "msg": "updated"})
        self.assertEqual(await db.get_effective_price(123, "ngotran_7", 70000), 39000)

    async def test_reseller_price_requires_existing_product(self):
        result = await db.set_reseller_price(123, "missing_product", 45000)

        self.assertEqual(result, {"ok": False, "msg": "product_not_found"})
        self.assertIsNone(await db.get_reseller_price(123, "missing_product"))

    async def test_reseller_price_list_only_returns_active_prices(self):
        await db.set_reseller_price(123, "ngotran_7", 45000)
        await db.set_reseller_price(456, "ngotran_7", 40000)
        await db.remove_reseller_price(456, "ngotran_7")

        rows = await db.get_reseller_prices(20)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["user_id"], 123)
        self.assertEqual(rows[0]["product_id"], "ngotran_7")
        self.assertEqual(rows[0]["price"], 45000)

    async def test_product_menu_displays_reseller_price_for_user(self):
        await db.set_reseller_price(123, "ngotran_7", 45000)

        markup = await product_menu("ngotran", user_id=123)
        button_texts = [
            button.text
            for row in markup.inline_keyboard
            for button in row
        ]

        reseller_button = next(text for text in button_texts if "ngotran_7" not in text and "7" in text)
        self.assertIn(money(45000), reseller_button)
        self.assertNotIn(money(70000), reseller_button)


if __name__ == "__main__":
    unittest.main()
