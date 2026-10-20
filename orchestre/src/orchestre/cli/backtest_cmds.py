"""Commandes de backtest de la CLI : backtest, walkforward, portfolio-backtest (§16). Données publiques, aucun ordre."""

from __future__ import annotations

from decimal import Decimal
from typing import Callable, TextIO

from orchestre.analysis.marketdata import INTERVAL_SECONDS
from orchestre.backtest.data import load_history
from orchestre.backtest.engine import BacktestConfig, run_backtest
from orchestre.backtest.portfolio_engine import align_candles, run_portfolio_backtest
from orchestre.backtest.references import BuyAndHold, CashOnly, RebalancedMix, WeeklyDca
from orchestre.backtest.report import format_table, summarize
from orchestre.backtest.team import regime_series, team_from_results
from orchestre.backtest.walkforward import walk_forward
from orchestre.config import ConfigError
from orchestre.core.clock import SystemClock
from orchestre.exchange.base import Candle
from orchestre.exchange.binance import BinanceExchange
from orchestre.portfolio.allocator_v2 import AllocatorConfig
from orchestre.strategies.breakout import DonchianBreakout
from orchestre.strategies.meanreversion import MeanReversion
from orchestre.strategies.rotation import ConstantMix, EqualWeightHold, RandomRotation, Rotation
from orchestre.strategies.trend import TrendFollowing

DEFAULT_WARMUP = {"1d": 120, "4h": 200}
DEFAULT_UNIVERSE = "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT"

# (classe, grille de paramètres, intervalle imposé par la spec)
WALKFORWARD = {
    "trend": (TrendFollowing, [{"lookback": lb, "atr_mult": m} for lb in (60, 90, 120) for m in (2.0, 3.0, 4.0)], "1d"),
    "breakout": (DonchianBreakout, [{"entry_len": e, "exit_len": x} for e in (10, 20, 40) for x in (5, 10, 20) if x <= e], "1d"),
    "meanreversion": (MeanReversion, [{"bb_k": k, "rsi_entry": r} for k in (1.5, 2.0, 2.5) for r in (25.0, 30.0, 35.0)], "4h"),
}
CATALOG_NOTE = ("Attention : période courte, résultats INDICATIFS (Sharpe et Calmar très incertains, EX-XP-05). "
                "'Essais' = nombre de combinaisons de paramètres essayées (contrôle du sur-ajustement, EX-BT-05). "
                "Le passé ne prédit pas l'avenir.")


def _geometry(interval: str) -> tuple[int, int]:
    """(bougies par jour, périodes par an)."""
    per_day = 86400 // INTERVAL_SECONDS[interval]
    return per_day, 365 * per_day


def _config(args, interval: str) -> BacktestConfig:
    return BacktestConfig(
        Decimal(str(args.cash)), Decimal(str(args.fee)), Decimal(str(args.slippage)),
        periods_per_year=_geometry(interval)[1],
    )


def _load(symbol: str, interval: str, needed: int, args, factory: Callable) -> list[Candle]:
    exchange = BinanceExchange(factory(), quote=symbol[-4:])
    report = load_history(exchange, symbol, interval, needed, args.data_dir, SystemClock().now())
    if not report.healthy:
        raise ConfigError(f"historique de {symbol} incohérent : " + "; ".join(report.problems[:5]))
    return report.accepted


def cmd_backtest(args, out: TextIO, err: TextIO, factory: Callable) -> int:
    per_day, _ = _geometry(args.interval)
    warmup = args.warmup if args.warmup is not None else DEFAULT_WARMUP[args.interval]
    if warmup + 30 > args.days * per_day:
        raise ConfigError(f"préchauffage ({warmup}) incompatible avec {args.days} jours d'historique")
    candles = _load(args.symbol, args.interval, args.days * per_day, args, factory)
    if len(candles) < warmup + 30:
        raise ConfigError(f"historique trop court ({len(candles)} bougies) pour un préchauffage de {warmup}")
    own = [MeanReversion()] if args.interval == "4h" else [TrendFollowing(), DonchianBreakout()]
    strategies = [BuyAndHold(), WeeklyDca(), RebalancedMix(), CashOnly(), *own]
    cfg = _config(args, args.interval)
    rows = [summarize(run_backtest(candles, s, cfg, warmup)) for s in strategies]
    print(f"BACKTEST {args.symbol} ({args.interval}) du {candles[warmup].open_time.date()} au {candles[-1].open_time.date()} "
          f"({len(candles) - warmup} bougies, capital {args.cash}, frais {args.fee:.2%}, glissement {args.slippage:.2%}). "
          "Mêmes règles d'exécution pour tous.", file=out)
    print(format_table(rows), file=out)
    print("\n" + CATALOG_NOTE, file=out)
    return 0


