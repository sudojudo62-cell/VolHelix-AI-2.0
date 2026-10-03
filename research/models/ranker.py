"""Cross-sectional pair/token ranking model (Model 1): features, labels, ridge fit, block-bootstrap confidence, rank-IC.

Design rules (they are what keeps this honest):
- Every feature at bar i uses data up to and including bar i only, and is z-scored ACROSS ASSETS at that same bar.
- A model fitted at bar r may train only on samples t <= r - H (H = label horizon), so no label ever peeks past r (purged).
- Output is a score and an uncertainty, not a promise: the caller compares it with baselines and out-of-sample rank IC.
"""
import warnings
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from research.lab.venues import INTERVAL_MS

FEATURE_NAMES = ["ret_1h", "ret_6h", "ret_24h", "ret_72h", "ret_168h", "vol_24h", "vol_168h", "volume_z", "drawdown_168h"]
_RET_H = (1, 6, 24, 72, 168)


def bars_for_hours(hours: float, interval: str) -> int:
    return max(1, int(round(hours * 3_600_000 / INTERVAL_MS[interval])))


def _rolling(a: np.ndarray, w: int, fn) -> np.ndarray:
    out = np.full_like(a, np.nan, dtype=float)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)   # all-NaN windows at the start of a series are expected
        for i in range(w - 1, a.shape[0]):
            out[i] = fn(a[i - w + 1:i + 1], axis=0)
    return out


