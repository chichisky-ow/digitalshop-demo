import unittest

from stock_texts import format_stock_detail_text, stock_counts_by_product


class FakeRow(dict):
    def __getitem__(self, key):
        return super().__getitem__(key)


class StockDetailTextTests(unittest.TestCase):
    def test_stock_counts_supports_rows_returned_by_get_stock(self):
        rows = [("ngotran_1", 2), ("ngotran_7", 5)]

        counts = stock_counts_by_product(rows)

        self.assertEqual(counts["ngotran_1"], 2)
        self.assertEqual(counts["ngotran_7"], 5)
        self.assertEqual(counts.get("missing", 0), 0)

    def test_stock_detail_shows_key_contents(self):
        detail = {
            "summary": FakeRow({"total": 2, "unused": 2, "used": 0}),
            "unused_keys": [
                FakeRow({"license_key": "User: alpha | Pass: 123 | Hạn: 1 Day"}),
                FakeRow({"license_key": "KEY-ABC-002"}),
            ],
        }
        product = {"emoji": "⭐", "name": "NgoTran - 1 ngày"}

        text = format_stock_detail_text("ngotran_1", product, detail)

        self.assertIn("<code>ngotran_1</code>", text)
        self.assertIn("Tổng key: <b>2</b>", text)
        self.assertIn("Chưa bán: <b>2</b>", text)
        self.assertIn("<code>User: alpha | Pass: 123 | Hạn: 1 Day</code>", text)
        self.assertIn("<code>KEY-ABC-002</code>", text)

    def test_stock_detail_escapes_key_contents(self):
        detail = {
            "summary": FakeRow({"total": 1, "unused": 1, "used": 0}),
            "unused_keys": [FakeRow({"license_key": "A<B&C>"})],
        }
        product = {"emoji": "⭐", "name": "NgoTran - 1 ngày"}

        text = format_stock_detail_text("ngotran_1", product, detail)

        self.assertIn("<code>A&lt;B&amp;C&gt;</code>", text)

    def test_stock_detail_mentions_when_keys_are_truncated(self):
        detail = {
            "summary": FakeRow({"total": 60, "unused": 60, "used": 0}),
            "unused_keys": [FakeRow({"license_key": f"KEY-{index}"}) for index in range(50)],
        }
        product = {"emoji": "⭐", "name": "NgoTran - 1 ngày"}

        text = format_stock_detail_text("ngotran_1", product, detail)

        self.assertIn("Key chưa bán gần nhất (50/60)", text)
        self.assertIn("Còn <b>10</b> key khác", text)


if __name__ == "__main__":
    unittest.main()
