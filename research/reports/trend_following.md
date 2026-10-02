# trend_following — research report (run 2: re-run on the fixed pipeline)

**Branch:** `research/trend_following` · **Run:** 2026-10-02, lab commit `d316bfe`, 6 venues × BTC/ETH/SOL × 1h/4h/6h/1d, window 2026-02-04 → 2026-10-02 00:00 UTC (`end_ms 1790899200000`): 90d warm-up + 60d train + **6 × 30d out-of-sample folds (OOS 2026-04-05 → 2026-10-01)**.
**Source:** "Systematic Trend-Following with Adaptive Portfolio Construction…" (arXiv 2602.11708). Strategy list unchanged: `research/reports/trend_following_strategies.md`.
Every number below was produced by a command run in this session (sweep log, `research/summaries/trend_following.json`, scripts in `research/strategies/trend_following/verify/`).

## Verdict
* **Lab gate: `viable: true`, 5 of 231 cells** (2592 trials). I re-implemented the best cell from scratch and it reproduces exactly (§2a-bis), so the gate result is not an accounting artefact. **My judgement: the evidence is weak and the paper's own strategy is not viable.**
  * All 5 passing cells are **BTC perp TSMOM long/short benchmarks** (`tsmom_ls` dYdX 4h; `tsmom_vs_ls` dYdX 4h/6h, Hyperliquid 4h, Deribit 4h), not AdaptiveTrend. Three of them pass only through the "halve the buy-and-hold drawdown" leg (OOS +18–19% vs B&H +26.0%, DD 3.3–3.7% vs 29.3%).
  * DSR 0.51–0.59 is a marginal pass and DSR counts bars as independent: `tsmom_ls` flips direction only 3 times in the analysis window (monthly sign of the 30d return: short Feb–Mar, long Apr–May, short Jun–Jul, long Aug→), i.e. ~6 independent monthly bets inside 1080 4h bars. The same return stream scores DSR 0.26 at 1h and 0.51 at 4h (§2a).
  * The **paper's AdaptiveTrend** is not reproduced: `at_portfolio` best OOS **+33.7%** (dYdX BTC+ETH+SOL 1d) vs B&H +35.0%, Sharpe 2.21, DSR 0.29; it beat B&H on return in **0 of 15** cells. `at_core_ls` mean OOS +3.0% vs B&H +32.9% (0 of 24 beat B&H, max DSR 0.19).
  * Best long-only cells: `at_core_long` SOL 1d on four venues, OOS +82–90% vs B&H +46%, DSR 0.44–0.49 (**just misses 0.5**; gate fails on DSR only).
* **Do the run-1 conclusions survive?** (1) "Paper headline not reproduced" — survives (at_portfolio ≤ B&H). (2) "Only long-only SOL trend filters earn" — mostly survives for the AdaptiveTrend family (SOL 1d long-only is still the top cell: +89.6% vs B&H +46.1%, now beating B&H on return and halving its drawdown, DSR 0.49), but the corrected gate and 6-month window add a different winner family (BTC long/short TSMOM). (3) "Not viable" — changes to *lab-viable but fragile*: 5 cells pass, all benchmark strategies on one asset in a market that swung ±4 times. Run-1's numbers (4-month OOS, buggy DSR, funding ignored) are superseded.

## 1. What changed vs run 1
| | run 1 | run 2 |
|---|---|---|
| OOS | 120 days (4 folds) | **180 days (6 folds)** |
| cells / trials / skipped | 129 / 788 / 91 | **231 / 2592 / 116** |
| venues with data | kucoin, dydx, hyperliquid, deribit (bitmex rejected) | + **bitfinex**; bitmex refused (settled); uniswap needs key |
| intervals | 1h 4h 1d | + **6h**; 4h/6h aggregated from 1h where no native bars |
| funding on perps | only twin plugins | automatic (twins deleted: 8 → 7 plugins) |
| DSR | pooled across intervals (bug) | per interval; `internal_trials` counted (at_portfolio 48) |
| viable | 4 (all via bug) | 5 (different cells, see verdict) |
Plugins now: `at_core_long` (any venue), `at_core_ls`, `at_portfolio` (perp, 3-asset), `tsmom`, `tsmom_vs` (any venue), `tsmom_ls`, `tsmom_vs_ls` (perp). Renamed from `tsmom_perp`/`tsmom_vs_perp` because they are long/short strategies, not funding twins. Declared: `warmup_days` 30 / 60 (at_portfolio) / 90 (tsmom); `internal_trials` 1 / 48 (8 inner configs × 2 sides × 3 assets, re-optimised monthly) / 1. 18 strategy tests + 234 total (`pytest research backend/tests`) pass offline.

