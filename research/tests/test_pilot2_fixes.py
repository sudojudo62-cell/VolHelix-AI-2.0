"""Regression tests for the bugs found by pilot run 2 (trend_following)."""
import numpy as np
import pytest

from backend.governor.report import decide
from backend.governor.trial import TrialState
from research.lab import data as D
from research.lab.engine import Costs
from research.lab.sweep import GATES, SweepConfig, run_sweep
from research.lab.summary import Source
from research.lab.venues import Bitfinex, Hyperliquid, aggregate_candles, fill_flat_gaps
from research.tests.synth import EmaTrend, FakeVenue, H, walk

N = 7920


def candle(i, c=100.0):
    return {"ts": i * H, "open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 5.0}


def test_decide_handles_6h_interval():
    summary = {"viable": True, "results": [], "viability_reasons": []}
    cfg = {"interval": "6h", "strategy_id": "x", "venue": "v", "assets": ["BTC"], "favorable_regimes": [], "unfavorable_regimes": []}
    out = decide(summary, cfg, TrialState(), 72.0, None)
    assert "checks" in out or "decision" in out


def test_fill_flat_gaps_only_inside_series():
    rows = [candle(0, 100), candle(3, 110)]
    out = fill_flat_gaps(rows, H)
    assert [r["ts"] for r in out] == [0, H, 2 * H, 3 * H]
    assert out[1]["open"] == out[1]["close"] == 100 and out[1]["volume"] == 0


def test_aggregation_with_gaps_dropped_by_default_but_filled_for_bitfinex_style():
    rows = [candle(i) for i in range(8) if i != 2]            # 4h bucket 0 misses hour 2
    assert [r["ts"] for r in aggregate_candles(rows, 4 * H)] == [4 * H]
    got = aggregate_candles(rows, 4 * H, fill_gaps=True)
    assert [r["ts"] for r in got] == [0, 4 * H] and got[0]["volume"] == 15.0
    assert Bitfinex.omits_empty_bars is True


def test_history_cap_is_named_in_poor_data_reason():
    class Short(FakeVenue):
        max_history_candles = 5000
        def candles(self, asset, interval, start_ms, end_ms, fresh=False):
            return [r for r in super().candles(asset, interval, start_ms, end_ms) if r["ts"] >= end_ms - 5000 * H]
    v = Short({"BTC": walk(N, 0, 0.004, 1)})
    with pytest.raises(D.DataQualityError, match="most recent ~5000"):
        D.build_market_data(v, ["BTC"], "1h", 0, N * H)
    assert Hyperliquid.max_history_candles == 5000


def test_bitfinex_cost_is_explicit_and_bitmex_not_default():
    from research.lab.engine import VENUE_COSTS
    import research.run_sweep as rs
    assert "bitfinex" in VENUE_COSTS and Costs.for_venue("bitfinex").fee_bps == 10.0
    src = open(rs.__file__).read()
    assert '"bitfinex", "bitmex"' not in src


def test_few_bets_are_counted_and_gated():
    """A strategy that rarely trades has independent_bets ~ number of position changes, and fails the min-bets gate."""
    class RarelyTrades(EmaTrend):
        id = "rare"
        grid = {"fast": [5], "slow": [30]}
        def weights(self, md, p):
            w = np.zeros((md.n, 1))
            w[:, 0] = 1.0                                       # always long: one entry, so a single bet
            return w
    v = FakeVenue({"BTC": walk(N, 0.0015, 0.004, 3)})
    s = run_sweep([RarelyTrades()], Source(slug="t", title="t", url="https://x.invalid"),
                  SweepConfig(venues=["fake"], assets=["BTC"], intervals=["1h"], end_ms=N * H), adapters={"fake": v})
    r = s.results[0]
    assert r.independent_bets <= 3
    assert not s.viable and any("independent bets" in x for x in s.viability_reasons)
    assert GATES["min_independent_bets"] >= 5
