import json
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.governor import service, store
from backend.main import fastapi_app
from research.scanner import build_snapshot, write_snapshot
from research.tests.synth import FakeVenue, H, panel_series

client = TestClient(fastapi_app)
HDR = {"X-Live-Token": "tok"}
N = 3000


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVERNOR_DB_PATH", str(tmp_path / "gov.db"))
    monkeypatch.setenv("SCANNER_SNAPSHOT_PATH", str(tmp_path / "snap.json"))
    monkeypatch.setattr(settings, "LIVE_API_TOKEN", "tok")
    monkeypatch.setattr(settings, "GOVERNOR_API_TOKEN", "")


def snap(planted=0.5):
    v = FakeVenue(panel_series(N, 12, planted=planted, seed=5))
    return build_snapshot(v, list(v.series), "1h", 24, 3, train_days=60, now_ms=N * H, health_days=7, n_boot=30)


def test_snapshot_shape_and_ranking_is_sorted():
    s = snap()
    assert s["available"] and s["n_assets"] == 12 and len(s["ranking"]) == 12
    scores = [r["score"] for r in s["ranking"]]
    assert scores == sorted(scores, reverse=True)
    assert sum(r["in_top_k"] for r in s["ranking"]) == 3
    assert s["health"]["status"] in {"ok", "degraded", "insufficient_data"}
    assert s["as_of_bar_open"] < N * H


def test_snapshot_unavailable_with_too_little_history():
    v = FakeVenue(panel_series(400, 12, seed=1))
    try:
        s = build_snapshot(v, list(v.series), "1h", 24, 3, train_days=90, now_ms=400 * H)
        assert s["available"] is False
    except Exception as exc:                                  # a data-quality refusal is also an honest outcome
        assert "data" in type(exc).__name__.lower() or "history" in str(exc).lower() or "asset" in str(exc).lower()


def test_latest_without_snapshot_and_with_snapshot():
    r = client.get("/api/scanner/latest").json()
    assert r["available"] is False and r["paper_only"] is True and r["live_authorized"] is False
    write_snapshot(snap())
    r = client.get("/api/scanner/latest").json()
    assert r["available"] is True and r["paper_only"] is True and "age_ms" in r


def test_ticket_needs_token_and_authorization():
    assert client.get("/api/scanner/ticket?asset=A00").status_code == 401
    assert client.get("/api/scanner/ticket?asset=A00", headers=HDR).status_code == 403


def _authorize(monkeypatch):
    store.set_live_authorization("pair_ranker", "pair_ranker_long", 1, 25.0)


def _fresh_snap(health="ok", flags=None):
    s = snap()
    s["generated_at"] = int(time.time() * 1000)
    s["health"]["status"] = health
    for r in s["ranking"]:
        r["flags"] = list(flags or [])
        r["top_k_probability"] = 0.9
    write_snapshot(s)
    return s


def test_ticket_issued_only_for_confident_top_k_when_healthy(monkeypatch):
    _authorize(monkeypatch)
    s = _fresh_snap()
    top, bottom = s["ranking"][0]["asset"], s["ranking"][-1]["asset"]
    t = client.get(f"/api/scanner/ticket?asset={top}", headers=HDR)
    assert t.status_code == 200
    j = t.json()
    assert j["side"] == "BUY" and j["suggested_stop"] < j["reference_price"] < j["suggested_target"] and j["max_order_usdt"] == 25.0
    assert client.get(f"/api/scanner/ticket?asset={bottom}", headers=HDR).status_code == 409
    assert client.get("/api/scanner/ticket?asset=NOPE", headers=HDR).status_code == 404


def test_ticket_refused_when_model_degraded_or_low_confidence_or_stale(monkeypatch):
    _authorize(monkeypatch)
    s = _fresh_snap(health="degraded")
    top = s["ranking"][0]["asset"]
    assert client.get(f"/api/scanner/ticket?asset={top}", headers=HDR).status_code == 409
    _fresh_snap(flags=["low_confidence"])
    assert client.get(f"/api/scanner/ticket?asset={top}", headers=HDR).status_code == 409
    s = _fresh_snap()
    s["generated_at"] -= 4 * 3_600_000
    write_snapshot(s)
    assert client.get(f"/api/scanner/ticket?asset={top}", headers=HDR).status_code == 409


def test_revoke_removes_ticket_access(monkeypatch):
    _authorize(monkeypatch)
    s = _fresh_snap()
    top = s["ranking"][0]["asset"]
    assert client.get(f"/api/scanner/ticket?asset={top}", headers=HDR).status_code == 200
    assert client.post("/api/governor/live-authorization/pair_ranker/revoke", headers=HDR).json()["revoked"] is True
    assert client.get(f"/api/scanner/ticket?asset={top}", headers=HDR).status_code == 403


def _trial(**kw):
    base = dict(id=1, slug="pair_ranker", strategy_id="pair_ranker_long", mode="live", status="FINISHED", decision="INTEGRATE",
                approval="approved", start_ts=0, end_ts=72 * 3_600_000)
    base.update(kw)
    return base


@pytest.mark.parametrize("over,err", [
    (dict(mode="replay"), "replay"), (dict(status="RUNNING"), "FINISHED"), (dict(decision="SHELVE"), "FINISHED"),
    (dict(approval="pending"), "FINISHED"), (dict(end_ts=48 * 3_600_000), "72h"),
])
def test_authorize_live_gates(monkeypatch, over, err):
    monkeypatch.setattr(store, "get_trial", lambda i: _trial(**over))
    with pytest.raises(service.GovernorError, match=err):
        service.authorize_live(1, service.AUTHORIZE_PHRASE, 10.0)


def test_authorize_live_phrase_cap_and_success(monkeypatch):
    monkeypatch.setattr(store, "get_trial", lambda i: _trial())
    with pytest.raises(service.GovernorError, match="confirm"):
        service.authorize_live(1, "yes", 10.0)
    with pytest.raises(service.GovernorError, match="max_order_usdt"):
        service.authorize_live(1, service.AUTHORIZE_PHRASE, settings.LIVE_MAX_ORDER_USDT + 1)
    with pytest.raises(service.GovernorError, match="max_order_usdt"):
        service.authorize_live(1, service.AUTHORIZE_PHRASE, 0)
    assert service.authorize_live(1, service.AUTHORIZE_PHRASE, 10.0)["authorized"] is True
    assert store.get_live_authorization("pair_ranker")["max_order_usdt"] == 10.0
    assert service.revoke_live("pair_ranker")["revoked"] is True and store.get_live_authorization("pair_ranker") is None


def test_authorize_route_requires_token_and_maps_errors():
    body = {"confirm": "x", "max_order_usdt": 5}
    assert client.post("/api/governor/trials/1/authorize-live", json=body).status_code == 401
    assert client.post("/api/governor/trials/1/authorize-live", json=body, headers=HDR).status_code == 409
    assert client.get("/api/governor/live-authorization").json() == {"authorizations": []}
