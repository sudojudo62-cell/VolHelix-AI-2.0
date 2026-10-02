import json

import httpx
import pytest

from research.lab import cache
from research.lab.venues import DYDX, REGISTRY, BitMEX, Bitfinex, Deribit, Hyperliquid, KuCoin, NotSupported, Uniswap, VenueError, aggregate_candles

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
        adapter(Deribit, lambda r: None).candles("DOGE2", "1h", 0, H)       # strict map: unknown asset is unsupported
    assert KuCoin(sleep=lambda s: None).venue_symbol("PEPE") == "PEPE-USDT"   # wide universes use the default symbol format
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


def test_deribit_has_no_native_4h_but_aggregates_and_parses_columns():
    a = adapter(Deribit, lambda r: httpx.Response(200, json={"result": {"ticks": [H], "open": [1], "high": [2], "low": [0.5], "close": [1.5], "volume": [3]}}))
    assert "4h" not in a.intervals and a.supports("4h")
    with pytest.raises(NotSupported):
        a.candles("BTC", "3m", 0, H)
    assert a.candles("BTC", "1h", 0, 2 * H)[0]["high"] == 2.0


def test_bitmex_timestamps_are_shifted_to_bar_open():
    def handler(req):
        return httpx.Response(200, json=[{"timestamp": "1970-01-01T02:00:00.000Z", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 5}])
    out = adapter(BitMEX, handler_with_state(handler)).candles("BTC", "1h", 0, 4 * H)
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
    assert set(REGISTRY) == {"kucoin", "dydx", "hyperliquid", "deribit", "bitfinex", "bitmex", "uniswap"}
    assert REGISTRY["uniswap"].kind == "dex" and REGISTRY["dydx"].has_funding and not REGISTRY["kucoin"].has_funding


def handler_with_state(inner, state="Open"):
    def h(req):
        if req.url.path.endswith("/instrument"):
            return httpx.Response(200, json=[{"symbol": req.url.params["symbol"], "state": state, "expiry": "2026-09-16T12:00:00.000Z"}])
        return inner(req)
    return h


def test_bitmex_settled_contract_is_refused_with_a_clear_reason():
    a = adapter(BitMEX, handler_with_state(lambda r: httpx.Response(200, json=[]), state="Settled"))
    with pytest.raises(NotSupported, match="not an active contract.*Settled"):
        a.candles("BTC", "1h", 0, 4 * H)


def test_bitmex_open_outside_range_is_widened_to_valid_ohlc():
    rows = [{"timestamp": "1970-01-01T02:00:00.000Z", "open": 10, "high": 9, "low": 8, "close": 9.5, "volume": 1}]  # open above high
    out = adapter(BitMEX, handler_with_state(lambda r: httpx.Response(200, json=rows))).candles("BTC", "1h", 0, 4 * H)
    assert out[0]["high"] == 10 and out[0]["low"] == 8


def test_bitfinex_column_order_is_mts_open_close_high_low():
    rows = [[H, 100, 105, 110, 95, 7]]
    out = adapter(Bitfinex, lambda r: httpx.Response(200, json=rows)).candles("BTC", "1h", 0, 3 * H)
    assert out[0] == {"ts": H, "open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0, "volume": 7.0}


def test_missing_native_intervals_are_aggregated_from_1h():
    def handler(req):
        rows = [{"t": i * H, "o": str(i + 1), "h": str(i + 2), "l": str(i), "c": str(i + 1.5), "v": "1"} for i in range(8)]
        return httpx.Response(200, json=rows)
    a = adapter(Deribit, lambda r: httpx.Response(200, json={"result": {"ticks": [i * H for i in range(8)], "open": [i + 1 for i in range(8)], "high": [i + 2 for i in range(8)],
                                                                 "low": [i for i in range(8)], "close": [i + 1.5 for i in range(8)], "volume": [1] * 8}}))
    assert a.supports("4h") and "4h" not in a.intervals
    out = a.candles("BTC", "4h", 0, 8 * H)
    assert [c["ts"] for c in out] == [0, 4 * H]
    assert out[0]["open"] == 1 and out[0]["close"] == 4.5 and out[0]["high"] == 5 and out[0]["low"] == 0 and out[0]["volume"] == 4


def test_aggregation_drops_incomplete_buckets():
    rows = [{"ts": i * H, "open": 1, "high": 2, "low": 0.5, "close": 1, "volume": 1} for i in range(1, 7)]  # hours 1..6: bucket 0 partial, 4h bucket 1 partial
    assert aggregate_candles(rows, 4 * H) == []


def test_deribit_declares_its_8h_daily_offset_and_hyperliquid_its_history_cap():
    assert Deribit.day_offset_ms == 8 * H and Hyperliquid.max_history_candles == 5000 and KuCoin.day_offset_ms == 0
