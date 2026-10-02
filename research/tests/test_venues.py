import json

import httpx
import pytest

from research.lab import cache
from research.lab.venues import DYDX, REGISTRY, BitMEX, Deribit, Hyperliquid, KuCoin, NotSupported, Uniswap, VenueError

H = 3_600_000


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_CACHE_DIR", str(tmp_path))


def adapter(cls, handler):
    return cls(client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)


def test_kucoin_parses_orders_and_dedupes():
    def handler(req):
        assert req.url.params["symbol"] == "BTC-USDT" and req.url.params["type"] == "1hour"
        rows = [[str(2 * 3600), "103", "104", "105", "102", "5", "500"], [str(1 * 3600), "100", "103", "104", "99", "7", "700"],
                [str(1 * 3600), "100", "103", "104", "99", "7", "700"]]  # newest first + duplicate
        return httpx.Response(200, json={"code": "200000", "data": rows})
    out = adapter(KuCoin, handler).candles("BTC", "1h", 0, 3 * H)
    assert [c["ts"] for c in out] == [H, 2 * H]
    assert out[0] == {"ts": H, "open": 100.0, "high": 104.0, "low": 99.0, "close": 103.0, "volume": 7.0}  # kucoin order is O,C,H,L


def test_kucoin_error_and_unsupported():
    bad = adapter(KuCoin, lambda r: httpx.Response(200, json={"code": "400100", "msg": "x"}))
    with pytest.raises(VenueError):
        bad.candles("BTC", "1h", 0, H)
    with pytest.raises(NotSupported):
        adapter(KuCoin, lambda r: None).candles("DOGE2", "1h", 0, H)
    with pytest.raises(NotSupported):
        adapter(KuCoin, lambda r: None).funding("BTC", 0, H)


def test_results_are_cached_on_disk():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json={"code": "200000", "data": [[str(3600), "1", "2", "3", "0.5", "4", "5"]]})
    a = adapter(KuCoin, handler)
    a.candles("BTC", "1h", 0, 2 * H)
    n = len(calls)
    a.candles("BTC", "1h", 0, 2 * H)
    assert len(calls) == n


def test_retries_on_429_then_succeeds():
    state = {"n": 0}

    def handler(req):
        state["n"] += 1
        if state["n"] < 3:
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, json={"code": "200000", "data": []})
    assert adapter(KuCoin, handler).candles("BTC", "1h", 0, H) == []
    assert state["n"] == 3


def test_dydx_pages_backwards_and_funding():
    def handler(req):
        if "candles" in req.url.path:
            to = req.url.params["toISO"]
            if to.startswith("1970-01-01T05"):   # first page: hours 3,4 (limit 100 > rows -> stop)
                rows = [{"startedAt": "1970-01-01T04:00:00.000Z", "open": "2", "high": "3", "low": "1", "close": "2.5", "baseTokenVolume": "9"},
                        {"startedAt": "1970-01-01T03:00:00.000Z", "open": "1", "high": "2", "low": "1", "close": "2", "baseTokenVolume": "8"}]
                return httpx.Response(200, json={"candles": rows})
            return httpx.Response(200, json={"candles": []})
        return httpx.Response(200, json={"historicalFunding": [{"effectiveAt": "1970-01-01T04:00:00.000Z", "rate": "0.0001"}]})
    a = adapter(DYDX, handler)
    out = a.candles("BTC", "1h", 3 * H, 5 * H)
    assert [c["ts"] for c in out] == [3 * H, 4 * H] and out[1]["close"] == 2.5
    assert a.funding("BTC", 3 * H, 5 * H) == [{"ts": 4 * H, "rate": 0.0001}]


def test_hyperliquid_candles_and_funding():
    def handler(req):
        body = json.loads(req.content)
        if body["type"] == "candleSnapshot":
            return httpx.Response(200, json=[{"t": H, "o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": "10"}])
        return httpx.Response(200, json=[{"time": 2 * H, "fundingRate": "0.00001"}])
    a = adapter(Hyperliquid, handler)
    assert a.candles("ETH", "1h", 0, 3 * H)[0]["close"] == 1.5
    assert a.funding("ETH", 0, 3 * H)[0]["rate"] == 0.00001


def test_deribit_has_no_native_4h_and_parses_columns():
    a = adapter(Deribit, lambda r: httpx.Response(200, json={"result": {"ticks": [H], "open": [1], "high": [2], "low": [0.5], "close": [1.5], "volume": [3]}}))
    assert "4h" not in a.intervals
    with pytest.raises(NotSupported):
        a.candles("BTC", "4h", 0, H)
    assert a.candles("BTC", "1h", 0, 2 * H)[0]["high"] == 2.0


def test_bitmex_timestamps_are_shifted_to_bar_open():
    def handler(req):
        return httpx.Response(200, json=[{"timestamp": "1970-01-01T02:00:00.000Z", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 5}])
    out = adapter(BitMEX, handler).candles("BTC", "1h", 0, 4 * H)
    assert out[0]["ts"] == H  # bucket stamped at close 02:00 -> open 01:00


def test_uniswap_needs_key_and_inverts_asset_per_usd_quotes(monkeypatch):
    monkeypatch.delenv("THEGRAPH_API_KEY", raising=False)
    with pytest.raises(NotSupported):
        adapter(Uniswap, lambda r: None).candles("ETH", "1h", 0, H)
    monkeypatch.setenv("THEGRAPH_API_KEY", "k")
    rows = [{"periodStartUnix": 3600, "open": "0.0004", "high": "0.0005", "low": "0.0004", "close": "0.0004", "volumeUSD": "1"}]
    out = adapter(Uniswap, lambda r: httpx.Response(200, json={"data": {"poolHourDatas": rows}})).candles("ETH", "1h", 0, 2 * H)
    assert out[0]["close"] == pytest.approx(2500.0) and out[0]["high"] >= out[0]["low"]


def test_registry_has_the_six_research_venues():
    assert set(REGISTRY) == {"kucoin", "dydx", "hyperliquid", "deribit", "bitmex", "uniswap"}
    assert REGISTRY["uniswap"].kind == "dex" and REGISTRY["dydx"].has_funding and not REGISTRY["kucoin"].has_funding
