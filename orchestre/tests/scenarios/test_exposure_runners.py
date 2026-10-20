"""Exécuteurs de positions (S-3 à S-7) sur FakeExchange : taille de position, stop de protection, garde-fous."""

from dataclasses import replace
from decimal import Decimal as D

import pytest

from orchestre.backtest.engine import Target
from orchestre.backtest.portfolio_engine import PortfolioTarget
from orchestre.exchange.fake import FakeExchange
from orchestre.exchange.filters import SymbolFilters
from orchestre.execution.service import ExecutionService
from orchestre.portfolio.ledger import Ledger
from orchestre.strategies.exposure_runner import ExposureRunner, PortfolioRunner
from tests.unit.analysis_data import make_candles
from tests.unit.test_risk import context

F = SymbolFilters("BTCUSDT", D("0.01"), D("0.00001"), D("0.00001"), D("100"), D("5"))
F_ETH = SymbolFilters("ETHUSDT", D("0.01"), D("0.0001"), D("0.0001"), D("1000"), D("5"))
BUDGET = D("200")


class Scripted:
    def __init__(self, targets):
        self.name, self.params_tried, self._t, self.calls = "scripted", 1, list(targets), []
        self.saved = {}

    def target(self, history, position):
        self.calls.append((len(history), position))
        return self._t.pop(0) if self._t else Target(None)

    def state(self):
        return dict(self.saved, calls=len(self.calls))

    def load_state(self, state):
        self.saved = {"restored": state["calls"]}


class ScriptedPortfolio(Scripted):
    def target(self, histories, positions, equity):
        self.calls.append((len(next(iter(histories.values()))), dict(positions), equity))
        return self._t.pop(0) if self._t else PortfolioTarget(None)


def world(n_candles=300, price="60000", symbols=("BTCUSDT",)):
    filters = {"BTCUSDT": F, "ETHUSDT": F_ETH}
    prices = {"BTCUSDT": D(price), "ETHUSDT": D("3000")}
    ex = FakeExchange({s: filters[s] for s in symbols}, {s: prices[s] for s in symbols}, {"USDT": D("2000")})
    for s in symbols:
        ex.set_candles(s, "1d", make_candles([float(prices[s])] * n_candles))
    ledger = Ledger(D("500"))
    ledger.allocate("trend", BUDGET)
    svc = ExecutionService(ex, ledger, dry_run=False)

    def ctx(proposal):
        return replace(
            context(), agent_budget_available=ledger.account("trend").available,
            balance_quote=ex.get_balances()["USDT"], max_price_deviation_pct=D("0.25"),
            max_open_orders_agent=50, max_orders_per_day=1000,
        )

    return ex, ledger, svc, ctx


def exposure_runner(strategy, **kw):
    ex, ledger, svc, ctx = world()
    runner = ExposureRunner(
        strategy, "BTCUSDT", "1d", svc, ctx, ex, "trend", "BTC",
        budget_fn=lambda: ledger.account("trend").allocated, **kw,
    )
    return ex, ledger, svc, runner


def advance(ex, runner, now_minutes, price=None, extra_candle=False):
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 10, 20, tzinfo=timezone.utc) + timedelta(minutes=now_minutes)
    if price is not None:
        ex.set_price("BTCUSDT", D(str(price)))
    if extra_candle:
        c = ex.get_candles("BTCUSDT", "1d", 1000)
        ex.set_candles("BTCUSDT", "1d", make_candles([float(x.close) for x in c] + [float(c[-1].close)]))
    runner.step(now, ex.get_price("BTCUSDT"))


def open_ids(ex):
    return sorted(o.client_order_id for o in ex.get_open_orders("BTCUSDT"))


def test_entry_buys_the_target_exposure_and_places_the_protective_stop():
    ex, ledger, svc, runner = exposure_runner(Scripted([Target(D("0.5"), D("57000"))]))
    advance(ex, runner, 0)
    assert runner.leg.qty == D("100") / D("60000") // D("0.00001") * D("0.00001")      # 50 % de 200 USDT
    stops = ex.get_open_orders("BTCUSDT")
    assert len(stops) == 1 and stops[0].side.value == "SELL" and stops[0].orig_qty == runner.leg.qty
    assert ledger.account("trend").committed > 0 and svc.export_tracked()[0]["protective"] is True


def test_each_closed_candle_is_processed_once():
    strategy = Scripted([Target(D("0.5"), D("57000"))])
    ex, ledger, svc, runner = exposure_runner(strategy)
    for minutes in (0, 20, 40, 60):
        advance(ex, runner, minutes)
    assert len(strategy.calls) == 1
    advance(ex, runner, 80, extra_candle=True)
    assert len(strategy.calls) == 2


