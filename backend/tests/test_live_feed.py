import time

from backend.marketdata.buffers import SymbolBuffers
from backend.marketdata.models import StreamHealth, Trade
from backend.marketdata.orderbook import LocalOrderBook
from backend.signals.live_feed import build_live_metrics, compute_live_snapshot


def _trade(i, price, side="BUY", ts=None, qty=0.5):
    return Trade(symbol="BTCUSDT", agg_id=i, price=price, qty=qty, quote_qty=price * qty,
                 ts=ts or int(time.time() * 1000), is_buyer_maker=(side == "SELL"), side=side, first_id=i, last_id=i)


def _state(status="LIVE", trades=True, book=True, age_ms=0):
    b = LocalOrderBook("BTCUSDT", 0.01)
    if book:
        b.apply_partial({"bids": [["100.00", "5"], ["99.99", "3"]], "asks": [["100.01", "2"], ["100.02", "1"]]})
    buf = SymbolBuffers("BTCUSDT")
    now = int(time.time() * 1000) - age_ms
    if trades:
        for i in range(10):
            buf.add_trade(_trade(i, 100.0, "BUY" if i % 3 else "SELL", ts=now - (10 - i) * 100))
    h = StreamHealth(symbol="BTCUSDT", connected=status == "LIVE", status=status, last_trade_ts=now)
    return {"book": b, "buffers": buf, "health": h}


def test_none_state_is_not_live():
    snap = compute_live_snapshot(None, "BTCUSDT")
    assert snap["live"] is False and "not subscribed" in snap["reason"]


def test_non_live_stream_never_returns_numbers():
    snap = compute_live_snapshot(_state(status="DISCONNECTED"), "BTCUSDT")
    assert snap["live"] is False and "price" not in snap and snap["health"]["status"] == "DISCONNECTED"


def test_no_trades_or_empty_book_or_stale_trades_are_not_live():
    assert build_live_metrics(_state(trades=False))[0] is None
    assert build_live_metrics(_state(book=False))[0] is None
    m, reason = build_live_metrics(_state(age_ms=60_000))
    assert m is None and "old" in reason


def test_live_snapshot_uses_real_buffered_values():
    snap = compute_live_snapshot(_state(), "BTCUSDT")
    assert snap["live"] is True
    assert snap["price"] == 100.0  # last received trade, not a fallback
    f = snap["features"]
    assert f["spread_bps"] == __import__("pytest").approx(1.0, abs=0.1)  # (100.01-100.00)/mid
    assert f["book_imbalance"] > 0  # 8 bid vs 3 ask
    assert snap["cvd_scope"] == "buffered_trades"
    assert snap["confluence"]["veto"] is True  # fewer than 20 closed bars -> honest veto, not a signal
    assert snap["confluence"]["score"] == 0.0
