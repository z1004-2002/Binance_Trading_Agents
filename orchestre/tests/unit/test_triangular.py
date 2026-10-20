from decimal import Decimal as D

from orchestre.exchange.base import OrderBook
from orchestre.exchange.binance import BinanceExchange
from orchestre.strategies.triangular import Triangle, evaluate_triangle, walk_buy, walk_sell
from tests.unit.test_binance_adapter import StubClient


def book(bid, ask, qty="100", levels=3, step="0"):
    return OrderBook([(D(bid) - D(step) * i, D(qty)) for i in range(levels)], [(D(ask) + D(step) * i, D(qty)) for i in range(levels)])


TRI = Triangle("BTC", "ETH", "USDT")      # BTCUSDT, ETHUSDT, ETHBTC


def books(btc=("59990", "60010"), eth=("2999", "3001"), ethbtc=("0.04998", "0.05002")):
    return {"BTCUSDT": book(*btc), "ETHUSDT": book(*eth), "ETHBTC": book(*ethbtc)}


def test_walking_the_book_buys_and_sells_with_slippage():
    asks = [(D("100"), D("1")), (D("110"), D("1"))]
    assert walk_buy(asks, D("100")) == D("1")                        # remplit le 1er niveau
    assert walk_buy(asks, D("210")) == D("2")                        # 100 + 110
    assert walk_buy(asks, D("1000")) is None                         # profondeur insuffisante
    bids = [(D("100"), D("1")), (D("90"), D("1"))]
    assert walk_sell(bids, D("1.5")) == D("100") + D("45")
    assert walk_sell(bids, D("5")) is None


def test_a_consistent_market_gives_a_negative_edge_after_fees():
    r = evaluate_triangle(TRI, books(), D("0.001"), D("100"))
    assert r.best_edge < 0 and r.direction in ("USDT>BTC>ETH>USDT", "USDT>ETH>BTC>USDT")
    assert not r.opportunity(D("0"))


def test_a_mispriced_cross_pair_creates_a_positive_edge_in_the_right_direction():
    # ETHBTC cote trop bas : acheter ETH avec du BTC est avantageux (BTC -> ETH -> USDT)
    cheap = books(ethbtc=("0.0480", "0.0482"))
    r = evaluate_triangle(TRI, cheap, D("0.001"), D("100"))
    assert r.direction == "USDT>BTC>ETH>USDT" and r.best_edge > D("0.02") and r.opportunity(D("0.0005"))


def test_edge_accounts_for_three_fees():
    free = evaluate_triangle(TRI, books(ethbtc=("0.0480", "0.0482")), D("0"), D("100")).best_edge
    paid = evaluate_triangle(TRI, books(ethbtc=("0.0480", "0.0482")), D("0.001"), D("100")).best_edge
    assert 0.0025 < float(free - paid) < 0.0035


def test_insufficient_depth_means_no_opportunity_not_an_exception():
    thin = {"BTCUSDT": book("59990", "60010", qty="0.0001"), "ETHUSDT": book("2999", "3001"), "ETHBTC": book("0.04998", "0.05002")}
    r = evaluate_triangle(TRI, thin, D("0.001"), D("100"))
    assert r.best_edge is None and not r.opportunity(D("0"))


def test_cross_pair_symbols_are_converted_for_ccxt():
    ex = BinanceExchange(StubClient())
    assert ex._pair("ETHBTC") == "ETH/BTC" and ex._pair("BTCUSDT") == "BTC/USDT" and ex._pair("SOLUSDC") == "SOL/USDC"
    assert ex._pair("BNBETH") == "BNB/ETH"
