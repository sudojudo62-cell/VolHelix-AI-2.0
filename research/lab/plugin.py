"""Strategy plugin interface. A research agent implements one or more subclasses per source in
research/strategies/<slug>/__init__.py and exposes them as STRATEGIES = [...]."""
import importlib
from typing import Dict, List, Sequence

import numpy as np

from research.lab.data import MarketData


class Strategy:
    id = "base"
    name = "Base"
    source_slug = ""
    description = ""
    param_grid: Dict[str, Sequence] = {}
    allowed_venue_kinds = {"spot", "perp", "dex"}
    requires_funding = False
    min_assets = 1
    max_assets = 1
    warmup_days = 0          # history the signal needs before it is valid; cells with less warm-up data are skipped
    internal_trials = 1      # extra configurations the strategy searches internally per run (counted in the Deflated Sharpe)

    def asset_sets(self, assets: Sequence[str]) -> List[List[str]]:
        """Which asset combinations to test. Default: each asset on its own."""
        return [[a] for a in assets]

    def weights(self, md: MarketData, params: dict) -> np.ndarray:
        """CAUSAL target weights, shape (n, k): row i may use only data up to bar i. Verified by assert_causal."""
        raise NotImplementedError

    def target_weights(self, md_window: MarketData, params: dict) -> np.ndarray:
        """Latest target weights for live/paper trading (shape (k,))."""
        return self.weights(md_window, params)[-1]

    def regime_hint(self) -> str:
        return ""


class SingleAssetSignal(Strategy):
    """Convenience base: implement `signal(close, params) -> float array in [0, 1]` (causal, per bar) for one asset."""
    max_assets = 1

    def signal(self, close: np.ndarray, params: dict) -> np.ndarray:
        raise NotImplementedError

    def weights(self, md: MarketData, params: dict) -> np.ndarray:
        s = np.asarray(self.signal(md.close[:, 0], params), dtype=float)
        return np.clip(s, 0.0, 1.0).reshape(-1, 1)


def load_strategies(slug: str) -> List[Strategy]:
    mod = importlib.import_module(f"research.strategies.{slug}")
    out = list(getattr(mod, "STRATEGIES"))
    if not out:
        raise ValueError(f"research.strategies.{slug}.STRATEGIES is empty")
    return out
