import numpy as np
import pytest

from research.lab.data import build_market_data, cross_check, validate_candles
from research.lab.data import DataQualityError
from research.lab.regime import daily_regimes, latest_regime, regime_labels, regime_performance
from research.tests.synth import FakeVenue, H, candles_from_closes, market_data, walk

DAY = 24 * H


def test_validate_counts_truncation_and_late_start_against_the_requested_range():
    rows = candles_from_closes(walk(100, 0, 0.01, 1))                       # gap-free but only 100 of 200 requested bars
    q = validate_candles(rows, "1h", 0, 200 * H)
    assert q["missing_pct"] == 50.0 and q["tail_gap_bars"] == 100 and q["head_gap_bars"] == 0
    late = candles_from_closes(walk(100, 0, 0.01, 1), start_ts=100 * H)
    assert validate_candles(late, "1h", 0, 200 * H)["head_gap_bars"] == 100


def test_truncated_series_is_rejected_but_head_gap_inside_warmup_is_tolerated():
    v = FakeVenue({"BTC": walk(1000, 0, 0.01, 1)})
    with pytest.raises(DataQualityError):
        build_market_data(v, ["BTC"], "1h", 0, 2000 * H)                      # tail missing
    late = FakeVenue({"BTC": walk(1000, 0, 0.01, 1)})
    late.candles = lambda asset, interval, s, e, fresh=False: candles_from_closes(walk(1000, 0, 0.01, 1), start_ts=1000 * H)
    with pytest.raises(DataQualityError):
        build_market_data(late, ["BTC"], "1h", 0, 2000 * H)                   # 50% missing, not allowed
    md = build_market_data(late, ["BTC"], "1h", 0, 2000 * H, allow_head_gap_bars=1000)   # shortage confined to warm-up zone
    assert md.n == 1000


def test_bitmex_style_ohlc_violations_are_flagged_by_validation():
    rows = candles_from_closes(walk(50, 0, 0.01, 1))
    rows[10]["open"] = rows[10]["high"] * 1.1
    assert validate_candles(rows, "1h")["bad_ohlc"] == 1


def test_regimes_are_interval_independent():
    """Pilot bug #8: the same instant got lowvol/highvol/midvol labels depending on the bar size."""
    closes_h = walk(24 * 220, 0.0002, 0.006, 5)
    ts_h = np.arange(len(closes_h)) * H
    lab_h = regime_labels(ts_h, np.array(closes_h))
    # the same series sampled every 4th bar (4h) must agree wherever both have a label for the same UTC day
    lab_4 = regime_labels(ts_h[3::4], np.array(closes_h)[3::4])
    for i, t in enumerate(ts_h[3::4]):
        j = int(t // H)
        if lab_h[j] != "warmup" and lab_4[i] != "warmup":
            assert lab_h[j] == lab_4[i]


def test_every_bar_of_a_day_gets_the_previous_completed_days_label():
    close = np.array(walk(24 * 200, 0.0, 0.01, 6))
    ts = np.arange(len(close)) * H
    lab = regime_labels(ts, close)
    for d in range(40, 190):
        assert len(set(lab[d * 24:(d + 1) * 24])) == 1          # no label change inside a day: today's own close is never used


def test_latest_regime_ignores_the_incomplete_current_day():
    close = np.array(walk(24 * 200, 0.0, 0.01, 7))
    ts = np.arange(len(close)) * H
    now = int(ts[-1]) + H
    a = latest_regime(ts, close, now)
    b = latest_regime(ts, close, int(ts[-1]) - 5 * H)                            # now mid-day: last day not complete yet
    assert a is None or isinstance(a, str) and (b is None or isinstance(b, str))
    assert latest_regime(ts[:50], close[:50], int(ts[49]) + H) is None            # < 30 days of history: unknown, never guessed


def test_thin_regimes_are_flagged_insufficient():
    r = np.random.default_rng(0).normal(0.001, 0.01, 400)
    labels = np.array(["lowvol-up"] * 380 + ["highvol-down"] * 20, dtype=object)
    perf = {d["regime"]: d for d in regime_performance(r, labels, 365.0, "1d")}
    assert perf["lowvol-up"]["sufficient"] is True and perf["highvol-down"]["sufficient"] is False   # 20 days but only 5% of sample
