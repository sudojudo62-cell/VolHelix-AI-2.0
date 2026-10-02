# Live Desk: live signals, signal log and live exchange adapter

The Live Desk (`/live`) is the live counterpart of the paper dashboard. It shows **only** data received from the
production Binance streams and the live account; when a stream is not live it says `NO LIVE DATA — <reason>` instead of
substituting numbers. (The shared top bar and the sidebar balance card carry paper/simulated figures, so both are hidden on
this page.)

> **Not verified against the live exchange.** This repository's CI/sandbox has no market-data or exchange access, so the
> adapter is covered by unit tests with a fake client only. Start in test mode, then use the minimum order size.

## What was added

| Piece | Where |
|---|---|
| Live feature snapshot + flow-confluence from real buffered data | `backend/signals/live_feed.py` |
| Persistent signal log with 1h/4h/24h forward-return labels, calibration query | `backend/signals/signal_log.py`, `sampler.py` |
| Paper scan evaluations also logged (`mode=paper`) | additive hook in `auto_trader.py` |
| Live Binance adapter (guard, audit, risk gate, OCO/flatten) | `backend/exchange/` |
| API | `backend/api/live_routes.py` (`/api/live/*`) |
| Dashboard | `frontend/src/app/live/page.tsx`, `frontend/src/lib/live.ts` |

## Safety model (enforced in code, covered by tests)

1. **Read-only by default.** `LIVE_TRADING_ENABLED=false` rejects every order attempt.
2. **Test mode by default once enabled.** `LIVE_ORDER_MODE=test` validates orders with Binance `/order/test`; nothing executes.
   Real orders need `LIVE_ORDER_MODE=live` **and** the confirmation phrase `I UNDERSTAND THIS USES REAL FUNDS` on every order.
3. **Access token.** Account, orders, order, close and kill-switch endpoints require header `X-Live-Token` equal to
   `LIVE_API_TOKEN`; with the token unset they are refused (403). Market-derived endpoints (status, snapshots, signals) are public.
4. **Hard limits.** Symbol allowlist (default: watched symbols), $10 minimum, `LIVE_MAX_ORDER_USDT` per order,
   `LIVE_MAX_DAILY_NOTIONAL_USDT` per UTC day (persisted, survives restarts).
5. **Deterministic risk gate.** Every entry goes through `CryptoRiskGate` with the **real** account equity. The adapter refuses if
   equity reads 0 (the gate would otherwise assume a fictional $10,000 NAV) or the account cannot trade.
6. **Long-only spot entries**, market BUY by USDT amount, stop < price < take-profit, reward:risk ≥ 1.5.
7. **Never unprotected.** After a fill the adapter places an OCO (take-profit + stop-limit). If that fails it market-sells the
   position immediately; if that also fails the audit row is marked `UNPROTECTED` and logged critical (manual action required).
8. **Kill switch** (persisted) blocks new entries in every mode. It never blocks `/api/live/close`, which cancels the symbol's open
   orders and market-sells the free balance.
9. **Audit trail.** Every attempt (accepted or rejected) is stored in `backend/data/live.db` with an idempotent client order id.
10. There is **no autonomous live trading loop**. Live orders are placed manually (API or dashboard).

## Setup

```bash
# .env — use a dedicated production API key: trading enabled, WITHDRAWALS DISABLED, IP-restricted
LIVE_BINANCE_API_KEY=...
LIVE_BINANCE_API_SECRET=...
LIVE_API_TOKEN=$(openssl rand -hex 24)
LIVE_TRADING_ENABLED=true
LIVE_ORDER_MODE=test          # switch to "live" only after test mode works
LIVE_MAX_ORDER_USDT=15
LIVE_MAX_DAILY_NOTIONAL_USDT=45
```

Then open `/live`, paste the token, and use **Validate order (test)**. Note `MAX_POSITION_PCT` (2.5% of NAV) also applies: a
$15 order needs about $600 of equity.

## Signal log and calibration

`signals.db` stores every evaluation with features, decision, price and later forward returns. `GET /api/live/signals/calibration`
returns average forward return and hit rate per score bucket, **before fees**. This is the data needed to calibrate the 0.70
confluence threshold and Kelly sizing (roadmap steps 2 and 5 in `STRATEGY_RESEARCH.md`). Labels are the first price seen *after*
each horizon, so they are never early, but can be a little late.

## Fixes made to the live-feed path along the way

- `hub.get_metrics()` returned an all-zero placeholder `FlowMetrics`, feeding fabricated data into the confluence gate. It now
  returns real metrics built from the buffered stream, or `None` (and `/api/flow/metrics` was unimplemented; it now serves the
  live snapshot).
- `DomAnalyticsEngine` did not exist, so `/api/flow/dom` raised `ImportError` and the websocket broadcaster task crashed on
  start. Added the alias.
- `LocalOrderBook.get_snapshot()` did not exist; added.

## Known limitations

- CVD/VWAP cover the trades currently in the bounded buffer, not the full 00:00-UTC session (`cvd_scope: buffered_trades`).
- The OCO call uses python-binance's `create_oco_order`; Binance has been migrating spot OCO endpoints, so this must be checked
  on first live use (the flatten fallback keeps positions safe if it fails).
- The existing paper `get_account()` values equity as USDT only (ignores non-USDT holdings). Not changed here.
