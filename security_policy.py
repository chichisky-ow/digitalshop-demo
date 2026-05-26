GROUP_CHAT_TYPES = {"group", "supergroup", "channel"}


def group_event_policy(chat_type: str, user_id: int, admin_ids: set[int]) -> str:
    if chat_type == "private":
        return "allow"
    if chat_type in GROUP_CHAT_TYPES and user_id in admin_ids:
        return "allow"
    if chat_type in GROUP_CHAT_TYPES:
        return "ignore"
    return "allow"
