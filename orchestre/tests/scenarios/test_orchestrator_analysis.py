"""Orchestrateur + agents d'analyse : régime -> budgets, liquidité -> risque, sentiment -> DCA."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from orchestre.analysis.regimes import Regime
from orchestre.analysis.service import AnalysisService
from orchestre.config import parse_settings
from orchestre.core.agent import AgentState
from orchestre.core.clock import SimulatedClock
from orchestre.exchange.base import IpBannedError, OrderBook
from orchestre.exchange.fake import FakeExchange
from orchestre.exchange.filters import SymbolFilters
from orchestre.persistence.db import Database
from orchestre.persistence.store import Store
from orchestre.portfolio.ledger import Ledger
from orchestre.runtime.orchestrator import Orchestrator, RuntimeLimits
from orchestre.strategies.dca import DcaConfig
from orchestre.strategies.grid import GridConfig
from tests.unit.analysis_data import downtrend, make_candles, uptrend, zigzag_range

F = SymbolFilters("BTCUSDT", D("0.01"), D("0.00001"), D("0.00001"), D("100"), D("5"))
T0 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
SETTINGS = parse_settings({"max_total_capital_quote": "500", "dry_run": False})
GRID = GridConfig("grid", "BTCUSDT", D("55000"), D("65000"), 20, D("200"), D("0.001"), D("5"), D("0.01"), D("0.00001"))
DCA = DcaConfig("dca", "BTCUSDT", D("20"), timedelta(days=7), T0, D("100"), D("5"))
DCA_MODULATED = DcaConfig("dca", "BTCUSDT", D("20"), timedelta(days=7), T0, D("100"), D("5"), modulated=True)


def team(tmp_path, candles, fetch_fng=None, dca=DCA):
    ex = FakeExchange({"BTCUSDT": F}, {"BTCUSDT": D("60000")}, {"USDT": D("2000")})
    if candles is not None:
        ex.set_candles("BTCUSDT", "1d", candles)
    clock = SimulatedClock(T0)
    store = Store(Database(":memory:"))
    analysis = AnalysisService(ex, clock, store, fetch_fng=fetch_fng)
    orch = Orchestrator(
        SETTINGS, ex, Ledger(D("500")), store, clock, tmp_path / "STOP",
        limits=RuntimeLimits(reconcile_interval=timedelta(0)), analysis=analysis,
    )
    orch.add_grid(GRID)
    orch.add_dca(dca)
    return orch, ex, clock


def tick(orch, clock):
    clock.advance(timedelta(minutes=1))
    return orch.run_once()


def test_range_regime_gives_full_budgets_and_trading_starts(tmp_path):
    orch, ex, clock = team(tmp_path, zigzag_range())
    assert tick(orch, clock) == "OK"
    assert orch.ledger.account("grid").allocated == D("200")
    assert orch.ledger.account("dca").allocated == D("100")
    assert len(ex.get_open_orders("BTCUSDT")) > 5
    assert any("RANGE" in text for _, text in orch.alerts.history)


def test_bear_regime_zeroes_the_grid_budget_but_not_the_dca(tmp_path):
    orch, ex, clock = team(tmp_path, downtrend())
    tick(orch, clock)
    assert orch.ledger.account("grid").allocated == D("0")
    assert ex.get_open_orders("BTCUSDT") == []
    assert any(d.rule_id == "R-03" for d in orch.units["grid"].runner.rejections)
    assert orch.ledger.account("dca").allocated == D("100") and ex.get_balances()["BTC"] > 0


def test_regime_change_reduces_budget_down_to_committed_and_restores_it(tmp_path):
    orch, ex, clock = team(tmp_path, zigzag_range())
    tick(orch, clock)
    committed = orch.ledger.account("grid").committed
    assert committed > 0
    orch._apply_regime(Regime.TENDANCE_BAISSE)
    assert orch.ledger.account("grid").allocated == committed  # jamais sous le montant engagé
    orch._apply_regime(Regime.RANGE)
    assert orch.ledger.account("grid").allocated == D("200")


def test_budgets_after_the_5_percent_tier_are_never_raised_back_by_the_regime(tmp_path):
    orch, ex, clock = team(tmp_path, zigzag_range())
    tick(orch, clock)
    orch.state.peak_equity = D("535")
    tick(orch, clock)
    reduced = orch.ledger.account("grid").allocated
    orch._apply_regime(Regime.RANGE)
    assert orch.ledger.account("grid").allocated <= reduced


def test_uptrend_puts_the_grid_in_paper_because_budget_is_below_minimum_viable(tmp_path):
    orch, ex, clock = team(tmp_path, uptrend())
    tick(orch, clock)
    assert orch.ledger.account("grid").allocated == D("0")  # 0,5 x 200 = 100 < 120 (EX-AL-03)
    assert ex.get_open_orders("BTCUSDT") == []


def test_illiquid_pair_blocks_new_orders_with_rule_r08(tmp_path):
    orch, ex, clock = team(tmp_path, zigzag_range())
    ex.set_order_book("BTCUSDT", OrderBook([(D("59000"), D("5"))], [(D("61000"), D("5"))]))
    tick(orch, clock)
    assert any(d.rule_id == "R-08" for d in orch.units["dca"].runner.rejections)
    assert "BTC" not in ex.get_balances() and ex.get_open_orders("BTCUSDT") == []


def test_fear_and_greed_modulates_the_dca_amount(tmp_path):
    fng = lambda: {"data": [{"value": "20", "value_classification": "Extreme Fear", "timestamp": "1700000000"}]}
    orch, ex, clock = team(tmp_path, zigzag_range(), fetch_fng=fng, dca=DCA_MODULATED)
    tick(orch, clock)
    assert ex.get_balances()["BTC"] == D("0.0005")  # 30 USDT : x1,5 en peur extrême


def test_without_sentiment_the_dca_uses_its_plain_amount(tmp_path):
    orch, ex, clock = team(tmp_path, zigzag_range(), dca=DCA_MODULATED)
    tick(orch, clock)
    assert ex.get_balances()["BTC"] == D("0.00033")


def test_ip_ban_during_analysis_pauses_the_team(tmp_path):
    orch, ex, clock = team(tmp_path, zigzag_range())
    ex.get_candles = lambda *a, **k: (_ for _ in ()).throw(IpBannedError("418"))
    assert tick(orch, clock) == "PAUSED"
    assert all(a.state is AgentState.PAUSED for a in orch.unit_agents.values())


def test_analysis_crash_does_not_stop_trading(tmp_path):
    orch, ex, clock = team(tmp_path, zigzag_range())
    orch.analysis.refresh = lambda symbol: (_ for _ in ()).throw(RuntimeError("bug d'analyse"))
    assert tick(orch, clock) == "OK"
    assert any("analyse" in text for _, text in orch.alerts.history)
    assert orch.ledger.account("grid").allocated == D("200")  # budgets de base conservés


def test_unknown_regime_keeps_base_budgets(tmp_path):
    orch, ex, clock = team(tmp_path, None)  # aucune bougie disponible
    tick(orch, clock)
    assert orch.ledger.account("grid").allocated == D("200")


def test_unhealthy_data_does_not_change_budgets_and_raises_one_alert(tmp_path):
    candles = zigzag_range()
    del candles[300]
    orch, ex, clock = team(tmp_path, candles)
    tick(orch, clock)
    tick(orch, clock)
    assert orch.ledger.account("grid").allocated == D("200")
    assert sum("trou" in text for _, text in orch.alerts.history) == 1  # pas de répétition à chaque tick
