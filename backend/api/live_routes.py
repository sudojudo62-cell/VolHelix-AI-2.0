"""Live desk API. Public: feed/status/signals (market-derived data only). Token-gated (X-Live-Token): account, audit,
orders, close, kill switch. With LIVE_API_TOKEN unset every token-gated call is refused."""
import asyncio
import hmac
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from backend.config import settings
from backend.exchange import audit, guard
from backend.exchange.live_binance import LiveBinanceAdapter, LiveOrderError
from backend.marketdata.hub import hub
from backend.signals import signal_log
from backend.signals.live_feed import compute_live_snapshot

router = APIRouter(prefix="/api/live", tags=["live"])
_adapter: Optional[LiveBinanceAdapter] = None
_snap_cache: Dict[str, Any] = {"ts": 0.0, "data": None}
SNAPSHOT_TTL_S = 1.0


def get_adapter() -> LiveBinanceAdapter:
    global _adapter
    if _adapter is None:
        def hub_price(symbol: str) -> Optional[float]:
            snap = compute_live_snapshot(hub.get_state(symbol), symbol)
            return snap.get("price") if snap["live"] else None  # fresh live trade price, else REST fallback in adapter
        _adapter = LiveBinanceAdapter(price_fn=hub_price)
    return _adapter


def require_token(x_live_token: Optional[str]) -> None:
    if not settings.LIVE_API_TOKEN:
        raise HTTPException(403, detail="LIVE_API_TOKEN is not configured; this endpoint is disabled")
    if not x_live_token or not hmac.compare_digest(x_live_token, settings.LIVE_API_TOKEN):
        raise HTTPException(401, detail="Invalid or missing X-Live-Token")


def _order_error(e: LiveOrderError) -> HTTPException:
    status = 502 if e.code in ("order_failed", "close_failed", "precheck_error", "test_order_failed") else 422
    return HTTPException(status, detail={"code": e.code, "message": e.message, **e.details})


@router.get("/status")
def live_status():
    health = {s: h.status for s, h in hub.health().items()} if settings.FLOW_ENABLED else {}
    return {
        "mode": guard.mode(),
        "trading_enabled": settings.LIVE_TRADING_ENABLED,
        "order_mode": settings.LIVE_ORDER_MODE.lower(),
        "kill_switch": guard.kill_switch_engaged(),
        "keys_configured": guard.keys_configured(),
        "token_configured": bool(settings.LIVE_API_TOKEN),
        "limits": {"max_order_usdt": settings.LIVE_MAX_ORDER_USDT, "max_daily_notional_usdt": settings.LIVE_MAX_DAILY_NOTIONAL_USDT,
                   "min_order_usdt": guard.MIN_NOTIONAL_USDT, "allowlist": settings.LIVE_SYMBOL_ALLOWLIST},
        "executed_notional_today": audit.executed_notional_today(),
        "confirm_phrase_required": guard.mode() == "live",
        "feeds": {"flow_enabled": settings.FLOW_ENABLED, "streams": health},
        "ts": int(time.time() * 1000),
    }


@router.get("/snapshots")
def live_snapshots():
    """Per-symbol live feature snapshots from the production streams. `live:false` entries carry the reason; no fallbacks."""
    now = time.time()
    if _snap_cache["data"] is not None and now - _snap_cache["ts"] < SNAPSHOT_TTL_S:
        return _snap_cache["data"]
    data = {"snapshots": [compute_live_snapshot(hub.get_state(s), s) for s in list(hub.books.keys())] if settings.FLOW_ENABLED else [],
            "flow_enabled": settings.FLOW_ENABLED}
    _snap_cache.update(ts=now, data=data)
    return data


@router.get("/signals")
def live_signals(limit: int = 100, symbol: Optional[str] = None):
    return {"signals": signal_log.recent(limit, symbol, mode="live")}


@router.get("/signals/calibration")
def live_calibration(horizon: str = "4h"):
    try:
        return {"horizon": horizon, "buckets": signal_log.calibration(mode="live", horizon=horizon)}
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


# ── token-gated ─────────────────────────────────────────────────────────────
@router.get("/account")
def live_account(x_live_token: Optional[str] = Header(default=None)):
    require_token(x_live_token)
    if not guard.keys_configured():
        raise HTTPException(503, detail="Live API keys are not configured")
    try:
        return get_adapter().get_account()
    except Exception as exc:
        raise HTTPException(502, detail=f"Exchange error: {exc}")


@router.get("/orders")
def live_orders(limit: int = 100, x_live_token: Optional[str] = Header(default=None)):
    require_token(x_live_token)
    return {"orders": audit.recent_orders(limit)}


class KillSwitchRequest(BaseModel):
    engaged: bool


@router.post("/kill-switch")
def live_kill_switch(req: KillSwitchRequest, x_live_token: Optional[str] = Header(default=None)):
    require_token(x_live_token)
    guard.set_kill_switch(req.engaged)
    return {"kill_switch": guard.kill_switch_engaged()}


class LiveOrderRequest(BaseModel):
    symbol: str
    quote_qty: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float = Field(gt=0)
    confirm: Optional[str] = None
    client_order_id: Optional[str] = Field(default=None, max_length=36, pattern=r"^[A-Za-z0-9_-]+$")


@router.post("/order")
async def live_order(req: LiveOrderRequest, x_live_token: Optional[str] = Header(default=None)):
    require_token(x_live_token)
    try:
        return await asyncio.get_running_loop().run_in_executor(
            None, lambda: get_adapter().submit_entry(req.symbol, req.quote_qty, req.stop_loss, req.take_profit, req.confirm, req.client_order_id))
    except LiveOrderError as e:
        raise _order_error(e)


class CloseRequest(BaseModel):
    symbol: str
    confirm: Optional[str] = None


@router.post("/close")
async def live_close(req: CloseRequest, x_live_token: Optional[str] = Header(default=None)):
    require_token(x_live_token)
    try:
        return await asyncio.get_running_loop().run_in_executor(None, lambda: get_adapter().close_position(req.symbol, req.confirm))
    except LiveOrderError as e:
        raise _order_error(e)
