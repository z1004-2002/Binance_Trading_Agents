"""Alertes hors machine par Telegram (EX-NOT-01, EX-NOT-02, EX-NOT-03). Bibliothèque standard uniquement.

N'envoie que WARNING et au-dessus, plus les rapports ; déduplique les alertes identiques ; masque tout secret ainsi que
le jeton du bot. Une panne réseau ne doit jamais faire tomber l'équipe : elle est comptée, jamais propagée.
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Callable, Mapping

from orchestre.core.secrets import mask_secrets

SENT = {"WARNING", "ERROR", "CRITICAL", "REPORT"}
MAX_LEN = 4000


def default_post(url: str, payload: dict) -> None:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=10):  # noqa: S310 : URL https constante
        pass


class TelegramNotifier:
    def __init__(
        self,
        token: str,
        chat_id: str,
        post: Callable[[str, dict], None] = default_post,
        dedup_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._token, self._chat_id, self._post = token, chat_id, post
        self._dedup, self._clock = dedup_seconds, clock
        self._recent: dict[tuple[str, str], float] = {}
        self.failures = 0

    def __call__(self, level: str, text: str) -> None:
        if level not in SENT:
            return
        now = self._clock()
        key = (level, text)
        if level != "REPORT":
            if key in self._recent and now - self._recent[key] < self._dedup:
                return
            self._recent[key] = now
        body = mask_secrets(f"[{level}] {text}").replace(self._token, "***")[:MAX_LEN]
        try:
            self._post(f"https://api.telegram.org/bot{self._token}/sendMessage", {"chat_id": self._chat_id, "text": body})
        except Exception:
            self.failures += 1


def from_env(environ: Mapping[str, str], post: Callable[[str, dict], None] = default_post) -> TelegramNotifier | None:
    token, chat = environ.get("TELEGRAM_BOT_TOKEN", ""), environ.get("TELEGRAM_CHAT_ID", "")
    return TelegramNotifier(token, chat, post=post) if token and chat else None
