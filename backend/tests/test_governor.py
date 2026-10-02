import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.governor import service, store
from backend.governor.trial import TrialState, advance
from backend.main import fastapi_app
from research.lab.data import build_market_data
from research.lab.engine import Costs, simulate
from research.lab.summary import Source
from research.lab.sweep import SweepConfig, run_sweep
from research.tests.synth import EmaTrend, FakeVenue, H, walk

N = 7920
SLUG = "test"
client = TestClient(fastapi_app)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVERNOR_DB_PATH", str(tmp_path / "gov.db"))
    monkeypatch.setenv("SIGNAL_DB_PATH", str(tmp_path / "sig.db"))
    monkeypatch.setattr("backend.governor.trial.load_strategies", lambda slug: [EmaTrend()])
    monkeypatch.setattr(settings, "LIVE_API_TOKEN", "tok")
    monkeypatch.setattr(settings, "GOVERNOR_API_TOKEN", "")


def venue(drift=0.0012, vol=0.004, seed=2):
    return FakeVenue({"BTC": walk(N, drift, vol, seed), "ETH": walk(N, drift, vol, seed + 1)})


def summary(drift=0.0012, seed=2):
    v = venue(drift, seed=seed)
    s = run_sweep([EmaTrend()], Source(slug=SLUG, title="t", url="https://x.invalid"),
                  SweepConfig(venues=["fake"], assets=["BTC"], intervals=["1h"], end_ms=N * H), adapters={"fake": v})
    return v, json.loads(s.model_dump_json())


def full_md(v, n_back=800):
    return build_market_data(v, ["BTC"], "1h", (N - n_back) * H, N * H)


def test_replay_trial_matches_the_backtest_engine_exactly():
    v, data = summary()
    service.ingest_summary(data)
    rec = data["recommended_trial"]
    md = full_md(v)
    first = int(md.ts[-1]) - 72 * H                       # trial starts 72 bars before the end
    tid = service.start_trial(SLUG, "replay", 72, now_ms=first + H, first_bar_ts=first)
    service.run_replay(tid, md)
    st = TrialState.from_dict(store.get_trial(tid)["state"])
    # engine on identical weights, flat before the first trial bar
    strat = EmaTrend()
    W = strat.weights(md, rec["params"])
    W[: int(np.searchsorted(md.ts, first))] = 0.0
    sim = simulate(md, W, Costs(rec["costs_bps"]["fee"], rec["costs_bps"]["slippage"]))
    i0 = int(np.searchsorted(md.ts, first))
    expect = sim.returns[i0 + 1: i0 + 1 + st.bars]
    assert st.bars == 72 and np.allclose(st.returns, expect, atol=1e-12)
    assert st.equity == pytest.approx(10_000 * np.prod(1 + expect))


def test_flow_integrates_or_shelves_and_requires_approval():
    v, data = summary()
    service.ingest_summary(data)
    md = full_md(v)
    first = int(md.ts[-1]) - 72 * H
    tid = service.start_trial(SLUG, "replay", 72, now_ms=first + H, first_bar_ts=first)
    out = service.run_replay(tid, md)
    t = store.get_trial(tid)
    assert t["status"] == "FINISHED" and t["decision"] == out["recommendation"]
    names = [c["name"] for c in t["report"]["checks"]]
    assert {"backtest viable", "trial integrity", "drawdown within limit", "return not below backtest p10", "market regime favorable"} <= set(names)
    if t["decision"] == "INTEGRATE":
        assert t["approval"] == "pending" and store.list_integrated() == []   # nothing is integrated without a human
        service.approve(tid)
        assert store.list_integrated()[0]["slug"] == SLUG
    else:
        assert t["approval"] is None and store.list_watch()[0]["slug"] == SLUG and t["report"]["revisit"]


def test_non_viable_backtest_is_always_shelved_and_cannot_be_approved():
    v, data = summary(drift=0.0, seed=5)
    assert data["viable"] is False
    service.ingest_summary(data)
    md = full_md(v)
    first = int(md.ts[-1]) - 72 * H
    tid = service.start_trial(SLUG, "replay", 72, now_ms=first + H, first_bar_ts=first)
    service.run_replay(tid, md)
    t = store.get_trial(tid)
    assert t["decision"] == "SHELVE"
    with pytest.raises(service.GovernorError):
        service.approve(tid)
    with pytest.raises(service.GovernorError):
        service.reject(tid)