def test_a_higher_trailing_stop_replaces_the_old_one_keeping_a_single_stop():
    ex, ledger, svc, runner = exposure_runner(Scripted([Target(D("0.5"), D("57000")), Target(D("0.5"), D("58000"))]))
    advance(ex, runner, 0)
    first = open_ids(ex)
    advance(ex, runner, 20, extra_candle=True)
    second = open_ids(ex)
    assert len(first) == len(second) == 1 and first != second
    assert ex.get_open_orders("BTCUSDT")[0].avg_price == D("57710.00")        # limite = stop 58000 - 0,5 %


def test_exit_cancels_the_stop_and_sells_everything():
    ex, ledger, svc, runner = exposure_runner(Scripted([Target(D("0.5"), D("57000")), Target(D("0"))]))
    advance(ex, runner, 0)
    advance(ex, runner, 20, extra_candle=True)
    assert runner.leg.qty == 0 and ex.get_open_orders("BTCUSDT") == []
    assert ex.get_balances().get("BTC", D("0")) == 0


def test_a_triggered_stop_closes_the_position_and_raises_an_alert():
    strategy = Scripted([Target(D("0.5"), D("57000"))])
    ex, ledger, svc, runner = exposure_runner(strategy)
    advance(ex, runner, 0)
    advance(ex, runner, 20, price=56900)
    assert runner.leg.qty == 0 and ex.get_open_orders("BTCUSDT") == []
    assert any("stop" in a for a in runner.leg.drain_alerts())


def test_a_gap_below_the_stop_limit_triggers_an_emergency_market_exit():
    ex, ledger, svc, runner = exposure_runner(Scripted([Target(D("0.5"), D("57000"))]))
    advance(ex, runner, 0)
    advance(ex, runner, 20, price=50000)       # sous la limite 56715 : le stop est déclenché mais ne s'exécute pas
    assert runner.leg.qty == 0 and ex.get_open_orders("BTCUSDT") == []
    assert any("urgence" in a for a in runner.leg.drain_alerts())


def test_a_stop_that_cannot_be_placed_forces_an_exit_rather_than_an_unprotected_position():
    ex, ledger, svc, runner = exposure_runner(Scripted([Target(D("0.5"), D("61000"))]))   # stop au-dessus du prix
    advance(ex, runner, 0)
    assert runner.leg.qty == 0 and ex.get_open_orders("BTCUSDT") == []
    assert any("non protégée" in a or "urgence" in a for a in runner.leg.drain_alerts())


def test_small_deviations_do_not_trade():
    ex, ledger, svc, runner = exposure_runner(Scripted([Target(D("0.5"), D("57000")), Target(D("0.505"), D("57000"))]))
    advance(ex, runner, 0)
    sent = svc.orders_sent
    advance(ex, runner, 20, extra_candle=True)
    assert svc.orders_sent == sent


def test_a_risk_rejection_sends_nothing_and_is_recorded():
    ex, ledger, svc, runner = exposure_runner(Scripted([Target(D("1"), D("57000"))]))
    ledger.commit("trend", D("195"))                # budget disponible quasi nul
    advance(ex, runner, 0)
    assert runner.leg.qty == 0 and runner.leg.rejections and runner.leg.rejections[0].rule_id == "R-03"
    assert ex.get_open_orders("BTCUSDT") == []


def test_unhealthy_candles_block_decisions():
    strategy = Scripted([Target(D("0.5"), D("57000"))])
    ex, ledger, svc, runner = exposure_runner(strategy)
    candles = ex.get_candles("BTCUSDT", "1d", 1000)
    del candles[100]
    ex.set_candles("BTCUSDT", "1d", candles)
    advance(ex, runner, 0)
    assert strategy.calls == [] and any("incohérentes" in a for a in runner.drain_alerts())


def test_state_roundtrip_restores_the_position_the_stop_and_the_strategy_state():
    strategy = Scripted([Target(D("0.5"), D("57000"))])
    ex, ledger, svc, runner = exposure_runner(strategy)
    advance(ex, runner, 0)
    state = runner.export_state()

    ex2, ledger2, svc2, _ = world()
    strategy2 = Scripted([])
    runner2 = ExposureRunner(strategy2, "BTCUSDT", "1d", svc, runner._context_for, ex, "trend", "BTC",
                             budget_fn=lambda: ledger.account("trend").allocated)
    runner2.import_state(state)
    assert runner2.leg.qty == runner.leg.qty and runner2.leg.stop_order_id == runner.leg.stop_order_id
    assert strategy2.saved == {"restored": 1}
    advance(ex, runner2, 20)                      # même bougie : rien à refaire, aucun ordre en double
    assert len(open_ids(ex)) == 1 and strategy2.calls == []


