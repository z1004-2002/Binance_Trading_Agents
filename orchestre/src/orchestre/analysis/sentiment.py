"""SentimentAgent (cœur) : indice Crypto Fear & Greed (EX-AN-13) et influence asymétrique (EX-AN-15).

Le sentiment ne peut QUE réduire le risque : multiplicateur dans [0,5 ; 1] ou pause d'une paire.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

ATTRIBUTION = "Source : Crypto Fear & Greed Index, Alternative.me (https://alternative.me/crypto/fear-and-greed-index/)"
URL = "https://api.alternative.me/fng/?limit=1"


@dataclass(frozen=True)
class FearGreedReading:
    value: int
    classification: str
    timestamp: datetime | None


def parse_fear_greed(payload: dict) -> FearGreedReading:
    try:
        row = payload["data"][0]
        value = int(row["value"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError(f"réponse Fear & Greed invalide : {exc!r}") from exc
    if not 0 <= value <= 100:
        raise ValueError(f"valeur hors de [0 ; 100] : {value}")
    ts = row.get("timestamp")
    stamp = datetime.fromtimestamp(int(ts), tz=timezone.utc) if ts else None
    return FearGreedReading(value, str(row.get("value_classification", "")), stamp)


def default_fetch() -> dict:
    with urllib.request.urlopen(URL, timeout=10) as response:  # noqa: S310 : URL constante, https
        return json.load(response)


def fetch_fear_greed(fetch: Callable[[], dict] = default_fetch) -> FearGreedReading | None:
    """None si la source est indisponible : le sentiment est optionnel, l'équipe fonctionne sans (EX-LLM-02)."""
    try:
        return parse_fear_greed(fetch())
    except Exception:
        return None


def sentiment_multiplier(value: int) -> Decimal:
    """Réduit l'exposition aux extrêmes (proposition de l'auteur, seuils à valider) ; jamais au-dessus de 1."""
    if value > 90 or value < 10:
        return Decimal("0.5")
    if value > 75 or value < 20:
        return Decimal("0.75")
    return Decimal("1")


@dataclass
class SentimentState:
    reasons: dict[str, str] = field(default_factory=dict)

    def pause_pair(self, pair: str, reason: str) -> None:
        self.reasons[pair] = reason

    def resume_pair(self, pair: str) -> None:
        self.reasons.pop(pair, None)

    def is_paused(self, pair: str) -> bool:
        return pair in self.reasons
