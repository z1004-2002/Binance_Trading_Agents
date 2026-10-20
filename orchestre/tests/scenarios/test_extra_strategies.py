"""S-9a observateur d'arbitrage, S-9b tenue de marché (simulation), S-8 adaptateur d'agents existants."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

import pytest

from orchestre.config import ConfigError
from orchestre.exchange.base import OrderBook
from orchestre.exchange.fake import FakeExchange
from orchestre.exchange.filters import Side, SymbolFilters
from orchestre.execution.service import ExecutionService
from orchestre.portfolio.ledger import Ledger
from orchestre.strategies.legacy import LegacyRunner, load_legacy_agent
from orchestre.strategies.marketmaker import MarketMakerRunner
from orchestre.strategies.triangular import Triangle, TriangleObserver
from tests.unit.test_risk import context

F = SymbolFilters("BTCUSDT", D("0.01"), D("0.00001"), D("0.00001"), D("100"), D("5"))
F_ETH = SymbolFilters("ETHUSDT", D("0.01"), D("0.0001"), D("0.0001"), D("1000"), D("5"))
F_X = SymbolFilters("ETHBTC", D("0.00001"), D("0.0001"), D("0.0001"), D("1000"), D("0.0001"))
T0 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def book(bid, ask, qty="100"):
    return OrderBook([(D(bid), D(qty))], [(D(ask), D(qty))])


# ---------- S-9a observateur ----------

def observer_world(ethbtc=("0.04998", "0.05002")):
    ex = FakeExchange({"BTCUSDT": F, "ETHUSDT": F_ETH, "ETHBTC": F_X}, {"BTCUSDT": D("60000"), "ETHUSDT": D("3000"), "ETHBTC": D("0.05")}, {})
    ex.set_order_book("BTCUSDT", book("59990", "60010"))
    ex.set_order_book("ETHUSDT", book("2999", "3001"))
    ex.set_order_book("ETHBTC", book(*ethbtc))
    return ex, TriangleObserver(Triangle("BTC", "ETH", "USDT"), ex, D("0.001"), D("100"), threshold=D("0.0005"), scan_every=timedelta(seconds=60))


def test_the_observer_measures_edges_without_ever_sending_an_order():
    ex, obs = observer_world()
    ex.place_order = lambda *a, **k: (_ for _ in ()).throw(AssertionError("l'observateur ne doit jamais passer d'ordre"))
    for i in range(3):
        obs.step(T0 + timedelta(seconds=61 * i))
    assert obs.stats["scans"] == 3 and obs.stats["opportunities"] == 0 and obs.stats["best_edge"] < 0
    assert obs.drain_alerts() == []


def test_scans_are_throttled_by_the_clock():
    ex, obs = observer_world()
    obs.step(T0)
    obs.step(T0 + timedelta(seconds=10))
    assert obs.stats["scans"] == 1


def test_a_mispricing_is_counted_and_reported_as_information():
    ex, obs = observer_world(ethbtc=("0.0480", "0.0482"))
    obs.step(T0)
    assert obs.stats["opportunities"] == 1 and obs.stats["best_edge"] > 0.02
    alerts = obs.drain_alerts()
    assert len(alerts) == 1 and alerts[0].startswith("info : ") and "USDT>BTC>ETH>USDT" in alerts[0]


def test_book_failures_are_counted_not_raised():
    ex, obs = observer_world()
    ex.get_order_book = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("hors ligne"))
    obs.step(T0)
    assert obs.stats["errors"] == 1 and obs.stats["scans"] == 0


def test_observer_state_roundtrip():
    ex, obs = observer_world(ethbtc=("0.0480", "0.0482"))
    obs.step(T0)
    ex2, fresh = observer_world()
    fresh.import_state(obs.export_state())
    assert fresh.stats == obs.stats


# ---------- S-9b tenue de marché ----------

def mm_world(**kw):
    ex = FakeExchange({"BTCUSDT": F}, {"BTCUSDT": D("60000")}, {"USDT": D("2000")})
    ledger = Ledger(D("500"))
    ledger.allocate("mm", D("200"))
    svc = ExecutionService(ex, ledger, dry_run=False)
    ctx = lambda p: replace(context(), agent_budget_available=ledger.account("mm").available, balance_quote=ex.get_balances()["USDT"],  # noqa: E731
                            max_open_orders_agent=50, max_orders_per_day=1000, max_order_notional=D("150"))
    runner = MarketMakerRunner("BTCUSDT", "BTC", svc, ctx, ex, "mm", lambda: ledger.account("mm").allocated, **kw)
    return ex, ledger, svc, runner


def open_by_side(ex):
    return {o.side.value: o for o in ex.get_open_orders("BTCUSDT")}


def test_without_inventory_only_a_bid_is_quoted_since_spot_cannot_short():
    ex, ledger, svc, mm = mm_world()
    mm.step(T0, D("60000"))
    orders = open_by_side(ex)
    assert set(orders) == {"BUY"} and orders["BUY"].avg_price == D("59820.00")           # 60 000 x (1 - 0,3 %)


def test_a_filled_bid_creates_inventory_and_an_ask_above_the_mid():
    ex, ledger, svc, mm = mm_world()
    mm.step(T0, D("60000"))
    ex.set_price("BTCUSDT", D("59800"))                                                    # le prix traverse le bid
    mm.step(T0 + timedelta(seconds=30), D("59800"))
    assert mm.qty > 0
    orders = open_by_side(ex)
    assert orders["SELL"].avg_price == D("59979.40") and orders["SELL"].orig_qty == mm.qty


def test_a_round_trip_earns_the_spread_minus_fees():
    ex, ledger, svc, mm = mm_world()
    mm.step(T0, D("60000"))
    ex.set_price("BTCUSDT", D("59800"))
    mm.step(T0 + timedelta(seconds=30), D("59800"))
    ex.set_price("BTCUSDT", D("60100"))                                                    # traverse l'ask
    mm.step(T0 + timedelta(seconds=60), D("60100"))
    assert mm.qty == 0 and -mm.net_spent > 0            # profit net positif : écart capté (0,3 % x 2) - frais (0,2 %)


def test_quotes_are_replaced_when_the_mid_moves_enough_and_kept_otherwise():
    ex, ledger, svc, mm = mm_world(requote_pct=D("0.002"))
    mm.step(T0, D("60000"))
    first = open_by_side(ex)["BUY"].client_order_id
    ex.set_price("BTCUSDT", D("60030"))                                                    # +0,05 % : on garde
    mm.step(T0 + timedelta(seconds=30), D("60030"))
    assert open_by_side(ex)["BUY"].client_order_id == first
    ex.set_price("BTCUSDT", D("60400"))                                                    # +0,67 % : on remplace
    mm.step(T0 + timedelta(seconds=60), D("60400"))
    new = open_by_side(ex)["BUY"]
    assert new.client_order_id != first and new.avg_price == D("60218.80")
    assert len(ex.get_open_orders("BTCUSDT")) == 1


def test_inventory_is_capped_so_no_more_bids_are_posted():
    ex, ledger, svc, mm = mm_world(max_inventory_pct=D("0.2"), quote_size_pct=D("0.4"))
    mm.step(T0, D("60000"))
    ex.set_price("BTCUSDT", D("59800"))
    mm.step(T0 + timedelta(seconds=30), D("59800"))
    assert "BUY" not in open_by_side(ex) and "SELL" in open_by_side(ex)               # stock plein : on ne fait que vendre


def test_a_suspended_market_maker_cancels_its_quotes_and_stays_flat():
    ex, ledger, svc, mm = mm_world()
    mm.step(T0, D("60000"))
    mm.suspended = True
    mm.step(T0 + timedelta(seconds=30), D("60000"))
    assert ex.get_open_orders("BTCUSDT") == []
    mm.step(T0 + timedelta(seconds=60), D("60000"))
    assert ex.get_open_orders("BTCUSDT") == []


def test_market_maker_state_survives_a_restart_without_duplicate_quotes():
    ex, ledger, svc, mm = mm_world()
    mm.step(T0, D("60000"))
    state = mm.export_state()
    fresh = MarketMakerRunner("BTCUSDT", "BTC", svc, mm._context_for, ex, "mm", lambda: ledger.account("mm").allocated)
    fresh.import_state(state)
    fresh.step(T0 + timedelta(seconds=30), D("60000"))
    assert len(ex.get_open_orders("BTCUSDT")) == 1


# ---------- S-8 agents existants ----------

LEGACY_OK = '''
class OldAgent:
    def decide(self, snapshot):
        if snapshot["prices"]["BTCUSDT"] < 61000 and not snapshot["positions"].get("BTCUSDT"):
            return [{"symbol": "BTCUSDT", "side": "BUY", "quantity": "0.001", "reason": "ancien signal d'achat"}]
        if snapshot["positions"].get("BTCUSDT"):
            return [{"symbol": "BTCUSDT", "side": "SELL", "quantity": "0.001", "reason": "ancien signal de vente"}]
        return []
'''


def legacy_file(tmp_path, source=LEGACY_OK, name="old_agent.py"):
    d = tmp_path / "legacy"
    d.mkdir(exist_ok=True)
    (d / name).write_text(source)
    return d / name


def legacy_world(tmp_path, source=LEGACY_OK):
    ex = FakeExchange({"BTCUSDT": F}, {"BTCUSDT": D("60000")}, {"USDT": D("2000")})
    ledger = Ledger(D("500"))
    ledger.allocate("legacy", D("200"))
    svc = ExecutionService(ex, ledger, dry_run=False)
    ctx = lambda p: replace(context(), agent_budget_available=ledger.account("legacy").available, balance_quote=ex.get_balances()["USDT"],  # noqa: E731
                            max_open_orders_agent=50, max_orders_per_day=1000, max_order_notional=D("150"))
    agent = load_legacy_agent(legacy_file(tmp_path, source), "OldAgent")
    runner = LegacyRunner(agent, ["BTCUSDT"], svc, ctx, ex, "legacy", {"BTCUSDT": "BTC"}, lambda: ledger.account("legacy").allocated)
    return ex, ledger, svc, runner


def test_a_safe_legacy_agent_is_loaded_and_its_intents_go_through_risk_and_execution(tmp_path):
    ex, ledger, svc, runner = legacy_world(tmp_path)
    runner.step(T0, {"BTCUSDT": D("60000")})
    assert runner.positions["BTCUSDT"] == D("0.001") and ledger.account("legacy").committed > 0
    runner.step(T0 + timedelta(minutes=1), {"BTCUSDT": D("60000")})
    assert runner.positions["BTCUSDT"] == 0


def test_intents_refused_by_risk_are_recorded_not_forced(tmp_path):
    ex, ledger, svc, runner = legacy_world(tmp_path)
    ledger.commit("legacy", D("199.9"))                                                    # budget épuisé
    runner.step(T0, {"BTCUSDT": D("60000")})
    assert runner.positions["BTCUSDT"] == 0 and runner.rejections and runner.rejections[0].rule_id == "R-03"


def test_malformed_or_unauthorised_intents_are_dropped_with_an_alert(tmp_path):
    bad = '''
class OldAgent:
    def decide(self, snapshot):
        return [{"symbol": "DOGEUSDT", "side": "BUY", "quantity": "1"}, {"symbol": "BTCUSDT", "side": "HODL", "quantity": "1"},
                {"symbol": "BTCUSDT", "side": "BUY", "quantity": "-1"}, "n'importe quoi", {"symbol": "BTCUSDT", "side": "SELL", "quantity": "5"}]
'''
    ex, ledger, svc, runner = legacy_world(tmp_path, bad)
    runner.step(T0, {"BTCUSDT": D("60000")})
    assert svc.orders_sent == 0 and len(runner.drain_alerts()) >= 4                        # même une vente de ce qu'il ne détient pas


def test_a_crashing_legacy_agent_is_contained(tmp_path):
    ex, ledger, svc, runner = legacy_world(tmp_path, "class OldAgent:\n    def decide(self, s):\n        raise RuntimeError('bug ancien')\n")
    runner.step(T0, {"BTCUSDT": D("60000")})
    assert any("bug ancien" in a for a in runner.drain_alerts()) and svc.orders_sent == 0


def test_the_loader_refuses_code_with_plain_keys_withdrawals_or_direct_orders(tmp_path):
    secret = "ABCDEFGHIJKLMNOPQRSTUVWX1234567890"
    for source, expected in (
        (f'api_key = "{secret}"\nclass OldAgent:\n    def decide(self, s):\n        return []\n', "clé en clair"),
        ('class OldAgent:\n    def decide(self, s):\n        client.order_market_buy(symbol="X", quantity=1)\n        return []\n', "ordre direct"),
        ('class OldAgent:\n    def decide(self, s):\n        client.withdraw(asset="USDT")\n        return []\n', "retrait"),
    ):
        path = legacy_file(tmp_path, source, name=f"a{abs(hash(source))}.py")
        with pytest.raises(ConfigError, match=expected) as exc:
            load_legacy_agent(path, "OldAgent")
        assert secret not in str(exc.value)


def test_the_loader_reports_a_missing_file_class_or_decide_method(tmp_path):
    with pytest.raises(ConfigError, match="introuvable"):
        load_legacy_agent(tmp_path / "absent.py", "OldAgent")
    with pytest.raises(ConfigError, match="classe"):
        load_legacy_agent(legacy_file(tmp_path), "Inexistante")
    with pytest.raises(ConfigError, match="decide"):
        load_legacy_agent(legacy_file(tmp_path, "class OldAgent:\n    pass\n", name="nodecide.py"), "OldAgent")
