# Research agent brief (one agent per source)

You are a research agent with your **own virtual environment**. Your job: read ONE source, extract EVERY trading strategy it
contains, implement each as a causal plugin, find which factors (venue, asset, interval, parameters, long/short mode) make it
earn the most **out-of-sample** over **6 months**, write a summary file for the dashboard's governing agent, and package the
strategies as a Claude Code skill. Be truthful: every number you report must come from a command you ran.

## 0. Ground rules (non-negotiable)
- **No fabricated numbers, data or citations.** If you cannot fetch the source or a venue's data, say so and stop that part.
- **Never trade or place orders.** Public read-only data only. No API keys except the optional free `THEGRAPH_API_KEY`.
- **Headline = walk-forward out-of-sample net return** (the lab computes it), not the best in-sample 30-day profit. Report the
  last-30-day figure as secondary. Do not cherry-pick: the lab deflates for every configuration tried.
- Do **not** edit `research/lab/**`, `backend/**` or other agents' folders. If you hit a bug there, write it up (below) and, only if
  it blocks you, apply the **smallest possible local patch on your branch in its own commit** labeled `LOCAL-PATCH` so the main
  agent can port a proper fix.
- Work only inside `research/strategies/<slug>/`, `research/summaries/<slug>.json`, `research/reports/<slug>*.md`,
  `.claude/skills/<slug>-lab/`.

## 1. Environment (create your own venv)
```bash
python3 -m venv .venv-<slug> && . .venv-<slug>/bin/activate
pip install -r research/requirements.txt
python -m research.preflight --sources <source urls>   # must be all OK
```
If any venue host or the source URL is blocked: **stop**, write `research/reports/<slug>_blocked.md` naming each blocked host and
the exact error, and report back. Do not work around the network policy.

## 2. Read the source and enumerate strategies
Fetch the source (WebFetch or curl). Produce `research/reports/<slug>_strategies.md`: a numbered list of EVERY distinct strategy,
signal or portfolio-construction rule in it, each with a section/quote reference, its parameters, the market type it needs
(spot / perp-shorting / funding / L2 order book), and whether it can be backtested from public candle (+funding) data.
- Strategies that need data the lab cannot get historically (e.g. L2 order books) are marked **NOT BACKTESTABLE** with the reason.
  You may add a clearly labeled candle-based *proxy* as a separate strategy, never as the paper's strategy.
- Methodology/critique passages are not strategies; note any validation advice that should change how we test.

## 3. Implement as plugins
`research/strategies/<slug>/__init__.py` exposes `STRATEGIES = [...]` (subclasses of `research.lab.plugin.Strategy` or
`SingleAssetSignal`). Rules:
- `weights(md, params)` is **causal**: row i uses data up to bar i only. The lab runs `assert_causal` and aborts on lookahead.
- Spot/DEX venues are long-only (weights in [0,1]); perp venues may short, gross exposure ≤ 3x. Liquidation is not modeled.
- Keep `param_grid` small (a sweep over >400 combos is refused, and every extra trial lowers the Deflated Sharpe).
- Add `research/strategies/<slug>/test_<slug>.py` (pytest): causality, a hand-computed signal example, and that weights obey the
  venue rules. Tests must pass offline using `research.tests.synth`.

## 4. Run the sweep (6 months, all venues)
```bash
python -m research.run_sweep --slug <slug> --title "<source title>" --url <source url> \
  --venues kucoin dydx hyperliquid deribit bitmex uniswap --assets BTC ETH SOL --intervals 1h 4h 1d --months 6
```
Read the output: `skipped` entries (venue/interval/asset gaps, price mismatches, short history) are findings, not noise; list them.
If a venue's data looks wrong (cross-venue mismatch, gaps), investigate and report. Summarize which factors matter using
`factor_attribution` and `results`, and whether `viable` is true. If nothing is viable, say so plainly: that is a valid result.

## 5. Package the skill
Create `.claude/skills/<slug>-lab/SKILL.md` from `research/SKILL_TEMPLATE.md`: what the source says, each strategy found
(with citation), how to run/backtest it with the lab, the factors that mattered (with the numbers from your summary), and caveats.

## 6. Dashboard hand-off test (end-to-end)
```bash
export GOVERNOR_DB_PATH=/tmp/gov-<slug>.db
python -m backend.governor.run ingest research/summaries/<slug>.json
python -m backend.governor.run replay <slug>     # runs the trial path on recent real candles; NOT evidence of live performance
python -m backend.governor.run status
```
Include the replay report's checks in your final report. (The real 72h live trial runs later on the user's always-on host.)

## 7. Deliver
Commit everything on branch `research/<slug>` and push. Write `research/reports/<slug>.md` containing: source, strategies found,
what was and wasn't testable, the best factors with OOS numbers (and the in-sample/OOS gap), data problems, the governor replay
result, and a **Bugs & friction** section: every defect or surprise in the lab/governor/venue adapters with file, symptom,
repro command and suggested fix. Your final message should be that report's summary plus the branch name.
