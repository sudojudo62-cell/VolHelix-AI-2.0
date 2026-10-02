---
name: <slug>-lab
description: Research-and-development tool for the strategies in "<source title>". Use to backtest these strategies on crypto venues (6-month walk-forward, fees included), see which factors mattered, and hand a summary to the dashboard governor. Triggers: <strategy names>, "<slug>", "backtest <strategy>".
---

# <Source title> — strategy lab

**Source:** <url> (<paper | blog | docs>; evidence quality: <note>)

## Strategies found (all of them)
| # | Strategy | Source reference | Needs | Backtestable here? | Plugin id |
|---|----------|------------------|-------|--------------------|-----------|

## How to run
```bash
python -m research.run_sweep --slug <slug> --title "<title>" --url <url> --venues ... --assets ... --intervals ... --months 6
python -m backend.governor.run ingest research/summaries/<slug>.json
```
Code: `research/strategies/<slug>/`. Results: `research/summaries/<slug>.json`.

## What mattered (from the last sweep; commit <sha>, <date>)
- Best out-of-sample cell: <venue / assets / interval / params> -> <OOS return>% net of fees vs buy-and-hold <x>%,
  Sharpe <s>, max drawdown <d>%, deflated Sharpe <p>. Last 30 days: <r>%.
- Factor attribution: <venue>, <interval>, <asset>, <parameters>.
- Regimes: favorable <...>; unfavorable <...>.
- Skipped / unsupported: <...>.

## Caveats
Backtests are assumptions, not forecasts: next-bar-open fills, assumed fees/slippage, no market impact, perp liquidation not
modeled, six months holds few regimes. A strategy is only integrated after a 72h paper trial AND human approval.
