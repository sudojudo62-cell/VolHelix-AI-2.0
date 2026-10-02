"""Market-regime tags used to say WHEN a strategy works (volatility tercile x trend). Descriptive, not predictive.

Regimes are defined ONCE, on daily closes, and mapped onto bars of any interval using the last COMPLETED UTC day, so the same
instant gets the same label whether the strategy runs on 1h, 4h or 1d bars (the pilot found per-interval labelling disagreed).
"""
from typing import Dict, List

import numpy as np

from research.lab.venues import INTERVAL_MS

DAY = 86_400_000
MIN_REGIME_DAYS = 20      # a regime needs at least this many days of out-of-sample data before we call it favorable/unfavorable
MIN_REGIME_SHARE = 0.10


def _ema(x: np.ndarray, n: int) -> np.ndarray:
    k, out = 2.0 / (n + 1), np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = x[i] * k + out[i - 1] * (1 - k)
    return out


def daily_regimes(day_close: np.ndarray, vol_window: int = 30, slow: int = 100) -> np.ndarray:
    """Label per daily bar: '<low|mid|high>vol-<up|down>'. 'warmup' until 30d of returns exist. Tercile cut-offs use the whole
    series (descriptive tagging, not a trading signal)."""
    lr = np.diff(np.log(day_close), prepend=np.log(day_close[0]))
    vol = np.full(len(day_close), np.nan)
    for i in range(vol_window, len(day_close)):
        vol[i] = lr[i - vol_window + 1:i + 1].std()
    valid = vol[~np.isnan(vol)]
    labels = np.full(len(day_close), "warmup", dtype=object)
    if len(valid) < 10:
        return labels
    lo, hi = np.percentile(valid, [33.3, 66.7])
    up = day_close > _ema(day_close, slow)
    for i in range(len(day_close)):
        if np.isnan(vol[i]):
            continue
        v = "lowvol" if vol[i] <= lo else ("highvol" if vol[i] >= hi else "midvol")
        labels[i] = f"{v}-{'up' if up[i] else 'down'}"
    return labels


def regime_labels(ts: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Per-bar regime for bars at `ts` (ms) with `close`, from daily closes built out of the same bars."""
    day = (ts // DAY).astype(np.int64)
    last = {}                                         # last close of each UTC day
    for i in range(len(ts)):
        last[int(day[i])] = close[i]
    day_ids = np.array(sorted(last))
    day_close = np.array([last[d] for d in day_ids])
    lab_by_day = dict(zip(day_ids.tolist(), daily_regimes(day_close)))
    out = np.full(len(ts), "warmup", dtype=object)
    for i in range(len(ts)):
        prev = int(day[i]) - 1                      # label of the last completed day: no lookahead inside the current day
        out[i] = lab_by_day.get(prev, "warmup")
    return out


def regime_performance(returns: np.ndarray, labels: np.ndarray, bpy: float, interval: str = "1d") -> List[Dict]:
    """Annualized mean return and hit rate within each regime. `sufficient` needs >= 20 days AND >= 10% of the sample."""
    out = []
    n = len(returns)
    bar_days = INTERVAL_MS[interval] / DAY
    for lab in sorted(set(labels)):
        if lab == "warmup":
            continue
        m = labels == lab
        r = returns[m]
        days = m.sum() * bar_days
        out.append({"regime": lab, "bars": int(m.sum()), "days": float(round(days, 1)), "share": float(m.mean()),
                    "ann_return_pct": float(r.mean() * bpy * 100), "hit_rate": float((r > 0).mean()),
                    "sufficient": bool(days >= MIN_REGIME_DAYS and m.mean() >= MIN_REGIME_SHARE)})
    return sorted(out, key=lambda d: -d["ann_return_pct"])


def latest_regime(ts: np.ndarray, close: np.ndarray, now_ms: int):
    """Regime of the most recent COMPLETED UTC day (None until 30 days of history exist). Same definition as the backtest tags."""
    keep = ts < (now_ms // DAY) * DAY
    if keep.sum() < 2:
        return None
    day = (ts[keep] // DAY).astype(np.int64)
    last = {}
    for i, d in enumerate(day):
        last[int(d)] = close[keep][i]
    lab = daily_regimes(np.array([last[d] for d in sorted(last)]))[-1]
    return None if lab == "warmup" else str(lab)
