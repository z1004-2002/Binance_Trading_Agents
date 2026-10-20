"""Synthèse et tableau comparatif des backtests (§17)."""

from __future__ import annotations

import math

from orchestre.backtest.engine import BacktestResult
from orchestre.metrics.performance import annualized_return, calmar, max_drawdown, sharpe, sortino, total_return


def summarize(result: BacktestResult, rf_annual: float = 0.0) -> dict:
    eq, days = result.equity, result.days
    return {
        "name": result.name,
        "total_return": total_return(eq),
        "annualized": annualized_return(eq, days),
        "max_drawdown": max_drawdown(eq),
        "sharpe": sharpe(eq, rf_annual, result.periods_per_year),
        "sortino": sortino(eq, rf_annual, result.periods_per_year),
        "calmar": calmar(eq, days),
        "fees": float(result.fees_paid),
        "trades": len(result.trades),
        "params_tried": result.params_tried,
    }


def _ratio(x: float) -> str:
    return "∞" if math.isinf(x) else f"{x:6.2f}"


def format_table(rows: list[dict]) -> str:
    header = (
        f"{'Stratégie':<24}{'Rendement':>10}{'Annualisé':>11}{'Drawdown max':>14}"
        f"{'Sharpe':>8}{'Sortino':>9}{'Calmar':>8}{'Frais':>9}{'Trades':>8}{'Essais':>8}"
    )
    lines = [header, "-" * len(header)]
    for r in rows:
        lines.append(
            f"{r['name']:<24}{r['total_return']:>10.1%}{r['annualized']:>11.1%}{r['max_drawdown']:>14.1%}"
            f"{_ratio(r['sharpe']):>8}{_ratio(r['sortino']):>9}{_ratio(r['calmar']):>8}"
            f"{r['fees']:>9.2f}{r['trades']:>8}{r['params_tried']:>8}"
        )
    return "\n".join(lines)