## 2. Fix verification (real data)
Scripts: `research/strategies/trend_following/verify/*.py`. All outputs below were printed by those scripts on 2026-10-02.

| # | Fix | Evidence (command → output) | Result |
|---|---|---|---|
| a | per-interval DSR | `dsr_recompute.py` recomputes every trial's per-period Sharpe over all 231 cells' parameter grids: variance 1h 0.00014205, 4h 0.00034851, 6h 0.00060802, 1d 0.00187249 — **identical to the lab's `trial_sharpe_variance_by_interval`** (to 8 digits). SR0 (N=2592) annualised: **1h 3.92, 4h 3.07, 6h 3.31, 1d 2.91**. Old pooled variance (0.00121016) would give 11.45 / 5.73 / 4.67 / 2.34. Max DSR by interval in the sweep: 1h 0.32, 4h 0.59, 6h 0.53, 1d 0.49. | **PASS** (see caveat 2a) |
| b | funding on perps | `funding_check.py`, `at_core_long` (L5,θ.02,α2) on dYdX SOL: 1h: 7920 events, summed raw funding while long −0.001730 = engine with/without difference −0.001730 (return −31.12% → −31.00%); 4h: −0.003877 = −0.003877 (−22.58% → −22.28%). `md.funding.sum()` −0.053613 = sum of all events −0.053613. Other perp venues: `md.funding.sum()` = raw event sum on Hyperliquid BTC 4h (+0.049320), Deribit BTC 1h (+0.017790), dYdX BTC 1d (−0.025076). | **PASS** |
| c | aggregation | `aggregation_vs_native.py` (330d, BTC/ETH/SOL): KuCoin 4h and 6h (native `4hour`/`6hour` exist; adapter only uses native 4h) — 1980/1320 bars, **max OHLC deviation 0.00 bps**, volume 0.00%; dYdX 4h (native `4HOURS`) 0.00 bps except **one SOL bar open 11.96 bps**; Hyperliquid native 4h 1980 bars vs 1246 aggregable (1h history capped ~208 d); Bitfinex native 6h (exists) 1320 vs aggregated 1318/1317/1317 with identical OHLC on shared bars (aggregation drops buckets with a missing 1h bar). | **PASS**; 2 findings (bugs 5, 6) |
| d | Bitfinex adapter | `bitfinex_raw_probe.py`/`bitfinex_vs_kucoin.py`: raw row `[MTS, OPEN, CLOSE, HIGH, LOW, VOLUME]` as assumed (BTC 1h `[1790863200000, 84002, 84116, 84411, 83612, 54.1]`, close>open consistent with next open); ms timestamps on UTC bar opens, 1D candles open 00:00 UTC; BTC/ETH/SOL/AVAX/LINK all exist (`tAVAX:USD`, `tLINK:USD` colon form correct; `tAVAXUSD`/`tLINKUSD` return `[]`); close ratio vs KuCoin median 1.0002 (min 0.9812, max 1.0174, LINK 1h), same bar counts 1d 330/330; **`end` is inclusive** (a bar at `end_ms` is returned; the adapter's `<end` filter drops it, ok); pagination: 500d of 1h → 11994 rows over 2 pages, 11994 unique, sorted, no duplicates; **no-trade hours are omitted** (BTC 7914/7920, SOL 7911, AVAX 7766 = 1.9% missing, LINK 7602 = 4.0%, max gap 14 h) → AVAX/LINK 4h/6h aggregates lose 5.8–16.4% of buckets (LINK 6h 16.4% > 3% limit would be rejected). Wrong/missing assumptions: no `VENUE_COSTS` entry (silently falls back to 10+5 bps); Bitfinex has native 3h/6h/12h not used. | **PASS** with findings (bugs 5, 7) |
| e | refusals/flags | BitMEX: `bitmex XBTUSD is not an active contract: Settled (expiry 2026-09-16T12:00:00.000Z)` (same for ETHUSD). Deribit: 7 `1d` cells skipped "daily candles open at +8h UTC…"; Deribit 4h/6h aggregated, 1h native. Hyperliquid: 1h/6h cells skipped (40), 1h rows over a 330d request = 4985 (207.7 d) vs `max_history_candles` 5000; native 4h/1d ran. `data_quality` has `warmup_days_available` (90.0 in all 231 cells) and `price_check`: 180 "verified vs kucoin", 36 "reference venue", 15 "NOT VERIFIED (no reference data)" — all 15 are multi-asset `at_portfolio` cells. | **PASS**; finding: Hyperliquid skip reason is `poor data {…}` without naming the history cap (bug 4); multi-asset cells never price-checked (bug 3) |
| f | regimes | `regime_consistency.py`, BTC 330d: KuCoin and dYdX: 330 day-open instants, **0 mismatches** 1d vs 4h and 1d vs 1h; all 1980 4h instants: 0 mismatches 4h vs 1h. | **PASS** |
| g | governor | `ingest` ok (viable true, 231 results); `replay` → trial 1 FINISHED, 18/18 bars (tsmom_ls dYdX BTC 4h), INTEGRATE (replay is not evidence). Funding in replay: same trial path with funding zeroed gives equity 10269.16 vs 10268.85 with funding (difference = −w·funding on the held position; last-72h funding sum 2.975e-05) → **applied**. Regime gate: favorable list has 4 of 6 regimes, unfavorable none → gate effectively always passes (now `midvol-up`: ok). "NOT GATED" path: `decide()` with `favorable_regimes=[]` → check `ok: True, gated: False`, detail "NOT GATED: backtest could not identify a favorable regime with enough days (now midvol-up)", mirrored in `warnings`; with unknown current regime → "NOT GATED: current regime could not be computed". Unfavorable regime (`highvol-down`) → `ok False` (gated). **`decide()` raises `KeyError '6h'`** (bug 1). | **PASS** for funding/regime paths; **FAIL** for 6h (bug 1) |

### 2a. DSR caveats found while verifying
* The same `tsmom_ls` dYdX BTC return stream: 1h OOS +72.0% Sharpe 3.03 DSR **0.26**; 4h +71.8% Sharpe 3.12 DSR **0.51**; 6h +70.6% Sharpe 3.17 DSR 0.46. SR0 differs by interval (3.92 vs 3.07 annualised) because 1h trials are noisier (cost drag, many cheap-to-flip signals); verdicts therefore depend on the sampling interval, not only the strategy.
* `n_obs` = bars. For monthly-rebalanced strategies the effective sample is the number of rebalances (see verdict).
* Trial variance is estimated from full-sample (partly in-sample) Sharpes of the *grid configs only*; `internal_trials` raises N (SR0) but not the variance. It is conservative here (N=2592 puts SR0 at ≈3 annualised).

### 2a-bis. Independent re-implementation of the top viable cell
`independent_tsmom_ls.py` (plain loops, own signal, own fills/costs/funding from raw dYdX candles/funding): per-fold returns +20.13/+6.83/+0.90/−0.76/+22.01/+9.60% = lab; total **+71.84%** (lab 71.84%), Sharpe 3.118 (lab 3.118), max DD 13.21% (13.21%), 1080 bars; my DSR **0.513** = lab 0.513. Monthly decisions in the window: 2026-02-01 short, 03-01 short, 04-01 long, 05-01 long, 06-01 short, 07-01 short, 08-01 long, 09-01 long, 10-01 long; BTC 67,120 (OOS start) → 84,850.

## 3. Results
`python -m research.run_sweep --slug trend_following … --venues kucoin dydx hyperliquid deribit bitfinex uniswap --assets BTC ETH SOL --intervals 1h 4h 6h 1d --months 6` → **231 cells, 2592 trials, 116 skipped, 185 s** (mostly cached data).

### Cells that pass every gate (OOS net of fees/slippage/funding)
| strategy | venue/asset/interval | OOS | B&H (DD) | Sharpe | maxDD | DSR | last 30d | params |
|---|---|---|---|---|---|---|---|---|
| tsmom_ls | dydx BTC 4h | +71.8% | +26.0% (29.3%) | 3.12 | 13.2% | 0.51 | +9.6% | 30d lookback |
| tsmom_vs_ls | dydx BTC 4h | +19.0% | +26.0% (29.3%) | 3.39 | 3.6% | 0.59 | +2.6% | 30d |
| tsmom_vs_ls | dydx BTC 6h | +19.3% | +26.0% | 3.42 | 3.3% | 0.53 | +2.5% | 30d |
| tsmom_vs_ls | hyperliquid BTC 4h | +18.1% | +26.0% | 3.23 | 3.6% | 0.55 | +2.4% | 30d |
| tsmom_vs_ls | deribit BTC 4h | +18.0% | +26.0% | 3.22 | 3.7% | 0.54 | +2.6% | 30d |

### Best cell per strategy
| strategy | cell | OOS | B&H | Sharpe | DD | DSR | 30d |
|---|---|---|---|---|---|---|---|
| at_core_long | dydx SOL 1d | +89.6% | +46.1% (DD 36.2%) | 2.89 | 12.0% | 0.49 | +18.2% |
| tsmom_ls | dydx BTC 1h | +72.0% | +26.0% | 3.03 | 14.1% | 0.26 | +9.6% |
| at_core_ls | dydx SOL 1d | +36.3% | +46.1% | 1.70 | 14.5% | 0.19 | −3.1% |
| at_portfolio | dydx BTC+ETH+SOL 1d | +33.7% | +35.0% (31.8%) | 2.21 | 11.8% | 0.29 | +9.2% |
| tsmom | dydx BTC 1h | +26.1% | ≈+26% | 2.05 | 10.8% | 0.09 | +9.6% |
| tsmom_vs_ls | dydx BTC 6h | +19.3% | +26.0% | 3.42 | 3.3% | 0.53 | +2.5% |
| tsmom_vs | dydx BTC 6h | +8.3% | ≈+26% | 2.23 | 3.2% | 0.21 | +2.5% |

Per strategy (cells / mean OOS / mean B&H / #OOS>0 / #beat B&H / max DSR): at_core_long 48 / 27.6 / 30.8 / 45 / 23 / 0.49; at_core_ls 24 / 3.0 / 32.9 / 13 / 0 / 0.19; at_portfolio 15 / 12.2 / 31.4 / 15 / 0 / 0.29; tsmom 48 / 15.6 / 30.8 / 42 / 1 / 0.22; tsmom_ls 24 / 31.4 / 32.9 / 18 / 16 / 0.51; tsmom_vs 48 / 4.2 / 30.8 / 42 / 0 / 0.27; tsmom_vs_ls 24 / 8.9 / 32.9 / 18 / 0 / 0.59. Overall 193/231 cells OOS>0, 40/231 beat B&H on return.

### Fold stability (the real risk)
* `at_core_long` dYdX SOL 1d: folds −2.6, +8.7, +24.0, **−10.6** (train Sharpe 4.58, the highest), +36.8, +18.2%. Two folds carry most of the return.
* `tsmom_ls` dYdX BTC 4h: +20.1, +6.8, +0.9, −0.8, +22.0, +9.6%; two folds (Apr, Aug: the V-turns) make 42 of the 72 points.
* `at_portfolio` dYdX 3-asset 1d: +9.4, +1.1, +1.0, −6.8, +17.6, +9.2% (λ=0.7 picked 5 of 6 times).

### Factors (means; attribution in the summary)
* **Interval** (mean OOS): 1d 22.4% (58 cells), 6h 17.0%, 4h 12.0%, 1h 9.5%. In-sample mean Sharpe 1d 0.97, 6h 0.14, 4h 0.14, 1h −0.39. The paper's 6h-beats-1d claim is not supported.
* **Venue** (mean OOS): deribit 18.0, kucoin 17.0, hyperliquid 15.9, dydx 14.5, bitfinex 10.9 (differences are mostly which strategies could run there: spot venues only get long-only plugins).
* **Asset** (mean OOS): BTC 19.0, ETH 15.7, BTC+ETH+SOL 15.0, BTC+ETH 10.3, SOL 10.2. SOL's top cells are long-only SOL 1d.
* **Long vs long/short:** BTC long/short TSMOM won this window; long/short ATR (`at_core_ls`) did not (3.0% mean). λ: 0.7 > 0.5 (in-sample Sharpe 0.45 vs 0.11).
* **Params** (in-sample mean Sharpe): TSMOM lookback 30d 1.49 vs 90d −0.45; L 5d 0.44 (2d 0.06, 10d −0.12); θ 0.05 0.21 vs 0.02 0.05; α 3.0 0.25 vs 2.0 0.01.
* **Regimes** (top cell, tsmom_ls dYdX BTC 4h; ≥20 days & ≥10% to count): midvol-down 204% ann., midvol-up 188%, lowvol-down 126%, highvol-up 0%; lowvol-up and highvol-down insufficient. Four of six regimes "favorable", none unfavorable → weak gate.
* **In-sample vs OOS:** best-of-grid in-sample for `at_core_long` dYdX SOL 1d Sharpe 1.95 / +79.4% vs OOS Sharpe 2.89 / +89.6% (the lab's "in-sample" window overlaps the OOS window, so this is not a clean gap); fold-to-fold instability is the honest uncertainty.

### Skipped (116)
uniswap 40 (24 need `THEGRAPH_API_KEY` — adapter's own error, I did not read the variable; 12 no SOL pool; 4 spot/perp-kind mismatch); hyperliquid 40 (1h and 6h: history cap → "poor data", see bug 4); deribit 28 (21 no SOL mapping, 7 `1d` +8h boundary); kucoin 4 and bitfinex 4 (perp-only plugins). BitMEX not requested (retired).

## 4. Governor replay
```
export GOVERNOR_DB_PATH=/tmp/gov-trend_following.db
python -m backend.governor.run ingest research/summaries/trend_following.json   # viable true, recommended_trial true, 231 results
python -m backend.governor.run replay trend_following                           # trial 1 FINISHED, 18 bars, INTEGRATE
python -m backend.governor.run report 1
```
Recommended trial: `tsmom_ls` dYdX BTC 4h (30d). Checks: backtest viable ✔; integrity ✔ (18/18, 0 gaps, 0 errors); drawdown ✔ (1.35% vs 19.82%); return vs backtest p10 ✔ (+2.69% vs −2.59%, mean +0.94%); regime ✔ (`midvol-up`; gated). **Replay INTEGRATE is a pipeline smoke test on 18 bars, not evidence**; the approval step is human only. (`status` printed no `report` key in the trial row; `report <id>` prints it.)

## 5. Bugs & friction
Format: file — symptom — repro — suggested fix. No LOCAL-PATCH needed (none blocking).

1. **[MED, governor, new] `decide()` crashes for 6h trials.** `backend/governor/report.py:29` bar-length dict has no `"6h"` (nor aggregated intervals generally). Repro: `decide(summary, {**cfg, "interval": "6h"}, TrialState(), 72.0, "midvol-up")` → `KeyError: '6h'`. A 6h recommended trial would finish its 72h run and then fail to produce a decision. Fix: use `research.lab.venues.INTERVAL_MS[interval]`.
2. **[MED, statistics, new] DSR counts bars, not independent bets.** `research/lab/sweep.py` passes `n_obs=len(r_oos)`. `tsmom_ls` dYdX BTC flips direction 3 times in the window yet scores DSR 0.51 on 1080 4h bars (0.26 on 4320 1h bars with the same returns). Fix: use an effective sample size (number of non-overlapping trades/rebalances or block-bootstrap/autocorrelation-adjusted n), or require a minimum number of trades per OOS window as a gate.
3. **[MED, data quality, new] Multi-asset cells are never price-checked.** `ref_md` is keyed by `(interval, tuple(aset))` and only KuCoin populates it; perp-only multi-asset plugins (`at_portfolio`) never have a KuCoin multi-asset cell → 15 cells `price_check = NOT VERIFIED (no reference data)`. Repro: count `data_quality[*].price_check` in the summary. Fix: check each asset of a multi-asset cell against the single-asset reference series.
4. **[LOW/MED, friction, new] Hyperliquid skip reason hides the cause.** 40 cells are skipped as `poor data {'n': 4985, 'expected': 7920, 'missing_pct': 37.058, … 'head_gap_bars': 2935}`; `max_history_candles = 5000` is known to the adapter but never mentioned. Fix: when `head_gap_bars > 0` and the adapter has `max_history_candles`, say "venue serves only ~N days of 1h history".
5. **[MED, data, new] Aggregation drops every 4h/6h bucket with any missing 1h bar; Bitfinex omits no-trade hours.** `research/lab/venues.py aggregate_candles` (`len(b) < need: continue`). Bitfinex AVAX 1h 1.9% missing → 4h 5.8%, 6h 7.9%; LINK 4h 12.4%, 6h 16.4% (rejected by the 3% rule). BTC/ETH/SOL lose 0.1–0.2%, so this sweep is unaffected. Repro: `verify/bitfinex_vs_kucoin.py`. Fix: for venues documented to omit empty bars, forward-fill flat zero-volume 1h candles at the previous close before aggregating (or accept ≥ N−1 bars).
6. **[LOW] Native 6h exists and is unused** on KuCoin (`6hour`) and Bitfinex (`6h`, also `3h`/`12h`); the lab aggregates instead. Aggregation matched native exactly on KuCoin (0.00 bps) so it is harmless where it ran, but Bitfinex native would fix bug 5 for 6h. One dYdX SOL 4h bar differs 11.96 bps on open between native and aggregated (informational).
7. **[LOW] Bitfinex has no `VENUE_COSTS` entry** (`research/lab/engine.py`); `Costs.for_venue` silently uses 10+5 bps. Add an explicit entry so the assumption is visible.
8. **[LOW] `run_sweep.py` default `--venues` still lists `bitmex`** (docs say it was retired from the default list). Default runs spend time on a guaranteed refusal.
9. **[LOW, DSR design] `internal_trials` raises N but not the trial variance**, and the variance comes from full-sample Sharpes of grid configs only; SR0 ≈ 3 annualised at N=2592 makes the gate very strict for 1h but marginal for 4h. Worth documenting; consider estimating variance from the same walk-forward statistic that is scored.
10. **[LOW, governor] Regime gate nearly vacuous**: favorable list = 4 of 6 regimes, unfavorable none for the recommended trial. The gate can only fail in `highvol-down`/`lowvol-up`. Suggest requiring a consistent sign across folds before naming a regime favorable.
11. **[LOW] `governor run status` rows hold no report** (use `report <id>`); fine but undocumented in the CLI help.
12. **[INFO] Run-1 bugs 1–2, 4–9 verified fixed (§2); preflight now prints WARN for Binance 451; `pytest research backend/tests` 234 passed.** Funding sign on dYdX is negative (longs earn) over the window: summed funding −0.0536 (330 d) — sign convention consistent with the engine (`ret − w·funding`).

## 6. Reproduce
```bash
python3 -m venv .venv-trend_following && . .venv-trend_following/bin/activate && pip install -r research/requirements.txt
python -m research.preflight
pytest research backend/tests
python -m research.run_sweep --slug trend_following --title "Systematic Trend-Following with Adaptive Portfolio Construction: Enhancing Risk-Adjusted Alpha in Cryptocurrency Markets" --url https://arxiv.org/pdf/2602.11708 --venues kucoin dydx hyperliquid deribit bitfinex uniswap --assets BTC ETH SOL --intervals 1h 4h 6h 1d --months 6
for s in research/strategies/trend_following/verify/*.py; do PYTHONPATH=. python $s; done
GOVERNOR_DB_PATH=/tmp/gov-trend_following.db python -m backend.governor.run ingest research/summaries/trend_following.json
GOVERNOR_DB_PATH=/tmp/gov-trend_following.db python -m backend.governor.run replay trend_following
```
