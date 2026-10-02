"""Offline tests (research.tests.synth only): causality, a hand-computed ATR-stop example, venue weight rules."""
import numpy as np
import pytest

from research.lab.engine import Costs, WeightError, assert_causal, simulate, validate_weights
from research.strategies.trend_following import (STRATEGIES, ATCoreLong, ATCoreLS, ATPortfolio, atr, trail_position)
from research.tests.synth import market_data, walk

H = 3_600_000


def _md(kind="perp", n=900, seed=3, interval="1h", drift=0.0003):
    return market_data(walk(n, drift=drift, vol=0.01, seed=seed), venue="x", kind=kind, interval=interval)


def _multi(n=1500, seed=5, kind="perp"):
    from research.lab.data import MarketData
    a, b = _md(kind, n, seed), _md(kind, n, seed + 1)
    cat = lambda f: np.hstack([getattr(a, f), getattr(b, f)])
    return MarketData("x", kind, "1h", ["BTC", "ETH"], a.ts, cat("open"), cat("high"), cat("low"), cat("close"), cat("volume"),
                      np.zeros((n, 2)))


def _kinds(strat):
    return [k for k in ("spot", "perp") if k in strat.allowed_venue_kinds]


@pytest.mark.parametrize("strat,kind", [(s, k) for s in STRATEGIES if s.id != "at_portfolio" for k in _kinds(s)],
                         ids=lambda v: v if isinstance(v, str) else v.id)
def test_single_asset_strategies_are_causal_and_obey_venue_rules(strat, kind):
    md = _md(kind)
    for params in (dict(zip(strat.param_grid, v)) for v in zip(*strat.param_grid.values())):
        assert_causal(strat, md, params)
        W = strat.weights(md, params)
        validate_weights(md, W)
        if kind == "spot":
            assert (W >= 0).all()


def test_portfolio_is_causal_and_within_gross_limit():
    md = _multi()
    s = ATPortfolio()
    for p in s.param_grid["lam"]:
        assert_causal(s, md, {"lam": p}, samples=12, warmup=800)
        W = s.weights(md, {"lam": p})
        validate_weights(md, W)
        assert np.abs(W).sum(axis=1).max() <= 1.0 + 1e-9


def test_portfolio_long_short_split_follows_lambda():
    md = _multi()
    W70 = ATPortfolio().weights(md, {"lam": 0.7})
    longs, shorts = W70.clip(min=0).sum(axis=1), (-W70).clip(min=0).sum(axis=1)
    assert longs.max() <= 0.7 + 1e-9 and shorts.max() <= 0.3 + 1e-9


def test_trailing_stop_hand_example():
    # flat ATR proxy: high=low=close => TR = |close - prev close|. close path with a +10% jump then a pullback.
    close = np.array([100, 100, 100, 110, 111, 112, 105, 104, 100.0])
    pos = trail_position(close, close, close, L=2, theta=0.05, alpha=1.0, side=+1)
    # TR = [0,0,0,10,1,1,7,..]; ATR is an expanding mean while fewer than k bars exist.
    # bar 3: MOM_2 = 110/100-1 = 10% > 5% -> long; ATR3 = 10/4 = 2.5, stop = 107.5
    # bar 4: ATR = 11/5 = 2.2 -> stop max(107.5, 111-2.2) = 108.8, close 111 stays long
    # bar 5: ATR = 12/6 = 2.0 -> stop 110, close 112 stays long
    # bar 6: close 105 < stop 110 -> closed (stop is not lowered by the new, wider ATR)
    assert pos.tolist() == [0, 0, 0, 1, 1, 1, 0, 0, 0]


def test_atr_is_causal_prefix_stable():
    rng = np.random.default_rng(0)
    c = 100 + np.cumsum(rng.normal(size=200))
    h, l = c + 1, c - 1
    full = atr(h, l, c)
    assert np.allclose(full[:120], atr(h[:120], l[:120], c[:120]))


def test_short_leg_mirrors_long():
    close = np.array([100, 100, 100, 90, 89, 88, 95, 96, 100.0])
    pos = trail_position(close, close, close, L=2, theta=0.05, alpha=1.0, side=-1)
    assert pos[3] == 1.0 and pos[:3].sum() == 0 and pos[6] == 0.0   # short opens on -10% momentum, stopped out on the bounce


def test_spot_rejects_shorting_plugin_ls():
    md = _md("spot")
    with pytest.raises(WeightError):
        validate_weights(md, ATCoreLS().weights(md, {"L_days": 2, "theta": 0.01, "alpha": 2.0}) - 0.5)
    assert (ATCoreLong().weights(md, {"L_days": 2, "theta": 0.01, "alpha": 2.0}) >= 0).all()


def test_no_perp_twins_and_no_requires_funding():
    ids = [s.id for s in STRATEGIES]
    assert len(ids) == len(set(ids))
    assert not [i for i in ids if i.endswith("_perp")]          # funding is automatic on perp venues; no twin plugins
    assert not [s.id for s in STRATEGIES if s.requires_funding]
    long_only = {"at_core_long", "tsmom", "tsmom_vs"}
    for s in STRATEGIES:
        assert ("spot" in s.allowed_venue_kinds) == (s.id in long_only), s.id   # only the shorting plugins are perp-only


def test_warmup_and_internal_trials_are_declared_honestly():
    by = {s.id: s for s in STRATEGIES}
    assert all(s.warmup_days > 0 for s in STRATEGIES)
    assert by["at_portfolio"].internal_trials == 8 * 2 * 3           # 8 inner configs x 2 sides x 3 assets, re-optimised monthly
    assert by["at_portfolio"].warmup_days >= 31                      # needs a full previous calendar month
    assert by["tsmom"].warmup_days >= max(by["tsmom"].param_grid["lookback_days"])
    assert by["at_core_long"].warmup_days >= max(INNER_L_DAYS_FOR_TEST) and by["at_core_long"].internal_trials == 1


INNER_L_DAYS_FOR_TEST = (2, 5, 10)


def test_ordinary_long_plugin_pays_funding_on_perp_via_the_engine():
    """The same plugin is charged funding automatically when the MarketData carries it (no perp twin needed)."""
    md = _md("perp")
    p = {"L_days": 2, "theta": 0.01, "alpha": 2.0}
    W = ATCoreLong().weights(md, p)
    base = simulate(md, W, Costs(0, 0)).returns
    md.funding = np.full((md.n, 1), 1e-4)
    paid = simulate(md, W, Costs(0, 0)).returns
    w1 = np.concatenate([[0.0], W[:-1, 0]])
    assert np.allclose(base - paid, w1 * 1e-4)
