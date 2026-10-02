"""AdaptiveTrend (arXiv 2602.11708) and its TSMOM benchmarks as causal lab plugins.

See research/reports/trend_following_strategies.md for the paper reference of every rule and for what is NOT backtestable.
Parameters the paper leaves open (ATR length k, grids for theta/L/alpha) are ours and are labelled as such.

Funding is loaded automatically on every perp venue by the lab, so the long-only plugins run on spot, DEX and perp venues
alike (on perps they pay/earn funding without any special class). Long/short variants are separate plugins because they are
different strategies (they need shorting), not because of funding.
"""
from typing import Dict, List, Tuple

import numpy as np

from research.lab.data import MarketData
from research.lab.plugin import Strategy

ATR_K = 14                      # not given in the paper
DAY_MS = 86_400_000
INNER_L_DAYS = (2, 5, 10)       # inner grid for the monthly re-optimisation (ours)
INNER_THETA = (0.02, 0.05)
INNER_ALPHA = (2.0, 3.0)
GAMMA_LONG, GAMMA_SHORT = 1.3, 1.7   # paper Eq. (4)

_PATH_CACHE: Dict[tuple, np.ndarray] = {}


def bars_per_day(md: MarketData) -> float:
    return DAY_MS / (md.ts[1] - md.ts[0]) if md.n > 1 else 1.0


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, k: int = ATR_K) -> np.ndarray:
    """Simple-moving-average ATR; uses the available TRs while fewer than k exist (causal)."""
    prev = np.concatenate([[close[0]], close[:-1]])
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    c = np.concatenate([[0.0], np.cumsum(tr)])
    idx = np.arange(1, len(tr) + 1)
    lo = np.maximum(idx - k, 0)
    return (c[idx] - c[lo]) / (idx - lo)


def momentum(close: np.ndarray, L: int) -> np.ndarray:
    """Eq. (2). NaN for the first L bars."""
    out = np.full(len(close), np.nan)
    if len(close) > L:
        out[L:] = close[L:] / close[:-L] - 1.0
    return out


def trail_position(high, low, close, L: int, theta: float, alpha: float, side: int) -> np.ndarray:
    """Algorithm 1 for one asset: 1.0 while a position (long if side=+1, short if side=-1) is open after bar i's close.

    Entry when side*MOM > theta; stop S = P - side*alpha*ATR is ratcheted in the favourable direction (Eq. 3) and the
    position is closed when the close crosses it. The short mirror is our reading of "for short signals ...".
    """
    key = (hash(close.tobytes()), hash(high.tobytes()), hash(low.tobytes()), L, theta, alpha, side)
    hit = _PATH_CACHE.get(key)
    if hit is not None:
        return hit
    a = atr(high, low, close)
    mom = momentum(close, L)
    n = len(close)
    pos = np.zeros(n)
    open_, stop = False, 0.0
    cl, at, mo = close.tolist(), a.tolist(), mom.tolist()
    out = [0.0] * n
    for i in range(n):
        p = cl[i]
        if not open_:
            m = mo[i]
            if m == m and side * m > theta:
                open_, stop = True, p - side * alpha * at[i]
                out[i] = 1.0
        else:
            cand = p - side * alpha * at[i]
            stop = max(stop, cand) if side > 0 else min(stop, cand)
            if side * (p - stop) < 0:
                open_ = False
            else:
                out[i] = 1.0
    pos[:] = out
    if len(_PATH_CACHE) > 6000:
        _PATH_CACHE.clear()
    _PATH_CACHE[key] = pos
    return pos


def month_ids(ts: np.ndarray) -> np.ndarray:
    return ts.astype("datetime64[ms]").astype("datetime64[M]").astype(np.int64)


def month_starts(ts: np.ndarray) -> List[int]:
    m = month_ids(ts)
    return [i for i in range(1, len(m)) if m[i] != m[i - 1]]


def _L_bars(md: MarketData, days: float) -> int:
    return max(2, int(round(days * bars_per_day(md))))


