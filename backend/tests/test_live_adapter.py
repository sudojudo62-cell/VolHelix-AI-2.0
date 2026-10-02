import pytest

from backend.config import settings
from backend.exchange import audit, guard
from backend.exchange.live_binance import LiveBinanceAdapter, LiveOrderError

CONFIRM = guard.CONFIRM_PHRASE


class FakeClient:
    def __init__(self, usdt=1000.0, price=100.0, can_trade=True, oco_fails=False, flatten_fails=False, btc=0.0):
        self.usdt, self.price, self.can_trade = usdt, price, can_trade
        self.oco_fails, self.flatten_fails, self.btc = oco_fails, flatten_fails, btc
        self.calls = []

    def get_symbol_ticker(self, symbol):
        return {"price": str(self.price)}

    def get_all_tickers(self):
        return [{"symbol": "BTCUSDT", "price": str(self.price)}]

    def get_account(self):
        b = [{"asset": "USDT", "free": str(self.usdt), "locked": "0"}]
        if self.btc:
            b.append({"asset": "BTC", "free": str(self.btc), "locked": "0"})
        return {"canTrade": self.can_trade, "balances": b}

    def get_asset_balance(self, asset):
        return {"asset": asset, "free": str(self.btc), "locked": "0"}

    def get_symbol_info(self, symbol):
        return {"filters": [{"filterType": "LOT_SIZE", "stepSize": "0.001"}, {"filterType": "PRICE_FILTER", "tickSize": "0.01"}]}

    def create_test_order(self, **p):
        self.calls.append(("test", p))

    def create_order(self, **p):
        self.calls.append(("order", p))
        if p["side"] == "SELL" and self.flatten_fails:
            raise RuntimeError("flatten rejected")
        qty = p.get("quantity") or round(p["quoteOrderQty"] / self.price, 6)
        return {"orderId": 1, "status": "FILLED", "executedQty": str(qty), "side": p["side"]}

    def create_oco_order(self, **p):
        self.calls.append(("oco", p))
        if self.oco_fails:
            raise RuntimeError("oco unsupported")
        return {"orderListId": 7}

    def cancel_all_open_orders(self, **p):
        self.calls.append(("cancel", p))


def kinds(c):
    return [k for k, _ in c.calls]


@pytest.fixture(autouse=True)
def live_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_DB_PATH", str(tmp_path / "live.db"))
    for k, v in dict(LIVE_BINANCE_API_KEY="k", LIVE_BINANCE_API_SECRET="s", LIVE_TRADING_ENABLED=True, LIVE_ORDER_MODE="test",
                     LIVE_MAX_ORDER_USDT=25.0, LIVE_MAX_DAILY_NOTIONAL_USDT=100.0, LIVE_SYMBOL_ALLOWLIST_STR="").items():
        monkeypatch.setattr(settings, k, v)


def adapter(client):
    return LiveBinanceAdapter(client=client)


def entry(a, **kw):
    args = dict(symbol="BTCUSDT", quote_qty=20.0, stop_loss=98.0, take_profit=104.0)
    args.update(kw)
    return a.submit_entry(**args)


# ── guard ───────────────────────────────────────────────────────────────────
def test_disabled_is_read_only(monkeypatch):
    monkeypatch.setattr(settings, "LIVE_TRADING_ENABLED", False)
    c = FakeClient()
    with pytest.raises(LiveOrderError) as e:
        entry(adapter(c))
    assert e.value.code == "trading_disabled" and c.calls == []


def test_missing_keys_blocks(monkeypatch):
    monkeypatch.setattr(settings, "LIVE_BINANCE_API_KEY", "")
    with pytest.raises(LiveOrderError) as e:
        entry(adapter(FakeClient()))
    assert e.value.code == "no_keys"


def test_kill_switch_blocks_entries_but_not_close():
    guard.set_kill_switch(True)
    c = FakeClient(btc=0.5)
    with pytest.raises(LiveOrderError) as e:
        entry(adapter(c))
    assert e.value.code == "kill_switch"
    res = adapter(c).close_position("BTCUSDT")  # test mode: validated, allowed despite kill switch
    assert res["status"] == "TEST_OK"


@pytest.mark.parametrize("kw,code", [
    (dict(symbol="DOGEUSDT"), "symbol_not_allowed"),
    (dict(quote_qty=5.0), "below_min_notional"),
    (dict(quote_qty=26.0), "over_order_cap"),
])
def test_guard_limits(kw, code):
    c = FakeClient()
    with pytest.raises(LiveOrderError) as e:
        entry(adapter(c), **kw)
    assert e.value.code == code and c.calls == []


def test_live_mode_requires_confirmation_phrase(monkeypatch):
    monkeypatch.setattr(settings, "LIVE_ORDER_MODE", "live")
    c = FakeClient()
    with pytest.raises(LiveOrderError) as e:
        entry(adapter(c))
    assert e.value.code == "confirmation_required" and c.calls == []
    with pytest.raises(LiveOrderError):
        entry(adapter(c), confirm="yes")


