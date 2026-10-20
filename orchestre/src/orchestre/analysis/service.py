"""Service d'analyse : orchestre les agents d'analyse avec cadence, journal et dégradation (§10).

Les rafraîchissements sont limités par l'horloge (mutualiser les appels évite de dépasser les limites de débit,
EX-AN-01). Une donnée incohérente rend le résultat « non sain » : l'appelant ne décide rien dessus.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Callable

from orchestre.analysis.liquidity import LiquidityReading, assess_liquidity
from orchestre.analysis.marketdata import validate_series
from orchestre.analysis.regime import RegimeConfig, RegimeReading, RegimeTracker, classify, replay
from orchestre.analysis.regimes import Regime
from orchestre.analysis.sentiment import FearGreedReading, fetch_fear_greed
from orchestre.analysis.volatility import VolatilityReading, compute_volatility
from orchestre.core.clock import Clock
from orchestre.exchange.base import Exchange, IpBannedError
from orchestre.persistence.store import Store


@dataclass(frozen=True)
class AnalysisSnapshot:
    symbol: str
    regime: Regime | None
    reading: RegimeReading | None
    volatility: VolatilityReading | None
    liquidity: LiquidityReading | None
    healthy: bool
    problems: list[str] = field(default_factory=list)


@dataclass
class _State:
    tracker: RegimeTracker
    last_candles_at: datetime | None = None
    last_book_at: datetime | None = None
    last_open_time: datetime | None = None
    reading: RegimeReading | None = None
    volatility: VolatilityReading | None = None
    liquidity: LiquidityReading | None = None
    healthy: bool = True
    problems: list[str] = field(default_factory=list)


class AnalysisService:
    def __init__(
        self,
        exchange: Exchange,
        clock: Clock,
        store: Store | None = None,
        regime_cfg: RegimeConfig = RegimeConfig(),
        fetch_fng: Callable[[], dict] | None = None,
        interval: str = "1d",
        candle_limit: int = 400,
        order_notional: Decimal = Decimal("50"),
        candle_refresh: timedelta = timedelta(hours=1),
        book_refresh: timedelta = timedelta(minutes=1),
        sentiment_refresh: timedelta = timedelta(hours=6),
    ) -> None:
        self._exchange, self._clock, self._store = exchange, clock, store
        self._cfg, self._fetch_fng = regime_cfg, fetch_fng
        self._interval, self._limit, self._notional = interval, candle_limit, order_notional
        self._candle_refresh, self._book_refresh, self._sentiment_refresh = candle_refresh, book_refresh, sentiment_refresh
        self._state: dict[str, _State] = {}
        self._fng: FearGreedReading | None = None
        self._fng_at: datetime | None = None

    def refresh(self, symbol: str) -> AnalysisSnapshot:
        st = self._state.setdefault(symbol, _State(RegimeTracker(self._cfg.hysteresis_k)))
        now = self._clock.now()
        if st.last_candles_at is None or now - st.last_candles_at >= self._candle_refresh:
            st.last_candles_at = now
            self._refresh_candles(symbol, st, now)
        if st.last_book_at is None or now - st.last_book_at >= self._book_refresh:
            st.last_book_at = now
            try:
                st.liquidity = assess_liquidity(self._exchange.get_order_book(symbol), self._notional)
            except IpBannedError:
                raise
            except Exception as exc:
                st.problems = [*[p for p in st.problems if "carnet" not in p], f"carnet indisponible : {exc}"]
        return AnalysisSnapshot(
            symbol, st.tracker.published, st.reading, st.volatility, st.liquidity, st.healthy, list(st.problems)
        )

    def _refresh_candles(self, symbol: str, st: _State, now: datetime) -> None:
        try:
            candles = self._exchange.get_candles(symbol, self._interval, self._limit)
        except IpBannedError:
            raise
        except Exception as exc:
            st.healthy, st.problems = False, [f"bougies indisponibles : {exc}"]
            return
        report = validate_series(candles, self._interval)
        st.healthy, st.problems = report.healthy, report.problems
        if not report.healthy or not report.accepted:
            return  # EX-AN-03 : données suspectes, aucune décision d'analyse
        newest = report.accepted[-1].open_time
        if st.last_open_time is None:
            last = replay(report.accepted, self._cfg, st.tracker, steps=10)
            st.reading = last
            if last is not None:
                self._log_regime(now, symbol, last)
        elif newest > st.last_open_time:
            reading = classify(report.accepted, self._cfg)
            if reading is not None:
                st.reading = reading
                if st.tracker.update(reading) is not None:
                    self._log_regime(now, symbol, reading)
        else:
            return  # même bougie clôturée : rien de nouveau
        st.last_open_time = newest
        st.volatility = compute_volatility(report.accepted)
        if st.volatility is not None and self._store is not None:
            v = st.volatility
            self._store.log_volatility(now, symbol, v.atr, v.realized, v.forecast)

    def _log_regime(self, now: datetime, symbol: str, reading: RegimeReading) -> None:
        if self._store is not None:
            self._store.log_regime(now, symbol, reading.regime.value, reading.confidence, reading.indicators)

    def refresh_sentiment(self) -> FearGreedReading | None:
        if self._fetch_fng is None:
            return None
        now = self._clock.now()
        if self._fng_at is None or now - self._fng_at >= self._sentiment_refresh:
            self._fng_at = now
            reading = fetch_fear_greed(self._fetch_fng)
            if reading is not None:
                self._fng = reading
                if self._store is not None:
                    self._store.log_sentiment(now, reading.value, reading.classification)
        return self._fng
