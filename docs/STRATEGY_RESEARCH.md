# Strategy Research: Improving VolHelix Returns

_Researched 2026-10-02. This is a literature/web survey mapped onto the current codebase. Nothing here is a
performance guarantee, and none of it has been backtested on VolHelix yet. Several sources are practitioner
blogs rather than peer-reviewed work; they are marked._

## Where VolHelix is today

- **Spot only** on Binance Spot Testnet: long entries, exits by selling holdings, no shorting or perps.
- Entry gate: order-flow confluence score ≥ 0.70 (`backend/engine/flow_confluence.py`), then LLM debate, then the
  deterministic 10-rule risk gate.
- Exits: fixed per-symbol stop/take-profit percentages (`risk_gate.py`: BTC 2%/4%, ETH 2.5%/5%, SOL 3.5%/7%…).
- Sizing: half-Kelly capped at 2.5% NAV (`position_sizer.py`), regime multipliers.
- No cost-aware backtester gates strategy changes (only `scripts/flow_replay.py` for flow replay).

## Findings, ranked by fit and evidence

| # | Idea | Evidence | Fits spot-only? | Expected effect |
|---|------|----------|-----------------|-----------------|
| 1 | **Validation first**: fee/slippage-aware backtests, walk-forward, deflated Sharpe | Strong (Bailey & López de Prado) | Yes | Stops false edges; no direct return |
| 2 | **Trend / time-series momentum + volatility targeting** | Moderate; vol-scaling raised Sharpe in cited study | Yes (long/flat) | Better risk-adjusted return, smaller drawdowns |
| 3 | **Vol-scaled exits and sizing** (ATR stops instead of fixed %) | Moderate (same vol-scaling result) | Yes | Fewer noise stop-outs |
| 4 | **Order-flow imbalance as short-horizon timing filter** | Moderate; effect is state-dependent | Yes | Better entries; costs dominate at short horizons |
| 5 | **Funding-rate / basis carry** | Moderate (practitioner sources) | **No** (needs perps/futures) | ~3-8% bear, ~10-40% bull annualized, per sources |
| 6 | **Cointegration pairs trading** | Moderate (academic, sensitive to costs) | **No** (needs shorting) | Market-neutral, pair selection risk |
| 7 | **LLM agents as signal source** | Weak to negative | Yes | Treat as filter/explainer, not alpha |

### 1. Validation (highest priority)
- Selection bias is the main reason backtests mislead: with ~1,000 random signals the best shows a Sharpe near 3.7
  by chance alone. The Deflated Sharpe Ratio corrects for the number of trials and non-normal returns.
- Use walk-forward evaluation and purged/embargoed cross-validation so the test period never leaks into training.
- A practitioner example: one 49-strategy BTC backtest ranked a strategy first that made only 2 trades in 37
  months, and a day-of-week strategy fell from 308% to 44.5% (Sharpe 0.77) after fixing backtest bugs. Blog-grade
  source, but it illustrates how easily results are inflated.
- **For VolHelix:** build a replay backtester that charges Binance taker/maker fees and slippage, records the number
  of parameter trials, and reports deflated Sharpe. Require it for any strategy or threshold change.

### 2. Trend following with volatility targeting
- Academic work finds time-series momentum earns abnormal returns across asset classes and that volatility-scaled
  position sizing increases cumulative return and Sharpe; it works better in more volatile assets such as crypto.
- Practitioner backtests (blog-grade) found mean-reversion variants had negative Sharpe on BTC while trend/momentum
  variants did better. Treat as a hypothesis to test, not a result.
- **For VolHelix:** add a daily/4h trend filter (e.g. price vs. long moving average, or multi-timeframe agreement)
  as a regime gate: long only when the trend is up, otherwise hold USDT. The existing `Regime` classifier is the
  natural place. Scale size inversely to realized volatility.

### 3. Volatility-scaled exits and sizing
- Fixed 2-3.5% stops sit inside normal intraday noise for SOL/XRP and can be too loose for BTC in calm regimes.
- **For VolHelix:** set stop/TP as multiples of ATR or Parkinson volatility (the project already computes the
  latter), keep reward:risk ≥ 2 after fees, and use Kelly only once the realized win rate and payoff are measured
  from the ledger (half-Kelly is already applied).

### 4. Order-flow imbalance
- Order-flow imbalance, spreads, depth and trade arrival explain return variation at very short horizons, and
  recent crypto studies find a compact set of top-of-book and trade-flow features is predictive across assets.
- The effect is **state-dependent**: stronger in disequilibrium (liquidity-stressed) states.
- At these horizons, round-trip fees are the main threat. Use limit orders where possible.
- **For VolHelix:** calibrate the 0.70 confluence threshold against realized forward returns net of fees rather
  than treating it as fixed; add a state filter (spread/depth stress) to the confluence score; use the signal for
  entry timing inside a higher-timeframe trend, not as a standalone strategy.

