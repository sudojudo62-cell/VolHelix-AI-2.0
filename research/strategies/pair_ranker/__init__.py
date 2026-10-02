"""Model 1: cross-sectional pair/token ranker as a lab Strategy.

Each bar the strategy scores every asset in the universe with a ridge model refit on a trailing window (purged by the label
horizon), then holds the top-k (optionally shorting the bottom-k on perp venues). Because it is a normal Strategy it automatically
gets: causality checking, venue-specific costs and funding, 6-month walk-forward out-of-sample scoring, the Deflated Sharpe over
every configuration tried, regime attribution, and the governor's 72h paper trial + human approval.
"""
import numpy as np

from research.lab.data import MarketData
from research.lab.plugin import Strategy
from research.models import ranker as R


class _Base(Strategy):
    source_slug = "pair_ranker"
    param_grid = {"horizon_h": [24, 72], "top_k": [3, 5], "train_days": [60, 120]}
    allowed_venue_kinds = {"spot", "perp"}
    tolerant_universe = True      # the sweep drops assets with bad/short data instead of failing the whole universe
    warmup_days = 37              # 7d of feature lookback + 30d minimum training history
    internal_trials = 1
    min_assets = 8
    max_assets = None
    long_short = False
    refit_days = 7

    def asset_sets(self, assets):
        return [list(assets)]

    def weights(self, md: MarketData, params: dict) -> np.ndarray:
        n, k = md.close.shape
        H = R.bars_for_hours(params["horizon_h"], md.interval)
        train = R.bars_for_hours(params["train_days"] * 24, md.interval)
        refit = R.bars_for_hours(self.refit_days * 24, md.interval)
        first = R.bars_for_hours(30 * 24, md.interval) + H            # minimum training history before the first fit
        lam = float(params.get("ridge", 10.0))
        feats = R.build_features(md.close, md.volume, md.interval)
        y = R.forward_returns(md.close, H)                               # only rows t <= r-H are ever used at refit bar r
        W = np.zeros((n, k))
        if first >= n:
            return W
        scores, _ = R.walk_forward_scores(feats, y, H, train, refit, lam, first)
        reb = max(H, 1)
        cur = np.zeros(k)
        for i in range(first, n):
            if (i - first) % reb == 0 and not np.isnan(scores[i]).all():
                cur = R.top_k_weights(scores[i], params["top_k"], self.long_short)
            W[i] = cur
        return W


class PairRankerLong(_Base):
    id = "pair_ranker_long"
    name = "Cross-sectional pair ranker (long top-k)"
    description = "Ridge on momentum/volatility/volume/drawdown features, standardised across assets; holds the top-k, rebalanced every horizon."
    long_short = False


class PairRankerLS(_Base):
    id = "pair_ranker_ls"
    name = "Cross-sectional pair ranker (long top-k / short bottom-k)"
    allowed_venue_kinds = {"perp"}
    long_short = True


STRATEGIES = [PairRankerLong(), PairRankerLS()]
