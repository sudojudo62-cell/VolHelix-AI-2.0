import math
import random

from backend.config import settings
from backend.quant import overlay


class FakeClient:
    def __init__(self, closes):
        self.closes = closes

    def get_klines(self, symbol, interval="1h", limit=100):
        bars, prev = [], self.closes[0]
        for c in self.closes[-limit:]:
            bars.append({"open": prev, "high": max(prev, c) * 1.002, "low": min(prev, c) * 0.998, "close": c})
            prev = c
        return bars


class BoomClient:
    def get_klines(self, *a, **k):
        raise RuntimeError("network down")


def _cand(sym="BTCUSDT"):
    return {"symbol": sym, "eval": {"score": 0.8}, "current_price": 100.0}


def _flags(monkeypatch, **kw):
    for k, v in kw.items():
        monkeypatch.setattr(settings, k, v)


def test_filter_is_noop_when_disabled(monkeypatch):
    _flags(monkeypatch, TREND_FILTER_ENABLED=False)
    cands = [_cand()]
    assert overlay.filter_candidates(BoomClient(), cands, {}) == cands


def test_filter_keeps_uptrend_and_vetoes_downtrend(monkeypatch):
    _flags(monkeypatch, TREND_FILTER_ENABLED=True)
    up = FakeClient([100 + i for i in range(300)])
    down = FakeClient([400 - i for i in range(300)])
    diag = {}
    assert len(overlay.filter_candidates(up, [_cand()], diag)) == 1
    assert overlay.filter_candidates(down, [_cand("ETHUSDT")], diag) == []
    assert diag["ETHUSDT"]["status_label"] == "TREND FILTER VETO"


def test_filter_fails_closed_on_data_error(monkeypatch):
    _flags(monkeypatch, TREND_FILTER_ENABLED=True)
    assert overlay.filter_candidates(BoomClient(), [_cand()], {}) == []


def test_adjust_order_never_increases_size_and_updates_levels(monkeypatch):
    _flags(monkeypatch, VOL_TARGET_ENABLED=True, ATR_LEVELS_ENABLED=True)
    rng = random.Random(2)
    px, closes = 100.0, []
    for _ in range(300):
        px *= math.exp(0.07 * rng.gauss(0, 1))
        closes.append(px)
    levels = {"stop_loss_price": 1.0, "take_profit_price": 2.0, "stop_loss_pct": 1.0, "take_profit_pct": 2.0}
    qty, new = overlay.adjust_order(FakeClient(closes), "BTCUSDT", closes[-1], levels, 200.0)
    assert 0 < qty <= 200.0
    assert new["stop_loss_price"] < closes[-1] < new["take_profit_price"]
    assert new["risk_reward_ratio"] >= 1.5


def test_adjust_order_fails_open_to_original_values(monkeypatch):
    _flags(monkeypatch, VOL_TARGET_ENABLED=True, ATR_LEVELS_ENABLED=True)
    levels = {"stop_loss_price": 1.0, "take_profit_price": 2.0}
    assert overlay.adjust_order(BoomClient(), "BTCUSDT", 100.0, levels, 200.0) == (200.0, levels)


def test_adjust_order_noop_when_disabled(monkeypatch):
    _flags(monkeypatch, VOL_TARGET_ENABLED=False, ATR_LEVELS_ENABLED=False)
    levels = {"x": 1}
    assert overlay.adjust_order(BoomClient(), "BTCUSDT", 100.0, levels, 200.0) == (200.0, levels)
