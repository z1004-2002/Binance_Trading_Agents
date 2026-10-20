from decimal import Decimal as D

import pytest

from orchestre.exchange.base import OrderRequest
from orchestre.exchange.fake import FakeExchange
from orchestre.exchange.filters import Side, SymbolFilters
from orchestre.execution.service import ExecutionService
from orchestre.portfolio.ledger import Ledger
from orchestre.risk.engine import review
from tests.unit.test_risk import context, proposal

F = SymbolFilters("BTCUSDT", D("0.01"), D("0.00001"), D("0.00001"), D("100"), D("5"))


def fake():
    return FakeExchange({"BTCUSDT": F}, {"BTCUSDT": D("60000")}, {"USDT": D("1000")})


def limit(side, price, qty="0.001", cid="L1"):
    return OrderRequest("BTCUSDT", side, D(qty), D(price), cid)


# --- FakeExchange : ordres limites ---

def test_maker_order_that_would_execute_immediately_is_rejected():
    ex = fake()
    assert ex.place_order(limit(Side.BUY, "60000.00")).status == "REJECTED"
    assert ex.place_order(limit(Side.BUY, "61000.00", cid="L2")).status == "REJECTED"
    assert ex.place_order(limit(Side.SELL, "59000.00", cid="L3")).status == "REJECTED"


def test_resting_buy_locks_funds_and_stays_open():
    ex = fake()
    r = ex.place_order(limit(Side.BUY, "59000.00"))
    assert r.status == "NEW" and r.filled_qty == D("0")
    assert ex.get_balances()["USDT"] == D("1000") - D("59") - D("0.059")
    assert [o.client_order_id for o in ex.get_open_orders("BTCUSDT")] == ["L1"]


def test_price_crossing_fills_resting_buy_at_limit():
    ex = fake()
    ex.place_order(limit(Side.BUY, "59000.00"))
    fills = ex.set_price("BTCUSDT", D("58900"))
    assert [f.status for f in fills] == ["FILLED"] and fills[0].avg_price == D("59000.00")
    assert ex.get_balances()["BTC"] == D("0.001")
    assert ex.get_open_orders("BTCUSDT") == []


def test_price_not_crossing_leaves_order_open():
    ex = fake()
    ex.place_order(limit(Side.BUY, "59000.00"))
    assert ex.set_price("BTCUSDT", D("59500")) == []
    assert len(ex.get_open_orders("BTCUSDT")) == 1


def test_resting_sell_fills_when_price_rises():
    ex = fake()
    ex.place_order(OrderRequest("BTCUSDT", Side.BUY, D("0.001"), None, "B1"))
    ex.place_order(limit(Side.SELL, "61000.00", cid="S1"))
    assert ex.get_balances()["BTC"] == D("0")
    fills = ex.set_price("BTCUSDT", D("61000"))
    assert fills[0].client_order_id == "S1" and ex.get_balances()["BTC"] == D("0")
    assert ex.get_balances()["USDT"] > D("1000") - D("60.06")


def test_cancel_refunds_locked_funds():
    ex = fake()
    ex.place_order(limit(Side.BUY, "59000.00"))
    assert ex.cancel_order("BTCUSDT", "L1").status == "CANCELED"
    assert ex.get_balances()["USDT"] == D("1000")
    assert ex.get_open_orders("BTCUSDT") == []


def test_cancel_of_filled_order_does_not_revert_it():
    ex = fake()
    ex.place_order(limit(Side.BUY, "59000.00"))
    ex.set_price("BTCUSDT", D("58000"))
    assert ex.cancel_order("BTCUSDT", "L1").status == "FILLED"


# --- ExecutionService : suivi des ordres en attente ---

def make():
    ex = fake()
    lg = Ledger(D("500"))
    lg.allocate("dca", D("200"))
    return ex, lg, ExecutionService(ex, lg, dry_run=False)


def approved_limit(side=Side.BUY, price="59500", **kw):
    d = review(proposal(side=side, limit_price=D(price), **kw), context())
    assert d.approved, d.reason
    return d.order


def test_limit_buy_is_open_and_reserves_budget():
    ex, lg, svc = make()
    r = svc.execute(approved_limit(), "L1")
    assert r.status == "OPEN" and svc.consecutive_errors == 0
    assert lg.account("dca").committed == D("59.5") + D("0.06")
    assert svc.open_order_ids("BTCUSDT") == ["L1"]


def test_sync_settles_fill_with_actual_amounts():
    ex, lg, svc = make()
    svc.execute(approved_limit(), "L1")
    ex.set_price("BTCUSDT", D("59000"))
    reports = svc.sync("BTCUSDT")
    assert [r.status for r in reports] == ["FILLED"]
    assert lg.account("dca").committed == D("59.5") + D("0.0595")
    assert svc.open_order_ids("BTCUSDT") == []


def test_sync_ignores_orders_still_open():
    ex, _, svc = make()
    svc.execute(approved_limit(), "L1")
    assert svc.sync("BTCUSDT") == []
    assert svc.open_order_ids("BTCUSDT") == ["L1"]


def test_cancel_releases_reserved_budget():
    ex, lg, svc = make()
    svc.execute(approved_limit(), "L1")
    r = svc.cancel("BTCUSDT", "L1")
    assert r.status == "CANCELED" and lg.account("dca").committed == D("0")


def test_cancel_all_leaves_zero_orphans():
    ex, lg, svc = make()
    svc.execute(approved_limit(price="59500"), "L1")
    svc.execute(approved_limit(price="59000"), "L2")
    assert len(svc.cancel_all_open("BTCUSDT")) == 2
    assert ex.get_open_orders("BTCUSDT") == [] and lg.account("dca").committed == D("0")


def test_reconcile_detects_orphan_on_exchange():
    ex, _, svc = make()
    svc.execute(approved_limit(), "L1")
    ex.place_order(limit(Side.BUY, "58000.00", cid="X9"))  # hors de l'exécuteur
    assert svc.reconcile("BTCUSDT") == ["ordre orphelin sur la plateforme: X9"]


def test_reconcile_detects_missing_order():
    ex, _, svc = make()
    svc.execute(approved_limit(), "L1")
    ex.cancel_order("BTCUSDT", "L1")  # annulé hors de l'exécuteur
    assert svc.reconcile("BTCUSDT") == ["ordre suivi absent de la plateforme: L1 (statut CANCELED)"]


def test_rejected_maker_counts_as_error():
    ex, _, svc = make()
    d = review(proposal(limit_price=D("60000")), context())
    r = svc.execute(d.order, "L9")
    assert r.status == "FAILURE" and svc.consecutive_errors == 1


def test_sell_proceeds_never_release_the_reservation_of_a_pending_buy_order():
    """Défaut trouvé par la tenue de marché : le produit d'une vente libérait le montant réservé par un achat limite en attente."""
    ex, lg, svc = make()
    bought = review(proposal(), context()).order
    svc.execute(bought, "B1")                                                   # achat au marché : engagé 60,06
    svc.execute(approved_limit(price="59500"), "L1")                            # achat limite en attente : réserve 59,56
    assert lg.account("dca").committed == D("60.06") + D("59.56")
    sell = review(proposal(side=Side.SELL, reduces_risk=True), context()).order
    svc.execute(sell, "S1")                                                     # le produit (~60) ne libère que le coût de la position
    assert lg.account("dca").committed >= D("59.56")                            # la réservation de L1 est intacte
    assert svc.cancel("BTCUSDT", "L1").status == "CANCELED"                     # et son annulation ne lève plus d'erreur
    assert D("0") <= lg.account("dca").committed < D("1")
