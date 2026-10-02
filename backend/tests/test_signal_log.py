import pytest

from backend.signals import signal_log as sl


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAL_DB_PATH", str(tmp_path / "signals.db"))


def test_log_and_read_back_roundtrip():
    rid = sl.log_signal("btcusdt", "live_flow", "live", 100.0, score=0.72, valid=True, direction="BUY",
                        decision="logged", features={"book_imbalance": 0.2}, ts=1_000)
    assert rid
    row = sl.recent(10)[0]
    assert row["symbol"] == "BTCUSDT" and row["valid"] is True and row["features"] == {"book_imbalance": 0.2}


def test_rejects_non_positive_price():
    assert sl.log_signal("BTCUSDT", "x", "live", 0.0) is None
    assert sl.log_signal("BTCUSDT", "x", "live", -5.0) is None
    assert sl.recent() == []


def test_labels_only_after_horizon_and_only_once():
    sl.log_signal("BTCUSDT", "s", "live", 100.0, score=0.8, ts=0)
    assert sl.label_pending("BTCUSDT", 110.0, now_ms=3_599_999) == 0  # not yet 1h
    assert sl.label_pending("BTCUSDT", 110.0, now_ms=3_600_000) == 1  # 1h label only
    row = sl.recent()[0]
    assert row["ret_1h"] == pytest.approx(0.10) and row["ret_4h"] is None and row["label_ts_1h"] == 3_600_000
    assert sl.label_pending("BTCUSDT", 120.0, now_ms=3_700_000) == 0  # 1h already labeled; never overwritten
    assert sl.label_pending("BTCUSDT", 120.0, now_ms=14_400_000) == 1  # only the 4h label; 24h not yet due
    assert sl.recent()[0]["ret_1h"] == pytest.approx(0.10)


def test_labels_are_per_symbol():
    sl.log_signal("BTCUSDT", "s", "live", 100.0, ts=0)
    sl.log_signal("ETHUSDT", "s", "live", 100.0, ts=0)
    sl.label_pending("ETHUSDT", 90.0, now_ms=3_600_000)
    by = {r["symbol"]: r for r in sl.recent()}
    assert by["BTCUSDT"]["ret_1h"] is None and by["ETHUSDT"]["ret_1h"] == pytest.approx(-0.10)


def test_calibration_buckets_and_validation():
    for sym, score, px_later in [("AAAUSDT", 0.75, 110.0), ("BBBUSDT", 0.78, 90.0), ("CCCUSDT", 0.31, 100.0)]:
        sl.log_signal(sym, "s", "live", 100.0, score=score, ts=0)
        sl.label_pending(sym, px_later, now_ms=14_400_000)
    rows = {r["bucket"]: r for r in sl.calibration(horizon="4h")}
    assert rows[0.7]["n"] == 2 and rows[0.7]["avg_ret"] == pytest.approx(0.0) and rows[0.7]["hit_rate"] == pytest.approx(0.5)
    assert rows[0.3]["n"] == 1
    assert sl.calibration(mode="paper", horizon="4h") == []
    with pytest.raises(ValueError):
        sl.calibration(horizon="7d")


def test_zero_timestamp_is_respected():
    sl.log_signal("BTCUSDT", "s", "live", 100.0, ts=0)
    assert sl.recent()[0]["ts"] == 0
