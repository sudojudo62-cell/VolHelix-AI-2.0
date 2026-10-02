import json

import pytest

from research.lab.engine import LookaheadError
from research.lab.summary import Source, StrategySummary, load_summary, save_summary
from research.lab.sweep import SweepConfig, expand_grid, run_sweep
from research.tests.synth import EmaTrend, FakeVenue, LookaheadStrategy, H, walk

N = 7920  # 90d warm-up + 60d train + 180d (6 months) out-of-sample, at 1h
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
    assert r.oos_bars == 6 * 720 and len(r.folds) == 6        # 6 months out-of-sample = 6 test folds of 30d
    assert 0 <= r.deflated_sharpe_prob <= 1 and r.modal_params.keys() == {"fast", "slow"}
    assert s.run["n_trials"] == 4 and s.run["months"] == 6 and s.viability_reasons
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
    s = run_sweep([EmaTrend()], SRC, cfg(intervals=["15m"]), adapters={"fake": venue()})
    assert any("interval not supported" in x["reason"] for x in s.skipped)
    short = FakeVenue({"BTC": walk(3000, 0, 0.01, 1)})
    s2 = run_sweep([EmaTrend()], SRC, cfg(end_ms=3000 * H), adapters={"fake": short})
    assert s2.results == [] and any(("history shorter" in x["reason"]) or ("poor data" in x["reason"]) for x in s2.skipped)


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


# ── regression tests for defects found by the pilot research run ─────────────────────────────────────────────
def test_dsr_threshold_uses_each_intervals_own_trial_variance():
    """Pilot bug #1: pooling per-period Sharpe variance across intervals made 1h unpassable and 1d too lenient."""
    v = venue(drift=0.0012, vol=0.004, seed=2)
    s = run_sweep([EmaTrend()], SRC, cfg(intervals=["1h", "1d"]), adapters={"fake": v})
    var = s.run["trial_sharpe_variance_by_interval"]
    assert set(var) == {"1h", "1d"} and var["1h"] != var["1d"] and all(x > 0 for x in var.values())
    by_i = {r.interval: r for r in s.results}
    # a strongly trending series must be able to clear the bar at the HIGH-frequency interval too (it never could when pooled)
    assert by_i["1h"].deflated_sharpe_prob > 0.5


def test_internal_trials_are_counted_in_the_deflation():
    class Searches(EmaTrend):
        id, internal_trials = "searches", 10
    a = run_sweep([EmaTrend()], SRC, cfg(), adapters={"fake": venue(seed=11)})
    b = run_sweep([Searches()], SRC, cfg(), adapters={"fake": venue(seed=11)})
    assert b.run["n_trials"] == 10 * a.run["n_trials"]


def test_funding_is_charged_to_ordinary_strategies_on_perp_venues():
    """Pilot bug #2: funding was only loaded for strategies that declared requires_funding."""
    base = {"BTC": walk(N, 0.0006, 0.004, 3), "ETH": walk(N, 0.0006, 0.004, 4)}
    spot = FakeVenue(base, kind="spot")
    perp = FakeVenue(base, name="fake", kind="perp", has_funding=True)
    perp._funding = {"BTC": [{"ts": i * H, "rate": 0.0005} for i in range(N)]}   # 5 bps/hour paid by longs
    a = run_sweep([EmaTrend()], SRC, cfg(), adapters={"fake": spot})
    b = run_sweep([EmaTrend()], SRC, cfg(), adapters={"fake": perp})
    assert b.results[0].oos_return_pct < a.results[0].oos_return_pct - 5


def test_warmup_shortage_is_reported_and_can_skip_the_cell():
    class NeedsLongWarmup(EmaTrend):
        id, warmup_days = "long_warmup", 200
    s = run_sweep([NeedsLongWarmup()], SRC, cfg(), adapters={"fake": venue()})
    assert s.results == [] and any("warm-up" in x["reason"] for x in s.skipped)
    ok = run_sweep([EmaTrend()], SRC, cfg(), adapters={"fake": venue()})
    assert ok.data_quality[0]["warmup_days_available"] >= 89 and ok.data_quality[0]["warmup_days_requested"] == 90


def test_price_check_status_is_recorded_not_silently_passed():
    ref = venue(name="kucoin", seed=1)
    other = venue(name="other", seed=1)
    s = run_sweep([EmaTrend()], SRC, cfg(venues=["kucoin", "other"]), adapters={"kucoin": ref, "other": other})
    pc = {q["cell"].split("/")[1]: q["price_check"] for q in s.data_quality}
    assert pc["kucoin"] == "reference venue" and pc["other"].startswith("verified")
    only = run_sweep([EmaTrend()], SRC, cfg(venues=["other"]), adapters={"other": other})
    assert only.data_quality[0]["price_check"].startswith("NOT VERIFIED")


def test_daily_boundary_offset_venues_skip_1d_instead_of_mixing_day_boundaries():
    class Offset(FakeVenue):
        day_offset_ms = 8 * H
    s = run_sweep([EmaTrend()], SRC, cfg(intervals=["1d"]), adapters={"fake": Offset({"BTC": walk(N, 0, 0.01, 1)})})
    assert s.results == [] and any("day boundary" in x["reason"] for x in s.skipped)


def test_default_cli_end_is_aligned_to_utc_day(monkeypatch):
    import research.run_sweep as rs
    seen = {}

    def fake_run(strategies, source, cfg_):
        seen["end"] = cfg_.end_ms
        raise SystemExit(0)
    monkeypatch.setattr(rs, "run_sweep", fake_run)
    monkeypatch.setattr(rs, "load_strategies", lambda slug: [EmaTrend()])
    with pytest.raises(SystemExit):
        rs.main(["--slug", "x", "--title", "t", "--url", "u"])
    assert seen["end"] % 86_400_000 == 0
