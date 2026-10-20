"""Messages du bus (EX-AG-02, EX-AG-03), inspirés de FIPA-ACL."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any


class Performative(str, Enum):
    INFORM = "inform"
    PROPOSE = "propose"
    ACCEPT_PROPOSAL = "accept-proposal"
    REJECT_PROPOSAL = "reject-proposal"
    REQUEST = "request"
    CONFIRM = "confirm"
    FAILURE = "failure"
    CFP = "cfp"
    CANCEL = "cancel"


@dataclass(frozen=True)
class Message:
    sender: str
    topic: str
    performative: Performative
    content: dict[str, Any]
    reason: str
    timestamp: datetime
    ttl: timedelta
    conversation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    in_reply_to: str | None = None
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("timestamp UTC avec fuseau requis")
        if not self.reason.strip():
            raise ValueError("toute décision porte une raison lisible (A-9)")

    def is_expired(self, now: datetime) -> bool:
        return now >= self.timestamp + self.ttl