# ── 1+2: momentum entry + ATR trailing stop, single asset ───────────────────
class _ATCore(Strategy):
    source_slug = "trend_following"
    side_mode = "long"          # "long" | "longshort"
    param_grid = {"L_days": list(INNER_L_DAYS), "theta": list(INNER_THETA), "alpha": list(INNER_ALPHA)}
    # longest lookback is L=10d; ATR(14) needs 14 bars (14 days on 1d bars). 30d lets the trailing-stop state forget its start.
    warmup_days = 30
    internal_trials = 1         # the (L, theta, alpha) grid IS param_grid, already counted

    def weights(self, md: MarketData, params: dict) -> np.ndarray:
        L = _L_bars(md, params["L_days"])
        w = trail_position(md.high[:, 0], md.low[:, 0], md.close[:, 0], L, params["theta"], params["alpha"], +1)
        if self.side_mode == "longshort":
            w = w - trail_position(md.high[:, 0], md.low[:, 0], md.close[:, 0], L, params["theta"], params["alpha"], -1)
        return w.reshape(-1, 1)

    def regime_hint(self) -> str:
        return "trending (up or down) markets; trail-stop exits cost whipsaws in sideways chop"


class ATCoreLong(_ATCore):
    id, name = "at_core_long", "AdaptiveTrend core (momentum entry + ATR trailing stop), long-only"
    description = "Paper §3.2 long leg: enter when MOM_L > theta, exit on ATR trailing stop. Long-only; any venue (perps pay/earn funding automatically)."


class ATCoreLS(_ATCore):
    id, name = "at_core_ls", "AdaptiveTrend core, long+short on perps"
    description = "Paper §3.2 long and short (mirrored) signals on one asset, +/-1x. Short mirror of Eq. (3) is our assumption."
    allowed_venue_kinds = {"perp"}
    side_mode = "longshort"


# ── 4+5+6: monthly selection + 70/30 allocation + monthly re-optimisation ───
class ATPortfolio(Strategy):
    id, name, source_slug = "at_portfolio", "AdaptiveTrend portfolio (monthly Sharpe selection, 70/30 allocation)", "trend_following"
    description = ("Paper §3.3-3.4: each calendar month, per asset and side grid-search (L, theta, alpha) on the PREVIOUS month; keep the asset "
                   "if that Sharpe >= 1.3 (long) / 1.7 (short); long leg gets lambda, short leg 1-lambda, equal weight within a leg. "
                   "Market-cap filter not applicable to 3 assets (every asset is eligible for both legs).")
    allowed_venue_kinds = {"perp"}
    min_assets, max_assets = 1, 3
    param_grid = {"lam": [0.7, 0.5]}
    # Selection uses the PREVIOUS calendar month (up to 31d) plus ~30d for the trailing-stop paths to settle; the first
    # month in the data is always flat. Each month the strategy re-optimises (L, theta, alpha) from 8 inner configs for every
    # asset and both sides: 8 x 2 sides x 3 assets = 48 configurations searched internally (an upper bound for 1-2 assets).
    warmup_days = 60
    internal_trials = 48

    def asset_sets(self, assets):
        a = list(assets)
        return [a] if len(a) < 3 else [a, a[:2]]

    def weights(self, md: MarketData, params: dict) -> np.ndarray:
        n, k = md.n, len(md.assets)
        lam = params["lam"]
        bpy = md.bars_per_year
        inner = [(_L_bars(md, d), th, al) for d in INNER_L_DAYS for th in INNER_THETA for al in INNER_ALPHA]
        paths: Dict[Tuple[int, int], List[np.ndarray]] = {}
        rets = np.zeros((n, k))
        rets[1:] = md.close[1:] / md.close[:-1] - 1
        for j in range(k):
            for side in (1, -1):
                paths[(j, side)] = [trail_position(md.high[:, j], md.low[:, j], md.close[:, j], L, th, al, side) for (L, th, al) in inner]
        starts = month_starts(md.ts)
        W = np.zeros((n, k))
        bounds = starts + [n]
        prev_start = 0
        for b0, b1 in zip(bounds[:-1], bounds[1:]):
            lo, hi = prev_start, b0          # previous calendar month: bars [lo, hi) all strictly before the new month
            prev_start = b0
            if hi - lo < 10:
                continue
            sel = {1: [], -1: []}
            for j in range(k):
                for side, gamma in ((1, GAMMA_LONG), (-1, GAMMA_SHORT)):
                    best_sr, best_g = -np.inf, -1
                    for g, p in enumerate(paths[(j, side)]):
                        # return of bar t uses the position decided at close t-1
                        r = side * p[lo - 1:hi - 1] * rets[lo:hi, j] if lo >= 1 else side * np.concatenate([[0.0], p[lo:hi - 1]]) * rets[lo:hi, j]
                        sd = r.std(ddof=1)
                        sr = r.mean() / sd * np.sqrt(bpy) if sd > 0 else 0.0
                        if sr > best_sr:
                            best_sr, best_g = sr, g
                    if best_sr >= gamma:
                        sel[side].append((j, best_g))
            for side, share in ((1, lam), (-1, 1.0 - lam)):
                if sel[side]:
                    w_each = share / len(sel[side])
                    for j, g in sel[side]:
                        W[b0:b1, j] += side * w_each * paths[(j, side)][g][b0:b1]
        return W

    def regime_hint(self) -> str:
        return "persistent trends with low month-to-month reversal; stays flat when no asset passes the Sharpe filter"