def _zscore_rows(x: np.ndarray) -> np.ndarray:
    """z-score each bar across assets (axis=1 of an (n,k) array); constant rows -> 0."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mu = np.nanmean(x, axis=1, keepdims=True)
        sd = np.nanstd(x, axis=1, keepdims=True)
    z = (x - mu) / np.where(sd > 1e-12, sd, 1.0)
    return np.clip(np.nan_to_num(z, nan=0.0), -3.0, 3.0)


def build_features(close: np.ndarray, volume: np.ndarray, interval: str) -> np.ndarray:
    """(n, k, F) causal, cross-sectionally standardised feature tensor."""
    n, k = close.shape
    lc = np.log(close)
    feats: List[np.ndarray] = []
    for h in _RET_H:
        b = bars_for_hours(h, interval)
        r = np.full((n, k), np.nan)
        if b < n:
            r[b:] = lc[b:] - lc[:-b]
        feats.append(r)
    r1 = np.vstack([np.full((1, k), np.nan), np.diff(lc, axis=0)])
    for h in (24, 168):
        w = max(bars_for_hours(h, interval), 3)
        feats.append(_rolling(r1, w, np.nanstd))
    lv = np.log1p(volume)
    w = max(bars_for_hours(168, interval), 3)
    mu, sd = _rolling(lv, w, np.nanmean), _rolling(lv, w, np.nanstd)
    feats.append((lv - mu) / np.where(sd > 1e-12, sd, np.nan))
    rmax = _rolling(close, w, np.max)
    feats.append(close / rmax - 1)
    return np.stack([_zscore_rows(f) for f in feats], axis=2)


def forward_returns(close: np.ndarray, horizon_bars: int) -> np.ndarray:
    """Log return from close[t] to close[t+H], demeaned across assets. NaN where the future does not exist yet."""
    n, k = close.shape
    y = np.full((n, k), np.nan)
    if horizon_bars < n:
        y[:-horizon_bars] = np.log(close[horizon_bars:] / close[:-horizon_bars])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            y = y - np.nanmean(y, axis=1, keepdims=True)
    return y


def fit_ridge(X: np.ndarray, y: np.ndarray, lam: float) -> np.ndarray:
    """Ridge on stacked (samples, F) with centred y (no intercept needed: labels are cross-sectionally demeaned)."""
    F = X.shape[1]
    return np.linalg.solve(X.T @ X + lam * np.eye(F), X.T @ y)


def _stack(feats: np.ndarray, y: np.ndarray, lo: int, hi: int) -> Tuple[np.ndarray, np.ndarray]:
    """Samples for bars lo..hi inclusive (assets stacked); rows with a NaN label are dropped."""
    X = feats[lo:hi + 1].reshape(-1, feats.shape[2])
    t = y[lo:hi + 1].reshape(-1)
    m = ~np.isnan(t)
    return X[m], t[m]


def fit_at(feats: np.ndarray, y: np.ndarray, r: int, horizon_bars: int, train_bars: int, lam: float, min_samples: int = 200) -> Optional[np.ndarray]:
    """Coefficients available at bar r: trains on t in [r-H-train_bars, r-H] only (purged by the label horizon)."""
    hi = r - horizon_bars
    lo = max(0, hi - train_bars + 1)
    if hi <= lo:
        return None
    X, t = _stack(feats, y, lo, hi)
    return fit_ridge(X, t, lam) if len(t) >= min_samples else None


def walk_forward_scores(feats: np.ndarray, y: np.ndarray, horizon_bars: int, train_bars: int, refit_bars: int, lam: float,
                        first_refit: int) -> Tuple[np.ndarray, np.ndarray]:
    """Causal scores for every bar >= first_refit using the most recent refit at or before it. Returns (scores (n,k), coef_index (n,))."""
    n, k, _ = feats.shape
    scores = np.full((n, k), np.nan)
    which = np.full(n, -1)
    beta, last_r = None, -1
    for i in range(first_refit, n):
        if (i - first_refit) % refit_bars == 0 or beta is None:
            b = fit_at(feats, y, i, horizon_bars, train_bars, lam)
            if b is not None:
                beta, last_r = b, i
        if beta is not None:
            scores[i] = feats[i] @ beta
            which[i] = last_r
    return scores, which


def rank_ic(scores: np.ndarray, realized: np.ndarray) -> np.ndarray:
    """Per-bar Spearman correlation between scores and realised forward returns (NaN where undefined)."""
    n = scores.shape[0]
    out = np.full(n, np.nan)
    for i in range(n):
        s, r = scores[i], realized[i]
        m = ~(np.isnan(s) | np.isnan(r))
        if m.sum() >= 5:
            rs, rr = np.argsort(np.argsort(s[m])), np.argsort(np.argsort(r[m]))
            if rs.std() > 0 and rr.std() > 0:
                out[i] = np.corrcoef(rs, rr)[0, 1]
    return out


def bootstrap_topk_probability(feats: np.ndarray, y: np.ndarray, r: int, horizon_bars: int, train_bars: int, lam: float, top_k: int,
                               n_boot: int = 100, block: int = 24, seed: int = 0) -> Optional[np.ndarray]:
    """P(asset is in the top-k) when the model is refit on block-bootstrapped training windows. Measures model instability."""
    hi = r - horizon_bars
    lo = max(0, hi - train_bars + 1)
    if hi - lo < 4 * block:
        return None
    rng = np.random.default_rng(seed)
    n_samples = hi - lo + 1
    n_blocks = max(n_samples // block, 1)
    k = feats.shape[1]
    hits = np.zeros(k)
    done = 0
    for _ in range(n_boot):
        starts = rng.integers(lo, hi - block + 2, size=n_blocks)
        idx = np.concatenate([np.arange(s, s + block) for s in starts])
        X = feats[idx].reshape(-1, feats.shape[2])
        t = y[idx].reshape(-1)
        m = ~np.isnan(t)
        if m.sum() < 200:
            continue
        sc = feats[r] @ fit_ridge(X[m], t[m], lam)
        hits[np.argsort(-sc)[:top_k]] += 1
        done += 1
    return hits / done if done else None


def top_k_weights(scores_row: np.ndarray, top_k: int, long_short: bool = False) -> np.ndarray:
    """Equal-weight long the top-k (gross 1.0); long/short splits gross 0.5 long / 0.5 short."""
    k = len(scores_row)
    w = np.zeros(k)
    if np.isnan(scores_row).all():
        return w
    order = np.argsort(-np.nan_to_num(scores_row, nan=-np.inf))
    kk = min(top_k, k // 2 if long_short else k)
    if kk < 1:
        return w
    if long_short:
        w[order[:kk]] = 0.5 / kk
        w[order[-kk:]] = -0.5 / kk
    else:
        w[order[:kk]] = 1.0 / kk
    return w
