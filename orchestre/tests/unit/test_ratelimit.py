import ccxt
import pytest

from orchestre.exchange.base import IpBannedError
from orchestre.exchange.binance import BinanceExchange, create_client
from orchestre.config import Env
from orchestre.exchange.ratelimit import RateLimiter
from tests.unit.test_binance_adapter import StubClient


def limiter(headers=None, **kw):
    sleeps = []
    lim = RateLimiter(headers=lambda: headers or {}, sleep=sleeps.append, **kw)
    return lim, sleeps


class Flaky:
    def __init__(self, failures, error):
        self.failures, self.error, self.calls = failures, error, 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return "ok"


def test_429_is_retried_with_retry_after_header():
    lim, sleeps = limiter({"Retry-After": "7"})
    fn = Flaky(2, ccxt.RateLimitExceeded("429"))
    assert lim.call(fn) == "ok" and fn.calls == 3 and sleeps == [7.0, 7.0]


def test_429_without_header_uses_exponential_backoff():
    lim, sleeps = limiter(base_backoff=1.0)
    lim.call(Flaky(3, ccxt.RateLimitExceeded("429")))
    assert sleeps == [1.0, 2.0, 4.0]


def test_gives_up_after_max_retries():
    lim, _ = limiter(max_retries=2)
    fn = Flaky(10, ccxt.RateLimitExceeded("429"))
    with pytest.raises(ccxt.RateLimitExceeded):
        lim.call(fn)
    assert fn.calls == 3


def test_418_ip_ban_is_never_retried():
    lim, sleeps = limiter()
    fn = Flaky(10, ccxt.DDoSProtection("418 banned"))
    with pytest.raises(IpBannedError):
        lim.call(fn)
    assert fn.calls == 1 and sleeps == []


def test_other_errors_propagate_untouched():
    lim, _ = limiter()
    with pytest.raises(ccxt.OrderNotFound):
        lim.call(Flaky(1, ccxt.OrderNotFound("x")))


def test_slows_down_at_80_percent_of_weight_budget():
    lim, sleeps = limiter({"x-mbx-used-weight-1m": "5000"}, weight_limit=6000, slow_delay=1.5)
    lim.call(lambda: "ok")
    assert sleeps == [1.5]


def test_no_slowdown_below_threshold():
    lim, sleeps = limiter({"x-mbx-used-weight-1m": "100"})
    lim.call(lambda: "ok")
    assert sleeps == []


def test_adapter_uses_the_limiter_for_every_call():
    stub = StubClient()
    calls = []

    class Spy(RateLimiter):
        def call(self, fn, *a, **k):
            calls.append(getattr(fn, "__name__", "?"))
            return super().call(fn, *a, **k)

    ex = BinanceExchange(stub, limiter=Spy(headers=lambda: {}, sleep=lambda s: None))
    ex.get_price("BTCUSDT")
    ex.get_balances()
    assert calls == ["fetch_ticker", "fetch_balance"]


def test_ip_ban_surfaces_through_the_adapter():
    class Banned(StubClient):
        def fetch_ticker(self, symbol):
            raise ccxt.DDoSProtection("418 I'm a teapot")

    ex = BinanceExchange(Banned(), limiter=RateLimiter(headers=lambda: {}, sleep=lambda s: None))
    with pytest.raises(IpBannedError):
        ex.get_price("BTCUSDT")


def test_client_uses_small_recv_window_and_time_adjustment():
    client = create_client(Env.TESTNET, "k", "s")
    assert client.options["recvWindow"] <= 5000
    assert client.options["adjustForTimeDifference"] is True


def test_sync_time_delegates_to_client():
    class Timed(StubClient):
        def load_time_difference(self):
            self.calls.append(("load_time_difference",))
            return 12

    stub = Timed()
    assert BinanceExchange(stub).sync_time() == 12
    assert ("load_time_difference",) in stub.calls
