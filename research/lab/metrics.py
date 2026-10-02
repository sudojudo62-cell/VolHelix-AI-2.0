"""Return-series statistics used by the sweep and the paper-trial governor."""
import math
from typing import Dict, List

import numpy as np

from research.lab.venues import INTERVAL_MS


def sharpe(r: np.ndarray, bpy: float) -> float:
    if len(r) < 2:
        return 0.0
    sd = float(np.std(r, ddof=1))
    return 0.0 if sd == 0 else float(np.mean(r) / sd * math.sqrt(bpy))


def total_return_pct(r: np.ndarray) -> float:
    return float((np.prod(1 + np.clip(r, -1, None)) - 1) * 100)


def max_drawdown_pct(r: np.ndarray) -> float:
    if len(r) == 0:
        return 0.0
    eq = np.cumprod(1 + np.clip(r, -1, None))
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    return float(np.max((peak - eq) / peak) * 100)


def last_days_return_pct(r: np.ndarray, interval: str, days: int = 30) -> float:
    k = int(days * 86_400_000 / INTERVAL_MS[interval])
    return total_return_pct(r[-k:]) if len(r) else 0.0


def moments(r: np.ndarray) -> tuple:
    if len(r) < 4 or np.std(r) == 0:
        return 0.0, 3.0
    m = r - r.mean()
    s2 = float((m ** 2).mean())
    return float((m ** 3).mean() / s2 ** 1.5), float((m ** 4).mean() / s2 ** 2)


def rolling_window_returns_pct(r: np.ndarray, window: int) -> np.ndarray:
    """Compounded return (%) of every consecutive `window`-bar slice (used for 72h trial expectations)."""
    if len(r) < window:
        return np.array([])
    lr = np.log1p(np.clip(r, -0.999999, None))
    c = np.concatenate([[0.0], np.cumsum(lr)])
    return (np.exp(c[window:] - c[:-window]) - 1) * 100


def window_stats(r: np.ndarray, interval: str, hours: float = 72.0) -> Dict[str, float]:
    w = int(hours * 3_600_000 / INTERVAL_MS[interval])
    x = rolling_window_returns_pct(r, max(w, 1))
    if len(x) == 0:
        return {"mean": 0.0, "std": 0.0, "p10": 0.0, "p90": 0.0, "n": 0}
    return {"mean": float(x.mean()), "std": float(x.std()), "p10": float(np.percentile(x, 10)), "p90": float(np.percentile(x, 90)), "n": int(len(x))}


def cell_stats(r: np.ndarray, interval: str, bpy: float, turnover: np.ndarray) -> Dict[str, float]:
    bar_days = INTERVAL_MS[interval] / 86_400_000
    return {
        "return_pct": total_return_pct(r), "sharpe": sharpe(r, bpy), "max_drawdown_pct": max_drawdown_pct(r),
        "bars": int(len(r)), "turnover_per_day": float(turnover.sum() / max(len(r) * bar_days, 1e-9)) if len(turnover) else 0.0,
        "last_30d_return_pct": last_days_return_pct(r, interval, 30),
    }
