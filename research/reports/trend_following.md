# trend_following — research report (pilot run)

**Branch:** `research/trend_following` · **Run:** 2026-10-02, sweep commit `438789e`, window = 180 days to 2026-10-02 00:00 UTC (`--end 1790899200000`), OOS = last 120 days (4 folds × 30d, 60d train).
**Source:** "Systematic Trend-Following with Adaptive Portfolio Construction: Enhancing Risk-Adjusted Alpha in Cryptocurrency Markets" (arXiv 2602.11708v1, fetched OK).
Every number below was produced by a command in this session (sweep log, `research/summaries/trend_following.json`, and the diagnostic scripts quoted).

## Verdict (short)
* **Not viable / not demonstrated.** The lab says `viable: true` (4 of 129 cells pass), but that comes from a Deflated-Sharpe scaling bug (below). With a scale-consistent DSR, **0 of 129 cells reach 0.5** (best 0.44; diagnostic ignores skew/kurtosis, so approximate). The paper's headline (Sharpe 2.41, MDD −12.7%) is **not reproduced**: its long/short portfolio (`at_portfolio`) returned at best +20.9% OOS vs buy-and-hold +49.1% on the same bars; plain long/short (`at_core_ls`) averaged −2.2% across its 22 cells.
* The only things that "work" are **long-only trend filters on SOL on 1d** (e.g. dYdX/SOL/1d +79.1% vs B&H +65.1%) — i.e. beta captured in one strong 120-day uptrend, on one asset, with one of four folds losing (−10.6%).
* Pipeline works end to end (sweep → ingest → replay → status), but has a correctness bug in the gate (DSR) and several data-quality blind spots; see **Bugs & friction**.

