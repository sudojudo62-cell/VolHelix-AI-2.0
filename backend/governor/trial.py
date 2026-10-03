"""72-hour paper trial: runs a research strategy bar-by-bar on live public candles with the SAME accounting as the backtester.

When bar t closes: ret_t = w2*gap + w1*intra - turnover*cost - funding (w1 = weights decided at close t-1, w2 = at t-2),
then new weights W_t = strategy.target_weights(history up to t). This reproduces research.lab.engine.simulate exactly
(tested), so trial results are directly comparable with the backtest's out-of-sample distribution.
Paper only: no orders are ever sent.
"""
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from research.lab.data import DataQualityError, MarketData, build_market_data
from research.lab.engine import Costs, WeightError, validate_row
from research.lab.plugin import Strategy, load_strategies
from research.lab.venues import INTERVAL_MS, VenueAdapter, get_venue

START_EQUITY = 10_000.0
LOOKBACK_BARS = 700


@dataclass
class TrialState:
    equity: float = START_EQUITY
    w1: List[float] = field(default_factory=list)
    w2: List[float] = field(default_factory=list)
    last_bar_ts: Optional[int] = None
    returns: List[float] = field(default_factory=list)
    curve: List[List[float]] = field(default_factory=list)   # [bar_close_ts, equity]
    turnover: float = 0.0
    bars: int = 0
    gaps: int = 0                                           # bars skipped because the venue returned no candle
    events: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TrialState":
        return cls(**d)


def find_strategy(slug: str, strategy_id: str) -> Strategy:
    for s in load_strategies(slug):
        if s.id == strategy_id:
            return s
    raise KeyError(f"strategy {strategy_id} not found in research.strategies.{slug}")


def advance(strategy: Strategy, params: dict, costs: Costs, md: MarketData, st: TrialState, end_ts: Optional[int] = None,
            min_ts: Optional[int] = None) -> int:
    """Process every closed bar in `md` newer than st.last_bar_ts (and >= min_ts, and ending by end_ts).

    The first processed bar only produces a decision (the trial starts flat); every later bar books the return of the
    weights decided one and two bars earlier. Returns the number of bars processed.
    """
    bar = INTERVAL_MS[md.interval]
    k = len(md.assets)
    done = 0
    if not st.w1:
        st.w1, st.w2 = [0.0] * k, [0.0] * k
    for j in range(md.n):
        t = int(md.ts[j])
        if (st.last_bar_ts is not None and t <= st.last_bar_ts) or (min_ts is not None and t < min_ts):
            continue
        if end_ts is not None and t + bar > end_ts:
            break
        had_prev = st.last_bar_ts is not None
        if had_prev and t - st.last_bar_ts > bar:
            st.gaps += int((t - st.last_bar_ts) // bar) - 1
        if had_prev and j >= 1:
            w1, w2 = np.array(st.w1), np.array(st.w2)
            gap = md.open[j] / md.close[j - 1] - 1
            intra = md.close[j] / md.open[j] - 1
            fund = md.funding[j] if md.funding is not None else np.zeros(k)
            dw = np.abs(w1 - w2)
            ret = float((w2 * gap + w1 * intra).sum() - dw.sum() * costs.per_unit - (w1 * fund).sum())
            st.equity *= (1 + max(ret, -1.0))
            st.returns.append(ret)
            st.turnover += float(dw.sum())
            st.bars += 1
            st.curve.append([t + bar, st.equity])
        try:
            w = np.asarray(strategy.target_weights(md.slice(0, j + 1), params), dtype=float).reshape(-1)
            if len(w) != k:
                raise WeightError(f"expected {k} weights, got {len(w)}")
            validate_row(md.kind, w)
        except Exception as exc:  # a broken decision must not silently trade: go flat and record it
            w = np.zeros(k)
            st.errors.append(f"bar {t}: {type(exc).__name__}: {str(exc)[:160]}")
        st.w2, st.w1 = st.w1, [float(x) for x in w]
        st.last_bar_ts = t
        st.events.append(f"{t}: target {[round(x, 3) for x in st.w1]}")
        st.events = st.events[-60:]
        done += 1
    return done


def fetch_live(adapter: VenueAdapter, assets: List[str], interval: str, now_ms: int, funding: bool) -> MarketData:
    bar = INTERVAL_MS[interval]
    end = (now_ms // bar) * bar            # exclusive: only fully closed bars
    return build_market_data(adapter, assets, interval, end - LOOKBACK_BARS * bar, end, with_funding=funding, fresh=True)


def metrics_from_state(st: TrialState, interval: str) -> Dict[str, float]:
    from research.lab import metrics as M
    r = np.array(st.returns)
    bpy = 365.0 * 86_400_000 / INTERVAL_MS[interval]
    # Sharpe over a handful of bars is noise (the pilot saw -8.8 on 3 bars), so it is reported only with >= 20 observations
    return {"return_pct": (st.equity / START_EQUITY - 1) * 100, "max_drawdown_pct": M.max_drawdown_pct(r), "bars": st.bars,
            "turnover": st.turnover, "sharpe": (M.sharpe(r, bpy) if len(r) >= 20 else None)}


def adapter_for(venue: str) -> VenueAdapter:
    return get_venue(venue)
