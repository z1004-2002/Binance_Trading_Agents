"""Horloge injectée (A-6) : réelle, simulée ou figée. Toujours en UTC."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class SimulatedClock:
    """Horloge contrôlée pour les tests et les backtests."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("l'horloge exige un datetime avec fuseau (UTC)")
        self._now = start.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta
