"""Backtest de l'équipe (§16.2, phase 6) : les stratégies sont combinées par l'allocateur, avec gouvernance par régime.

Méthode : chaque stratégie est d'abord rejouée seule (moteurs de backtest réels, frais inclus) ; la courbe de l'équipe
combine ensuite leurs rendements quotidiens avec les poids de l'allocateur, recalculés chaque semaine avec les
SEULS rendements passés. Une réserve de trésorerie reste en liquidités.

Approximations assumées (EX-BT-05) : les poids sont tenus constants entre deux rééquilibrages ; le coût de
réallocation entre stratégies et la sortie effective des positions d'un stratège dont le budget tombe à 0 ne sont
pas modélisés (résultat légèrement optimiste).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Sequence

from orchestre.analysis.regime import RegimeConfig, RegimeTracker, classify
from orchestre.analysis.regimes import Regime
from orchestre.backtest.engine import BacktestError, BacktestResult
from orchestre.exchange.base import Candle
from orchestre.portfolio.allocator import MULTIPLIERS
from orchestre.portfolio.allocator_v2 import AllocatorConfig, target_weights

REGIME_WINDOW = 450  # bougies suffisantes pour la moyenne 200 j et la volatilité 30 j sur 1 an


@dataclass
class TeamPath:
    equity: list[float]
    weights: list[dict[str, float]] = field(default_factory=list)
    rebalance_days: list[int] = field(default_factory=list)


def regime_series(candles: Sequence[Candle], cfg: RegimeConfig = RegimeConfig()) -> list[Regime | None]:
    """Régime publié (avec hystérésis) à la clôture de chaque jour, sans regard vers l'avenir."""
    tracker, out = RegimeTracker(cfg.hysteresis_k), []
    for i in range(len(candles)):
        reading = classify(list(candles[max(0, i + 1 - REGIME_WINDOW) : i + 1]), cfg) if i + 1 >= cfg.min_history else None
        if reading is not None:
            tracker.update(reading)
        out.append(tracker.published)
    return out


def combine(
    returns: dict[str, list[float]],
    multipliers_at: Callable[[int], dict[str, float]],
    method: str,
    cfg: AllocatorConfig,
    reserve: float = 0.20,
    every: int = 7,
    initial: float = 1000.0,
) -> TeamPath:
    length = len(next(iter(returns.values())))
    equity, path, previous = [initial], TeamPath([initial]), None
    invested: dict[str, float] = {}
    for k in range(length):
        if k % every == 0:
            multipliers = multipliers_at(k)
            if method == "v2":
                weights = target_weights({a: r[:k] for a, r in returns.items()}, multipliers, previous, cfg)
            else:  # v1 : budgets fixes (1/N chacun) x multiplicateur de régime
                weights = {a: multipliers.get(a, 0.0) / len(returns) for a in returns if multipliers.get(a, 0.0) > 0}
            previous = weights
            invested = {a: w * (1 - reserve) for a, w in weights.items()}
            path.weights.append(weights)
            path.rebalance_days.append(k)
        equity.append(equity[-1] * (1 + sum(w * returns[a][k] for a, w in invested.items())))
    path.equity = equity
    return path


def team_from_results(
    results: dict[str, BacktestResult],
    regimes: Sequence[Regime | None],
    cfg: AllocatorConfig = AllocatorConfig(),
    reserve: float = 0.20,
    every: int = 7,
) -> dict[str, BacktestResult]:
    """`results` : code de stratégie ("S-3"...) -> backtest autonome ; `regimes` : régime à chaque clôture (même longueur)."""
    lengths = {len(r.equity) for r in results.values()}
    if len(lengths) != 1:
        raise BacktestError("les courbes des stratégies doivent avoir la même longueur (même fenêtre et même préchauffage)")
    n = lengths.pop()
    returns = {
        code: [r.equity[i + 1] / r.equity[i] - 1 for i in range(n - 1)] for code, r in results.items()
    }

    def multipliers_at(k: int) -> dict[str, float]:
        regime = regimes[k] if k < len(regimes) else None
        return {
            code: 1.0 if regime is None or code not in MULTIPLIERS else float(MULTIPLIERS[code][regime])
            for code in results
        }

    out: dict[str, BacktestResult] = {}
    initial = next(iter(results.values())).equity[0]
    for method, label in (("v1", "Équipe v1 (budgets fixes x régime)"), ("v2", "Équipe v2 (allocateur ajusté du risque)")):
        path = combine(returns, multipliers_at, method, cfg, reserve, every, initial)
        avg_weight = {
            code: sum(w.get(code, 0.0) for w in path.weights) / len(path.weights) * (1 - reserve) for code in results
        }
        out[method] = BacktestResult(
            label, [], path.equity, [t for r in results.values() for t in r.trades],
            sum((r.fees_paid * Decimal(str(avg_weight[c])) for c, r in results.items()), Decimal("0")),
            Decimal("0"), len(results), Decimal("0"), next(iter(results.values())).periods_per_year,
        )
        out[method].weights = path.weights  # type: ignore[attr-defined]
    return out
