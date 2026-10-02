"""Causal vectorized simulator: target weights -> net returns, with next-bar-open fills, fees, slippage and funding.

Timing convention (no lookahead): weights W[i] are computed from data up to and including bar i's close and are filled at
bar i+1's OPEN. The position held when bar t opens is therefore W[t-2], and it is rebalanced to W[t-1] at that open:

    ret[t] = W[t-2] * (open[t]/close[t-1] - 1) + W[t-1] * (close[t]/open[t] - 1)
             - |W[t-1] - W[t-2]| * (fee + slippage) - W[t-1] * funding[t]
"""
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np

from research.lab.data import MarketData

# Default per-side taker fee (bps) and slippage (bps) per venue. Override per run; these are assumptions, not quotes.
VENUE_COSTS: Dict[str, Dict[str, float]] = {
    "kucoin": {"fee_bps": 10.0, "slippage_bps": 5.0},
    "dydx": {"fee_bps": 5.0, "slippage_bps": 4.0},
    "hyperliquid": {"fee_bps": 4.5, "slippage_bps": 4.0},
    "deribit": {"fee_bps": 5.0, "slippage_bps": 4.0},
    "bitmex": {"fee_bps": 7.5, "slippage_bps": 5.0},
    "uniswap": {"fee_bps": 30.0, "slippage_bps": 10.0},  # 0.3% pool fee class + price impact/gas proxy
}
MAX_LEVERAGE = {"spot": 1.0, "dex": 1.0, "perp": 3.0}  # gross exposure caps; liquidation is NOT modeled, so keep these low


class WeightError(ValueError):
    pass


class LookaheadError(AssertionError):
    pass


@dataclass
class Costs:
    fee_bps: float = 10.0
    slippage_bps: float = 5.0

    @property
    def per_unit(self) -> float:
        return (self.fee_bps + self.slippage_bps) / 1e4

    @classmethod
    def for_venue(cls, venue: str) -> "Costs":
        return cls(**VENUE_COSTS.get(venue, {}))


@dataclass
class SimResult:
    returns: np.ndarray        # (n,) net per-bar portfolio return
    turnover: np.ndarray       # (n,) sum |dW| per bar

    @property
    def equity(self) -> np.ndarray:
        return np.cumprod(1 + np.clip(self.returns, -1.0, None))


def validate_weights(md: MarketData, W: np.ndarray) -> None:
    if W.shape != (md.n, len(md.assets)):
        raise WeightError(f"weights shape {W.shape} != {(md.n, len(md.assets))}")
    if not np.all(np.isfinite(W)):
        raise WeightError("weights contain NaN/inf")
    if md.kind in ("spot", "dex") and (W < -1e-12).any():
        raise WeightError(f"{md.venue} is {md.kind}: short weights are not allowed")
    if (np.abs(W).sum(axis=1) > MAX_LEVERAGE[md.kind] + 1e-9).any():
        raise WeightError(f"gross exposure exceeds {MAX_LEVERAGE[md.kind]}x on {md.kind} venue")


def validate_row(kind: str, w: np.ndarray) -> None:
    """Same rules as validate_weights for a single live target-weight row."""
    if not np.all(np.isfinite(w)):
        raise WeightError("weights contain NaN/inf")
    if kind in ("spot", "dex") and (w < -1e-12).any():
        raise WeightError(f"{kind} venue: short weights are not allowed")
    if np.abs(w).sum() > MAX_LEVERAGE[kind] + 1e-9:
        raise WeightError(f"gross exposure exceeds {MAX_LEVERAGE[kind]}x on {kind} venue")


def simulate(md: MarketData, W: np.ndarray, costs: Optional[Costs] = None) -> SimResult:
    validate_weights(md, W)
    costs = costs or Costs.for_venue(md.venue)
    n = md.n
    w1 = np.vstack([np.zeros((1, W.shape[1])), W[:-1]])       # W[t-1]
    w2 = np.vstack([np.zeros((2, W.shape[1])), W[:-2]])[:n]   # W[t-2]
    gap = np.zeros_like(md.close)
    gap[1:] = md.open[1:] / md.close[:-1] - 1
    intra = md.close / md.open - 1
    dw = np.abs(w1 - w2)
    ret = (w2 * gap + w1 * intra).sum(axis=1) - dw.sum(axis=1) * costs.per_unit
    if md.funding is not None:
        ret = ret - (w1 * md.funding).sum(axis=1)
    ret[0] = 0.0
    return SimResult(ret, dw.sum(axis=1))


def assert_causal(strategy, md: MarketData, params: dict, samples: int = 25, seed: int = 0, warmup: int = 30) -> None:
    """Lookahead detector: weights for bar i must not change when every bar after i is removed."""
    full = strategy.weights(md, params)
    validate_weights(md, full)
    rng = np.random.default_rng(seed)
    lo = min(warmup, md.n - 2)
    for i in rng.choice(np.arange(lo, md.n), size=min(samples, md.n - lo), replace=False):
        part = strategy.weights(md.slice(0, int(i) + 1), params)[-1]
        if not np.allclose(full[i], part, atol=1e-9):
            raise LookaheadError(f"{strategy.id}: weights at bar {i} change when future bars are removed (lookahead)")
