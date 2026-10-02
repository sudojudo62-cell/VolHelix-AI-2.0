import pytest
from fastapi.testclient import TestClient

from backend.api import live_routes
from backend.config import settings
from backend.exchange import guard
from backend.exchange.live_binance import LiveBinanceAdapter
from backend.main import fastapi_app
from backend.tests.test_live_adapter import FakeClient

client = TestClient(fastapi_app)
TOKEN = {"X-Live-Token": "secret-token"}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_DB_PATH", str(tmp_path / "live.db"))
    for k, v in dict(LIVE_BINANCE_API_KEY="k", LIVE_BINANCE_API_SECRET="s", LIVE_TRADING_ENABLED=True, LIVE_ORDER_MODE="test",
                     LIVE_API_TOKEN="secret-token", LIVE_SYMBOL_ALLOWLIST_STR="").items():
        monkeypatch.setattr(settings, k, v)
    monkeypatch.setattr(live_routes, "_adapter", LiveBinanceAdapter(client=FakeClient()))
    live_routes._snap_cache.update(ts=0.0, data=None)


BODY = {"symbol": "BTCUSDT", "quote_qty": 20.0, "stop_loss": 98.0, "take_profit": 104.0}
GATED = [("get", "/api/live/account", None), ("get", "/api/live/orders", None),
         ("post", "/api/live/kill-switch", {"engaged": True}), ("post", "/api/live/order", BODY),
         ("post", "/api/live/close", {"symbol": "BTCUSDT"})]


@pytest.mark.parametrize("method,url,body", GATED)
def test_gated_endpoints_refused_when_token_unconfigured(monkeypatch, method, url, body):
    monkeypatch.setattr(settings, "LIVE_API_TOKEN", "")
    r = getattr(client, method)(url, **({"json": body} if body else {}), headers=TOKEN)
    assert r.status_code == 403


@pytest.mark.parametrize("method,url,body", GATED)
def test_gated_endpoints_require_correct_token(method, url, body):
    kw = {"json": body} if body else {}
    assert getattr(client, method)(url, **kw).status_code == 401
    assert getattr(client, method)(url, **kw, headers={"X-Live-Token": "wrong"}).status_code == 401


def test_status_is_public_and_never_leaks_secrets():
    r = client.get("/api/live/status")
    assert r.status_code == 200
    j = r.json()
    assert j["mode"] == "test" and j["keys_configured"] is True and j["token_configured"] is True
    text = r.text
    assert "secret-token" not in text and '"k"' not in text


def test_order_in_test_mode_and_audit_trail():
    r = client.post("/api/live/order", json=BODY, headers=TOKEN)
    assert r.status_code == 200 and r.json()["status"] == "TEST_OK" and r.json()["executed"] is False
    orders = client.get("/api/live/orders", headers=TOKEN).json()["orders"]
    assert orders[0]["status"] == "TEST_OK"


def test_guard_rejection_maps_to_422_with_code():
    r = client.post("/api/live/order", json={**BODY, "quote_qty": 999.0}, headers=TOKEN)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "over_order_cap"


def test_invalid_payloads_rejected():
    assert client.post("/api/live/order", json={**BODY, "quote_qty": -1}, headers=TOKEN).status_code == 422
    assert client.post("/api/live/order", json={**BODY, "client_order_id": "bad id!"}, headers=TOKEN).status_code == 422


def test_kill_switch_roundtrip_blocks_orders():
    assert client.post("/api/live/kill-switch", json={"engaged": True}, headers=TOKEN).json()["kill_switch"] is True
    r = client.post("/api/live/order", json=BODY, headers=TOKEN)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "kill_switch"
    client.post("/api/live/kill-switch", json={"engaged": False}, headers=TOKEN)
    assert guard.kill_switch_engaged() is False


def test_account_endpoint_returns_real_shape():
    j = client.get("/api/live/account", headers=TOKEN).json()
    assert j["equity"] == 1000.0 and j["usdt_free"] == 1000.0


def test_snapshots_with_no_streams_returns_empty_not_fabricated(monkeypatch):
    monkeypatch.setattr(live_routes.hub, "books", {})
    assert client.get("/api/live/snapshots").json()["snapshots"] == []


def test_signals_and_calibration_endpoints_public():
    assert client.get("/api/live/signals").status_code == 200
    assert client.get("/api/live/signals/calibration?horizon=4h").status_code == 200
    assert client.get("/api/live/signals/calibration?horizon=9d").status_code == 400
