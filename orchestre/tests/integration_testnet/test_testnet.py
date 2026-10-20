"""Intégration TESTNET (opt-in, EX-SEC-18). Jamais exécuté par défaut.

Activer :  ORCHESTRE_TESTNET=1 pytest tests/integration_testnet -v
Clés lues dans l'environnement ou le fichier .env (BINANCE_TESTNET_API_KEY / _SECRET).
Chaque test annule ses ordres ; les prix du testnet sont indépendants du marché réel.
"""

import os
import uuid
from decimal import ROUND_CEILING, Decimal as D
from pathlib import Path

import pytest

from orchestre.cli.main import load_env_file
from orchestre.config import Env
from orchestre.exchange.base import OrderRequest
from orchestre.exchange.binance import BinanceExchange, create_client
from orchestre.exchange.credentials import load_credentials
from orchestre.exchange.filters import Side, round_price, validate_order
from orchestre.execution.service import ExecutionService
from orchestre.portfolio.ledger import Ledger

SYMBOL = "BTCUSDT"
ENV = dict(os.environ)
load_env_file(Path(__file__).parents[2] / ".env", ENV)

pytestmark = pytest.mark.skipif(
    ENV.get("ORCHESTRE_TESTNET") != "1" or not ENV.get("BINANCE_TESTNET_API_KEY"),
    reason="test testnet désactivé (ORCHESTRE_TESTNET=1 et clés testnet requis)",
)


@pytest.fixture(scope="module")
def ex():
    key, secret = load_credentials(Env.TESTNET, ENV)
    return BinanceExchange(create_client(Env.TESTNET, key, secret))


def new_id() -> str:
    return "it-" + uuid.uuid4().hex[:20]


def resting_buy(ex):
    """Achat LIMIT_MAKER valide, 10 % sous le prix, à la taille minimale ×2."""
    f = ex.get_filters(SYMBOL)
    price = round_price(ex.get_price(SYMBOL) * D("0.9"), Side.BUY, f)
    qty = (f.min_notional * 2 / price / f.step_size).to_integral_value(rounding=ROUND_CEILING) * f.step_size
    validate_order(qty, price, f)
    return OrderRequest(SYMBOL, Side.BUY, qty, price, new_id())


def test_market_data_and_filters(ex):
    f = ex.get_filters(SYMBOL)
    assert f.tick_size > 0 and f.step_size > 0 and f.min_notional > 0
    assert ex.get_price(SYMBOL) > 0
    assert isinstance(ex.get_balances(), dict)


def test_resting_order_lifecycle(ex):
    req = resting_buy(ex)
    try:
        placed = ex.place_order(req)
        assert placed.status == "NEW" and placed.client_order_id == req.client_order_id
        assert ex.get_order(SYMBOL, req.client_order_id).status == "NEW"
        assert req.client_order_id in [o.client_order_id for o in ex.get_open_orders(SYMBOL)]
    finally:
        canceled = ex.cancel_order(SYMBOL, req.client_order_id)
    assert canceled.status == "CANCELED"
    assert ex.get_order(SYMBOL, req.client_order_id).status == "CANCELED"


def test_unknown_order_returns_none(ex):
    assert ex.get_order(SYMBOL, "it-n-existe-pas") is None


def test_maker_order_that_would_execute_is_rejected(ex):
    f = ex.get_filters(SYMBOL)
    price = round_price(ex.get_price(SYMBOL) * D("1.05"), Side.BUY, f)  # achat au-dessus du marché
    qty = (f.min_notional * 2 / price / f.step_size).to_integral_value(rounding=ROUND_CEILING) * f.step_size
    req = OrderRequest(SYMBOL, Side.BUY, qty, price, new_id())
    try:
        result = ex.place_order(req)
        rejected = result.status in ("REJECTED", "CANCELED")
    except Exception:  # ccxt lève une erreur sur le rejet LIMIT_MAKER (comportement à confirmer)
        rejected = True
    finally:
        leftover = ex.get_order(SYMBOL, req.client_order_id)
        if leftover is not None and leftover.status == "NEW":
            ex.cancel_order(SYMBOL, req.client_order_id)
    assert rejected, "un ordre maker qui s'exécuterait immédiatement doit être refusé"


def test_executor_cancel_all_leaves_zero_orphans_and_clean_reconcile(ex):
    ledger = Ledger(D("100000"))
    ledger.allocate("it", D("100000"))
    svc = ExecutionService(ex, ledger, dry_run=False)
    req = resting_buy(ex)
    ex.place_order(req)
    assert f"ordre orphelin sur la plateforme: {req.client_order_id}" in svc.reconcile(SYMBOL)
    svc.cancel_all_open(SYMBOL)
    assert ex.get_open_orders(SYMBOL) == []
    assert svc.reconcile(SYMBOL) == []
