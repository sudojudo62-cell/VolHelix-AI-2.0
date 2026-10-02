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


def validate_candles(rows: List[dict], interval: str, start_ms: Optional[int] = None, end_ms: Optional[int] = None) -> dict:
    """Quality report for one asset's candles. `bad_ohlc` counts rows violating high >= max(o,c) / low <= min(o,c).

    With start_ms/end_ms the expected bar count covers the REQUESTED range, so a venue that stops early, starts late or caps
    history shows up as missing data instead of looking complete (a gap-free but truncated series used to pass).
    """
    bar = INTERVAL_MS[interval]
    if not rows:
        return {"n": 0, "expected": 0, "missing_pct": 100.0, "max_gap_bars": 0, "bad_ohlc": 0, "first_ts": None, "last_ts": None}
    ts = [r["ts"] for r in rows]
    if start_ms is not None and end_ms is not None:
        expected = max((end_ms - start_ms) // bar, 1)
    else:
        expected = (ts[-1] - ts[0]) // bar + 1
    gaps = [(b - a) // bar - 1 for a, b in zip(ts, ts[1:])]
    bad = sum(1 for r in rows if r["high"] < max(r["open"], r["close"]) * (1 - 1e-9) or r["low"] > min(r["open"], r["close"]) * (1 + 1e-9)
              or min(r["open"], r["high"], r["low"], r["close"]) <= 0)
    out = {"n": len(rows), "expected": int(expected), "missing_pct": round(max(0.0, 100 * (1 - len(rows) / expected)), 3),
           "max_gap_bars": int(max(gaps) if gaps else 0), "bad_ohlc": bad, "first_ts": ts[0], "last_ts": ts[-1]}
    if start_ms is not None and end_ms is not None:
        out["head_gap_bars"] = int(max(ts[0] - start_ms, 0) // bar)      # data missing before the first bar (history cap / late listing)
        out["tail_gap_bars"] = int(max(end_ms - bar - ts[-1], 0) // bar)  # data missing at the end (venue stopped serving)
    return out


def build_market_data(venue: VenueAdapter, assets: Sequence[str], interval: str, start_ms: int, end_ms: int,
                      with_funding: bool = False, max_missing_pct: float = 3.0, fresh: bool = False,
                      allow_head_gap_bars: int = 0) -> MarketData:
    """Fetch, validate and inner-join assets on timestamp. Raises DataQualityError rather than returning bad data."""
    per_asset, quality = {}, {}
    for a in assets:
        rows = venue.candles(a, interval, start_ms, end_ms, fresh=fresh) if fresh else venue.candles(a, interval, start_ms, end_ms)
        q = validate_candles(rows, interval, start_ms, end_ms)
        quality[a] = q
        if q["n"] == 0:
            raise DataQualityError(f"{venue.name}/{a}/{interval}: no candles returned")
        missing = q["missing_pct"]
        head = q.get("head_gap_bars", 0)
        if 0 < head <= allow_head_gap_bars and q["expected"] > head:   # shortage confined to the warm-up zone is tolerated, but recorded
            missing = max(0.0, 100 * (1 - q["n"] / (q["expected"] - head)))
        if missing > max_missing_pct or q["bad_ohlc"] > 0.01 * q["n"]:
            cap = getattr(venue, "max_history_candles", None)
            why = f" (venue serves only the most recent ~{cap} {interval} candles = ~{cap * INTERVAL_MS[interval] / 86_400_000:.0f} days)" if cap and head > 0 else ""
            raise DataQualityError(f"{venue.name}/{a}/{interval}: poor data{why} {q}")
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
            # one fetch per asset for the whole requested window, so the cache is shared across intervals
            evs = venue.funding(a, start_ms, end_ms, fresh=True) if fresh else venue.funding(a, start_ms, end_ms)
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


def build_universe(venue: VenueAdapter, assets: Sequence[str], interval: str, start_ms: int, end_ms: int, with_funding: bool = False,
                   min_assets: int = 8, allow_head_gap_bars: int = 0, fresh: bool = False):
    """Tolerant multi-asset build for ranking models: assets that fail a quality check are DROPPED and reported, not fatal.

    Returns (MarketData, dropped) where dropped maps asset -> reason. Raises DataQualityError if fewer than `min_assets` survive,
    because a cross-sectional ranker over a handful of assets is not a ranking.
    """
    good, dropped, nbars = [], {}, {}
    for a in assets:
        try:
            m = build_market_data(venue, [a], interval, start_ms, end_ms, with_funding=False, fresh=fresh, allow_head_gap_bars=allow_head_gap_bars)
            good.append(a)
            nbars[a] = m.n
        except Exception as exc:  # NotSupported, VenueError, DataQualityError: one bad asset must not sink the universe
            dropped[a] = f"{type(exc).__name__}: {str(exc)[:160]}"
    if nbars:  # the joint build keeps only common timestamps, so a recent listing would shorten everyone's history: drop it instead
        longest = max(nbars.values())
        for a in list(good):
            if nbars[a] < 0.9 * longest:
                good.remove(a)
                dropped[a] = f"short history ({nbars[a]} bars vs {longest} for the longest asset)"
    if len(good) < min_assets:
        raise DataQualityError(f"{venue.name}: only {len(good)} of {len(assets)} assets usable (< {min_assets}); dropped={dropped}")
    md = build_market_data(venue, good, interval, start_ms, end_ms, with_funding=with_funding, fresh=fresh, allow_head_gap_bars=allow_head_gap_bars)
    return md, dropped