def test_daily_notional_cap(monkeypatch):
    monkeypatch.setattr(settings, "LIVE_ORDER_MODE", "live")
    monkeypatch.setattr(settings, "LIVE_MAX_DAILY_NOTIONAL_USDT", 30.0)
    c = FakeClient()
    entry(adapter(c), confirm=CONFIRM)  # $20 used
    with pytest.raises(LiveOrderError) as e:
        entry(adapter(c), confirm=CONFIRM)  # +$20 > $30
    assert e.value.code == "over_daily_cap"


# ── test mode ───────────────────────────────────────────────────────────────
def test_test_mode_validates_but_never_executes():
    c = FakeClient()
    res = entry(adapter(c))
    assert res["status"] == "TEST_OK" and res["executed"] is False
    assert kinds(c) == ["test"]
    assert audit.recent_orders()[0]["status"] == "TEST_OK"


# ── live mode ───────────────────────────────────────────────────────────────
def test_live_fill_places_oco_with_exchange_rounding(monkeypatch):
    monkeypatch.setattr(settings, "LIVE_ORDER_MODE", "live")
    c = FakeClient(price=100.0)
    res = entry(adapter(c), confirm=CONFIRM)
    assert res["executed"] is True and res["protective_exit"]["status"] == "PROTECTED"
    oco = dict(c.calls)["oco"]
    assert oco["side"] == "SELL" and oco["quantity"] == 0.2 and oco["price"] == "104.0" and oco["stopPrice"] == "98.0"
    assert float(oco["stopLimitPrice"]) < 98.0
    assert audit.executed_notional_today() == pytest.approx(20.0)


def test_oco_failure_flattens_position(monkeypatch):
    monkeypatch.setattr(settings, "LIVE_ORDER_MODE", "live")
    c = FakeClient(oco_fails=True)
    res = entry(adapter(c), confirm=CONFIRM)
    assert res["protective_exit"]["status"] == "FLATTENED"
    assert kinds(c) == ["order", "oco", "order"] and c.calls[-1][1]["side"] == "SELL"
    assert audit.recent_orders()[0]["status"] == "FLATTENED"


def test_oco_and_flatten_failure_is_flagged_unprotected(monkeypatch):
    monkeypatch.setattr(settings, "LIVE_ORDER_MODE", "live")
    c = FakeClient(oco_fails=True, flatten_fails=True)
    res = entry(adapter(c), confirm=CONFIRM)
    assert res["protective_exit"]["status"] == "UNPROTECTED"
    assert "MANUAL ACTION" in audit.recent_orders()[0]["reason"]


# ── pre-trade checks (no order may be sent) ─────────────────────────────────
@pytest.mark.parametrize("client_kw,call_kw,code", [
    (dict(usdt=0.0), {}, "no_equity"),
    (dict(usdt=10.0), {}, "insufficient_usdt"),
    (dict(can_trade=False), {}, "account_cannot_trade"),
    ({}, dict(stop_loss=101.0), "bad_levels"),
    ({}, dict(take_profit=101.0), "bad_reward_risk"),
    (dict(usdt=500.0), {}, "risk_gate_rejected"),  # $20 > 2.5% of $500 NAV
])
def test_prechecks_block_without_sending_orders(client_kw, call_kw, code):
    c = FakeClient(**client_kw)
    with pytest.raises(LiveOrderError) as e:
        entry(adapter(c), **call_kw)
    assert e.value.code == code
    assert not {"test", "order", "oco"} & set(kinds(c))
    assert audit.recent_orders()[0]["status"] in ("REJECTED", "ERROR")


def test_duplicate_client_order_id_is_rejected():
    c = FakeClient()
    entry(adapter(c), client_order_id="abc-1")
    with pytest.raises(LiveOrderError) as e:
        entry(adapter(c), client_order_id="abc-1")
    assert e.value.code == "duplicate_order"


def test_rejected_attempts_are_audited():
    with pytest.raises(LiveOrderError):
        entry(adapter(FakeClient()), quote_qty=5.0)
    rows = audit.recent_orders()
    assert rows and rows[0]["status"] == "REJECTED" and "below_min_notional" in rows[0]["reason"]


# ── closing ─────────────────────────────────────────────────────────────────
def test_close_live_cancels_orders_then_sells_floor_qty(monkeypatch):
    monkeypatch.setattr(settings, "LIVE_ORDER_MODE", "live")
    c = FakeClient(btc=0.12349)
    res = adapter(c).close_position("BTCUSDT", confirm=CONFIRM)
    assert res["executed"] is True
    assert kinds(c) == ["cancel", "order"] and c.calls[1][1]["quantity"] == 0.123 and c.calls[1][1]["side"] == "SELL"


def test_close_with_nothing_to_sell_is_noop():
    res = adapter(FakeClient(btc=0.0)).close_position("BTCUSDT")
    assert res["status"] == "NOOP"
