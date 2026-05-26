import unittest
from unittest.mock import AsyncMock, patch

import bot


class NoticeRecallTests(unittest.IsolatedAsyncioTestCase):
    async def test_notice_wizard_returns_recall_button_for_sent_message(self):
        message = FakeMessage("Cap nhat don hang")
        state = {"flow": "notice", "step": "content", "data": {"user_id": 101}}
        sent_message = FakeSentMessage(101, 501)
        recall_markup = object()

        with (
            patch.object(bot.bot, "send_message", new=AsyncMock(return_value=sent_message)),
            patch.object(bot, "remember_broadcast", return_value="notice-1") as remember_broadcast,
            patch.object(bot, "recall_broadcast_menu", return_value=recall_markup),
            patch.dict(bot.ADMIN_WIZARDS, {message.from_user.id: state}, clear=True),
        ):
            await bot.handle_admin_simple_wizard(message, state, message.text)

        remember_broadcast.assert_called_once()
        kind, deliveries = remember_broadcast.call_args.args
        self.assertEqual(kind, "notice")
        self.assertEqual([(item.user_id, item.message_id) for item in deliveries], [(101, 501)])
        self.assertIs(message.answers[-1]["kwargs"]["reply_markup"], recall_markup)

    async def test_broadcast_wizard_returns_recall_button_for_sent_messages(self):
        message = FakeMessage("Thong bao he thong")
        state = {"flow": "broadcast", "step": "content", "data": {}}
        recall_markup = object()

        with (
            patch.object(bot, "get_all_users", new=AsyncMock(return_value=[{"user_id": 101}, {"user_id": 202}])),
            patch.object(
                bot.bot,
                "send_message",
                new=AsyncMock(side_effect=[FakeSentMessage(101, 601), FakeSentMessage(202, 602)]),
            ),
            patch.object(bot, "remember_broadcast", return_value="broadcast-1") as remember_broadcast,
            patch.object(bot, "recall_broadcast_menu", return_value=recall_markup),
            patch.object(bot.asyncio, "sleep", new=AsyncMock()),
            patch.dict(bot.ADMIN_WIZARDS, {message.from_user.id: state}, clear=True),
        ):
            await bot.handle_admin_simple_wizard(message, state, message.text)

        remember_broadcast.assert_called_once()
        kind, deliveries = remember_broadcast.call_args.args
        self.assertEqual(kind, "broadcast")
        self.assertEqual([(item.user_id, item.message_id) for item in deliveries], [(101, 601), (202, 602)])
        self.assertIs(message.answers[-1]["kwargs"]["reply_markup"], recall_markup)


class FakeUser:
    id = 123


class FakeChat:
    type = "private"
    id = 123


class FakeSentMessage:
    def __init__(self, user_id: int, message_id: int):
        self.chat = type("Chat", (), {"type": "private", "id": user_id})()
        self.message_id = message_id


class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.from_user = FakeUser()
        self.chat = FakeChat()
        self.answers = []

    async def answer(self, text: str, **kwargs):
        self.answers.append({"text": text, "kwargs": kwargs})


if __name__ == "__main__":
    unittest.main()
