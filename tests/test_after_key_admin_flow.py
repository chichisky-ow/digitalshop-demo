import unittest
from unittest.mock import AsyncMock, patch

import bot


class AfterKeyAdminPanelTests(unittest.IsolatedAsyncioTestCase):
    def test_admin_panel_contains_after_key_message_button(self):
        markup = bot.admin_panel_menu()
        button_texts = [button.text for row in markup.inline_keyboard for button in row]

        self.assertIn("📝 Lời nhắn sau key", button_texts)

    async def test_admin_action_starts_after_key_message_wizard(self):
        call = FakeCallback("admin_action:afterkey_message")
        start_admin_wizard = AsyncMock()

        with (
            patch.object(bot, "is_admin", return_value=True),
            patch.object(bot, "get_setting", new=AsyncMock(return_value="Huong dan kich hoat.")),
            patch.object(bot, "start_admin_wizard", new=start_admin_wizard),
        ):
            await bot.admin_action_cb(call)

        start_admin_wizard.assert_awaited_once()
        self.assertEqual(start_admin_wizard.await_args.args[1], "afterkey_message")
        self.assertIn("Huong dan kich hoat.", start_admin_wizard.await_args.args[3])

    async def test_after_key_message_wizard_saves_new_content(self):
        message = FakeMessage("Sau khi nhan key, lien he support neu can.")
        state = {"flow": "afterkey_message", "step": "content", "data": {}}

        with (
            patch.dict(bot.ADMIN_WIZARDS, {message.from_user.id: state}, clear=True),
            patch.object(bot, "set_setting", new=AsyncMock()) as set_setting,
        ):
            await bot.handle_admin_simple_wizard(message, state, message.text)

        set_setting.assert_awaited_once_with(bot.AFTER_KEY_MESSAGE_SETTING, "Sau khi nhan key, lien he support neu can.")
        self.assertEqual(bot.ADMIN_WIZARDS, {})
        self.assertIn("lưu", message.answers[-1]["text"].lower())


class FakeUser:
    id = 123


class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.from_user = FakeUser()
        self.answers = []

    async def answer(self, text: str, **kwargs):
        self.answers.append({"text": text, "kwargs": kwargs})


class FakeCallback:
    def __init__(self, data: str):
        self.data = data
        self.from_user = FakeUser()
        self.message = FakeMessage("")
        self.answers = []

    async def answer(self, text: str = "", **kwargs):
        self.answers.append((text, kwargs))


if __name__ == "__main__":
    unittest.main()
