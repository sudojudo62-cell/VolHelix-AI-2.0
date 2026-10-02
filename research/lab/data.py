"""Aligned multi-asset market data for one venue/interval, with quality checks."""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np

from research.lab.venues import INTERVAL_MS, VenueAdapter


class DataQualityError(Exception):
    pass


def bars_per_year(interval: str) -> float:
    return 365.0 * 86_400_000 / INTERVAL_MS[interval]


@dataclass
class MarketData:
    venue: str
    kind: str                       # spot | perp | dex
    interval: str
    assets: List[str]
    ts: np.ndarray                  # (n,) bar-open ms
    open: np.ndarray                # (n, k)
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    funding: Optional[np.ndarray] = None   # (n, k) fraction charged to longs during each bar (None = no funding data)
    quality: Dict[str, dict] = field(default_factory=dict)

    @property
    def n(self) -> int:
        return len(self.ts)

    @property
    def bars_per_year(self) -> float:
        return bars_per_year(self.interval)

    def slice(self, a: int, b: int) -> "MarketData":
        f = None if self.funding is None else self.funding[a:b]
        return MarketData(self.venue, self.kind, self.interval, self.assets, self.ts[a:b], self.open[a:b], self.high[a:b],
                          self.low[a:b], self.close[a:b], self.volume[a:b], f, self.quality)

    def pick(self, assets: Sequence[str]) -> "MarketData":
        idx = [self.assets.index(a) for a in assets]
        f = None if self.funding is None else self.funding[:, idx]
        return MarketData(self.venue, self.kind, self.interval, list(assets), self.ts, self.open[:, idx], self.high[:, idx],
                          self.low[:, idx], self.close[:, idx], self.volume[:, idx], f, self.quality)


def validate_candles(rows: List[dict], interval: str) -> dict:
    """Quality report for one asset's candles. `bad_ohlc` counts rows violating high >= max(o,c) / low <= min(o,c)."""
    bar = INTERVAL_MS[interval]
    if not rows:
        return {"n": 0, "expected": 0, "missing_pct": 100.0, "max_gap_bars": 0, "bad_ohlc": 0, "first_ts": None, "last_ts": None}
    ts = [r["ts"] for r in rows]
    expected = (ts[-1] - ts[0]) // bar + 1
    gaps = [(b - a) // bar - 1 for a, b in zip(ts, ts[1:])]
    bad = sum(1 for r in rows if r["high"] < max(r["open"], r["close"]) * (1 - 1e-9) or r["low"] > min(r["open"], r["close"]) * (1 + 1e-9)
              or min(r["open"], r["high"], r["low"], r["close"]) <= 0)
    return {"n": len(rows), "expected": int(expected), "missing_pct": round(100 * (1 - len(rows) / expected), 3),
            "max_gap_bars": int(max(gaps) if gaps else 0), "bad_ohlc": bad, "first_ts": ts[0], "last_ts": ts[-1]}


def build_market_data(venue: VenueAdapter, assets: Sequence[str], interval: str, start_ms: int, end_ms: int,
                      with_funding: bool = False, max_missing_pct: float = 3.0, fresh: bool = False) -> MarketData:
    """Fetch, validate and inner-join assets on timestamp. Raises DataQualityError rather than returning bad data."""
    per_asset, quality = {}, {}
    for a in assets:
        rows = venue.candles(a, interval, start_ms, end_ms, fresh=fresh) if fresh else venue.candles(a, interval, start_ms, end_ms)
        q = validate_candles(rows, interval)
        quality[a] = q
        if q["n"] == 0:
            raise DataQualityError(f"{venue.name}/{a}/{interval}: no candles returned")
        if q["missing_pct"] > max_missing_pct or q["bad_ohlc"] > 0.01 * q["n"]:
            raise DataQualityError(f"{venue.name}/{a}/{interval}: poor data {q}")
        per_asset[a] = {r["ts"]: r for r in rows if min(r["open"], r["high"], r["low"], r["close"]) > 0}
    common = sorted(set.intersection(*(set(d) for d in per_asset.values())))
    if len(common) < 50:
        raise DataQualityError(f"{venue.name}: only {len(common)} common bars across {list(assets)}")

    def col(field_: str) -> np.ndarray:
        return np.array([[per_asset[a][t][field_] for a in assets] for t in common], dtype=float)

    ts = np.array(common, dtype=np.int64)
    funding = None
    if with_funding:
        bar = INTERVAL_MS[interval]
        funding = np.zeros((len(ts), len(assets)))
        for j, a in enumerate(assets):
            evs = venue.funding(a, int(ts[0]), int(ts[-1]) + bar, fresh=True) if fresh else venue.funding(a, int(ts[0]), int(ts[-1]) + bar)
            for ev in evs:
                i = np.searchsorted(ts, ev["ts"], side="right") - 1  # bar containing the event
                if 0 <= i < len(ts) and ev["ts"] < ts[i] + bar:
                    funding[i, j] += ev["rate"]
    return MarketData(venue.name, venue.kind, interval, list(assets), ts, col("open"), col("high"), col("low"), col("close"),
                      col("volume"), funding, quality)


def cross_check(a: MarketData, b: MarketData, asset: str, tol: float = 0.05) -> dict:
    """Median close ratio between two venues on shared bars. Flags unit/inversion/scale mistakes (e.g. a DEX quote)."""
    common, ia, ib = np.intersect1d(a.ts, b.ts, return_indices=True)
    if len(common) < 20:
        return {"ok": None, "reason": "too few shared bars", "shared": int(len(common))}
    ra = a.close[ia, a.assets.index(asset)]
    rb = b.close[ib, b.assets.index(asset)]
    ratio = float(np.median(ra / rb))
    return {"ok": abs(ratio - 1) <= tol, "median_ratio": ratio, "shared": int(len(common))}
