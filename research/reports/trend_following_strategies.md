# Strategy enumeration — "Systematic Trend-Following with Adaptive Portfolio Construction" (arXiv 2602.11708v1)

Source fetched from https://arxiv.org/pdf/2602.11708 (7 pages, Talyxion Research, Feb 2026). Section numbers below are the paper's.
The paper's framework is called **AdaptiveTrend**; it trades 6-hour bars on Binance USD-M perpetuals (150+ pairs).

| # | Strategy / rule | Reference | Parameters | Market type | Backtestable from public candles(+funding)? | Plugin id |
|---|---|---|---|---|---|---|
| 1 | **Momentum entry signal**: `MOM_t = (P_t - P_{t-L})/P_{t-L}`; long when `> theta`, short when `< -theta` | §3.2.1, Eq. (2) | `L`, `theta` (paper: "optimized monthly", no values given) | perp (shorts) / spot long-only | YES | `at_core_long`, `at_core_ls` |
| 2 | **Dynamic ATR trailing stop**: `S_t = max(S_{t-1}, P_t - alpha*ATR_t)`, exit when `P_t < S_t`; stop initialised `P - alpha*ATR` (Algorithm 1). Short mirror is *implied only* ("for short signals ...") | §3.2.2, Eq. (3), Alg. 1 | `alpha` (default 2.5, plateau 2.0–3.5, §5.4), ATR length `k` (**not specified**; we fix k=14) | perp / spot long-only | YES (mirror for shorts is our assumption) | same as #1 (1+2 are one rule) |
| 3 | **Monthly universe filter by market cap**: long candidates = top K_L=15, short candidates = bottom K_S by market cap | §3.3 Stage 1 | `K_L=15`, `K_S` (not given) | needs CoinGecko market caps + 150-pair universe | **NOT BACKTESTABLE** (needs historical market-cap series and a 150+ asset universe; the lab sweeps 3 assets). With 3 assets every asset is eligible for both legs. | — |
| 4 | **Monthly performance-based selection**: per candidate, grid-search (theta, alpha, L) on the *previous month*, keep asset if prior-month Sharpe `>= gamma_L=1.3` (long) / `gamma_S=1.7` (short) | §3.3 Stage 2, Eq. (4) | `gamma_L`, `gamma_S`; grid values not given | perp | YES for the 3-asset universe (our grids are ours) | `at_portfolio` |
| 5 | **Asymmetric 70/30 capital allocation**, equal weight within each leg: `w_long = lambda/n_L`, `w_short = (1-lambda)/n_S`, `lambda=0.7` | §3.4, Eq. (5) | `lambda` (0.7; 0.5 = "dollar-neutral" ablation) | perp (shorts) | YES | `at_portfolio` (grid lambda in {0.7, 0.5}) |
| 6 | **Monthly re-optimisation of parameters** (walk-forward with 24h buffer) | §3.3, §6 | monthly | — | folded into #4 (`at_portfolio` re-picks params each calendar month on the prior month). The 24h buffer is not modelled. | `at_portfolio` |
| 7 | Benchmark: **TSMOM-1M / TSMOM-3M** (sign of 1/3-month return, monthly rebalance) | §4.3 | lookback 30/90 d | spot long-only / perp long-short | YES (a benchmark, not the paper's strategy) | `tsmom`, `tsmom_ls` |
| 8 | Benchmark: **Vol-scaled TSMOM**, 10% annualised vol target | §4.3 | lookback, target 10% | same | YES (benchmark) | `tsmom_vs`, `tsmom_vs_ls` |
| 9 | Benchmarks BTC-BH / EW-BH of top-20 | §4.3 | — | — | BTC-BH is the lab's built-in buy-and-hold benchmark; EW top-20 not testable (universe) | (lab benchmark) |
| 10 | **Timeframe choice H6** (H1/H4/H6/H8/H12/D1 compared, Table 5) | §5.6 | interval | — | PARTIAL: the lab has 1h/4h/1d only; **6h is not a lab interval** (no 6h on kucoin/dydx/hyperliquid via these adapters; 6h could be aggregated from 1h by the lab but is not). Intervals 1h/4h/1d are swept as the factor. | interval factor |

## Not strategies / validation advice that changes how we test
* §6.1 / Table 6: block-bootstrap significance vs benchmarks. Our equivalent is the lab's walk-forward OOS + Deflated Sharpe.
* §6 Limitations: the authors admit look-ahead risk in the monthly re-optimisation; they use a 24 h buffer. Our implementation uses only the *completed previous calendar month* and is checked by `assert_causal`.
* §4.4 Sharpe uses rf = 4.5%; the lab's Sharpe has rf = 0 (documented in the report).
* Transaction cost used by the paper: 4 bps taker (+ slippage + funding); the lab's venue costs are 9–40 bps per unit turnover, i.e. much harsher than the paper (Table 4 shows the paper's own Sharpe falls from 2.41 to 1.62 between 4 and 12 bps).

## Internal inconsistencies noticed in the paper (affect how much weight to give its headline numbers)
* Table 1 Sharpe 2.41 = 40.5% / 16.8% — i.e. computed **without** subtracting the stated rf = 4.5% (with it, (40.5-4.5)/16.8 = 2.14). The 50/50 row also does not reconcile (34.2/15.1 = 2.26 vs the printed 2.12).
* OOS window is "Jan 2022 – Dec 2024 (36 months)" in §4.1/Table 1 but Figure 1 is labelled "Jan 2022 – Oct 2025".
* §5.6 says H6 "aligns with the 4-times-daily funding rate cycle"; Binance perpetuals fund every 8 h (3 times a day).
* Figure 1 shows ~140% cumulative return over the window while Table 1 reports 40.5% *annualised* return over 3 years (~177% compounded).
* No ATR length, no `theta`/`L` grids, no `K_S` value are given, so the paper is not reproducible as written.