def test_live_tick_path_with_fake_venue_runs_to_completion():
    v, data = summary()
    service.ingest_summary(data)
    first = (N - 100) * H
    tid = service.start_trial(SLUG, "live", 72, now_ms=(N - 99) * H, first_bar_ts=first)
    r1 = service.tick_trial(tid, now_ms=(N - 60) * H, adapter=v)
    assert r1["status"] == "RUNNING" and r1["bars"] > 0
    r2 = service.tick_trial(tid, now_ms=N * H, adapter=v)           # past end_ts -> finalize
    assert r2["status"] == "FINISHED"
    assert store.get_trial(tid)["state"]["bars"] >= 70
    assert service.tick_trial(tid, now_ms=N * H, adapter=v)["status"] == "FINISHED"  # idempotent


def test_data_outage_is_recorded_and_hurts_integrity_not_silently_ignored():
    v, data = summary()
    service.ingest_summary(data)
    first = (N - 100) * H
    tid = service.start_trial(SLUG, "live", 72, now_ms=(N - 99) * H, first_bar_ts=first)

    class Down(FakeVenue):
        def candles(self, *a, **k):
            raise RuntimeError("venue down")
    service.tick_trial(tid, now_ms=(N - 10) * H, adapter=Down({"BTC": [1.0]}))
    st = store.get_trial(tid)["state"]
    assert st["errors"] and "fetch failed" in st["errors"][0]
    service.tick_trial(tid, now_ms=N * H, adapter=Down({"BTC": [1.0]}))
    t = store.get_trial(tid)
    integrity = next(c for c in t["report"]["checks"] if c["name"] == "trial integrity")
    assert integrity["ok"] is False and t["decision"] == "SHELVE"


def test_broken_strategy_decision_goes_flat_and_is_logged():
    class Boom(EmaTrend):
        def target_weights(self, md_window, params):
            raise RuntimeError("bad signal")
    v = venue()
    md = full_md(v, 300)
    st = TrialState()
    advance(Boom(), {"fast": 5, "slow": 30}, Costs(0, 0), md, st)
    assert st.errors and all(w == 0 for w in st.w1) and st.equity == 10_000


def test_short_weights_on_spot_are_rejected_in_trial():
    class Shorty(EmaTrend):
        def target_weights(self, md_window, params):
            return np.array([-1.0])
    st = TrialState()
    advance(Shorty(), {}, Costs(0, 0), full_md(venue(), 100), st)
    assert st.errors and "short" in st.errors[0]


def test_watchlist_flags_ready_after_two_favorable_checks(monkeypatch):
    store.upsert_watch("s1", "ema_trend", ["lowvol-up"], "shelved", {"venue": "fake", "asset": "BTC"})
    seq = iter(["lowvol-up", "lowvol-up"])
    monkeypatch.setattr("backend.governor.service.current_regime", lambda *a, **k: next(seq))
    assert service.check_watchlist(1, adapter_factory=lambda v: None)[0]["status"] == "SHELVED"
    assert service.check_watchlist(2, adapter_factory=lambda v: None)[0]["status"] == "READY_FOR_RETRIAL"


def test_api_requires_token_and_ingests_summary():
    _, data = summary()
    assert client.post("/api/governor/summaries", json=data).status_code == 401
    r = client.post("/api/governor/summaries", json=data, headers={"X-Live-Token": "tok"})
    assert r.status_code == 200 and r.json()["slug"] == SLUG
    assert client.post("/api/governor/summaries", json={"bad": 1}, headers={"X-Live-Token": "tok"}).status_code == 422
    assert client.get("/api/governor/overview").json()["summaries"][0]["slug"] == SLUG
    assert client.post("/api/governor/trials/1/approve").status_code == 401
    assert client.post("/api/governor/trials/999/approve", headers={"X-Live-Token": "tok"}).status_code == 409


def test_api_writes_disabled_without_any_token(monkeypatch):
    monkeypatch.setattr(settings, "LIVE_API_TOKEN", "")
    assert client.post("/api/governor/summaries", json={}, headers={"X-Live-Token": "x"}).status_code == 403
