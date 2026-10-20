"""Limites de débit (EX-EX-07) : ralentit à 80 %, respecte Retry-After sur 429, jamais de retry sur 418.

Hypothèse à confirmer sur testnet : ccxt lève RateLimitExceeded pour 429 et DDoSProtection pour 418.
"""

from __future__ import annotations

import time
from typing import Callable, Mapping

import ccxt

from orchestre.exchange.base import IpBannedError


class RateLimiter:
    def __init__(
        self,
        headers: Callable[[], Mapping[str, str]] = lambda: {},
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 3,
        weight_limit: int = 6000,
        slow_ratio: float = 0.8,
        slow_delay: float = 1.0,
        base_backoff: float = 1.0,
    ) -> None:
        self._headers = headers
        self._sleep = sleep
        self._max_retries = max_retries
        self._weight_limit = weight_limit
        self._slow_ratio = slow_ratio
        self._slow_delay = slow_delay
        self._base_backoff = base_backoff

    def call(self, fn: Callable, *args, **kwargs):
        attempt = 0
        while True:
            try:
                result = fn(*args, **kwargs)
            except ccxt.RateLimitExceeded:
                attempt += 1
                if attempt > self._max_retries:
                    raise
                self._sleep(self._retry_after() or self._base_backoff * 2 ** (attempt - 1))
                continue
            except ccxt.DDoSProtection as exc:
                raise IpBannedError(f"adresse IP bannie ou protection active : {exc}") from exc
            self._throttle()
            return result

    def _header(self, name: str) -> str | None:
        for key, value in self._headers().items():
            if key.lower() == name.lower():
                return value
        return None

    def _retry_after(self) -> float | None:
        value = self._header("Retry-After")
        return float(value) if value is not None else None

    def _throttle(self) -> None:
        for key, value in self._headers().items():
            if key.lower().startswith("x-mbx-used-weight") and float(value) / self._weight_limit >= self._slow_ratio:
                self._sleep(self._slow_delay)
                return