## 1. Strategies found / testability
Full list with section references in `research/reports/trend_following_strategies.md`. Summary: the paper's AdaptiveTrend = (1) momentum entry + (2) ATR trailing stop, (3) market-cap universe filter, (4) monthly Sharpe selection, (5) 70/30 allocation, (6) monthly re-optimisation, plus TSMOM benchmarks.
Testable on public candles(+funding): 1, 2, 4, 5, 6 (3-asset version) and the TSMOM benchmarks. **NOT BACKTESTABLE:** #3 market-cap filter / 150-pair universe (no market-cap history, lab sweeps 3 assets), and the paper's 6h bars (lab intervals are 1h/4h/1d; 1h/4h/1d swept as the timeframe factor). Parameters the paper omits (ATR length, theta/L grids, K_S, short-side stop mirror) are our assumptions and are marked in code.
Plugins (8): `at_core_long`, `at_core_long_perp`, `at_core_ls`, `at_portfolio`, `tsmom`, `tsmom_perp`, `tsmom_vs`, `tsmom_vs_perp` (perp twins exist only because of the funding limitation, bug #2). 13 offline tests pass (`pytest research/strategies/trend_following`): causality for every plugin, a hand-computed ATR-stop example, short mirror, 70/30 split, venue weight rules.
Paper-internal inconsistencies (affect how much to trust its numbers): Table 1 Sharpe 2.41 = 40.5/16.8 (rf not subtracted despite rf=4.5%); OOS window stated Jan 2022–Dec 2024 but Fig. 1 says to Oct 2025; "4× daily funding" is wrong for 8h funding; Fig. 1 ~140% vs Table 1 40.5% p.a. over 3 years.

## 2. Sweep
`python -m research.run_sweep --slug trend_following ... --venues kucoin dydx hyperliquid deribit bitmex uniswap --assets BTC ETH SOL --intervals 1h 4h 1d --months 6 --end 1790899200000`
→ **129 cells, 788 trials, 91 skipped, 62 s** (data pre-cached). Cells per venue: dydx 42, hyperliquid 42, kucoin 27, deribit 18, bitmex 0, uniswap 0.

### Skipped (findings, not noise)
| venue | count | reason |
|---|---|---|
| uniswap | 12 / 6 / 3 / 5 | needs `THEGRAPH_API_KEY` (the adapter reported it unset; I did not read the variable, the adapter's own error is the evidence) / no SOL pool mapping / no 4h / strategy kind (perp-only plugins) |
| bitmex | 20 | "poor data": ~10% of BTC/ETH hourly bars have open outside [low, high] (see bug #4); plus 5 no 4h, 8 no SOL, 3 kind |
| deribit | 5 / 10 / 3 | no 4h / no SOL mapping / kind |
| dydx, hyperliquid, kucoin | 3, 3, 5 | strategy kind not applicable (spot-only vs perp-only twins) |

### Best cells per strategy (walk-forward OOS, net of lab fees/slippage + funding on perps)
| strategy | venue / asset / interval | OOS | B&H | Sharpe | maxDD (B&H DD) | lab DSR | last 30d | modal params |
|---|---|---|---|---|---|---|---|---|
| at_core_long_perp | dydx SOL 1d | +79.1% | +65.1% | 3.55 | 12.0% (13.1%) | 0.61 | +18.2% | L=5d θ=0.05 α=2.0 |
| at_core_long | kucoin SOL 1d | +74.6% | +65.2% | 3.40 | 12.8% (13.2%) | 0.58 | +18.4% | same |
| tsmom | kucoin ETH 1h/4h/1d | +43.6% | +49.1% | 3.36/3.34/2.80 | 9.1% (17.4%) | 0.00 (1h) | +11.9% | lookback 90d |
| tsmom_perp | hyperliquid ETH 1d | +36.7% | +49.3% | 1.94 | 21.6% (13.6%) | 0.24 | +11.0% | 30d |
| at_core_ls | dydx SOL 1d | +31.5% | +65.1% | 2.09 | 12.8% (13.1%) | 0.27 | −3.1% | L=2d θ=0.02 α=2.0 |
| at_portfolio | dydx BTC+ETH+SOL 1d | +20.9% | +49.1% | 2.11 | 11.8% (10.5%) | 0.27 | +9.2% | λ=0.7 |
| tsmom_vs_perp | dydx BTC 1d | +11.2% | +32.3% | 2.81 | 3.3% (11.6%) | 0.43 | +2.3% | 30d |
| tsmom_vs | kucoin BTC 4h | +9.9% | +32.2% | 3.76 | 2.1% (13.3%) | 0.01 | +2.7% | 30d |

Per-strategy summary over all cells (mean OOS / mean B&H / #cells beating B&H on return / #cells OOS>0): at_core_long_perp 27.5 / 47.8 / 2 / 19 of 22; at_core_long 26.9 / 48.8 / 1 / 8 of 9; tsmom 31.2 / 48.8 / 3 / 9 of 9; tsmom_perp 13.2 / 47.8 / 7 / 15 of 22; at_core_ls −2.2 / 47.8 / 0 / 11 of 22; at_portfolio 9.2 / 44.7 / 0 / 14 of 14; tsmom_vs_perp 4.3 / 47.8 / 0 / 15 of 22; tsmom_vs 7.0 / 48.8 / 0 / 9 of 9.

### Which factors mattered
* **Interval:** mean OOS 1d 18.5% (n=46), 4h 11.0% (n=37), 1h 8.6% (n=46); in-sample mean Sharpe 1.56 / 0.84 / 0.10. (Partly the DSR/turnover-cost story: 1h turnover is expensive at 9–40 bps; the paper's own claim that 6h beats 1d is untestable here.)
* **Venue** (mean OOS): kucoin 21.7% (27 cells, spot long-only, mostly long strategies → more B&H beta), dydx 10.7%, hyperliquid 10.5%, deribit 10.0%.
* **Asset:** BTC 16.6%, ETH 13.1%, 3-asset portfolio 10.8%, SOL 9.4%, BTC+ETH 7.9% (mean over all strategies). The best single cells are SOL only because SOL rose most (B&H +65%).
* **Long vs long/short:** long-only ≫ long/short in this uptrend (at_core_long_perp 27.5% vs at_core_ls −2.2% mean). **λ:** 0.7 beat 0.5 (mean in-sample Sharpe 1.36 vs 1.10; mean in-sample return 16.8% vs 10.7%), consistent with the paper's drift argument, but this is just "more long".
* **Params** (in-sample mean Sharpe): L=5d 1.40 (2d 0.75, 10d 0.69); θ 0.05 1.04 vs 0.02 0.86; α 3.0 1.11 vs 2.0 0.79; TSMOM lookback 30d 1.46 vs 90d −1.16.
* **In-sample vs OOS gap:** small for the top cells (e.g. dYdX SOL 1d: best-of-grid full-sample +81.7% / Sharpe 2.69 vs OOS +79.1% / 3.55) because the lab's "in-sample" window overlaps the OOS window; the real instability is fold-to-fold: train Sharpe 1.11 → OOS +24.0%; 4.58 → −10.6%; 1.74 → +36.8%; 2.72 → +18.2% (the highest train Sharpe was the losing fold).
* **Regimes:** with 120 daily bars only `lowvol-down` (31 bars, ann. +21%) is "sufficient" for the top cell, so `favorable_regimes=['lowvol-down']`, `unfavorable=[]` (see bug #8).

## 3. Data checks across venues (adversarial)
Commands: `fetch.py`/`xcheck` scripts (kept in the session scratchpad; the repro one-liners are in the bug list).
* **Price scale/bar-open convention: consistent.** Median close ratio vs KuCoin spot = 0.9994–0.9996 on dYdX, Hyperliquid, Deribit (perps ≈ 4 bps under USDT spot, consistent for all assets and intervals); same-timestamp return correlation 0.98–1.00 at lag 0 and ≈0 at lags ±1 (no bar-shift). BitMEX BTC ratio 0.9990; **BitMEX ETH 1h max deviation 4.48%, return correlation 0.968** (illiquid/settling).
* **Gaps:** none (missing_pct 0.0, max_gap 0) for kucoin/dydx/hyperliquid/deribit; BitMEX ends 2026-09-16 12:00 (15.5 days short), not flagged.
* **History length:** Hyperliquid 1h has 4985 bars only (API serves the last ~5000 candles) → 27.7 days of warm-up instead of 120 (bug #5).
* **Funding units/sign:** all hourly except BitMEX (8h, 3 events/day). Annualised sums over the window: dYdX BTC −3.5%, ETH −2.8%, SOL −5.6%; Hyperliquid BTC +5.2%, ETH +6.1%, SOL +0.5%; Deribit BTC +2.2%, ETH +1.4%; BitMEX BTC +0.5%, ETH +17.5% (ETH quanto; ends at settlement). Magnitudes are plausible; dYdX's sign differs from the others (not a unit bug as far as I can tell; funding definitions differ). Engine wiring verified: `md.funding.sum()` equals the adapter's event sum (dydx 1d −0.028757, hyperliquid 4h +0.042958, deribit 1d/1h +0.018023/+0.017962).
* **Engine accounting verified independently:** I re-implemented `at_core_long` on KuCoin SOL 1d with plain loops using each fold's chosen params: per-fold returns +22.88/−11.65/+35.82/+18.42%, total **+74.61%** = lab `oos_return_pct` 74.61%; my buy-and-hold **+65.16%** (net of 15 bps) = lab 65.16%. So the high numbers are real for this window, not an accounting artifact.
* Uniswap: not run (no key); nothing invented.

## 4. Governor replay
```
export GOVERNOR_DB_PATH=/tmp/gov-trend_following.db
python -m backend.governor.run ingest research/summaries/trend_following.json   # ok: viable true, 129 results
python -m backend.governor.run replay trend_following                           # trial 1 FINISHED, 3 bars, SHELVE
python -m backend.governor.run status
```
Replay of dYdX/SOL/1d (the recommended trial) on the last 72h of real candles: **SHELVE**. Checks: backtest viable ✔ ("passed OOS gates" — only because of the DSR bug); trial integrity ✔ (3/3 bars, 0 gaps, 0 errors); drawdown ✔ (0.83% vs limit 17.96%); return not below backtest p10 ✔ (−0.66% vs p10 −4.50%, mean +1.62%); **market regime favorable ✘** (now `lowvol-up`; favorable `['lowvol-down']`). Watchlist entry created (`SHELVED`).
Extra stress (doctored copies of the summary kept outside the repo, recommending a 1h and a 4h cell): both ran clean (72/72 and 18/18 bars), both SHELVE on the regime check, but the "current regime" at the same instant was `lowvol-up` (1d), `highvol-up` (1h), `midvol-up` (4h) — see bug #8.

## 5. Bugs & friction
Format: file — symptom — repro — suggested fix.

1. **[HIGH, wrong gate] Deflated Sharpe pools per-period Sharpe variance across intervals.** `research/lab/sweep.py` (`evaluate_cell` appends `sharpe/sqrt(bpy)` to `trial_sr`; `run_sweep` uses one `sr_var` for every cell). The threshold SR₀ therefore differs by interval: lab pooled SR₀ per-period = 0.1616, i.e. **annualised 15.1 (1h), 7.6 (4h), 3.1 (1d)**, while the annualised trial-Sharpe spread is ≈1.0–1.2 for every interval. 1h/4h cells can **never** pass (0 of 83 do), and 1d cells pass against a lenient 3.1. Result: `viable: true` for 4 cells (3 SOL long-only, tsmom_vs BTC 1d); recomputed with annualised pooling (var of annualised SR, divided by bpy per cell) SR₀ ≈ 4.0 and **0 of 129 cells reach DSR 0.5** (best 0.44). Repro: wrap `evaluate_cell` to capture `trial_sr` per interval (script `dsr_diag.py`, essentially: `sd_annual = std(trial_sr_interval)*sqrt(bpy)`; prints the above). Fix: pool annualised Sharpes, pass `var_ann / bpy` into `deflated_sharpe` per cell (or compute DSR per interval); also pass the strategy-internal grid size when a plugin optimises internally (`at_portfolio` re-optimises 12×2 combos monthly, not counted in `n_trials`).
2. **[HIGH, accounting] Funding is only loaded for strategies with `requires_funding=True`, and such strategies are skipped on venues without funding** (`sweep.py` `build_market_data(..., with_funding=strat.requires_funding)` and the `needs funding rates` skip). A perp backtest of an ordinary strategy therefore ignores funding entirely (longs pay/shorts earn nothing), and a strategy can't be both spot-capable and funding-aware. I worked around it with perp twin plugins, which doubles the plugin count and trials. Fix: load funding whenever `ad.has_funding`; make `requires_funding` mean "fail without it".
3. **[MED, perf/cache] Disk cache never hits across runs with the default end.** `run_sweep.py` defaults `--end` to `now` in ms, and the cache key includes exact start/end (`venues.py candles`, `cache._path`), so every default re-run re-downloads (dYdX 1h ≈ 55 s per asset-interval; funding ≈ 52 s per asset, re-fetched per interval because its key uses the cell's first/last ts). Repro: run the sweep twice without `--end`. Fix: align `end` to the bar/day, key the cache by (venue, asset, interval) and extend/merge ranges. I pinned `--end` to get cache hits.
4. **[HIGH, data] BitMEX adapter points at settled contracts and its candles violate OHLC.** `venues.py BitMEX.assets` (XBTUSD, ETHUSD). Evidence: `GET /api/v1/instrument?symbol=XBTUSD` → `state: Settled, expiry 2026-09-16T12:00Z` (also XBTUSDT, ETHUSDT; SOLUSDT settled 2026-09-02); `/instrument/active` lists only unlisted spot pairs. Candles stop 2026-09-16 (last_ts 1789556400000 vs end 1790899200000), include flat stale bars (e.g. 78521.9 O=H=L=C for hours on 09-15), and 707/6828 (BTC) and 1369/6828 (ETH) hourly bars have open outside [low, high] (BitMEX opens at the previous close). `validate_candles` computes `missing_pct` against its own last ts, so the 15.5-day truncation is invisible there, and `build_market_data` rejects the cell as "poor data" — the log reason hides the real cause. Repro: `python -c "from research.lab.venues import get_venue as g; import research.lab.data as d; r=g('bitmex').candles('BTC','1h',START,END); print(d.validate_candles(r,'1h'))"`. Fix: drop/flag settled symbols (check `state`), compare the last bar with the requested `end_ms`, and normalise high/low to include open/close for venues that stamp opens from the prior close.
5. **[MED, silent bias] Warm-up shortfall is not checked.** Hyperliquid serves ≤5000 1h candles (4985 bars from 2026-03-08), so 1h cells have 27.7 days of warm-up instead of the configured 120, but `run_sweep` only enforces coverage of the analysis window (`0.97*window`) and `missing_pct` is relative to the first bar. Effect: `tsmom*` with a 90-day lookback is flat for the first ~62 days, and regime EMAs are cold. Fix: require `md.ts[0] <= warm_start` or record `warmup_days_available` in `data_quality` and skip/flag.
6. **[MED, silent skip] Deribit 1d bars open at 08:00 UTC**, 8 h after every other venue (first_ts 1765008000000 vs 1764979200000). Cross-venue `shared bars = 0`, `cross_check` returns `ok: None`, and `run_sweep` treats `None` as a pass, so Deribit 1d is never price-checked and its daily bars cover a different day. Fix: treat `ok: None` as a warning in `data_quality`, and either document/align the daily boundary or compare via 1h data.
7. **[LOW] Coverage gaps in adapters:** Deribit and BitMEX have no 4h (not aggregated from 1h even though 1h exists), Deribit/BitMEX have no SOL mapping (I did not verify whether Deribit lists a SOL perp), Uniswap has no SOL pool and no 4h; 6h (the paper's interval) is not available anywhere. Suggest aggregating 1h→4h/6h inside the lab so every perp venue has the same interval set.
8. **[MED, governor] Regime labelling is inconsistent and too thin to gate on.** (a) `regime_labels` tercile cut-offs and EMA are computed on whatever series is passed: backtest = the cell's own interval over ~300d; `current_regime` (watchlist) = 300 daily bars; replay = ~760 bars of the cell interval (`backend/governor/service.py run_replay`). Same instant, SOL/dYdX: `lowvol-up` (1d replay), `highvol-up` (1h replay), `midvol-up` (4h replay). (b) `favorable_regimes` only counts regimes with ≥5% share and ≥30 bars (`regime_performance`): in a 120-bar daily OOS that left a single regime (`lowvol-down`), so the "regime favorable" gate was effectively "is the market in the one regime that had 31 bars". Fix: label every interval with the same daily-based definition, and mark favorable only when a regime has enough bars AND is consistent across folds; otherwise return "unknown" and don't gate on it.
9. **[LOW/MED, governor] Replay drops funding.** `backend/governor/run.py _replay` calls `build_market_data(..., with_funding=False)` even when the strategy `requires_funding`, so replay accounting differs from the backtest (live `tick_trial` passes `strat.requires_funding`). Found by code reading; effect not measured. Also trial Sharpe is computed on 3 bars (−8.8) and printed as if meaningful.
10. **[LOW] OOS is 4 months, not 6.** 60d train + 4×30d tests = 120 OOS bars (1d). The brief/README say "6-month walk-forward"; reports should say "6 months of data, 120 days OOS". Also Sharpe for a constant-weight strategy changes with bar size (`tsmom` kucoin ETH: 3.36 @1h, 3.34 @4h, 2.80 @1d for the same +43.6% return), so Sharpe isn't comparable across intervals.
11. **[LOW] `research/preflight.py` reports OK for HTTP 404/451.** The run printed `OK uniswap(the graph) HTTP 404` (expected, no key) and `OK binance (control: VolHelix live feed) HTTP 451`; 451 means Binance blocks this environment (a later log line: "Service unavailable from a restricted location"). The control host is the one the live VolHelix feed uses, so the line should be BAD/WARN.
12. **[LOW] `pytest research` triggers a live Binance call** (an ERROR line from `backend.mcp.client get_all_tickers` appears during collection via the `backend.quant.backtest` import) — tests should be hermetic. Also pytest's reported paths are wrong when run as `pytest research backend/governor` (rootdir becomes `backend`, files print as `backend/tests/...`), and the governor tests live in `backend/tests/test_governor.py` (10 pass), not `backend/governor`.
13. **[INFO] `viability_reasons` is empty when viable** and `RULES["must_beat_p10"]` in `backend/governor/report.py` is unused (the p10 check is unconditional). Both harmless.
14. **[INFO] I did not need any LOCAL-PATCH**; no lab/backend file was modified.

## 6. Reproduce
```bash
python3 -m venv .venv-trend_following && . .venv-trend_following/bin/activate && pip install -r research/requirements.txt
python -m research.preflight --sources https://arxiv.org/pdf/2602.11708
pytest research/strategies/trend_following
python -m research.run_sweep --slug trend_following --title "..." --url https://arxiv.org/pdf/2602.11708 --venues kucoin dydx hyperliquid deribit bitmex uniswap --assets BTC ETH SOL --intervals 1h 4h 1d --months 6 --end 1790899200000
GOVERNOR_DB_PATH=/tmp/gov-trend_following.db python -m backend.governor.run ingest research/summaries/trend_following.json
GOVERNOR_DB_PATH=/tmp/gov-trend_following.db python -m backend.governor.run replay trend_following
```
