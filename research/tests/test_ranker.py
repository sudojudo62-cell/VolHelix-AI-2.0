import numpy as np
import pytest

from research.lab.data import DataQualityError, build_universe
from research.lab.engine import assert_causal, simulate
from research.lab.summary import Source
from research.lab.sweep import SweepConfig, run_sweep
from research.models import ranker as R
from research.strategies.pair_ranker import STRATEGIES, PairRankerLong, PairRankerLS
from research.tests.synth import FakeVenue, H, candles_from_closes, panel, panel_series

N = 7920


def md_from(series_px, kind="spot", interval="1h"):
    from research.tests.synth import market_data
    from research.lab.data import MarketData
    n, k = series_px.shape
    ts = np.arange(n, dtype=np.int64) * H
    open_ = np.vstack([series_px[:1], series_px[:-1]])
    return MarketData("fake", kind, interval, [f"A{j:02d}" for j in range(k)], ts, open_, series_px * 1.001, series_px * 0.999, series_px,
                      np.full_like(series_px, 100.0))


def test_features_are_cross_sectionally_standardised_and_finite():
    px = panel(600, 10, seed=1)
    F = R.build_features(px, np.full_like(px, 50.0), "1h")
    assert F.shape == (600, 10, len(R.FEATURE_NAMES)) and np.isfinite(F).all()
    late = F[400]                                           # every feature has mean ~0 across assets at a bar
    assert np.allclose(late.mean(axis=0), 0, atol=1e-9)
    assert np.abs(F).max() <= 3.0


def test_features_use_only_the_past():
    px = panel(500, 8, seed=2)
    vol = np.full_like(px, 10.0)
    full = R.build_features(px, vol, "1h")
    cut = R.build_features(px[:300], vol[:300], "1h")
    assert np.allclose(full[:300], cut)                     # truncating the future leaves the past unchanged


def test_forward_returns_are_demeaned_and_nan_at_the_end():
    px = panel(300, 6, seed=3)
    y = R.forward_returns(px, 24)
    assert np.isnan(y[-24:]).all() and not np.isnan(y[:-24]).any()
    assert np.allclose(np.nanmean(y[:100], axis=1), 0, atol=1e-12)


def test_fit_is_purged_by_the_label_horizon():
    """Changing prices AFTER bar r must not change the coefficients fitted at r."""
    px = panel(1500, 10, planted=0.0, seed=4)
    vol = np.full_like(px, 10.0)
    F, y = R.build_features(px, vol, "1h"), R.forward_returns(px, 24)
    a = R.fit_at(F, y, 1000, 24, 500, 10.0)
    px2 = px.copy()
    px2[1001:] *= 1.5                                        # wreck the future
    F2, y2 = R.build_features(px2, vol, "1h"), R.forward_returns(px2, 24)
    b = R.fit_at(F2, y2, 1000, 24, 500, 10.0)
    assert a is not None and np.allclose(a, b)


def test_ranker_finds_a_planted_effect_and_not_a_null():
    for planted, expect_positive in ((0.8, True), (0.0, False)):
        px = panel(3000, 14, planted=planted, seed=5)
        vol = np.full_like(px, 10.0)
        F, y = R.build_features(px, vol, "1h"), R.forward_returns(px, 24)
        sc, _ = R.walk_forward_scores(F, y, 24, 24 * 60, 24 * 7, 10.0, 24 * 30 + 24)
        ic = R.rank_ic(sc, y)
        mean_ic = np.nanmean(ic[24 * 40:-24])
        if expect_positive:
            assert mean_ic > 0.05
        else:
            assert abs(mean_ic) < 0.05


def test_top_k_weights_gross_and_sides():
    s = np.array([0.1, 0.9, -0.3, 0.5, 0.0, -0.8])
    w = R.top_k_weights(s, 2)
    assert w.sum() == pytest.approx(1.0) and set(np.flatnonzero(w)) == {1, 3}
    ls = R.top_k_weights(s, 2, long_short=True)
    assert ls[ls > 0].sum() == pytest.approx(0.5) and ls[ls < 0].sum() == pytest.approx(-0.5) and np.abs(ls).sum() == pytest.approx(1.0)
    assert R.top_k_weights(np.full(5, np.nan), 2).sum() == 0


