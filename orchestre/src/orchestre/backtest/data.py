"""Historique pour les backtests (EX-BT-01) : récupération paginée, contrôle de cohérence, cache local."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from orchestre.analysis.marketdata import INTERVAL_SECONDS, SeriesReport, validate_series
from orchestre.exchange.base import Candle, Exchange

API_LIMIT = 1000  # bougies par appel klines


def _encode(c: Candle) -> dict:
    return {
        "open_time": c.open_time.isoformat(), "close_time": c.close_time.isoformat(),
        "open": str(c.open), "high": str(c.high), "low": str(c.low), "close": str(c.close), "volume": str(c.volume),
    }


def _decode(d: dict) -> Candle:
    return Candle(
        datetime.fromisoformat(d["open_time"]), datetime.fromisoformat(d["close_time"]),
        Decimal(d["open"]), Decimal(d["high"]), Decimal(d["low"]), Decimal(d["close"]), Decimal(d["volume"]), True,
    )


def _read(path: Path) -> list[Candle]:
    try:
        return [_decode(d) for d in json.loads(path.read_text(encoding="utf-8"))]
    except (OSError, ValueError, KeyError):
        return []  # cache absent ou corrompu : on retélécharge


def _floor(now: datetime, seconds: int) -> datetime:
    ts = int(now.timestamp())
    return datetime.fromtimestamp(ts - ts % seconds, tz=timezone.utc)


def _fetch(exchange: Exchange, symbol: str, interval: str, needed: int) -> list[Candle]:
    """Pages successives vers le passé (endTime) jusqu'à `needed` bougies ou épuisement de l'historique."""
    candles: list[Candle] = []
    end = None
    while len(candles) < needed:
        want = min(API_LIMIT, needed - len(candles))
        page = exchange.get_candles(symbol, interval, want, end)
        if not page or (candles and page[-1].open_time >= candles[0].open_time):
            break  # plus rien de plus ancien
        candles = page + candles
        end = page[0].open_time
        if len(page) < want:
            break
    return candles


def load_history(
    exchange: Exchange, symbol: str, interval: str, needed: int, data_dir: str | Path, now: datetime
) -> SeriesReport:
    seconds = INTERVAL_SECONDS[interval]
    path = Path(data_dir) / f"{symbol}_{interval}.json"
    cached = _read(path)
    last_closed = _floor(now, seconds) - timedelta(seconds=seconds)
    if len(cached) >= needed and cached[-1].open_time >= last_closed:
        return validate_series(cached[-needed:], interval)
    report = validate_series(_fetch(exchange, symbol, interval, needed), interval)
    if report.accepted:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([_encode(c) for c in report.accepted]), encoding="utf-8")
    return report
