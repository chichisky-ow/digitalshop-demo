import unittest

from security_policy import group_event_policy


class GroupPrivacyPolicyTests(unittest.TestCase):
    def test_private_chat_is_allowed_for_customers(self):
        self.assertEqual(group_event_policy("private", 111, {999}), "allow")

    def test_group_message_from_non_admin_is_ignored(self):
        self.assertEqual(group_event_policy("supergroup", 111, {999}), "ignore")

    def test_group_message_from_admin_is_allowed(self):
        self.assertEqual(group_event_policy("group", 999, {999}), "allow")

    def test_channel_context_from_admin_is_allowed(self):
        self.assertEqual(group_event_policy("channel", 999, {999}), "allow")


if __name__ == "__main__":
    unittest.main()
