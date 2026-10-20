from decimal import Decimal as D

import pytest

from orchestre.config import Env
from orchestre.exchange.base import OrderRequest, OrderResult
from orchestre.exchange.fake import FakeExchange
from orchestre.exchange.filters import Side, SymbolFilters
from orchestre.execution.service import ExecutionService
from orchestre.portfolio.ledger import Ledger
from orchestre.risk.engine import ApprovedOrder, review
from orchestre.risk.models import OrderProposal
from tests.unit.test_risk import context, proposal

F = SymbolFilters("BTCUSDT", D("0.01"), D("0.00001"), D("0.00001"), D("100"), D("5"))


def make(exchange_cls=FakeExchange, dry_run=False):
    ex = exchange_cls({"BTCUSDT": F}, {"BTCUSDT": D("60000")}, {"USDT": D("1000")})
    lg = Ledger(D("500"))
    lg.allocate("dca", D("200"))
    return ex, lg, ExecutionService(ex, lg, dry_run=dry_run)


def approved(**kw):
    d = review(proposal(**kw), context())
    assert d.approved
    return d.order


class TimeoutAfterFill(FakeExchange):
    """Simule un délai dépassé alors que l'ordre a bien été exécuté."""

    def place_order(self, request: OrderRequest) -> OrderResult:
        super().place_order(request)
        raise TimeoutError("réponse perdue")


class AlwaysDown(FakeExchange):
    def place_order(self, request):
        raise ConnectionError("hors ligne")


def test_rejects_non_approved_objects():
    _, _, svc = make()
    with pytest.raises(TypeError):
        svc.execute(proposal(), "c1")


def test_buy_fills_and_commits_budget():
    ex, lg, svc = make()
    r = svc.execute(approved(), "c1")
    assert r.status == "FILLED"
    assert lg.account("dca").committed == D("60.06")
    assert ex.get_balances()["BTC"] == D("0.001")


def test_dry_run_sends_nothing():
    ex, lg, svc = make(dry_run=True)
    r = svc.execute(approved(), "c1")
    assert r.status == "DRY_RUN"
    assert ex.get_balances() == {"USDT": D("1000")}
    assert lg.account("dca").committed == D("0")


def test_same_client_order_id_never_sent_twice():
    ex, _, svc = make()
    svc.execute(approved(), "c1")
    again = svc.execute(approved(), "c1")
    assert again.status == "FILLED"
    assert ex.get_balances()["BTC"] == D("0.001")


def test_quantity_rounded_down_before_sending():
    ex, _, svc = make()
    r = svc.execute(approved(quantity=D("0.0010099")), "c1")
    assert r.status == "FILLED" and r.result.filled_qty == D("0.00100")


def test_filter_violation_rejected_locally_without_exchange_call():
    ex, _, svc = make()
    order = review(proposal(quantity=D("0.000001")), context(min_notional=D("0"))).order
    r = svc.execute(order, "c1")
    assert r.status == "FAILURE"  # arrondi à 0 puis sous min_qty, rejeté localement
    assert ex.get_order("BTCUSDT", "c1") is None


def test_unknown_status_checks_exchange_before_any_retry():
    ex, lg, svc = make(TimeoutAfterFill)
    r = svc.execute(approved(), "c1")
    assert r.status == "FILLED"
    assert ex.get_balances()["BTC"] == D("0.001")  # un seul achat
    assert lg.account("dca").committed == D("60.06")


def test_failure_counts_consecutive_errors_and_success_resets():
    ex, _, svc = make(AlwaysDown)
    assert svc.execute(approved(), "c1").status == "FAILURE"
    assert svc.execute(approved(), "c2").status == "FAILURE"
    assert svc.consecutive_errors == 2
    ex2, _, svc2 = make()
    svc2.consecutive_errors = 2
    svc2.execute(approved(), "c3")
    assert svc2.consecutive_errors == 0


def test_sell_releases_committed_budget():
    ex, lg, svc = make()
    svc.execute(approved(), "b1")
    sell = review(
        proposal(side=Side.SELL, reduces_risk=True), context()
    ).order
    r = svc.execute(sell, "s1")
    assert r.status == "FILLED"
    assert lg.account("dca").committed == D("0.06")  # 60.06 - 60 de produit de vente
