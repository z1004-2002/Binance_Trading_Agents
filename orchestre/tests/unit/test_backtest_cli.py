import io
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from orchestre.backtest.data import load_history
from orchestre.backtest.engine import BacktestConfig, run_backtest
from orchestre.backtest.references import BuyAndHold
from orchestre.backtest.report import format_table, summarize
from orchestre.cli.main import main
from orchestre.exchange.binance import BinanceExchange
from tests.unit.test_binance_adapter import StubClient

NOW = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
DAY_MS = 86_400_000


class History(StubClient):
    """Historique synthétique de `n` jours se terminant hier ; honore `limit` et `endTime` (pagination)."""

    def __init__(self, n=1000, step_ms=DAY_MS):
        super().__init__()
        self.n, self.step_ms = n, step_ms
        self.klines_calls = 0

    def publicGetKlines(self, params):
        self.klines_calls += 1
        step = self.step_ms
        last_open = int(NOW.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000) - step
        index = list(range(self.n))
        if "endTime" in params:
            index = [i for i in index if last_open - (self.n - 1 - i) * step <= params["endTime"]]
        index = index[-int(params["limit"]):]
        rows = []
        for i in index:
            open_ms = last_open - (self.n - 1 - i) * step
            price = 100.0 * (1.003 ** ((i + 1) // 2)) * (1.001 ** (i // 2))
            rows.append([open_ms, f"{price:.2f}", f"{price * 1.01:.2f}", f"{price * 0.99:.2f}", f"{price:.2f}",
                         "10", open_ms + step - 1, "0", 1, "0", "0", "0"])
        return rows


def exchange(n=1000):
    stub = History(n)
    return stub, BinanceExchange(stub, now_ms=lambda: int(NOW.timestamp() * 1000))


def test_history_is_fetched_validated_and_cached(tmp_path):
    stub, ex = exchange()
    first = load_history(ex, "BTCUSDT", "1d", 400, tmp_path, NOW)
    assert first.healthy and len(first.accepted) == 400 and stub.klines_calls == 1
    again = load_history(ex, "BTCUSDT", "1d", 400, tmp_path, NOW)
    assert stub.klines_calls == 1 and [c.close for c in again.accepted] == [c.close for c in first.accepted]


def test_stale_or_too_short_cache_is_refreshed(tmp_path):
    stub, ex = exchange()
    load_history(ex, "BTCUSDT", "1d", 200, tmp_path, NOW)
    load_history(ex, "BTCUSDT", "1d", 400, tmp_path, NOW)           # cache trop court
    assert stub.klines_calls == 2
    load_history(ex, "BTCUSDT", "1d", 400, tmp_path, NOW + timedelta(days=5))  # cache périmé
    assert stub.klines_calls == 3


def test_history_is_paginated_backwards_beyond_the_api_limit(tmp_path):
    stub, ex = exchange(2500)
    r = load_history(ex, "BTCUSDT", "1d", 2200, tmp_path, NOW)
    assert len(r.accepted) == 2200 and stub.klines_calls == 3 and r.healthy
    assert [c.open_time for c in r.accepted] == sorted(c.open_time for c in r.accepted)  # pas de doublon ni de trou


def test_pagination_stops_when_the_history_is_exhausted(tmp_path):
    stub, ex = exchange(1300)
    r = load_history(ex, "BTCUSDT", "1d", 5000, tmp_path, NOW)
    assert len(r.accepted) == 1300 and r.healthy


def test_four_hour_history_uses_the_interval_for_gaps_and_freshness(tmp_path):
    stub = History(2400, step_ms=4 * 3600 * 1000)
    ex = BinanceExchange(stub, now_ms=lambda: int(NOW.timestamp() * 1000))
    r = load_history(ex, "BTCUSDT", "4h", 1500, tmp_path, NOW)
    assert r.healthy and len(r.accepted) <= 1500 and len(r.accepted) >= 1499


def test_summary_and_table(tmp_path):
    _, ex = exchange(300)
    accepted = load_history(ex, "BTCUSDT", "1d", 300, tmp_path, NOW).accepted
    result = run_backtest(accepted, BuyAndHold(), BacktestConfig(), warmup=50)
    s = summarize(result)
    assert s["name"] == result.name and s["total_return"] > 0 and s["max_drawdown"] >= 0
    table = format_table([s])
    assert "B-1 détention" in table and "Rendement" in table and "Drawdown" in table


def test_table_renders_infinite_ratios(tmp_path):
    result = run_backtest(
        load_history(exchange(300)[1], "BTCUSDT", "1d", 300, tmp_path, NOW).accepted,
        BuyAndHold(), BacktestConfig(fee_rate=D("0"), slippage=D("0")), warmup=50,
    )
    s = summarize(result)
    assert math.isinf(s["sortino"]) and "∞" in format_table([s])


def run_cli(argv, stub):
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, environ={}, out=out, err=err, public_client_factory=lambda: stub)
    return code, out.getvalue(), err.getvalue()


def test_backtest_command_compares_all_strategies(tmp_path):
    code, out, err = run_cli(["backtest", "--days", "500", "--warmup", "120", "--data-dir", str(tmp_path)], History())
    assert code == 0, err
    for name in ("B-1 détention", "B-2 DCA hebdomadaire", "B-3 50/50 mensuel", "B-4 cash", "S-3 tendance", "S-4 cassure"):
        assert name in out
    assert "essais" in out.lower() and "courte" in out.lower()  # avertissement statistique (EX-XP-05)


def test_backtest_refuses_unhealthy_data(tmp_path):
    class Gappy(History):
        def publicGetKlines(self, params):
            rows = super().publicGetKlines(params)
            del rows[100]
            return rows

    code, _, err = run_cli(["backtest", "--days", "500", "--warmup", "120", "--data-dir", str(tmp_path)], Gappy())
    assert code == 2 and "trou" in err


def test_backtest_rejects_a_window_too_short_for_the_warmup(tmp_path):
    code, _, err = run_cli(["backtest", "--days", "100", "--warmup", "120", "--data-dir", str(tmp_path)], History())
    assert code == 2 and "préchauffage" in err


# --- walkforward ---

def test_walkforward_command_reports_windows_oos_and_trials(tmp_path):
    code, out, err = run_cli(
        ["walkforward", "--strategy", "trend", "--days", "1000", "--data-dir", str(tmp_path)], History()
    )
    assert code == 0, err
    assert "Fenêtre" in out and "hors échantillon" in out and "B-1 détention" in out
    assert "45" in out and "essais" in out.lower()  # 9 combinaisons x 5 fenêtres


def test_walkforward_supports_the_breakout_strategy(tmp_path):
    code, out, err = run_cli(
        ["walkforward", "--strategy", "breakout", "--days", "1000", "--data-dir", str(tmp_path)], History()
    )
    assert code == 0, err
    assert "S-4 cassure" in out


def test_walkforward_needs_enough_history(tmp_path):
    code, _, err = run_cli(["walkforward", "--days", "300", "--data-dir", str(tmp_path)], History())
    assert code == 2 and "insuffisant" in err


def test_walkforward_rejects_unknown_strategy(tmp_path):
    with pytest.raises(SystemExit):
        run_cli(["walkforward", "--strategy", "martingale", "--data-dir", str(tmp_path)], History())


def run_cli_4h(argv, n=6000):
    stub = History(n, step_ms=4 * 3600 * 1000)
    return run_cli(argv, stub)


def test_backtest_in_4h_runs_the_mean_reversion_strategy(tmp_path):
    code, out, err = run_cli_4h(["backtest", "--interval", "4h", "--days", "300", "--data-dir", str(tmp_path)])
    assert code == 0, err
    assert "S-5 retour moyenne" in out and "(4h)" in out and "S-3 tendance" not in out


def test_walkforward_meanreversion_requires_4h(tmp_path):
    code, _, err = run_cli(["walkforward", "--strategy", "meanreversion", "--data-dir", str(tmp_path)], History())
    assert code == 2 and "4h" in err


def test_walkforward_meanreversion_in_4h(tmp_path):
    code, out, err = run_cli_4h(["walkforward", "--strategy", "meanreversion", "--interval", "4h", "--days", "700",
                                 "--data-dir", str(tmp_path)], n=4200)
    assert code == 0, err
    assert "S-5 retour moyenne" in out and "hors échantillon" in out


def test_portfolio_backtest_compares_rotation_and_rebalancing(tmp_path):
    code, out, err = run_cli(["portfolio-backtest", "--symbols", "BTCUSDT,ETHUSDT,SOLUSDT", "--days", "600",
                              "--data-dir", str(tmp_path)], History())
    assert code == 0, err
    for name in ("BTC détenu", "Panier égal détenu", "S-7 50/50 BTC", "S-6 rotation"):
        assert name in out
    assert "survivant" in out.lower()


def test_portfolio_backtest_requires_btc(tmp_path):
    code, _, err = run_cli(["portfolio-backtest", "--symbols", "ETHUSDT,SOLUSDT", "--data-dir", str(tmp_path)], History())
    assert code == 2 and "BTCUSDT" in err


def test_portfolio_backtest_null_model_reports_the_percentile(tmp_path):
    code, out, err = run_cli(["portfolio-backtest", "--symbols", "BTCUSDT,ETHUSDT,SOLUSDT", "--days", "500",
                              "--null-runs", "5", "--data-dir", str(tmp_path)], History())
    assert code == 0, err
    assert "Modèle nul (5 rotations" in out and "bat" in out


def test_team_backtest_compares_references_solo_strategies_and_both_team_variants(tmp_path):
    code, out, err = run_cli(["team-backtest", "--symbols", "BTCUSDT,ETHUSDT,SOLUSDT", "--days", "700",
                              "--data-dir", str(tmp_path)], History())
    assert code == 0, err
    for name in ("BTC détenu", "Panier égal détenu", "S-3 seul", "S-6 seul", "S-7 seul", "Équipe v1", "Équipe v2"):
        assert name in out
    assert "Régimes de BTC" in out and "Poids moyens" in out and "optimiste" in out


def test_team_backtest_needs_btc_and_enough_history(tmp_path):
    code, _, err = run_cli(["team-backtest", "--symbols", "ETHUSDT", "--data-dir", str(tmp_path)], History())
    assert code == 2 and "BTCUSDT" in err
    code, _, err = run_cli(["team-backtest", "--symbols", "BTCUSDT", "--days", "200", "--data-dir", str(tmp_path)], History())
    assert code == 2 and "trop court" in err
