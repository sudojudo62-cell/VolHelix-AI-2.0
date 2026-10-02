import math
import random

import pytest

from backend.quant.backtest import Costs, StopConfig, buy_and_hold, deflated_sharpe, run_backtest, walk_forward
from backend.quant.signals import atr, atr_levels, ema, realized_vol_annualized, trend_is_up, vol_size_multiplier


def _candles(closes, spread=0.5):
    out, prev = [], closes[0]
    for c in closes:
        out.append({"open": prev, "high": max(prev, c) + spread, "low": min(prev, c) - spread, "close": c})
        prev = c
    return out


def _walk(n, drift, vol, seed=1, start=100.0):
    rng = random.Random(seed)
    px, out = start, []
    for _ in range(n):
        px *= math.exp(drift + vol * rng.gauss(0, 1))
        out.append(px)
    return out


def test_trend_is_up_fails_closed_on_short_history():
    assert trend_is_up([100.0] * 50, fast=20, slow=100) is False


def test_trend_detection_up_vs_down():
    assert trend_is_up(list(range(100, 400)), 20, 100) is True
    assert trend_is_up(list(range(400, 100, -1)), 20, 100) is False


def test_vol_multiplier_never_exceeds_one_and_respects_floor():
    calm = [100 + 0.01 * i for i in range(100)]
    wild = _walk(100, 0.0, 0.08, seed=3)
    assert vol_size_multiplier(calm) == 1.0
    m = vol_size_multiplier(wild)
    assert 0.25 <= m < 1.0
    assert vol_size_multiplier([100.0] * 5) == 1.0  # not enough data -> unchanged


def test_atr_levels_valid_and_rejects_bad_inputs():
    lv = atr_levels(100.0, 2.0, sl_mult=2.0, tp_mult=4.0)
    assert lv["stop_loss_price"] == 96.0 and lv["take_profit_price"] == 108.0
    with pytest.raises(ValueError):
        atr_levels(100.0, 2.0, sl_mult=2.0, tp_mult=2.0)  # reward:risk below minimum
    with pytest.raises(ValueError):
        atr_levels(100.0, 60.0)  # stop would go non-positive


def test_atr_constant_range():
    h = [101.0] * 30
    l = [99.0] * 30
    c = [100.0] * 30
    assert atr(h, l, c, 14) == pytest.approx(2.0)
    assert atr(h[:5], l[:5], c[:5], 14) is None


def test_ema_and_realized_vol_basics():
    assert ema([1, 1, 1], 3) == [1, 1, 1]
    assert realized_vol_annualized([100.0] * 40, 30) == 0.0


def test_entry_uses_next_bar_open_no_lookahead():
    # Signal fires at bar 2's close; fill must be bar 3's open (price 200), not bar 2's close.
    closes = [100, 100, 100, 200, 200, 200]
    candles = _candles(closes)
    candles[3]["open"] = 200.0
    res = run_backtest(candles, lambda h: len(h) >= 3, Costs(0, 0), warmup=1)
    assert res.equity[-1] == pytest.approx(10_000.0)  # bought at 200, price stayed 200


def test_costs_reduce_returns():
    candles = _candles(_walk(300, 0.001, 0.01, seed=5))
    free = run_backtest(candles, lambda h: True, Costs(0, 0))
    paid = run_backtest(candles, lambda h: True, Costs(10, 5))
    assert paid.metrics["total_return_pct"] < free.metrics["total_return_pct"]


def test_stop_loss_exits_on_gap_down():
    closes = [100, 100, 100, 100, 100, 60, 60]
    candles = _candles(closes, spread=0.1)
    candles[5]["open"] = 70.0
    candles[5]["low"] = 59.0
    res = run_backtest(candles, lambda h: True, Costs(0, 0), stops=StopConfig(atr_period=3, sl_mult=2.0, tp_mult=4.0))
    # exit at the gap open (70) since it is below the stop, not at the stop price
    assert res.trades and res.trades[0]["pnl_pct"] == pytest.approx(-30.0, abs=0.5)


def test_vol_target_reduces_exposure():
    candles = _candles(_walk(300, 0.0, 0.06, seed=7))
    full = run_backtest(candles, lambda h: True, Costs(0, 0), warmup=40)
    scaled = run_backtest(candles, lambda h: True, Costs(0, 0), vol_target=0.2, warmup=40)
    assert scaled.metrics["max_drawdown_pct"] <= full.metrics["max_drawdown_pct"]


def test_deflated_sharpe_penalizes_more_trials():
    one = deflated_sharpe(0.1, 500, 1, 0.0)
    many = deflated_sharpe(0.1, 500, 200, 0.001)
    assert many < one
    assert 0.0 <= many <= 1.0
    assert deflated_sharpe(0.1, 2, 5, 0.001) == 0.0


def test_walk_forward_runs_and_reports_trials():
    candles = _candles(_walk(900, 0.0008, 0.015, seed=11))

    def make(fast, slow):
        return lambda h: trend_is_up([c["close"] for c in h], fast, slow)

    wf = walk_forward(candles, make, {"fast": [10, 20], "slow": [50, 100]}, train_bars=400, test_bars=100)
    assert wf["n_trials"] == 4
    assert len(wf["folds"]) == 5
    assert all("oos_sharpe" in f for f in wf["folds"])
    assert 0.0 <= wf["deflated_sharpe_prob"] <= 1.0


def test_buy_and_hold_baseline_matches_price_move_without_costs():
    closes = [100, 110, 121, 133.1, 146.41]
    candles = _candles(closes)
    res = buy_and_hold(candles, Costs(0, 0))
    # first decision is taken after bar 1, so the fill is bar 2's open (= bar 1's close)
    assert res.metrics["total_return_pct"] == pytest.approx((closes[-1] / closes[1] - 1) * 100, abs=0.01)