def cmd_walkforward(args, out: TextIO, err: TextIO, factory: Callable) -> int:
    strategy_cls, grid, required = WALKFORWARD[args.strategy]
    if args.interval != required:
        raise ConfigError(f"la stratégie '{args.strategy}' se définit sur bougies {required} (spec §11) : utiliser --interval {required}")
    per_day, _ = _geometry(args.interval)
    warmup = args.warmup if args.warmup is not None else DEFAULT_WARMUP[args.interval]
    train, test = args.train * per_day, args.test * per_day
    candles = _load(args.symbol, args.interval, args.days * per_day, args, factory)
    cfg = _config(args, args.interval)
    try:
        result = walk_forward(candles, lambda p: strategy_cls(**p), grid, cfg, train, test, warmup)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    print(f"WALK-FORWARD {args.symbol} ({args.interval}) : entraînement {args.train} j, test {args.test} j, "
          f"{len(result.windows)} fenêtres, {len(grid)} combinaisons par fenêtre.", file=out)
    print(f"{'Fenêtre':<9}{'Paramètres choisis (entraînement)':<44}{'Score entr.':>12}{'Test':>9}{'B-1 même période':>18}", file=out)
    for i, w in enumerate(result.windows, 1):
        params = ", ".join(f"{k}={v}" for k, v in w.params.items())
        print(f"{i:<9}{params:<44}{min(w.train_score, 999):>12.2f}{w.test_return:>9.1%}{w.benchmark_return:>18.1%}", file=out)
    print("", file=out)
    print(format_table([summarize(result.oos), summarize(result.benchmark)]), file=out)
    print(f"\nEssais : {result.params_tried} combinaisons évaluées au total ({len(grid)} x {len(result.windows)} fenêtres). "
          "Seule la partie HORS ÉCHANTILLON compte ; période courte, résultats indicatifs (EX-XP-05).", file=out)
    return 0


def _null_model(args, candle_map, cfg, warmup, actual: float) -> str:
    """Rotation aléatoire (mêmes règles, choix au hasard) : où se situe S-6 dans la distribution du hasard ?"""
    results = sorted(
        summarize(run_portfolio_backtest(candle_map, RandomRotation(seed), cfg, warmup))["total_return"]
        for seed in range(args.null_runs)
    )
    below = sum(1 for r in results if r < actual)
    pct = lambda q: results[min(int(q * len(results)), len(results) - 1)]  # noqa: E731
    return (f"Modèle nul ({args.null_runs} rotations au hasard, mêmes dates et même règle cash) : rendement médian "
            f"{pct(0.5):.1%}, 10e-90e centile [{pct(0.1):.1%} ; {pct(0.9):.1%}]. S-6 ({actual:.1%}) bat {below / len(results):.0%} "
            "des tirages au hasard (au moins 95 % pour parler d'un effet du momentum).")