# ── benchmarks: TSMOM ───────────────────────────────────────────────────────
class _TSMOM(Strategy):
    source_slug = "trend_following"
    param_grid = {"lookback_days": [30, 90]}
    vol_target = None           # None -> unit weight; else annualised vol target (paper: 10%)
    long_only = True
    warmup_days = 90            # longest lookback (3M); the 30d vol window is shorter
    internal_trials = 1

    def weights(self, md: MarketData, params: dict) -> np.ndarray:
        n, k = md.n, len(md.assets)
        L = _L_bars(md, params["lookback_days"])
        volw = _L_bars(md, 30)
        W = np.zeros((n, k))
        starts = [0] + month_starts(md.ts)
        lr = np.zeros((n, k))
        lr[1:] = np.log(md.close[1:] / md.close[:-1])
        cur = np.zeros(k)
        nxt = set(starts)
        for i in range(n):
            if i in nxt and i >= L:                    # rebalance on the first bar of each month
                sig = np.sign(md.close[i] / md.close[i - L] - 1)
                if self.long_only:
                    sig = np.maximum(sig, 0.0)
                if self.vol_target is not None and i >= volw:
                    vol = lr[i - volw + 1:i + 1].std(axis=0, ddof=1) * np.sqrt(md.bars_per_year)
                    sig = sig * np.minimum(1.0 if self.long_only else 3.0, self.vol_target / np.maximum(vol, 1e-9))
                cur = sig / k if k > 1 else sig
                cur = cur.copy()
            W[i] = cur
        return W


class TSMOMLong(_TSMOM):
    id, name = "tsmom", "TSMOM benchmark (1M/3M sign, monthly rebalance), long-only"
    description = "Paper §4.3 TSMOM-1M/3M benchmark, long/flat. Any venue (perps pay/earn funding automatically)."


class TSMOMVolLong(_TSMOM):
    id, name = "tsmom_vs", "Vol-scaled TSMOM benchmark (10% vol target), long-only"
    description = "Paper §4.3 vol-targeted TSMOM (10% annualised), long/flat; weights capped at 1x. Any venue."
    vol_target = 0.10


class TSMOMLS(_TSMOM):
    id, name = "tsmom_ls", "TSMOM benchmark, long/short on perps"
    description = "Paper §4.3 TSMOM-1M/3M benchmark, long/short (needs shorting, so perps only)."
    allowed_venue_kinds = {"perp"}
    long_only = False


class TSMOMVolLS(_TSMOM):
    id, name = "tsmom_vs_ls", "Vol-scaled TSMOM benchmark, long/short on perps"
    description = "Paper §4.3 vol-targeted TSMOM (10% annualised), long/short; weights capped at 3x."
    allowed_venue_kinds = {"perp"}
    long_only = False
    vol_target = 0.10


STRATEGIES = [ATCoreLong(), ATCoreLS(), ATPortfolio(), TSMOMLong(), TSMOMVolLong(), TSMOMLS(), TSMOMVolLS()]
