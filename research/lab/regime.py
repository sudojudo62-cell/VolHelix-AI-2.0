"""Market-regime tags used to say WHEN a strategy works (volatility tercile x trend). Descriptive, not predictive."""
from typing import Dict, List

import numpy as np

from research.lab.venues import INTERVAL_MS


def _ema(x: np.ndarray, n: int) -> np.ndarray:
    k, out = 2.0 / (n + 1), np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = x[i] * k + out[i - 1] * (1 - k)
    return out


def regime_labels(close: np.ndarray, interval: str) -> np.ndarray:
    """Per-bar label like 'highvol-up'. Vol = 30-day realized vol tercile; trend = price vs 100-day EMA."""
    bars_day = 86_400_000 / INTERVAL_MS[interval]
    w, slow = max(int(30 * bars_day), 5), max(int(100 * bars_day), 10)
    lr = np.diff(np.log(close), prepend=np.log(close[0]))
    vol = np.full(len(close), np.nan)
    for i in range(w, len(close)):
        vol[i] = lr[i - w + 1:i + 1].std()
    valid = vol[~np.isnan(vol)]
    labels = np.full(len(close), "warmup", dtype=object)
    if len(valid) < 10:
        return labels
    lo, hi = np.percentile(valid, [33.3, 66.7])
    trend_up = close > _ema(close, slow)
    for i in range(len(close)):
        if np.isnan(vol[i]):
            continue
        v = "lowvol" if vol[i] <= lo else ("highvol" if vol[i] >= hi else "midvol")
        labels[i] = f"{v}-{'up' if trend_up[i] else 'down'}"
    return labels


def regime_performance(returns: np.ndarray, labels: np.ndarray, bpy: float, min_share: float = 0.05) -> List[Dict]:
    """Annualized mean return and hit rate of `returns` within each regime; thin regimes are flagged, not hidden."""
    out = []
    n = len(returns)
    for lab in sorted(set(labels)):
        if lab == "warmup":
            continue
        m = labels == lab
        r = returns[m]
        out.append({"regime": lab, "bars": int(m.sum()), "share": float(m.mean()), "ann_return_pct": float(r.mean() * bpy * 100),
                    "hit_rate": float((r > 0).mean()), "sufficient": bool(m.mean() >= min_share and m.sum() >= 30)})
    return sorted(out, key=lambda d: -d["ann_return_pct"])
