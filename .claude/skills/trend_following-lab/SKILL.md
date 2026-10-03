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
| 1+2 | Momentum entry `MOM_L > theta` + ATR trailing stop `S=max(S,P-alpha*ATR)` (long; short is a mirror we assumed) | §3.2, Eq. 2–3, Alg. 1 | any venue (long) / perp (short) | yes | `at_core_long` (any venue; perps pay funding automatically), `at_core_ls` (perp) |
| 3 | Market-cap universe filter (top-15 long / bottom-K short of 150+ pairs) | §3.3 stage 1 | market caps, big universe | **NOT BACKTESTABLE** | — |
| 4+5+6 | Monthly Sharpe selection (gamma 1.3 long / 1.7 short) with monthly re-optimisation and 70/30 allocation | §3.3 stage 2, §3.4, Eq. 4–5 | perp | yes, 3-asset version | `at_portfolio` (lambda 0.7 / 0.5) |
| 7 | TSMOM-1M/3M benchmark | §4.3 | any / perp (long-short) | yes (benchmark) | `tsmom`, `tsmom_ls` |
| 8 | Vol-scaled TSMOM benchmark (10% vol target) | §4.3 | any / perp (long-short) | yes (benchmark) | `tsmom_vs`, `tsmom_vs_ls` |
| 10 | 6-hour bars | §5.6 | 6h candles | yes: aggregated from 1h (native on some venues) | interval factor |

Unspecified in the paper and fixed by us: ATR length 14, grids for L/theta/alpha, short-side stop mirror. Declared in code: `warmup_days` 30 (core) / 60 (portfolio) / 90 (TSMOM); `internal_trials` 48 for `at_portfolio` (monthly re-optimisation), 1 otherwise. Details: `research/reports/trend_following_strategies.md`.

## How to run
```bash
python -m research.preflight
python -m research.run_sweep --slug trend_following --title "Systematic Trend-Following with Adaptive Portfolio Construction: Enhancing Risk-Adjusted Alpha in Cryptocurrency Markets" \
  --url https://arxiv.org/pdf/2602.11708 --venues kucoin dydx hyperliquid deribit bitfinex uniswap --assets BTC ETH SOL --intervals 1h 4h 6h 1d --months 6
python -m backend.governor.run ingest research/summaries/trend_following.json
```
Code: `research/strategies/trend_following/` (tests: `pytest research/strategies/trend_following`; fix-verification scripts in `verify/`). Results: `research/summaries/trend_following.json`.

## What mattered (run 2: lab commit d316bfe, 2026-10-02; 231 cells, 2592 trials; 90d warm-up + 60d train + 6 × 30d OOS, OOS 2026-04-05 → 2026-10-01)
- Lab gate: **`viable: true`, 5 cells**, all BTC perp TSMOM long/short benchmarks (not the paper's strategy): `tsmom_ls` dYdX BTC 4h 30d lookback OOS **+71.8%** vs B&H +26.0%, Sharpe 3.12, max DD 13.2%, DSR 0.51, last 30d +9.6%
  (independently re-implemented: identical); `tsmom_vs_ls` BTC 4h/6h on dYdX/Hyperliquid/Deribit OOS +18–19% (B&H +26.0%, DD 3.3–3.7% vs 29.3%), DSR 0.53–0.59.
  **Fragile:** `tsmom_ls` flips direction only 3 times in the window (~6 monthly bets); the same returns score DSR 0.26 at 1h and 0.51 at 4h; two folds make 42 of the 72 points.
- The paper's AdaptiveTrend is **not reproduced**: `at_portfolio` best +33.7% OOS vs B&H +35.0% (dYdX BTC+ETH+SOL 1d, DSR 0.29), 0 of 15 cells beat B&H; `at_core_ls` mean +3.0% (0 of 24 beat B&H).
  Best long-only: `at_core_long` dYdX SOL 1d (L=5d, theta 0.05, alpha 2.0) OOS +89.6% vs B&H +46.1%, Sharpe 2.89, DD 12.0%, DSR 0.49 (just misses the 0.5 gate), one of six folds lost (−10.6%).
- Factors (mean OOS): 1d 22.4% > 6h 17.0% > 4h 12.0% > 1h 9.5% (the paper's 6h-beats-1d claim is not supported); BTC 19.0% > ETH 15.7% > SOL 10.2%; TSMOM lookback 30d (in-sample Sharpe 1.49) ≫ 90d (−0.45); L=5d, theta 0.05, alpha 3.0 best in-sample; lambda 0.7 > 0.5.
  193/231 cells earn OOS>0 but only 40/231 beat buy-and-hold on return.
- Regimes (top cell): favorable midvol-down/midvol-up/lowvol-down/highvol-up, none unfavorable → the governor's regime gate barely discriminates.
- Governor replay (tsmom_ls dYdX BTC 4h): INTEGRATE on 18 bars — a pipeline smoke test, not evidence; needs a real 72h trial and human approval.
- Skipped: Uniswap (no `THEGRAPH_API_KEY`, no SOL pool), Hyperliquid 1h/6h (API serves ~5000 candles → ~208 days), Deribit 1d (08:00 UTC boundary) and SOL, BitMEX refused (perps settled 2026-09-16).
- Run-1 numbers (4-month OOS, pooled-DSR bug, funding ignored) are superseded; run-1's "paper headline not reproduced" survives.

## Caveats
Backtests are assumptions, not forecasts: next-bar-open fills, assumed fees/slippage, no market impact, perp liquidation not
modeled, six months holds few regimes (BTC swung down/up/down/up). The paper's headline (Sharpe 2.41, MDD 12.7%) was NOT reproduced. DSR counts bars as independent, which flatters
low-turnover strategies. A strategy is only integrated after a 72h paper trial AND human approval.
