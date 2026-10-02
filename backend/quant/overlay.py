"""Optional live overlays built on backend.quant.signals. Every overlay is flag-gated (default OFF).

Design rules (see Upgrade_Plan.md guardrails):
- Overlays may only *veto* a candidate or *reduce* size; they never raise size or loosen the risk gate.
- The trend filter fails closed (unknown data -> veto). Sizing/level overlays fail open to the caller's
  original values, because the downstream deterministic risk gate still validates every order.
"""
from typing import Any, Dict, List, Tuple

from backend.config import settings
from backend.quant.signals import atr, atr_levels, trend_is_up, vol_size_multiplier
from backend.utils.logger import get_logger

logger = get_logger("quant_overlay")


def _closes_highs_lows(client: Any, symbol: str) -> Tuple[List[float], List[float], List[float]]:
    limit = max(settings.TREND_SLOW + 50, 150)
    bars = client.get_klines(symbol, interval=settings.QUANT_INTERVAL, limit=limit) or []
    return [b["close"] for b in bars], [b["high"] for b in bars], [b["low"] for b in bars]


def filter_candidates(client: Any, candidates: List[Dict], diagnostics: Dict[str, Dict]) -> List[Dict]:
    """Drop candidates that are not in an uptrend. No-op unless TREND_FILTER_ENABLED."""
    if not settings.TREND_FILTER_ENABLED:
        return candidates
    kept = []
    for cand in candidates:
        sym = cand["symbol"]
        try:
            closes, _, _ = _closes_highs_lows(client, sym)
            ok = trend_is_up(closes, settings.TREND_FAST, settings.TREND_SLOW)
        except Exception as exc:  # fail closed
            logger.warning(f"Trend filter error for {sym}: {exc}; vetoing candidate")
            ok = False
        if ok:
            kept.append(cand)
        else:
            diag = diagnostics.setdefault(sym, {"symbol": sym})
            diag["is_valid"] = False
            diag["status_label"] = "TREND FILTER VETO"
            diag.setdefault("reasons", []).append(
                f"Trend filter: not in uptrend on {settings.QUANT_INTERVAL} (EMA{settings.TREND_FAST}/EMA{settings.TREND_SLOW})"
            )
    return kept


def adjust_order(client: Any, symbol: str, price: float, levels: Dict[str, float], quote_qty: float) -> Tuple[float, Dict[str, float]]:
    """Return (quote_qty, levels), possibly volatility-reduced and/or ATR-based. Never increases quote_qty."""
    if not (settings.VOL_TARGET_ENABLED or settings.ATR_LEVELS_ENABLED):
        return quote_qty, levels
    try:
        closes, highs, lows = _closes_highs_lows(client, symbol)
        if settings.VOL_TARGET_ENABLED:
            mult = vol_size_multiplier(closes, settings.VOL_TARGET_ANNUAL, bars_per_year=settings.QUANT_BARS_PER_YEAR)
            quote_qty = round(quote_qty * min(1.0, mult), 2)
        if settings.ATR_LEVELS_ENABLED:
            a = atr(highs, lows, closes, 14)
            if a:
                new = atr_levels(price, a, settings.ATR_SL_MULT, settings.ATR_TP_MULT)
                rr = round(new["take_profit_pct"] / new["stop_loss_pct"], 2)
                levels = {
                    **levels,
                    **new,
                    "sl_reason": f"ATR stop ({settings.ATR_SL_MULT}x ATR14)",
                    "tp_reason": f"ATR target ({settings.ATR_TP_MULT}x ATR14)",
                    "risk_reward_ratio": rr,
                }
    except Exception as exc:
        logger.warning(f"Quant order adjustment skipped for {symbol}: {exc}")
    return quote_qty, levels