# --- portefeuille ---

def portfolio_runner(strategy, **kw):
    ex, ledger, svc, ctx = world(symbols=("BTCUSDT", "ETHUSDT"))
    runner = PortfolioRunner(
        strategy, ["BTCUSDT", "ETHUSDT"], "1d", svc, ctx, ex, "trend", {"BTCUSDT": "BTC", "ETHUSDT": "ETH"},
        budget_fn=lambda: ledger.account("trend").allocated, **kw,
    )
    return ex, ledger, svc, runner


def step_all(ex, runner, minutes):
    from datetime import datetime, timedelta, timezone
    runner.step(datetime(2026, 10, 20, tzinfo=timezone.utc) + timedelta(minutes=minutes),
                {s: ex.get_price(s) for s in ("BTCUSDT", "ETHUSDT")})


def test_portfolio_buys_each_weight_and_protects_every_position():
    ex, ledger, svc, runner = portfolio_runner(ScriptedPortfolio([PortfolioTarget({"BTCUSDT": D("0.5"), "ETHUSDT": D("0.5")})]))
    step_all(ex, runner, 0)
    assert runner.legs["BTCUSDT"].qty > 0 and runner.legs["ETHUSDT"].qty > 0
    stops = ex.get_open_orders("BTCUSDT") + ex.get_open_orders("ETHUSDT")
    assert len(stops) == 2 and all(o.side.value == "SELL" for o in stops)
    assert stops[0].avg_price < D("60000") * D("0.8")          # protection par défaut ~25 % sous le prix d'entrée (non backtestée)


def test_portfolio_exits_symbols_missing_from_the_new_weights():
    targets = [PortfolioTarget({"BTCUSDT": D("0.5"), "ETHUSDT": D("0.5")}), PortfolioTarget({"BTCUSDT": D("0.5")})]
    ex, ledger, svc, runner = portfolio_runner(ScriptedPortfolio(targets))
    step_all(ex, runner, 0)
    for s in ("BTCUSDT", "ETHUSDT"):
        c = ex.get_candles(s, "1d", 1000)
        ex.set_candles(s, "1d", make_candles([float(x.close) for x in c] + [float(c[-1].close)]))
    step_all(ex, runner, 30)
    assert runner.legs["ETHUSDT"].qty == 0 and runner.legs["BTCUSDT"].qty > 0
    assert ex.get_open_orders("ETHUSDT") == []


def test_portfolio_equity_accounts_for_what_was_spent_and_held():
    strategy = ScriptedPortfolio([PortfolioTarget({"BTCUSDT": D("1")})])
    ex, ledger, svc, runner = portfolio_runner(strategy)
    step_all(ex, runner, 0)
    c = ex.get_candles("BTCUSDT", "1d", 1000)
    ex.set_candles("BTCUSDT", "1d", make_candles([float(x.close) for x in c] + [float(c[-1].close)]))
    step_all(ex, runner, 30)
    equity = strategy.calls[-1][2]
    assert D("195") < equity < D("201")          # ~budget (frais payés) : rien n'a bougé en prix


def test_the_protective_stop_is_a_ratchet_it_is_never_lowered_while_the_position_is_open():
    ex, ledger, svc, runner = exposure_runner(Scripted([Target(D("0.5"), D("57000")), Target(D("0.5"), D("40000"))]))
    advance(ex, runner, 0)
    first = ex.get_open_orders("BTCUSDT")[0]
    advance(ex, runner, 20, extra_candle=True)                                   # la stratégie propose 40 000 : refusé
    stops = ex.get_open_orders("BTCUSDT")
    assert len(stops) == 1 and stops[0].client_order_id == first.client_order_id and runner.leg.wanted_stop == D("57000")


def test_a_fresh_position_after_a_full_exit_takes_the_new_stop():
    ex, ledger, svc, runner = exposure_runner(Scripted([Target(D("0.5"), D("57000")), Target(D("0")), Target(D("0.5"), D("40000"))]))
    advance(ex, runner, 0)
    advance(ex, runner, 20, extra_candle=True)
    assert runner.leg.qty == 0 and runner.leg.wanted_stop is None
    advance(ex, runner, 40, extra_candle=True)
    assert runner.leg.qty > 0 and runner.leg.wanted_stop == D("40000")
