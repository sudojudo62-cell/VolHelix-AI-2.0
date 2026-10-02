"""Hard pre-trade limits for real-money orders. Pure logic over settings + audit state; no network.

Semantics:
- LIVE_TRADING_ENABLED=false        -> read-only: every order attempt is rejected.
- enabled + LIVE_ORDER_MODE=test    -> orders are validated by Binance /order/test and never executed.
- enabled + LIVE_ORDER_MODE=live    -> real orders, and only with the confirmation phrase.
The kill switch (persisted, survives restarts) blocks new entries in every mode. It never blocks closing a position.
"""
from typing import Optional

from backend.config import settings
from backend.exchange import audit

CONFIRM_PHRASE = "I UNDERSTAND THIS USES REAL FUNDS"
MIN_NOTIONAL_USDT = 10.0  # Binance spot minimum
KILL_KEY = "kill_switch"


class LiveGuardError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def kill_switch_engaged() -> bool:
    return audit.get_state(KILL_KEY, "0") == "1"


def set_kill_switch(on: bool) -> None:
    audit.set_state(KILL_KEY, "1" if on else "0")


def keys_configured() -> bool:
    return bool(settings.LIVE_BINANCE_API_KEY and settings.LIVE_BINANCE_API_SECRET)


def mode() -> str:
    """Effective mode: 'disabled' | 'test' | 'live'."""
    if not settings.LIVE_TRADING_ENABLED:
        return "disabled"
    return "live" if settings.LIVE_ORDER_MODE.lower() == "live" else "test"


def check_entry(symbol: str, side: str, notional: float, confirm: Optional[str] = None, now_ms: Optional[int] = None) -> str:
    """Validate a new entry order. Returns the effective mode ('test' or 'live'); raises LiveGuardError otherwise."""
    m = mode()
    if m == "disabled":
        raise LiveGuardError("trading_disabled", "Live trading is disabled (LIVE_TRADING_ENABLED=false); adapter is read-only")
    if not keys_configured():
        raise LiveGuardError("no_keys", "LIVE_BINANCE_API_KEY / LIVE_BINANCE_API_SECRET are not configured")
    if kill_switch_engaged():
        raise LiveGuardError("kill_switch", "Kill switch is engaged; new entries are blocked")
    if side.upper() != "BUY":
        raise LiveGuardError("side_not_allowed", "Only BUY entries are supported (spot, long-only); use /api/live/close to exit")
    sym = symbol.upper()
    if sym not in settings.LIVE_SYMBOL_ALLOWLIST:
        raise LiveGuardError("symbol_not_allowed", f"{sym} is not in the live symbol allowlist")
    if notional < MIN_NOTIONAL_USDT:
        raise LiveGuardError("below_min_notional", f"Notional ${notional:.2f} is below the ${MIN_NOTIONAL_USDT:.0f} minimum")
    if notional > settings.LIVE_MAX_ORDER_USDT:
        raise LiveGuardError("over_order_cap", f"Notional ${notional:.2f} exceeds LIVE_MAX_ORDER_USDT ${settings.LIVE_MAX_ORDER_USDT:.2f}")
    if m == "live":
        if confirm != CONFIRM_PHRASE:
            raise LiveGuardError("confirmation_required", f'Live orders require confirm="{CONFIRM_PHRASE}"')
        used = audit.executed_notional_today(now_ms)
        if used + notional > settings.LIVE_MAX_DAILY_NOTIONAL_USDT:
            raise LiveGuardError(
                "over_daily_cap",
                f"Daily notional ${used + notional:.2f} would exceed LIVE_MAX_DAILY_NOTIONAL_USDT ${settings.LIVE_MAX_DAILY_NOTIONAL_USDT:.2f}",
            )
    return m


def check_close(symbol: str, confirm: Optional[str] = None) -> str:
    """Closing reduces risk, so the kill switch does not block it; everything else still applies."""
    m = mode()
    if m == "disabled":
        raise LiveGuardError("trading_disabled", "Live trading is disabled (LIVE_TRADING_ENABLED=false); adapter is read-only")
    if not keys_configured():
        raise LiveGuardError("no_keys", "LIVE_BINANCE_API_KEY / LIVE_BINANCE_API_SECRET are not configured")
    if symbol.upper() not in settings.LIVE_SYMBOL_ALLOWLIST:
        raise LiveGuardError("symbol_not_allowed", f"{symbol.upper()} is not in the live symbol allowlist")
    if m == "live" and confirm != CONFIRM_PHRASE:
        raise LiveGuardError("confirmation_required", f'Live orders require confirm="{CONFIRM_PHRASE}"')
    return m
