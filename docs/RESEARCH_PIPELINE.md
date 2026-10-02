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
| `research/lab/venues.py` | Public-data adapters: KuCoin, dYdX v4, Hyperliquid, Deribit, BitMEX, Uniswap v3 (needs free `THEGRAPH_API_KEY`) |
| `research/lab/engine.py` | Causal vectorized simulator (next-bar-open fills, fees, slippage, funding), lookahead detector |
| `research/lab/sweep.py` | Factor sweep, 6-month walk-forward OOS, Deflated Sharpe over *all* trials, regimes, factor attribution |
| `research/lab/summary.py` | Summary-file schema handed to the governor |
| `research/AGENT_BRIEF.md`, `SKILL_TEMPLATE.md`, `sources.json` | Instructions/templates for each research agent |
| `backend/governor/` | Ingest, 72h paper trial (same accounting as the backtester, tested), decision rules, watchlist, approval |
| `backend/api/governor_routes.py` | `/api/governor/*` (reads public; writes need `X-Live-Token`) |

## Ranking rule
Strategies are ranked by **walk-forward out-of-sample net return** (parameters chosen on 60-day train windows, scored on the next
unseen 30 days, 4 folds in 6 months). Gates: OOS net return > 0, beats buy-and-hold on return or halves its drawdown, deflated
Sharpe >= 0.5 (computed over every configuration tried in the sweep), max drawdown <= 35%. Last-30-day profit is shown, not ranked on.

## Governor decision (after 72h of live paper trading)
INTEGRATE only if all hold: backtest viable; trial integrity (>= 90% of bars, no data gaps/errors); drawdown within
max(8%, 1.5x the backtest's OOS drawdown); trial return not below the backtest's 72h 10th percentile; current market regime is one
the backtest found favorable. Otherwise SHELVE and add to the watchlist, which re-checks the live regime and flags
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