def test_bootstrap_confidence_is_a_probability_and_sharper_for_a_real_signal():
    px = panel(3000, 14, planted=0.8, seed=6)
    F, y = R.build_features(px, np.full_like(px, 10.0), "1h"), R.forward_returns(px, 24)
    p = R.bootstrap_topk_probability(F, y, 2900, 24, 24 * 90, 10.0, 3, n_boot=40)
    assert p.shape == (14,) and (p >= 0).all() and (p <= 1).all() and p.sum() == pytest.approx(3.0, abs=1e-6)
    assert R.bootstrap_topk_probability(F, y, 100, 24, 24 * 90, 10.0, 3) is None   # not enough history -> no confidence claimed


def test_ranker_strategies_are_causal_and_obey_venue_rules():
    px = panel(2500, 10, planted=0.5, seed=7)
    md = md_from(px)
    for strat in (PairRankerLong(),):
        assert_causal(strat, md, {"horizon_h": 24, "top_k": 3, "train_days": 60}, samples=8, warmup=24 * 40)
        W = strat.weights(md, {"horizon_h": 24, "top_k": 3, "train_days": 60})
        assert (W >= 0).all() and W.sum(axis=1).max() <= 1.0 + 1e-9
        simulate(md, W)                                      # spot rules accept it
    perp = md_from(px, kind="perp")
    Wls = PairRankerLS().weights(perp, {"horizon_h": 24, "top_k": 3, "train_days": 60})
    assert (Wls < 0).any() and np.abs(Wls).sum(axis=1).max() <= 1.0 + 1e-9
    simulate(perp, Wls)
    assert PairRankerLS.allowed_venue_kinds == {"perp"} and "spot" in PairRankerLong.allowed_venue_kinds


def test_build_universe_drops_bad_assets_and_requires_a_minimum():
    series = panel_series(900, 10, seed=8)
    series["TRUNC"] = series["A00"][:200]                    # venue stopped serving it
    v = FakeVenue(series)
    md, dropped = build_universe(v, list(series), "1h", 0, 900 * H, min_assets=8)
    assert "TRUNC" in dropped and len(md.assets) == 10
    with pytest.raises(DataQualityError):
        build_universe(v, ["A00", "A01", "TRUNC"], "1h", 0, 900 * H, min_assets=8)


def test_recent_listings_are_dropped_instead_of_shortening_everyones_history():
    series = panel_series(900, 10, seed=8)
    series["LATE"] = series["A00"]

    class LateVenue(FakeVenue):
        def candles(self, asset, interval, start_ms, end_ms, fresh=False):
            rows = super().candles(asset, interval, start_ms, end_ms, fresh)
            return [r for r in rows if r["ts"] >= 300 * H] if asset == "LATE" else rows
    md, dropped = build_universe(LateVenue(series), list(series), "1h", 0, 900 * H, min_assets=8, allow_head_gap_bars=400)
    assert "short history" in dropped["LATE"] and md.n == 900


def test_sweep_runs_the_ranker_end_to_end_and_is_honest_on_a_null_universe():
    series = panel_series(N, 10, planted=0.0, seed=9)
    v = FakeVenue(series)
    cfg = SweepConfig(venues=["fake"], assets=list(series), intervals=["1h"], end_ms=N * H, max_combos=20)
    s = run_sweep([PairRankerLong()], Source(slug="pair_ranker", title="t", url="u"), cfg, adapters={"fake": v})
    assert s.results and s.results[0].oos_bars == 6 * 720
    assert s.viable is False                                  # pure noise must not pass the gates
    assert s.data_quality[0]["dropped_assets"] == {}
    assert set(s.results[0].assets) == set(series)
