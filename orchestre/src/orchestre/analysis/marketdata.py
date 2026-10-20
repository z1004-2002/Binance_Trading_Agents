"""MarketDataAgent (cœur) : contrôle de cohérence des bougies (EX-AN-02, EX-AN-03).

Seules les bougies clôturées sont diffusées. Toute anomalie est rejetée et signalée : les agents
qui en dépendent passent en DEGRADED (décision prise par l'appelant d'après `healthy`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from orchestre.exchange.base import Candle

INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


@dataclass(frozen=True)
class SeriesReport:
    accepted: list[Candle]
    problems: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return not self.problems


def _candle_problems(c: Candle) -> list[str]:
    problems = []
    if min(c.open, c.high, c.low, c.close) <= 0:
        problems.append(f"{c.open_time:%Y-%m-%d} : prix nul ou négatif")
    elif c.high < c.low or not (c.low <= c.open <= c.high) or not (c.low <= c.close <= c.high):
        problems.append(f"{c.open_time:%Y-%m-%d} : bougie incohérente (OHLC)")
    if c.volume < 0:
        problems.append(f"{c.open_time:%Y-%m-%d} : volume négatif")
    return problems


def validate_series(
    candles: list[Candle], interval: str, max_jump_pct: Decimal = Decimal("0.40")
) -> SeriesReport:
    seconds = INTERVAL_SECONDS[interval]
    accepted: list[Candle] = []
    problems: list[str] = []
    previous: Candle | None = None
    for c in candles:
        if not c.closed:
            continue  # EX-AN-02
        own = _candle_problems(c)
        if previous is not None and not own:
            if c.open_time <= previous.open_time:
                own.append(f"{c.open_time:%Y-%m-%d} : horodatage non croissant")
            else:
                gap = (c.open_time - previous.open_time).total_seconds() / seconds - 1
                if gap > 0:
                    problems.append(f"{c.open_time:%Y-%m-%d} : trou de données ({gap:.0f} bougie(s) manquante(s))")
            if previous.close > 0 and abs(c.close - previous.close) / previous.close > max_jump_pct:
                own.append(f"{c.open_time:%Y-%m-%d} : saut aberrant (> {max_jump_pct:.0%})")
        if own:
            problems.extend(own)
            continue
        accepted.append(c)
        previous = c
    return SeriesReport(accepted, problems)
