#!/usr/bin/env python3
"""Compare baseline buy&hold against the research-driven overlays, net of fees, with walk-forward + Deflated Sharpe.

Usage:
  python scripts/backtest_strategies.py --fetch BTCUSDT --interval 4h --bars 4000
  python scripts/backtest_strategies.py --csv candles.csv --interval 4h     # columns: open,high,low,close (+ any others)

Prints metrics only; it never trades. Treat results as evidence about *this* sample, not a forecast.
"""
import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.quant.backtest import Costs, StopConfig, buy_and_hold, run_backtest, walk_forward  # noqa: E402
from backend.quant.signals import trend_is_up  # noqa: E402

BARS_PER_YEAR = {"1h": 8760, "2h": 4380, "4h": 2190, "6h": 1460, "12h": 730, "1d": 365}


def load_csv(path):
    with open(path, newline="") as f:
        return [{k: float(r[k]) for k in ("open", "high", "low", "close")} for r in csv.DictReader(f)]


def fetch_binance(symbol, interval, bars):
    import httpx  # production market data only (never testnet)

    out, end = [], None
    while len(out) < bars:
        params = {"symbol": symbol, "interval": interval, "limit": min(1000, bars - len(out))}
        if end:
            params["endTime"] = end
        rows = httpx.get("https://api.binance.com/api/v3/klines", params=params, timeout=20).raise_for_status().json()
        if not rows:
            break
        out = [{"open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4])} for r in rows] + out
        end = rows[0][0] - 1
    return out[-bars:]


def make_trend(fast, slow):
    return lambda h: trend_is_up([c["close"] for c in h], fast, slow)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv")
    src.add_argument("--fetch", metavar="SYMBOL")
    ap.add_argument("--interval", default="4h", choices=sorted(BARS_PER_YEAR))
    ap.add_argument("--bars", type=int, default=4000)
    ap.add_argument("--fee-bps", type=float, default=10.0)
    ap.add_argument("--slippage-bps", type=float, default=5.0)
    ap.add_argument("--vol-target", type=float, default=0.40)
    ap.add_argument("--train-bars", type=int, default=1500)
    ap.add_argument("--test-bars", type=int, default=500)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args()

    candles = load_csv(a.csv) if a.csv else fetch_binance(a.fetch, a.interval, a.bars)
    bpy = BARS_PER_YEAR[a.interval]
    costs = Costs(a.fee_bps, a.slippage_bps)
    base = dict(costs=costs, bars_per_year=bpy)
    sig = make_trend(20, 100)
    runs = {
        "buy_and_hold": buy_and_hold(candles, **base),
        "trend": run_backtest(candles, sig, warmup=100, **base),
        "trend+voltarget": run_backtest(candles, sig, vol_target=a.vol_target, warmup=100, **base),
        "trend+atr_stops": run_backtest(candles, sig, stops=StopConfig(), warmup=100, **base),
        "trend+voltarget+atr": run_backtest(candles, sig, vol_target=a.vol_target, stops=StopConfig(), warmup=100, **base),
    }
    wf = walk_forward(
        candles, make_trend, {"fast": [10, 20, 30], "slow": [50, 100, 200]},
        train_bars=a.train_bars, test_bars=a.test_bars, vol_target=a.vol_target, **base,
    )
    report = {
        "bars": len(candles),
        "costs_bps": {"fee": a.fee_bps, "slippage": a.slippage_bps},
        "strategies": {k: v.metrics for k, v in runs.items()},
        "walk_forward": {k: wf[k] for k in ("oos_sharpe", "n_trials", "folds") if k in wf} | {"deflated_sharpe_prob": wf.get("deflated_sharpe_prob")},
    }
    if a.json:
        print(json.dumps(report, indent=2, default=str))
        return
    print(f"{len(candles)} bars, fees {a.fee_bps}bps + slippage {a.slippage_bps}bps per side\n")
    print(f"{'strategy':<22}{'return%':>9}{'sharpe':>8}{'maxDD%':>8}{'trades':>8}{'win%':>7}{'PF':>7}")
    for name, r in runs.items():
        m = r.metrics
        print(f"{name:<22}{m['total_return_pct']:>9.1f}{m['sharpe']:>8.2f}{m['max_drawdown_pct']:>8.1f}{m['trades']:>8.0f}{m['win_rate_pct']:>7.0f}{m['profit_factor']:>7.2f}")
    dsr = wf.get("deflated_sharpe_prob")
    print(f"\nWalk-forward (trend+voltarget, {wf['n_trials']} param combos/fold, {len(wf['folds'])} folds): "
          f"OOS Sharpe {wf['oos_sharpe']:.2f}, Deflated Sharpe prob {dsr if dsr is None else round(dsr, 3)} (>=0.95 is the usual bar)")
    for i, f in enumerate(wf["folds"]):
        print(f"  fold {i}: params={f['params']} train_sharpe={f['train_sharpe']:.2f} oos_sharpe={f['oos_sharpe']:.2f}")


if __name__ == "__main__":
    main()
