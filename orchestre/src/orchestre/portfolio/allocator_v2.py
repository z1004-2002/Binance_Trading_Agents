"""Allocateur v2 (§12.1) : pondération par rendement ajusté du risque, bornée et lissée [24][30].

poids brut = max(Sortino, 0) x (1 / volatilité) x multiplicateur de régime ; normalisation ; plafond par agent
(l'excédent va aux autres, sinon reste en liquidités) ; variation maximale de `max_step` par rééquilibrage.
Toute baisse de risque est automatique ; l'augmentation reste bornée par les plafonds de configuration (C-08).
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Mapping, Sequence

SORTINO_CAP = 10.0


@dataclass(frozen=True)
class AllocatorConfig:
    window: int = 60
    max_weight: float = 0.6
    max_step: float = 0.10


def score(returns: Sequence[float]) -> float:
    """Sortino quotidien (plafonné) divisé par la volatilité ; 0 si rendement négatif, nul ou historique court."""
    if len(returns) < 2:
        return 0.0
    mean, vol = statistics.mean(returns), statistics.stdev(returns)
    if mean <= 0 or vol == 0:
        return 0.0
    downside = math.sqrt(sum(min(r, 0.0) ** 2 for r in returns) / len(returns))
    sortino = SORTINO_CAP if downside == 0 else min(mean / downside, SORTINO_CAP)
    return sortino / vol


def _cap(weights: dict[str, float], cap: float) -> dict[str, float]:
    w, fixed = dict(weights), set()
    for _ in range(len(w) + 1):
        over = [a for a in w if a not in fixed and w[a] > cap + 1e-12]
        if not over:
            break
        for a in over:
            w[a] = cap
            fixed.add(a)
        free = [a for a in w if a not in fixed]
        spare = 1.0 - sum(w.values())
        if not free or spare <= 1e-12:
            break
        total = sum(w[a] for a in free)
        for a in free:
            w[a] += spare * (w[a] / total if total > 0 else 1 / len(free))
    return w


def target_weights(
    returns: Mapping[str, Sequence[float]],
    multipliers: Mapping[str, float],
    previous: Mapping[str, float] | None,
    cfg: AllocatorConfig = AllocatorConfig(),
) -> dict[str, float]:
    eligible = [a for a in returns if multipliers.get(a, 0.0) > 0]
    if not eligible:
        return {}
    raw = {a: score(returns[a][-cfg.window :]) * multipliers[a] for a in eligible}
    if sum(raw.values()) <= 0:
        raw = {a: multipliers[a] for a in eligible}  # personne n'a de score positif : poids égaux (modulés)
    total = sum(raw.values())
    weights = _cap({a: v / total for a, v in raw.items()}, cfg.max_weight)

    if previous is not None:
        for a in set(weights) | set(previous):
            low, high = previous.get(a, 0.0) - cfg.max_step, previous.get(a, 0.0) + cfg.max_step
            weights[a] = min(max(weights.get(a, 0.0), low), high)
        # le plafond l'emporte sur le lissage : une baisse de risque est immédiate (C-08)
        weights = {a: min(max(0.0, w), cfg.max_weight) for a, w in weights.items()}
        excess = sum(weights.values())
        if excess > 1.0:
            weights = {a: w / excess for a, w in weights.items()}
    return {a: w for a, w in weights.items() if w > 0}


def should_rebalance(last, now, days: int = 7) -> bool:
    return last is None or (now - last).days >= days
