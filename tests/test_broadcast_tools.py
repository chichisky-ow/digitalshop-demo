import unittest
from types import SimpleNamespace

from broadcast_tools import BroadcastDelivery, recall_deliveries, send_media_with_retry


class BroadcastToolsTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_failed_user_until_success(self):
        attempts = {}

        async def send_media(user_id):
            attempts[user_id] = attempts.get(user_id, 0) + 1
            if attempts[user_id] < 3:
                raise RuntimeError("temporary failure")
            return SimpleNamespace(message_id=900 + user_id)

        result = await send_media_with_retry([1], send_media, max_attempts=3)

        self.assertEqual(result.sent_count, 1)
        self.assertEqual(result.failed_count, 0)
        self.assertEqual(result.sent[0], BroadcastDelivery(user_id=1, message_id=901))
        self.assertEqual(result.attempts_by_user[1], 3)

    async def test_gives_up_after_three_failed_attempts(self):
        async def send_media(user_id):
            raise RuntimeError("permanent failure")

        result = await send_media_with_retry([1, 2], send_media, max_attempts=3)

        self.assertEqual(result.sent_count, 0)
        self.assertEqual(result.failed_user_ids, [1, 2])
        self.assertEqual(result.attempts_by_user, {1: 3, 2: 3})

    async def test_recall_deletes_successful_deliveries(self):
        deleted = []

        async def delete_message(user_id, message_id):
            deleted.append((user_id, message_id))

        recalled, failed = await recall_deliveries(
            [
                BroadcastDelivery(user_id=1, message_id=101),
                BroadcastDelivery(user_id=2, message_id=202),
            ],
            delete_message,
        )

        self.assertEqual((recalled, failed), (2, 0))
        self.assertEqual(deleted, [(1, 101), (2, 202)])

    async def test_recall_can_pause_between_deletes(self):
        deleted = []
        pauses = []

        async def delete_message(user_id, message_id):
            deleted.append((user_id, message_id))

        async def sleep(seconds):
            pauses.append(seconds)

        recalled, failed = await recall_deliveries(
            [
                BroadcastDelivery(user_id=1, message_id=101),
                BroadcastDelivery(user_id=2, message_id=202),
                BroadcastDelivery(user_id=3, message_id=303),
            ],
            delete_message,
            delay_seconds=0.1,
            sleep=sleep,
        )

        self.assertEqual((recalled, failed), (3, 0))
        self.assertEqual(deleted, [(1, 101), (2, 202), (3, 303)])
        self.assertEqual(pauses, [0.1, 0.1])


if __name__ == "__main__":
    unittest.main()
