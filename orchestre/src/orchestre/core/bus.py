"""Bus de messages publication/abonnement en processus (EX-ARCH-01). Journalise tout (EX-AG-06)."""

from __future__ import annotations

from collections import defaultdict
from typing import Callable

from orchestre.core.clock import Clock
from orchestre.core.messages import Message

Handler = Callable[[Message], None]


class MessageBus:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self.log: list[Message] = []
        self.handler_errors: list[tuple[Message, Exception]] = []

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._subs[topic].append(handler)

    def publish(self, message: Message) -> None:
        self.log.append(message)
        if message.is_expired(self._clock.now()):
            return
        for handler in self._subs[message.topic]:
            try:
                handler(message)
            except Exception as exc:  # EX-ARCH-03 : un agent défaillant n'arrête pas les autres
                self.handler_errors.append((message, exc))
