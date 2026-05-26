import unittest
from unittest.mock import patch

import bot


MOJIBAKE_MARKERS = ("Báº", "Ä", "ðŸ", "âž", "âš", "âœ", "ï¸")


class AdminMenuTextTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_command_uses_readable_vietnamese_text(self):
        message = FakeMessage()

        with patch.object(bot, "is_admin", return_value=True):
            await bot.admin_cmd(message)

        text = message.answers[0]["text"]
        self.assertIn("BẢNG ĐIỀU KHIỂN ADMIN", text)
        self.assertIn("Dùng các nút bên dưới để quản lý shop", text)
        self.assertFalse(any(marker in text for marker in MOJIBAKE_MARKERS))

    def test_admin_panel_buttons_use_readable_vietnamese_text(self):
        markup = bot.admin_panel_menu()
        button_texts = [button.text for row in markup.inline_keyboard for button in row]

        self.assertIn("➕ Thêm sản phẩm", button_texts)
        self.assertIn("📦 Tồn kho", button_texts)
        self.assertIn("💰 Sửa giá chung", button_texts)
        self.assertIn("📖 Hướng dẫn lệnh", button_texts)
        self.assertFalse(any(marker in text for text in button_texts for marker in MOJIBAKE_MARKERS))


class FakeUser:
    id = 123


class FakeMessage:
    def __init__(self):
        self.from_user = FakeUser()
        self.answers = []

    async def answer(self, text: str, **kwargs):
        self.answers.append({"text": text, "kwargs": kwargs})


if __name__ == "__main__":
    unittest.main()
