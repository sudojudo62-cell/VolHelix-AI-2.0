#!/usr/bin/env python3
"""Run a strategy sweep for one source and write research/summaries/<slug>.json.

    python -m research.run_sweep --slug trend_following --title "Systematic Trend-Following ..." --url https://arxiv.org/... \
        --venues kucoin dydx hyperliquid deribit bitfinex uniswap --assets BTC ETH SOL --intervals 1h 4h 1d --months 6

Strategies come from research/strategies/<slug>/__init__.py (STRATEGIES = [...]).
"""
import argparse
import os
import sys
import time

from research.lab.plugin import load_strategies
from research.lab.summary import Source, save_summary
from research.lab.sweep import SweepConfig, run_sweep

OUT_DIR = os.path.join(os.path.dirname(__file__), "summaries")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--venues", nargs="+", default=["kucoin", "dydx", "hyperliquid", "deribit", "bitfinex", "uniswap"])
    ap.add_argument("--assets", nargs="+", default=["BTC", "ETH", "SOL"])
    ap.add_argument("--intervals", nargs="+", default=["1h", "4h", "6h", "1d"])
    ap.add_argument("--months", type=int, default=6)
    ap.add_argument("--end", type=int, default=None, help="end timestamp ms (default: today 00:00 UTC, so re-runs the same day hit the data cache)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    cfg = SweepConfig(venues=a.venues, assets=a.assets, intervals=a.intervals, months=a.months, end_ms=a.end or (int(time.time() * 1000) // 86_400_000) * 86_400_000)
    summary = run_sweep(load_strategies(a.slug), Source(slug=a.slug, title=a.title, url=a.url), cfg)
    out = a.out or os.path.join(OUT_DIR, f"{a.slug}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    save_summary(summary, out)

    print(f"\n{len(summary.results)} cells, {summary.run['n_trials']} trials, {len(summary.skipped)} skipped -> {out}")
    print(f"viable: {summary.viable}  {'; '.join(summary.viability_reasons)}")
    for r in summary.results[:10]:
        print(f"  {r.strategy_id:24s} {r.venue:12s} {'+'.join(r.assets):10s} {r.interval:3s} OOS {r.oos_return_pct:7.1f}% (B&H {r.benchmark_oos_return_pct:7.1f}%)  "
              f"Sharpe {r.oos_sharpe:5.2f}  DD {r.oos_max_drawdown_pct:5.1f}%  DSR {r.deflated_sharpe_prob:.2f}  30d {r.oos_last_30d_return_pct:6.1f}%")
    for s in summary.skipped[:15]:
        print(f"  skipped: {s}")
    return 0 if summary.results else 2


if __name__ == "__main__":
    sys.exit(main())
