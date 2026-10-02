import json

import pytest

from research.lab.engine import LookaheadError
from research.lab.summary import Source, StrategySummary, load_summary, save_summary
from research.lab.sweep import SweepConfig, expand_grid, run_sweep
from research.tests.synth import EmaTrend, FakeVenue, LookaheadStrategy, H, walk

N = 7200  # 120d warmup + 180d analysis at 1h
SRC = Source(slug="test", title="Test source", url="https://example.invalid")


def cfg(**kw):
    base = dict(venues=["fake"], assets=["BTC"], intervals=["1h"], end_ms=N * H)
    base.update(kw)
    return SweepConfig(**base)


def venue(drift=0.0, vol=0.01, seed=1, **kw):
    return FakeVenue({"BTC": walk(N, drift, vol, seed), "ETH": walk(N, drift, vol, seed + 1)}, **kw)


def test_expand_grid_limit_message():
    assert len(expand_grid({"a": [1, 2], "b": [1, 2, 3]}, 10)) == 6
    with pytest.raises(ValueError, match="Deflated Sharpe"):
        expand_grid({"a": list(range(30)), "b": list(range(30))}, 400)


def test_end_to_end_summary_is_valid_and_roundtrips(tmp_path):
    s = run_sweep([EmaTrend()], SRC, cfg(), adapters={"fake": venue()})
    assert isinstance(s, StrategySummary) and s.results
    r = s.results[0]
    assert r.oos_bars == 4 * 720 and len(r.folds) == 4        # 180d: train 60d + 4 x 30d tests
    assert 0 <= r.deflated_sharpe_prob <= 1 and r.modal_params.keys() == {"fast", "slow"}
    assert s.run["n_trials"] == 4 and s.run["months"] == 6
    p = tmp_path / "s.json"
    save_summary(s, str(p))
    assert load_summary(str(p)).source.slug == "test"
    assert "factor_attribution" in json.loads(p.read_text())


def test_driftless_random_walk_is_not_viable():
    s = run_sweep([EmaTrend()], SRC, cfg(), adapters={"fake": venue(drift=0.0, seed=5)})
    assert s.viable is False and s.viability_reasons
    assert s.recommended_trial is not None  # still reports the best candidate, flagged as not viable


def test_strong_uptrend_can_pass_gates_and_recommend_a_trial():
    s = run_sweep([EmaTrend()], SRC, cfg(), adapters={"fake": venue(drift=0.0012, vol=0.004, seed=2)})
    best = s.results[0]
    assert best.oos_return_pct > 0
    assert s.recommended_trial and s.recommended_trial.assets == ["BTC"] and s.recommended_trial.expected_72h_std_pct >= 0


def test_more_trials_lower_the_deflated_sharpe():
    a = run_sweep([EmaTrend()], SRC, cfg(), adapters={"fake": venue(drift=0.001, vol=0.005, seed=3)})
    two = {"fake": venue(drift=0.001, vol=0.005, seed=3), "fake2": venue(drift=0.001, vol=0.005, seed=3, name="fake2")}
    b = run_sweep([EmaTrend()], SRC, cfg(venues=["fake", "fake2"]), adapters=two)
    assert b.run["n_trials"] == 2 * a.run["n_trials"]
    assert b.results[0].deflated_sharpe_prob <= a.results[0].deflated_sharpe_prob + 1e-9


def test_lookahead_strategy_aborts_the_sweep():
    with pytest.raises(LookaheadError):
        run_sweep([LookaheadStrategy()], SRC, cfg(), adapters={"fake": venue()})


def test_inapplicable_venues_are_skipped_with_reasons():
    class NeedsFunding(EmaTrend):
        id, requires_funding = "needs_funding", True

    class PerpOnly(EmaTrend):
        id, allowed_venue_kinds = "perp_only", {"perp"}

    s = run_sweep([NeedsFunding(), PerpOnly()], SRC, cfg(), adapters={"fake": venue()})
    reasons = " | ".join(x["reason"] for x in s.skipped)
    assert "needs funding" in reasons and "not applicable" in reasons and s.results == []
    assert s.viable is False


def test_unsupported_interval_and_short_history_are_reported():
    s = run_sweep([EmaTrend()], SRC, cfg(intervals=["4h"]), adapters={"fake": venue()})
    assert any("interval not supported" in x["reason"] for x in s.skipped)
    short = FakeVenue({"BTC": walk(3000, 0, 0.01, 1)})
    s2 = run_sweep([EmaTrend()], SRC, cfg(end_ms=3000 * H), adapters={"fake": short})
    assert s2.results == [] and any("history shorter" in x["reason"] for x in s2.skipped)


def test_cross_venue_price_mismatch_is_discarded():
    ref = venue(name="kucoin", seed=1)
    bad = FakeVenue({"BTC": walk(N, 0, 0.01, 1), "ETH": walk(N, 0, 0.01, 2)}, name="dexish", scale=1 / 2500)  # asset-per-USD style quote
    s = run_sweep([EmaTrend()], SRC, cfg(venues=["kucoin", "dexish"]), adapters={"kucoin": ref, "dexish": bad})
    assert {r.venue for r in s.results} == {"kucoin"}
    assert any("price mismatch" in x["reason"] for x in s.skipped)


def test_factor_attribution_lists_every_factor():
    s = run_sweep([EmaTrend()], SRC, cfg(), adapters={"fake": venue(drift=0.0005, seed=7)})
    fa = s.factor_attribution
    assert {"venue", "interval", "assets", "param:fast", "param:slow", "oos_by_venue"} <= set(fa)
    assert all(d["n"] > 0 for d in fa["param:fast"])
