---
name: trend_following-lab
description: Research-and-development tool for the strategies in "Systematic Trend-Following with Adaptive Portfolio Construction" (AdaptiveTrend, arXiv 2602.11708). Use to backtest momentum-entry + ATR-trailing-stop trend following and its monthly Sharpe-selected 70/30 long-short portfolio on crypto venues (6-month walk-forward, fees and funding included), see which factors mattered, and hand a summary to the dashboard governor. Triggers: AdaptiveTrend, ATR trailing stop, TSMOM, 70/30 long-short, "trend_following", "backtest trend following".
---

# Systematic Trend-Following with Adaptive Portfolio Construction — strategy lab

**Source:** https://arxiv.org/pdf/2602.11708 (arXiv preprint, Talyxion Research, Feb 2026; evidence quality: low-to-moderate — one preprint, internally
inconsistent numbers, key parameters unspecified, results on Binance 6h data 2022–2024 that we cannot reproduce on public candles. See
`research/reports/trend_following.md`).

## Strategies found (all of them)
| # | Strategy | Source reference | Needs | Backtestable here? | Plugin id |
|---|----------|------------------|-------|--------------------|-----------|
| 1+2 | Momentum entry `MOM_L > theta` + ATR trailing stop `S=max(S,P-alpha*ATR)` (long; short is a mirror we assumed) | §3.2, Eq. 2–3, Alg. 1 | spot (long) / perp (short) | yes | `at_core_long` (spot/DEX), `at_core_long_perp`, `at_core_ls` |
| 3 | Market-cap universe filter (top-15 long / bottom-K short of 150+ pairs) | §3.3 stage 1 | market caps, big universe | **NOT BACKTESTABLE** | — |
| 4+5+6 | Monthly Sharpe selection (gamma 1.3 long / 1.7 short) with monthly re-optimisation and 70/30 allocation | §3.3 stage 2, §3.4, Eq. 4–5 | perp | yes, 3-asset version | `at_portfolio` (lambda 0.7 / 0.5) |
| 7 | TSMOM-1M/3M benchmark | §4.3 | spot / perp | yes (benchmark) | `tsmom`, `tsmom_perp` |
| 8 | Vol-scaled TSMOM benchmark (10% vol target) | §4.3 | spot / perp | yes (benchmark) | `tsmom_vs`, `tsmom_vs_perp` |
| 10 | 6-hour bars | §5.6 | 6h candles | lab has no 6h; 1h/4h/1d swept instead | interval factor |

Unspecified in the paper and fixed by us: ATR length 14, grids for L/theta/alpha, short-side stop mirror. Details: `research/reports/trend_following_strategies.md`.

## How to run
```bash
python -m research.run_sweep --slug trend_following --title "Systematic Trend-Following with Adaptive Portfolio Construction: Enhancing Risk-Adjusted Alpha in Cryptocurrency Markets" \
  --url https://arxiv.org/pdf/2602.11708 --venues kucoin dydx hyperliquid deribit bitmex uniswap --assets BTC ETH SOL --intervals 1h 4h 1d --months 6 \
  --end <day-aligned ms>        # pin --end so the disk cache is reused
python -m backend.governor.run ingest research/summaries/trend_following.json
```
Code: `research/strategies/trend_following/` (tests: `pytest research/strategies/trend_following`). Results: `research/summaries/trend_following.json`.

## What mattered (last sweep: commit 438789e, 2026-10-02, window = 180 days to 2026-10-02, OOS = last 120 days)
- Best out-of-sample cell: `at_core_long_perp` dYdX / SOL / 1d, params L=5d theta=0.05 alpha=2.0 -> OOS +79.1% net vs buy-and-hold +65.1%,
  Sharpe 3.55, max drawdown 12.0% (B&H 13.1%), lab DSR 0.61, last 30 days +18.2%. One asset, one 120-day uptrend, one of four folds lost (-10.6%).
- **Do not trust the lab's `viable: true`.** The DSR in this sweep pools per-period Sharpe variance across 1h/4h/1d (bug, see the report). Re-scaled consistently
  (annualised pooling, my diagnostic, skew/kurt ignored) no cell reaches DSR 0.5 (best 0.44) — i.e. nothing is demonstrated viable.
- Factors: 1d beats 4h beats 1h (mean OOS 18.5% / 11.0% / 8.6%); long-only beats long/short (mean OOS: at_core_long_perp 27.5%, at_core_ls -2.2%, at_portfolio 9.2%);
  mean buy-and-hold over the same cells was ~48%, so only 13 of 129 cells beat it on return. SOL > BTC/ETH only because SOL rose most. Params (in-sample mean Sharpe): L=5d, theta 0.05, alpha 3.0 best.
  The 90-day TSMOM lookback was worst (mean in-sample Sharpe -1.16).
- Regimes: only `lowvol-down` had enough bars (31 of 120) to be called favorable for the top cell — treat as unknown.
- Skipped/unsupported: Uniswap (no THEGRAPH_API_KEY in this run, and no SOL pool), Deribit/BitMEX 4h and SOL, BitMEX entirely (perps settled 2026-09-16 + bad OHLC).

## Caveats
Backtests are assumptions, not forecasts: next-bar-open fills, assumed fees/slippage, no market impact, perp liquidation not
modeled, six months holds few regimes. The paper's headline (Sharpe 2.41, MDD 12.7%) was NOT reproduced. A strategy is only integrated after a 72h paper trial AND human approval.
