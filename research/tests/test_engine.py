import numpy as np
import pytest

from research.lab.engine import Costs, LookaheadError, WeightError, assert_causal, simulate
from research.tests.synth import EmaTrend, LookaheadStrategy, market_data, walk

ZERO = Costs(0, 0)


def test_flat_weights_earn_nothing():
    md = market_data(walk(100))
    assert simulate(md, np.zeros((100, 1)), ZERO).returns.sum() == 0


def test_next_open_timing_exact():
    # bars: close 100,100,110,110. Weight 1 decided at close of bar 0 is filled at open of bar 1 (=100).
    md = market_data([100.0, 100.0, 110.0, 110.0])
    md.open[:] = np.array([[100.0], [100.0], [100.0], [110.0]])  # bar 2 gaps nothing; rallies intrabar 100 -> 110
    W = np.zeros((4, 1))
    W[:2] = 1.0                                 # decided at closes 0 and 1 -> held during bars 1 and 2
    r = simulate(md, W, ZERO).returns
    assert r[1] == pytest.approx(0.0)          # filled at open of bar 1, price flat during bar 1
    assert r[2] == pytest.approx(0.10)         # holds through the 100 -> 110 move
    assert r[3] == pytest.approx(0.0)          # W[2]=0: exits at bar 3's open (110), no further move


def test_costs_charged_on_turnover_only():
    md = market_data([100.0] * 10)
    W = np.zeros((10, 1))
    W[2:] = 1.0                                 # one entry, never exits
    r = simulate(md, W, Costs(10, 0))
    assert r.returns.sum() == pytest.approx(-0.001)  # 10 bps once
    assert r.turnover.sum() == pytest.approx(1.0)


def test_funding_charged_to_longs_and_paid_to_shorts():
    md = market_data([100.0] * 6, kind="perp")
    md.funding = np.full((6, 1), 0.001)
    long_w, short_w = np.ones((6, 1)), -np.ones((6, 1))
    assert simulate(md, long_w, ZERO).returns.sum() < 0
    assert simulate(md, short_w, ZERO).returns.sum() > 0


def test_shorts_and_leverage_rules():
    spot, perp = market_data(walk(20)), market_data(walk(20), kind="perp")
    with pytest.raises(WeightError):
        simulate(spot, -np.ones((20, 1)), ZERO)
    simulate(perp, -np.ones((20, 1)), ZERO)
    with pytest.raises(WeightError):
        simulate(perp, np.full((20, 1), 4.0), ZERO)
    with pytest.raises(WeightError):
        simulate(spot, np.full((20, 1), 1.5), ZERO)


def test_causality_check_passes_honest_and_catches_cheater():
    md = market_data(walk(300, seed=3))
    assert_causal(EmaTrend(), md, {"fast": 5, "slow": 30})
    with pytest.raises(LookaheadError):
        assert_causal(LookaheadStrategy(), md, {"x": 1})


def test_cheater_would_look_great_without_the_check():
    md = market_data(walk(500, seed=4))
    W = LookaheadStrategy().weights(md, {"x": 1})
    assert simulate(md, W, ZERO).returns.sum() > 0  # shows why assert_causal exists: lookahead earns fake profits
