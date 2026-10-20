"""Stratèges de portefeuille : S-6 rotation momentum (§11.7), S-7 rééquilibrage à poids constants (§11.8),
et le panier à poids égaux détenu (référence)."""

from __future__ import annotations

import random
from decimal import Decimal

from orchestre.analysis.regime import classify
from orchestre.analysis.regimes import Regime
from orchestre.backtest.portfolio_engine import PortfolioTarget
from orchestre.exchange.base import Candle


class EqualWeightHold:
    """Référence : panier à poids égaux acheté une fois et conservé."""

    name, params_tried = "Panier égal détenu", 1

    def __init__(self) -> None:
        self._done = False

    def target(self, histories, positions, equity) -> PortfolioTarget:
        if self._done:
            return PortfolioTarget(None)
        self._done = True
        weight = Decimal(1) / Decimal(len(histories))
        return PortfolioTarget({s: weight for s in histories})

    def state(self) -> dict:
        return {"done": self._done}

    def load_state(self, state: dict) -> None:
        self._done = state["done"]


class Rotation:
    """S-6 : chaque semaine, détenir les K cryptos au meilleur rendement sur `lookback` jours, à poids égaux [29].

    Tout en devise de cotation si le régime de BTC est TENDANCE_BAISSE (règle v1 de la spec).
    """

    name = "S-6 rotation"
    params_tried = 1

    def __init__(self, top_k: int = 3, lookback: int = 21, rebalance_days: int = 7, btc: str = "BTCUSDT") -> None:
        self._k, self._lookback, self._every, self._btc = top_k, lookback, rebalance_days, btc
        self._last = None

    def target(self, histories: dict[str, list[Candle]], positions, equity) -> PortfolioTarget:
        now = next(iter(histories.values()))[-1].open_time
        if self._last is not None and (now - self._last).days < self._every:
            return PortfolioTarget(None)
        returns = {
            s: float(h[-1].close / h[-1 - self._lookback].close) - 1
            for s, h in histories.items()
            if len(h) > self._lookback
        }
        if not returns:
            return PortfolioTarget(None)
        self._last = now
        regime = classify(histories[self._btc]) if self._btc in histories else None
        if regime is not None and regime.regime is Regime.TENDANCE_BAISSE:
            return PortfolioTarget({})
        top = self._pick(returns)
        return PortfolioTarget({s: Decimal(1) / Decimal(len(top)) for s in top})

    def _pick(self, returns: dict[str, float]) -> list[str]:
        return sorted(returns, key=returns.__getitem__, reverse=True)[: self._k]

    def state(self) -> dict:
        return {"last": self._last.isoformat() if self._last else None}

    def load_state(self, state: dict) -> None:
        from datetime import datetime
        self._last = datetime.fromisoformat(state["last"]) if state["last"] else None


class RandomRotation(Rotation):
    """Modèle nul de S-6 : mêmes dates, même règle « tout en cash si BTC baissier », mais choix AU HASARD.

    Sert à savoir si le résultat de S-6 vient du momentum ou seulement de l'univers choisi (biais du survivant).
    """

    name = "Rotation aléatoire"

    def __init__(self, seed: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self._rng = random.Random(seed)

    def _pick(self, returns: dict[str, float]) -> list[str]:
        return self._rng.sample(sorted(returns), min(self._k, len(returns)))


class ConstantMix:
    """S-7 : poids constants ; rééquilibre si un poids s'écarte de plus de `band` de sa cible, ou chaque mois [32]."""

    name = "S-7 rééquilibrage"
    params_tried = 1

    def __init__(self, weights: dict[str, Decimal], band: Decimal = Decimal("0.05"), monthly: bool = True) -> None:
        self._weights, self._band, self._monthly = dict(weights), band, monthly
        self._month = None

    def target(self, histories: dict[str, list[Candle]], positions, equity) -> PortfolioTarget:
        last = next(iter(histories.values()))[-1]
        month = (last.open_time.year, last.open_time.month)
        if self._month is None:
            self._month = month
            return PortfolioTarget(dict(self._weights))
        drift = max(
            abs(positions.get(s, Decimal("0")) * histories[s][-1].close / equity - w) for s, w in self._weights.items()
        )
        if drift > self._band or (self._monthly and month != self._month):
            self._month = month
            return PortfolioTarget(dict(self._weights))
        return PortfolioTarget(None)

    def state(self) -> dict:
        return {"month": list(self._month) if self._month else None}

    def load_state(self, state: dict) -> None:
        self._month = tuple(state["month"]) if state["month"] else None
