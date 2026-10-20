from decimal import Decimal as D

import pytest

from orchestre.exchange.filters import Side
from orchestre.strategies.grid import (
    GridConfig,
    GridFill,
    build_levels,
    cycle_profit,
    initial_orders,
    on_fill,
    range_status,
)


def cfg(**kw):
    base = dict(
        agent_id="grid", symbol="BTCUSDT", lower=D("55000"), upper=D("65000"), intervals=20,
        budget_quote=D("200"), fee_rate=D("0.001"), min_notional=D("5"),
        tick_size=D("0.01"), step_size=D("0.00001"),
    )
    base.update(kw)
    return GridConfig(**base)


def test_spec_example_spacing_and_net_profit():
    c = cfg()
    assert round(c.spacing * 100, 3) == D("0.839")
    assert round(c.net_profit_per_cycle * 100, 2) == D("0.64")


def test_too_many_intervals_loses_money_and_is_refused():
    with pytest.raises(ValueError, match="rentab"):
        cfg(intervals=200, budget_quote=D("2000"))


def test_budget_below_min_viable_refused():
    with pytest.raises(ValueError, match="budget"):
        cfg(budget_quote=D("100"))  # 20 x 5 x 1,2 = 120


def test_level_notional_below_minimum_refused():
    with pytest.raises(ValueError):
        cfg(budget_quote=D("120"), min_notional=D("7"))


def test_max_num_orders_refused():
    with pytest.raises(ValueError, match="MAX_NUM_ORDERS"):
        cfg(max_num_orders=10)


def test_levels_geometric_monotonic_and_bounded():
    lv = build_levels(cfg())
    assert len(lv) == 21 and lv[0] == D("55000.00") and lv[-1] == D("65000.00")
    assert all(a < b for a, b in zip(lv, lv[1:]))
    ratios = [lv[i + 1] / lv[i] for i in range(20)]
    assert max(ratios) - min(ratios) < D("0.0001")


def test_initial_orders_are_buys_below_price_only_and_unique():
    c = cfg()
    orders = initial_orders(c, D("60000"))
    levels = build_levels(c)
    assert orders and all(o.side is Side.BUY for o in orders)
    assert all(o.price < D("60000") for o in orders)
    assert len({o.level for o in orders}) == len(orders)
    assert len({o.client_order_id for o in orders}) == len(orders)
    assert all(o.price * o.quantity >= c.min_notional for o in orders)
    assert {o.price for o in orders} <= set(levels)


def test_no_orders_outside_range_and_status():
    c = cfg()
    assert initial_orders(c, D("70000")) == []
    assert range_status(c, D("60000")) == "IN_RANGE"
    assert range_status(c, D("70000")) == "ABOVE"
    assert range_status(c, D("55000") * D("0.97")) == "BELOW_STOP"


def test_buy_fill_places_sell_one_level_up_with_received_quantity():
    c = cfg()
    lv = build_levels(c)
    nxt = on_fill(c, GridFill(5, Side.BUY, D("0.000199"), lv[5]))
    assert nxt.side is Side.SELL and nxt.level == 6 and nxt.price == lv[6]
    assert nxt.quantity == D("0.00019")  # arrondi vers le bas au stepSize


def test_sell_fill_places_buy_one_level_down():
    c = cfg()
    lv = build_levels(c)
    nxt = on_fill(c, GridFill(6, Side.SELL, D("0.00019"), lv[6]))
    assert nxt.side is Side.BUY and nxt.level == 5 and nxt.price == lv[5]


def test_no_order_beyond_grid_edges():
    c = cfg()
    lv = build_levels(c)
    assert on_fill(c, GridFill(20, Side.BUY, D("0.0001"), lv[20])) is None
    assert on_fill(c, GridFill(0, Side.SELL, D("0.0001"), lv[0])) is None


def test_client_order_ids_differ_between_cycles():
    c = cfg()
    lv = build_levels(c)
    a = on_fill(c, GridFill(5, Side.BUY, D("0.0002"), lv[5], cycle=1))
    b = on_fill(c, GridFill(5, Side.BUY, D("0.0002"), lv[5], cycle=2))
    assert a.client_order_id != b.client_order_id


def test_cycle_profit_net_of_fees():
    p = cycle_profit(D("60000"), D("60504"), D("0.001"), D("0.001"))
    assert p == D("0.504") - D("0.060") - D("0.060504")