def cmd_portfolio_backtest(args, out: TextIO, err: TextIO, factory: Callable) -> int:
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if "BTCUSDT" not in symbols:
        raise ConfigError("l'univers doit contenir BTCUSDT (référence de régime et de comparaison)")
    candle_map = {s: _load(s, "1d", args.days, args, factory) for s in symbols}
    cfg = _config(args, "1d")
    warmup = args.warmup if args.warmup is not None else DEFAULT_WARMUP["1d"]
    runs = [
        ("BTC détenu", {"BTCUSDT": candle_map["BTCUSDT"]}, EqualWeightHold()),
        ("Panier égal détenu", candle_map, EqualWeightHold()),
        ("S-7 50/50 BTC", {"BTCUSDT": candle_map["BTCUSDT"]}, ConstantMix({"BTCUSDT": Decimal("0.5")})),
        ("S-6 rotation", candle_map, Rotation()),
    ]
    rows = []
    for label, data, strategy in runs:
        result = run_portfolio_backtest(data, strategy, cfg, warmup)
        result.name = label
        rows.append(summarize(result))
    null_line = _null_model(args, candle_map, cfg, warmup, rows[-1]["total_return"]) if args.null_runs else None
    print(f"BACKTEST PORTEFEUILLE ({', '.join(symbols)}), capital {args.cash}, frais {args.fee:.2%}, glissement {args.slippage:.2%}.", file=out)
    print(format_table(rows), file=out)
    if null_line:
        print("\n" + null_line, file=out)
    print("\nBiais du survivant : l'univers est composé de cryptos qui existent AUJOURD'HUI, ce qui flatte la rotation "
          "et le panier (EX-BT-05). " + CATALOG_NOTE, file=out)
    return 0


TEAM_WARMUP = 250  # le régime (moyenne 200 j) est défini dès le premier jour de la fenêtre


def cmd_team_backtest(args, out: TextIO, err: TextIO, factory: Callable) -> int:
    """Équipe complète (S-3, S-4, S-6, S-7) pilotée par l'allocateur et la gouvernance par régime (phase 6)."""
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if "BTCUSDT" not in symbols:
        raise ConfigError("l'univers doit contenir BTCUSDT (référence de régime et de comparaison)")
    aligned = align_candles({s: _load(s, "1d", args.days, args, factory) for s in symbols})
    btc = aligned["BTCUSDT"]
    warmup = args.warmup if args.warmup is not None else TEAM_WARMUP
    if len(btc) < warmup + 60:
        raise ConfigError(f"historique commun trop court ({len(btc)} bougies) pour un préchauffage de {warmup}")
    cfg = _config(args, "1d")

    solo = {
        "S-3": run_backtest(btc, TrendFollowing(), cfg, warmup),
        "S-4": run_backtest(btc, DonchianBreakout(), cfg, warmup),
        "S-6": run_portfolio_backtest(aligned, Rotation(), cfg, warmup),
        "S-7": run_portfolio_backtest({"BTCUSDT": btc}, ConstantMix({"BTCUSDT": Decimal("0.5")}), cfg, warmup),
    }
    regimes = regime_series(btc)[warmup:]
    try:
        teams = team_from_results(solo, regimes, AllocatorConfig(), reserve=0.20)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    refs = [
        ("BTC détenu", run_portfolio_backtest({"BTCUSDT": btc}, EqualWeightHold(), cfg, warmup)),
        ("Panier égal détenu", run_portfolio_backtest(aligned, EqualWeightHold(), cfg, warmup)),
        ("B-4 cash", run_backtest(btc, CashOnly(), cfg, warmup)),
    ]
    rows = []
    for label, result in [*refs, *((f"{c} seul", r) for c, r in solo.items()), *((None, t) for t in teams.values())]:
        if label:
            result.name = label
        rows.append(summarize(result))
    days = len(regimes)
    shares = {r: sum(1 for x in regimes if x is r) / days for r in {x for x in regimes if x is not None}}
    avg = {c: sum(w.get(c, 0.0) for w in teams["v2"].weights) / len(teams["v2"].weights) for c in solo}
    print(f"BACKTEST ÉQUIPE ({', '.join(symbols)}), {days} jours, capital {args.cash}, réserve de trésorerie 20 %, "
          "rééquilibrage hebdomadaire.", file=out)
    print(format_table(rows), file=out)
    print("\nRégimes de BTC sur la période : " + ", ".join(f"{r.value} {v:.0%}" for r, v in sorted(shares.items(), key=lambda kv: -kv[1])), file=out)
    print("Poids moyens de l'équipe v2 (part du capital investissable) : " + ", ".join(f"{c} {w:.0%}" for c, w in avg.items()), file=out)
    print("\nApproximation : les rendements de chaque stratégie sont combinés par l'allocateur ; le coût de réallocation et "
          "la sortie effective des positions d'un stratège écarté ne sont pas modélisés (résultat légèrement optimiste). "
          + CATALOG_NOTE, file=out)
    return 0