### 5. Funding-rate and basis carry (needs a futures account)
- Long spot plus short perp earns the funding rate with little directional exposure. Sources report roughly 10-40%
  annualized in bull phases and 3-8% in bear phases (practitioner sources, historical, not guaranteed).
- Risks: funding can turn negative, the perp leg can be liquidated, and entry/exit costs two spreads.
- **For VolHelix:** out of scope for the current spot-only design. If wanted, it needs a futures client, margin and
  liquidation-distance rules in the risk gate, and separate paper-trading validation.

### 6. Cointegration pairs trading (needs shorting)
- Dynamic cointegration pairs (e.g. ETC/FIL in one study) beat buy-and-hold on a futures venue with reasonably low
  drawdown, using Engle-Granger/Johansen tests and Ornstein-Uhlenbeck half-life to choose pairs and windows.
- Results are unreliable if transaction costs and pair-selection bias are ignored.
- Same infrastructure constraint as #5.

### 7. LLM agents
- Reported LLM-agent alpha is often not deployment evidence: studies find information leakage (memorized
  tickers/dates), collapse after transaction costs, and no multiple-testing correction.
- **For VolHelix:** keep the deterministic risk gate authoritative (it already is), and use the LLM debate as a
  veto/explanation layer. Measure its marginal value: log signals with and without the debate and compare net
  outcomes before trusting it to add return.

## Suggested roadmap (smallest steps first)

1. Backtester with fees/slippage + walk-forward + deflated Sharpe (gates everything else).
2. Log every signal and decision with features and forward returns (also needed to calibrate Kelly and the 0.70
   threshold).
3. Trend regime filter + volatility-scaled sizing; compare against the current baseline net of fees.
4. ATR/vol-scaled stops and take-profits.
5. Calibrate and state-filter the order-flow confluence score.
6. Only then consider futures-based carry or pairs, as a separate, separately-validated module.

## Realistic expectations

No source here supports promising a specific return. The most reliable improvements are lower costs, fewer
false-positive trades and smaller drawdowns, which compound into a better net percentage. Strategy edges in crypto
decay and regime-dependent results (bull vs. bear carry yields, momentum vs. mean reversion) are large.

## Sources

- [Systematic Trend-Following with Adaptive Portfolio Construction in Crypto (arXiv)](https://arxiv.org/pdf/2602.11708)
- [Momentum and Trend Following Strategies for Currencies Revisited (SSRN)](https://papers.ssrn.com/Sol3/Delivery.cfm/SSRN_ID2949379_code2672176.pdf?abstractid=2949379)
- [High frequency momentum trading with cryptocurrencies (ScienceDirect)](https://www.sciencedirect.com/science/article/abs/pii/S0275531919308062)
- [I Backtested 49 Crypto Trading Strategies (DEV Community, blog)](https://dev.to/maymay5692/i-backtested-49-crypto-trading-strategies-heres-every-single-result-4gg5)
- [Day-of-week momentum strategy for crypto (Medium, blog)](https://medium.com/coinmonks/day-of-week-momentum-strategy-for-crypto-with-308-annual-returns-and-sharpe-2-96-but-4bd4ef0a31a8)
- [Dynamic Cointegration-Based Pairs Trading in Crypto (arXiv)](https://ar5iv.labs.arxiv.org/html/2109.10662)
- [Crypto Pairs Trading series (Amberdata, practitioner)](https://blog.amberdata.io/empirical-results-performance-analysis)
- [When Does Order Flow Matter? State-Dependent L2 Liquidity-State Transitions in Crypto Futures (arXiv)](https://arxiv.org/pdf/2607.09230)
- [Hawkes-based cryptocurrency forecasting via Limit Order Book data (arXiv)](https://arxiv.org/pdf/2312.16190)
- [Carry Trade (tradingstrategy.ai)](https://tradingstrategy.ai/docs/learn/carry-trade.html)
- [The crypto arbitrage playbook (CCXT docs blog)](https://docs.ccxt.com/blog/crypto-arbitrage-strategies)
- [Funding rate arbitrage: when the basis pays and when it does not (practitioner)](https://athenum.mataroa.blog/blog/funding-rate-arbitrage-how-the-perp-spot-basis-pays-and-when-it-does-not/)
- [The Deflated Sharpe Ratio (SSRN)](https://papers.ssrn.com/abstract=2460551)
- [Statistical Overfitting and Backtest Performance (SSRN)](https://papers.ssrn.com/abstract=2507040)
- [Backtesting & Research Methodology (tradingstrategy.ai)](https://tradingstrategy.ai/docs/learn/backtesting.html)
- [What survives honest evaluation? LLM-driven trading strategy discovery (arXiv)](https://arxiv.org/pdf/2608.27734)
- [The Alpha Illusion: Reported Alpha from LLM trading agents (alphaXiv)](https://www.alphaxiv.org/abs/2605.16895)
