"""Honest live-feed snapshots built ONLY from the production market-data hub.

Rules (Upgrade_Plan guardrail 4): never fabricate. If a stream is not LIVE, or a quantity cannot be derived from
data actually received, the snapshot says so (`live: False` + reason) instead of returning zeros or a fallback price.

Caveat: CVD/VWAP here cover the trades currently held in the hub's bounded buffer (FLOW_TRADE_BUFFER), not a full
00:00-UTC session, because the hub does not persist trades. The snapshot reports `cvd_scope` to make this explicit.
"""
import time
from types import SimpleNamespace
from typing import Any, Dict, Optional

from backend.config import settings
from backend.engine.delta_engine import DeltaEngine
from backend.engine.dom_analytics import DomEngine
from backend.engine.flow_confluence import build_flow_metrics, evaluate_flow_confluence
from backend.engine.flow_models import FlowMetrics

MAX_TRADE_AGE_MS = 15_000  # a stream whose last trade is older than this is treated as stale


def _not_live(symbol: str, reason: str, health: Any = None) -> Dict[str, Any]:
    return {
        "symbol": symbol,
        "live": False,
        "reason": reason,
        "ts": int(time.time() * 1000),
        "health": health.model_dump() if health is not None else None,
    }


def build_live_metrics(state: Optional[dict], now_ms: Optional[int] = None) -> tuple[Optional[FlowMetrics], Optional[str]]:
    """Return (FlowMetrics, None) from real buffered data, or (None, reason) when it cannot be built honestly."""
    now_ms = now_ms or int(time.time() * 1000)
    if not state:
        return None, "Symbol not subscribed"
    health = state["health"]
    if health.status != "LIVE":
        return None, f"Stream status {health.status}"
    buffers, book = state["buffers"], state["book"]
    trades = list(buffers.trades)
    if not trades:
        return None, "No trades received yet"
    if now_ms - trades[-1].ts > MAX_TRADE_AGE_MS:
        return None, f"Last trade {int((now_ms - trades[-1].ts) / 1000)}s old"

    snap = book.get_snapshot(settings.FLOW_DEPTH_LEVELS)
    if not snap.bids or not snap.asks:
        return None, "Order book empty"

    delta = DeltaEngine(book.symbol)
    for t in trades:
        delta.process_trade(t)
    dom_engine = DomEngine(book.symbol, book.tick_size)
    dom = dom_engine.compute_analytics(snap, [t for t in trades if t.ts > now_ms - 60_000]).model_dump()

    bars = buffers.bars.get("1m")
    current_bar = bars[-1] if bars else None
    footprint = SimpleNamespace(current_bar=current_bar)
    price = trades[-1].price
    metrics = build_flow_metrics(book.symbol, price, footprint, delta, dom, health)
    return metrics, None


def compute_live_snapshot(state: Optional[dict], symbol: str, now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Live feature snapshot plus the spot-long flow-confluence evaluation, or an explicit not-live record."""
    metrics, reason = build_live_metrics(state, now_ms)
    if metrics is None:
        return _not_live(symbol, reason or "unavailable", state["health"] if state else None)
    bars = list(state["buffers"].bars.get("1m", []))
    closed = [b for b in bars if b.is_closed]
    conf = evaluate_flow_confluence(symbol, metrics, closed, "BUY")
    m = metrics.model_dump()
    return {
        "symbol": symbol,
        "live": True,
        "ts": m["ts"],
        "price": m["price"],
        "features": {k: m[k] for k in (
            "session_cvd", "bar_delta", "delta_percent", "cvd_slope", "cvd_divergence", "buy_sell_ratio",
            "aggression_index", "absorption_flag", "stacked_imbalance_bias", "poc_price", "vah", "val",
            "price_vs_value", "book_imbalance", "spread_bps", "vwap", "trade_rate", "volume_rate",
        )},
        "cvd_scope": "buffered_trades",
        "closed_bars_1m": len(closed),
        "confluence": {k: conf.get(k) for k in ("score", "components", "reasons", "veto", "veto_reason")},
        "health": state["health"].model_dump(),
    }
