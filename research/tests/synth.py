"""Synthetic market data + a fake venue adapter for offline tests (no network, no real prices)."""
import math
import random
from typing import Dict, List

import numpy as np

from research.lab.data import MarketData
from research.lab.plugin import SingleAssetSignal
from research.lab.venues import INTERVAL_MS, VenueAdapter

H = 3_600_000


def walk(n, drift=0.0, vol=0.01, seed=1, start=100.0):
    rng = random.Random(seed)
    px, out = start, []
    for _ in range(n):
        px *= math.exp(drift + vol * rng.gauss(0, 1))
        out.append(px)
    return out


def candles_from_closes(closes, start_ts=0, interval_ms=H, spread=0.002) -> List[dict]:
    rows, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        rows.append({"ts": start_ts + i * interval_ms, "open": o, "high": max(o, c) * (1 + spread), "low": min(o, c) * (1 - spread), "close": c, "volume": 10.0})
        prev = c
    return rows


def market_data(closes, venue="kucoin", kind="spot", interval="1h", asset="BTC") -> MarketData:
    rows = candles_from_closes(closes, interval_ms=INTERVAL_MS[interval])
    col = lambda k: np.array([[r[k]] for r in rows], dtype=float)
    return MarketData(venue, kind, interval, [asset], np.array([r["ts"] for r in rows], dtype=np.int64), col("open"), col("high"), col("low"), col("close"), col("volume"))


class FakeVenue(VenueAdapter):
    """Serves pre-built candles; used to run the whole sweep offline."""
    name, kind, has_funding = "fake", "spot", False
    intervals = {"1h": "1h", "1d": "1d"}
    assets = {"BTC": "BTC", "ETH": "ETH"}

    def __init__(self, series: Dict[str, List[float]], name="fake", kind="spot", has_funding=False, scale=1.0):
        self.name, self.kind, self.has_funding = name, kind, has_funding
        self.series, self.scale = series, scale
        self.assets = {a: a for a in series}      # any asset present in the fake data is "listed"
        self._funding = {}

    def _fetch_candles(self, symbol, interval, start_ms, end_ms):
        rows = candles_from_closes([c * self.scale for c in self.series[symbol]], interval_ms=INTERVAL_MS[interval])
        return rows

    def candles(self, asset, interval, start_ms, end_ms, fresh=False):  # bypass disk cache
        return [r for r in self._fetch_candles(self.venue_symbol(asset), interval, start_ms, end_ms) if start_ms <= r["ts"] < end_ms]

    def _fetch_funding(self, symbol, start_ms, end_ms):
        return self._funding.get(symbol, [])

    def funding(self, asset, start_ms, end_ms, fresh=False):
        return [e for e in self._fetch_funding(self.venue_symbol(asset), start_ms, end_ms) if start_ms <= e["ts"] < end_ms]


class EmaTrend(SingleAssetSignal):
    id, name, source_slug = "ema_trend", "EMA trend (test)", "test"
    param_grid = {"fast": [5, 10], "slow": [30, 60]}

    def signal(self, close, params):
        def ema(x, n):
            k, o = 2 / (n + 1), np.empty_like(x)
            o[0] = x[0]
            for i in range(1, len(x)):
                o[i] = x[i] * k + o[i - 1] * (1 - k)
            return o
        return (ema(close, params["fast"]) > ema(close, params["slow"])).astype(float)


class LookaheadStrategy(SingleAssetSignal):
    id, name, source_slug = "cheater", "Looks at tomorrow", "test"
    param_grid = {"x": [1]}

    def signal(self, close, params):
        out = np.zeros(len(close))
        out[:-1] = (close[1:] > close[:-1]).astype(float)  # uses the NEXT close: lookahead
        return out


def panel(n, k=12, planted=0.0, vol=0.006, seed=0, start=100.0):
    """k assets of log-price paths. `planted` > 0 adds a persistent per-asset drift (slowly varying OU alpha), i.e. a REAL
    cross-sectional momentum effect the ranker should find; planted = 0 is a pure null (no predictability)."""
    rng = np.random.default_rng(seed)
    alpha = np.zeros(k)
    lp = np.zeros((n, k))
    cur = np.full(k, math.log(start))
    for t in range(n):
        alpha = 0.995 * alpha + 0.0004 * rng.standard_normal(k)
        cur = cur + planted * alpha + vol * rng.standard_normal(k)
        lp[t] = cur
    return np.exp(lp)


def panel_series(n, k=12, **kw):
    px = panel(n, k, **kw)
    return {f"A{j:02d}": list(px[:, j]) for j in range(k)}
