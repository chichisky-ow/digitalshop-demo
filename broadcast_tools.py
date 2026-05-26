# -*- coding: utf-8 -*-
from dataclasses import dataclass
from typing import Awaitable, Callable


SendMedia = Callable[[int], Awaitable[object]]
DeleteMessage = Callable[[int, int], Awaitable[None]]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class BroadcastDelivery:
    user_id: int
    message_id: int


@dataclass(frozen=True)
class BroadcastResult:
    sent: list[BroadcastDelivery]
    failed_user_ids: list[int]
    attempts_by_user: dict[int, int]

    @property
    def sent_count(self) -> int:
        return len(self.sent)

    @property
    def failed_count(self) -> int:
        return len(self.failed_user_ids)


async def send_media_with_retry(
    user_ids: list[int],
    send_media: SendMedia,
    *,
    max_attempts: int = 3,
    delay_seconds: float = 0.0,
    sleep: Sleep | None = None,
) -> BroadcastResult:
    sent: list[BroadcastDelivery] = []
    failed_user_ids: list[int] = []
    attempts_by_user: dict[int, int] = {}

    for user_id in user_ids:
        delivered = False
        for attempt in range(1, max_attempts + 1):
            attempts_by_user[user_id] = attempt
            try:
                message = await send_media(user_id)
                sent.append(BroadcastDelivery(user_id=user_id, message_id=int(message.message_id)))
                delivered = True
                break
            except Exception:
                if attempt < max_attempts and sleep is not None and delay_seconds > 0:
                    await sleep(delay_seconds)
        if not delivered:
            failed_user_ids.append(user_id)

    return BroadcastResult(sent=sent, failed_user_ids=failed_user_ids, attempts_by_user=attempts_by_user)


async def recall_deliveries(
    deliveries: list[BroadcastDelivery],
    delete_message: DeleteMessage,
    *,
    delay_seconds: float = 0.0,
    sleep: Sleep | None = None,
) -> tuple[int, int]:
    recalled = 0
    failed = 0
    for index, delivery in enumerate(deliveries):
        try:
            await delete_message(delivery.user_id, delivery.message_id)
            recalled += 1
        except Exception:
            failed += 1
        if index < len(deliveries) - 1 and sleep is not None and delay_seconds > 0:
            await sleep(delay_seconds)
    return recalled, failed
