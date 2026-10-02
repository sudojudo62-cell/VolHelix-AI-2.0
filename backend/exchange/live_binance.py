"""Binance Spot LIVE adapter (production endpoint, real funds).

Safety design (all enforced in code, see guard.py / tests):
  * read-only unless LIVE_TRADING_ENABLED; /order/test mode unless LIVE_ORDER_MODE=live + confirmation phrase
  * long-only MARKET BUY by quote amount; exits via OCO placed right after the fill; if the protective OCO cannot
    be placed the position is immediately flattened (we never hold an unprotected position)
  * every entry passes the deterministic CryptoRiskGate with the REAL account equity (fails closed on equity <= 0,
    because the gate would otherwise substitute a fictional $10,000 NAV)
  * every attempt, accepted or rejected, is written to the audit table with an idempotent client order id

NOT VERIFIED against the live exchange in this repository (no network in CI/sandbox). First use: LIVE_ORDER_MODE=test,
then a minimum-size live order while watching the exchange UI.
"""
import time
import uuid
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from typing import Any, Callable, Dict, Optional

from loguru import logger

from backend.config import settings
from backend.exchange import audit, guard
from backend.models.market import CryptoAssetBalance, Regime, StrategyType
from backend.models.portfolio import PortfolioSnapshot
from backend.models.trade import TradeProposal
from backend.risk.risk_gate import CryptoRiskGate

MIN_REWARD_RISK = 1.5
STOP_LIMIT_BUFFER = 0.002  # stop-limit price sits 0.2% below the stop trigger so the limit leg can fill


class LiveOrderError(Exception):
    def __init__(self, code: str, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}


def _floor_to_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    d = (Decimal(str(value)) / Decimal(str(step))).to_integral_value(rounding=ROUND_DOWN) * Decimal(str(step))
    return float(d)


