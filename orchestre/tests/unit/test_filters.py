from decimal import Decimal as D

import pytest

from orchestre.exchange.filters import (
    FilterError,
    Side,
    SymbolFilters,
    round_price,
    round_qty,
    validate_order,
)

F = SymbolFilters("BTCUSDT", D("0.01"), D("0.00001"), D("0.00001"), D("100"), D("5"))


def test_round_qty_floors_to_step():
    assert round_qty(D("0.123456789"), F) == D("0.12345")


def test_round_qty_never_increases():
    for q in ("0.000019", "1.999999", "0.00001"):
        assert round_qty(D(q), F) <= D(q)


def test_round_price_favorable_direction():
    assert round_price(D("100.019"), Side.BUY, F) == D("100.01")
    assert round_price(D("100.011"), Side.SELL, F) == D("100.02")


def test_round_price_exact_value_unchanged():
    assert round_price(D("100.01"), Side.BUY, F) == D("100.01")
    assert round_price(D("100.01"), Side.SELL, F) == D("100.01")


def test_validate_ok():
    validate_order(D("0.001"), D("60000.00"), F)


def test_validate_rejects_bad_step():
    with pytest.raises(FilterError):
        validate_order(D("0.000011"), D("60000"), F)


def test_validate_rejects_bad_tick():
    with pytest.raises(FilterError):
        validate_order(D("0.001"), D("60000.005"), F)


def test_validate_rejects_below_min_notional():
    with pytest.raises(FilterError):
        validate_order(D("0.00001"), D("100"), F)  # 0.001 USDT < 5


def test_validate_rejects_qty_out_of_range():
    with pytest.raises(FilterError):
        validate_order(D("101"), D("1"), F)


def test_no_float_precision_error():
    assert round_qty(D("0.3"), SymbolFilters("X", D("1"), D("0.1"), D("0.1"), D("9"), D("0"))) == D("0.3")
