"""Alertes (EX-NOT-02) : historique en mémoire, journal en base, notifieur externe optionnel."""

from __future__ import annotations

from typing import Callable

from orchestre.core.clock import Clock
from orchestre.persistence.store import Store


class Alerts:
    def __init__(
        self,
        clock: Clock,
        store: Store | None = None,
        notifier: Callable[[str, str], None] | None = None,
    ) -> None:
        self._clock = clock
        self._store = store
        self._notifier = notifier
        self.history: list[tuple[str, str]] = []

    def send(self, level: str, text: str) -> None:
        self.history.append((level, text))
        if self._store is not None:
            self._store.log_event(self._clock.now(), level, "alert", text)
        if self._notifier is not None:
            try:
                self._notifier(level, text)
            except Exception:  # une alerte ne doit jamais faire tomber l'équipe
                pass