class LiveBinanceAdapter:
    def __init__(self, client: Any = None, risk_gate: Optional[CryptoRiskGate] = None,
                 price_fn: Optional[Callable[[str], Optional[float]]] = None):
        self._client = client
        self._gate = risk_gate or CryptoRiskGate()
        self._price_fn = price_fn

    # ── client / market data ────────────────────────────────────────────────
    @property
    def client(self):
        if self._client is None:
            if not guard.keys_configured():
                raise LiveOrderError("no_keys", "Live API keys are not configured")
            from binance.client import Client as BinanceSDKClient  # production endpoint: testnet=False
            c = BinanceSDKClient(settings.LIVE_BINANCE_API_KEY, settings.LIVE_BINANCE_API_SECRET, ping=False)
            try:
                c.timestamp_offset = c.get_server_time()["serverTime"] - int(time.time() * 1000)
            except Exception as exc:
                logger.warning(f"Live client time sync failed: {exc}")
            self._client = c
        return self._client

    def price(self, symbol: str) -> float:
        px = self._price_fn(symbol) if self._price_fn else None
        if not px:
            px = float(self.client.get_symbol_ticker(symbol=symbol)["price"])
        if not px or px <= 0:
            raise LiveOrderError("no_price", f"No live price for {symbol}")
        return float(px)

    def _filters(self, symbol: str) -> Dict[str, Any]:
        info = self.client.get_symbol_info(symbol) or {}
        return {f["filterType"]: f for f in info.get("filters", [])}

    # ── account ─────────────────────────────────────────────────────────────
    def get_account(self) -> Dict[str, Any]:
        raw = self.client.get_account()
        tickers = {t["symbol"]: float(t["price"]) for t in self.client.get_all_tickers()}
        balances: Dict[str, Dict[str, float]] = {}
        unpriced = []
        equity = 0.0
        for b in raw.get("balances", []):
            free, locked = float(b["free"]), float(b["locked"])
            if free <= 0 and locked <= 0:
                continue
            asset = b["asset"]
            px = 1.0 if asset == "USDT" else tickers.get(f"{asset}USDT")
            if px is None:
                unpriced.append(asset)
                value = 0.0
            else:
                value = (free + locked) * px
            equity += value
            balances[asset] = {"free": free, "locked": locked, "total": free + locked, "value_usdt": value}
        return {
            "can_trade": bool(raw.get("canTrade", False)),
            "equity": equity,
            "usdt_free": balances.get("USDT", {}).get("free", 0.0),
            "balances": balances,
            "unpriced_assets": unpriced,  # excluded from equity; shown so the number is never silently wrong
        }

    def portfolio_snapshot(self, account: Dict[str, Any]) -> PortfolioSnapshot:
        equity = account["equity"]
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        base_key = f"day_start_equity_{day}"
        base = audit.get_state(base_key)
        if base is None:
            audit.set_state(base_key, str(equity))
            base = str(equity)
        daily_pnl = equity - float(base)
        balances = {a: CryptoAssetBalance(asset=a, **v) for a, v in account["balances"].items()}
        non_usdt = {a: b for a, b in balances.items() if a != "USDT"}
        return PortfolioSnapshot(
            timestamp=datetime.now().isoformat(), equity=equity, buying_power=account["usdt_free"],
            daily_pnl=daily_pnl, daily_pnl_pct=(daily_pnl / float(base) * 100) if float(base) > 0 else 0.0,
            open_positions=len(non_usdt), open_position_count=len(non_usdt), balances=balances,
            exposures={f"{a}USDT": b.value_usdt for a, b in non_usdt.items()}, current_regime=Regime.NORMAL,
        )

    # ── entries ─────────────────────────────────────────────────────────────
    def submit_entry(self, symbol: str, quote_qty: float, stop_loss: float, take_profit: float,
                     confirm: Optional[str] = None, client_order_id: Optional[str] = None) -> Dict[str, Any]:
        symbol = symbol.upper()
        coid = client_order_id or f"vh-{uuid.uuid4().hex[:24]}"
        req = {"symbol": symbol, "side": "BUY", "type": "MARKET", "quote_qty": quote_qty,
               "stop_loss": stop_loss, "take_profit": take_profit}

        def reject(code: str, message: str, details: Optional[dict] = None, mode: str = "n/a"):
            audit.record_order(coid, symbol, "BUY", "MARKET", quote_qty, mode, "REJECTED", f"{code}: {message}", req)
            raise LiveOrderError(code, message, details)

        # 1. hard limits / mode / kill switch / confirmation
        try:
            mode = guard.check_entry(symbol, "BUY", quote_qty, confirm)
        except guard.LiveGuardError as e:
            reject(e.code, e.message)

        # 2. idempotency: claim the id before touching the exchange
        if not audit.record_order(coid, symbol, "BUY", "MARKET", quote_qty, mode, "PENDING", "", req):
            raise LiveOrderError("duplicate_order", f"client_order_id {coid} already used")

        def fail(code: str, message: str, details: Optional[dict] = None, status: str = "REJECTED"):
            audit.update_order(coid, status, f"{code}: {message}", details)
            raise LiveOrderError(code, message, details)

        try:
            px = self.price(symbol)
            if not (0 < stop_loss < px < take_profit):
                fail("bad_levels", f"Need 0 < stop ({stop_loss}) < price ({px}) < take-profit ({take_profit})")
            rr = (take_profit - px) / (px - stop_loss)
            if rr < MIN_REWARD_RISK:
                fail("bad_reward_risk", f"Reward:risk {rr:.2f} is below {MIN_REWARD_RISK}")

            account = self.get_account()
            if not account["can_trade"]:
                fail("account_cannot_trade", "Exchange reports canTrade=false")
            if account["equity"] <= 0:
                fail("no_equity", "Account equity read as 0; refusing (risk gate would assume a fictional NAV)")
            if account["usdt_free"] < quote_qty:
                fail("insufficient_usdt", f"Free USDT {account['usdt_free']:.2f} < order {quote_qty:.2f}")
            snapshot = self.portfolio_snapshot(account)
            proposal = TradeProposal(id=coid, symbol=symbol, strategy_type=StrategyType.MASTER_ORDER_FLOW, side="BUY",
                                     order_type="MARKET", quote_qty=quote_qty, entry_price=px,
                                     take_profit=take_profit, stop_loss=stop_loss)
            verdict = self._gate.evaluate(proposal, snapshot)
            if not verdict.approved:
                fail("risk_gate_rejected", verdict.reason or "rejected", {"checks": {k: v.model_dump() for k, v in verdict.checks.items()}})
        except LiveOrderError:
            raise
        except Exception as exc:  # network/API errors before any order was sent
            fail("precheck_error", str(exc), status="ERROR")

        params = {"symbol": symbol, "side": "BUY", "type": "MARKET", "quoteOrderQty": quote_qty, "newClientOrderId": coid}
        if mode == "test":
            try:
                self.client.create_test_order(**params)
            except Exception as exc:
                fail("test_order_failed", str(exc), status="ERROR")
            audit.update_order(coid, "TEST_OK", "validated by /order/test; not executed", {"price": px})
            return {"status": "TEST_OK", "executed": False, "client_order_id": coid, "mode": "test", "price": px,
                    "risk_gate": verdict.reason}

        # live execution
        try:
            order = self.client.create_order(**params)
        except Exception as exc:
            fail("order_failed", str(exc), status="ERROR")
        executed_qty = float(order.get("executedQty", 0) or 0)
        audit.update_order(coid, "FILLED" if executed_qty > 0 else "SUBMITTED", "", order)
        result: Dict[str, Any] = {"status": order.get("status"), "executed": executed_qty > 0, "client_order_id": coid,
                                  "mode": "live", "order": order, "protective_exit": None}
        if executed_qty > 0:
            result["protective_exit"] = self._protect_or_flatten(symbol, executed_qty, stop_loss, take_profit, coid)
        return result

    def _protect_or_flatten(self, symbol: str, qty: float, stop_loss: float, take_profit: float, coid: str) -> Dict[str, Any]:
        """Place an OCO (take-profit limit + stop-limit). On any failure, market-sell the position at once."""
        try:
            f = self._filters(symbol)
            step = float(f.get("LOT_SIZE", {}).get("stepSize", 0) or 0)
            tick = float(f.get("PRICE_FILTER", {}).get("tickSize", 0) or 0)
            sell_qty = _floor_to_step(qty, step)
            tp = _floor_to_step(take_profit, tick)
            sl = _floor_to_step(stop_loss, tick)
            sl_limit = _floor_to_step(stop_loss * (1 - STOP_LIMIT_BUFFER), tick)
            if sell_qty <= 0:
                raise ValueError("quantity rounds to zero")
            oco = self.client.create_oco_order(
                symbol=symbol, side="SELL", quantity=sell_qty, price=str(tp), stopPrice=str(sl),
                stopLimitPrice=str(sl_limit), stopLimitTimeInForce="GTC", listClientOrderId=f"{coid}-oco",
            )
            return {"status": "PROTECTED", "oco": oco}
        except Exception as exc:
            logger.error(f"[LIVE] OCO failed for {symbol} ({exc}); flattening position")
            try:
                f = self._filters(symbol)
                step = float(f.get("LOT_SIZE", {}).get("stepSize", 0) or 0)
                sell = self.client.create_order(symbol=symbol, side="SELL", type="MARKET",
                                                quantity=_floor_to_step(qty, step), newClientOrderId=f"{coid}-flat")
                audit.update_order(coid, "FLATTENED", f"OCO failed ({exc}); position market-sold", sell)
                return {"status": "FLATTENED", "reason": str(exc), "order": sell}
            except Exception as exc2:
                audit.update_order(coid, "UNPROTECTED", f"OCO failed ({exc}) AND flatten failed ({exc2}) - MANUAL ACTION REQUIRED")
                logger.critical(f"[LIVE] {symbol} position is UNPROTECTED: {exc2}")
                return {"status": "UNPROTECTED", "reason": f"{exc}; {exc2}"}

    # ── exits ───────────────────────────────────────────────────────────────
    def close_position(self, symbol: str, confirm: Optional[str] = None) -> Dict[str, Any]:
        """Cancel the symbol's open orders, then market-sell the whole free balance. Not blocked by the kill switch."""
        symbol = symbol.upper()
        coid = f"vh-close-{uuid.uuid4().hex[:18]}"
        try:
            mode = guard.check_close(symbol, confirm)
        except guard.LiveGuardError as e:
            audit.record_order(coid, symbol, "SELL", "MARKET", 0.0, "n/a", "REJECTED", f"{e.code}: {e.message}", {"symbol": symbol})
            raise LiveOrderError(e.code, e.message)
        base = symbol.replace("USDT", "")
        audit.record_order(coid, symbol, "SELL", "MARKET", 0.0, mode, "PENDING", "", {"symbol": symbol})
        try:
            if mode == "live":
                try:
                    self.client.cancel_all_open_orders(symbol=symbol)
                except Exception as exc:  # "no open orders" surfaces as an API error on some versions
                    logger.info(f"[LIVE] cancel_all_open_orders({symbol}): {exc}")
            bal = self.client.get_asset_balance(asset=base) or {}
            free = float(bal.get("free", 0) or 0)
            step = float(self._filters(symbol).get("LOT_SIZE", {}).get("stepSize", 0) or 0)
            qty = _floor_to_step(free, step)
            if qty <= 0:
                audit.update_order(coid, "NOOP", "nothing to sell")
                return {"status": "NOOP", "executed": False, "reason": f"No free {base} balance to sell"}
            params = {"symbol": symbol, "side": "SELL", "type": "MARKET", "quantity": qty, "newClientOrderId": coid}
            if mode == "test":
                self.client.create_test_order(**params)
                audit.update_order(coid, "TEST_OK", "validated by /order/test; not executed")
                return {"status": "TEST_OK", "executed": False, "mode": "test", "quantity": qty}
            order = self.client.create_order(**params)
            audit.update_order(coid, "FILLED", "", order)
            return {"status": order.get("status"), "executed": True, "mode": "live", "order": order}
        except Exception as exc:
            audit.update_order(coid, "ERROR", str(exc))
            raise LiveOrderError("close_failed", str(exc))
