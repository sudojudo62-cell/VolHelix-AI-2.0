# Strategy research pipeline and governing agent

```
source (paper/blog) --> research agent (own venv, cloud session)
   | enumerates every strategy, implements causal plugins, sweeps venues x assets x intervals x params over 6 months
   v
research/summaries/<slug>.json  --(ingest)-->  dashboard governing agent (backend/governor)
   |                                              | starts a 72h PAPER trial on live public candles
   v                                              v
.claude/skills/<slug>-lab/SKILL.md           decision report: INTEGRATE (needs human approval) | SHELVE (+ watchlist)
```

## Pieces
| Path | Role |
|---|---|
| `research/lab/venues.py` | Public-data adapters: KuCoin, dYdX v4, Hyperliquid, Deribit, Bitfinex, Uniswap v3 (needs free `THEGRAPH_API_KEY`); BitMEX kept but refuses retired contracts; 4h/6h aggregated from 1h |
| `research/lab/engine.py` | Causal vectorized simulator (next-bar-open fills, fees, slippage, funding), lookahead detector |
| `research/lab/sweep.py` | Factor sweep, 6-month walk-forward OOS, Deflated Sharpe over *all* trials, regimes, factor attribution |
| `research/lab/summary.py` | Summary-file schema handed to the governor |
| `research/AGENT_BRIEF.md`, `SKILL_TEMPLATE.md`, `sources.json` | Instructions/templates for each research agent |
| `backend/governor/` | Ingest, 72h paper trial (same accounting as the backtester, tested), decision rules, watchlist, approval |
| `backend/api/governor_routes.py` | `/api/governor/*` (reads public; writes need `X-Live-Token`) |

## Ranking rule
Strategies are ranked by **walk-forward out-of-sample net return** (parameters chosen on 60-day train windows, scored on the next
unseen 30 days, 6 folds = 6 months out-of-sample, after 90 days of indicator warm-up). Gates: OOS net return > 0, beats buy-and-hold on return or halves its drawdown, deflated
Sharpe >= 0.5 (computed over every configuration tried in the sweep, with the trial-variance taken per bar interval), max drawdown <= 35%. Last-30-day profit is shown, not ranked on.

## Governor decision (after 72h of live paper trading)
INTEGRATE only if all hold: backtest viable; trial integrity (>= 90% of bars, no data gaps/errors); drawdown within
max(8%, 1.5x the backtest's OOS drawdown); trial return not below the backtest's 72h 10th percentile; current market regime is one
the backtest found favorable (regimes are daily-based, need >= 20 days and >= 10% of the sample to count, and are reported as
"not gated" when the backtest could not name one). Otherwise SHELVE and add to the watchlist, which re-checks the live regime and flags
`READY_FOR_RETRIAL` after two consecutive favorable readings. **Nothing is integrated without human approval** (`approve`), and
approval only creates a paper sleeve record: it never touches the live exchange adapter.

## Running the 72h trial on your always-on host
```bash
git clone ... && cd VolHelix-AI-2.0 && python3 -m venv .venv && . .venv/bin/activate && pip install -r research/requirements.txt
python -m research.preflight                                   # venues reachable?
python -m backend.governor.run ingest research/summaries/<slug>.json
python -m backend.governor.run start <slug>                    # prints trial_id
nohup python -m backend.governor.run loop --interval-sec 300 > governor.log 2>&1 &
python -m backend.governor.run status                          # after 72h: report <trial_id>, then approve/reject
```
The loop ticks every running trial (a 1h-interval trial books a result each closed bar, so a 72h trial has ~72 data points; 4h has
18, 1d has 3) and re-checks the watchlist. Keep the host online: gaps lower the integrity check and push the result to SHELVE.

## Known limits
- 72 hours is a smoke test, not statistical proof; the gate compares it with the backtest's own 72h distribution.
- Order-flow strategies need historical L2/trade data that candle APIs do not provide; they can only be paper-traded live.
- Venue adapters were written from public API docs and mock-tested; the first real run is also their integration test.
- Fees/slippage are assumptions (`research/lab/engine.py::VENUE_COSTS`).

## Lessons from the pilot run (fixed)
The first research agent (trend-following) ran the whole workflow against real venues and found: a Deflated-Sharpe scaling bug that
made one cell look viable (pooled trial variance across intervals), funding ignored for ordinary strategies on perps, a retired
BitMEX contract set, silent history truncation and warm-up shortfalls, a Deribit daily-candle boundary mismatch, per-interval
regime labels that disagreed, and a replay that dropped funding. All are fixed and have regression tests. Its own conclusion
(the paper's headline Sharpe is not reproduced; only long-only SOL trend filters earned in one strong uptrend) is in
`research/reports/trend_following.md`; its numbers predate the fixes and are being re-run.

## Model 1: Pair Scanner (cross-sectional token ranker)

- `research/models/ranker.py`: causal, z-scored features → ridge, purged labels, block-bootstrap top-k probability, rank-IC health check.
- `research/strategies/pair_ranker/`: the same model as a sweep strategy (`pair_ranker`), so it goes through the normal OOS/deflation/72h-trial path.
- `python -m research.scanner --venue kucoin --loop 900` writes the snapshot the `/scanner` page reads (`SCANNER_SNAPSHOT_PATH` overrides).
- The page is paper-only. `GET /api/scanner/ticket` (token-gated) returns suggested order parameters only if a human has run
  `POST /api/governor/trials/{id}/authorize-live` (after the full 72h live paper trial, INTEGRATE, approved; `max_order_usdt` ≤ `LIVE_MAX_ORDER_USDT`),
  model health is `ok`, and the asset is a confident, fresh top-k pick. It never places orders; the live adapter's own safeguards still apply.
  `POST /api/governor/live-authorization/pair_ranker/revoke` removes access.
- Not yet validated on real data: the sandbox has no market-data egress. Run the sweep in a cloud session first.
