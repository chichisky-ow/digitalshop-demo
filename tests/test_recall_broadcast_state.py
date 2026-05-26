import unittest
from unittest.mock import AsyncMock, patch

import bot
from broadcast_tools import BroadcastDelivery


class RecallBroadcastStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_recall_marks_broadcast_as_recalling_before_deleting(self):
        call = FakeCallback("recall_broadcast:abc123")
        record = {
            "kind": "broadcast",
            "deliveries": [BroadcastDelivery(user_id=1, message_id=101)],
            "created_at": 1,
            "recalled": False,
            "recalling": False,
        }

        async def recall_deliveries(deliveries, delete_message, **kwargs):
            self.assertTrue(record["recalling"])
            self.assertEqual(deliveries, record["deliveries"])
            self.assertGreater(kwargs["delay_seconds"], 0)
            return (1, 0)

        with (
            patch.object(bot, "is_admin", return_value=True),
            patch.dict(bot.BROADCAST_HISTORY, {"abc123": record}, clear=True),
            patch.object(bot, "recall_deliveries", new=recall_deliveries),
            patch.object(bot.bot, "delete_message", new=AsyncMock()),
            patch.object(bot.asyncio, "sleep", new=AsyncMock()),
        ):
            await bot.recall_broadcast_cb(call)

        self.assertTrue(record["recalled"])
        self.assertFalse(record["recalling"])
        self.assertIn("Dang thu hoi", call.answers[0][0])
        self.assertIn("thu", call.message.answers[-1]["text"].lower())

    async def test_recall_ignores_second_click_while_recalling(self):
        call = FakeCallback("recall_broadcast:abc123")
        record = {
            "kind": "broadcast",
            "deliveries": [BroadcastDelivery(user_id=1, message_id=101)],
            "created_at": 1,
            "recalled": False,
            "recalling": True,
        }

        with (
            patch.object(bot, "is_admin", return_value=True),
            patch.dict(bot.BROADCAST_HISTORY, {"abc123": record}, clear=True),
            patch.object(bot, "recall_deliveries", new=AsyncMock()) as recall_deliveries,
        ):
            await bot.recall_broadcast_cb(call)

        recall_deliveries.assert_not_awaited()
        self.assertEqual(call.answers[0][1], {"show_alert": True})


class FakeUser:
    id = 123


class FakeMessage:
    def __init__(self):
        self.answers = []

    async def answer(self, text: str, **kwargs):
        self.answers.append({"text": text, "kwargs": kwargs})


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
