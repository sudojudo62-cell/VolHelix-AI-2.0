"""Trend, volatility and ATR helpers. All functions use only data passed in (no lookahead)."""
import math
from typing import Dict, List, Optional, Sequence

TRADING_DAYS = 365  # crypto trades every day


def ema(values: Sequence[float], period: int) -> List[float]:
    """Exponential moving average; first value seeds the series."""
    if period < 1:
        raise ValueError("period must be >= 1")
    k = 2.0 / (period + 1)
    out: List[float] = []
    for v in values:
        out.append(v if not out else v * k + out[-1] * (1 - k))
    return out


def true_ranges(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> List[float]:
    trs: List[float] = []
    for i in range(len(closes)):
        if i == 0:
            trs.append(highs[i] - lows[i])
        else:
            trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    return trs


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14) -> Optional[float]:
    """Wilder ATR at the last bar, or None if there is not enough data."""
    if len(closes) < period + 1:
        return None
    trs = true_ranges(highs, lows, closes)
    value = sum(trs[1 : period + 1]) / period
    for tr in trs[period + 1 :]:
        value = (value * (period - 1) + tr) / period
    return value


def realized_vol_annualized(closes: Sequence[float], window: int = 30, bars_per_year: float = TRADING_DAYS) -> Optional[float]:
    """Annualized stdev of log returns over the last `window` returns."""
    if len(closes) < window + 1:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - window, len(closes)) if closes[i - 1] > 0 and closes[i] > 0]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(bars_per_year)


def trend_is_up(closes: Sequence[float], fast: int = 20, slow: int = 100) -> bool:
    """Long-allowed trend state: fast EMA above slow EMA and price above slow EMA.

    Needs at least `slow` closes; with less data the answer is False (fail closed).
    """
    if len(closes) < slow:
        return False
    f = ema(closes, fast)[-1]
    s = ema(closes, slow)[-1]
    return f > s and closes[-1] > s


def vol_size_multiplier(
    closes: Sequence[float],
    target_vol: float = 0.40,
    window: int = 30,
    bars_per_year: float = TRADING_DAYS,
    floor: float = 0.25,
) -> float:
    """Scale position size by target_vol / realized_vol, clamped to [floor, 1.0].

    The cap of 1.0 is deliberate: volatility targeting here only ever *reduces* size relative to the
    caller's own limit, so it can never weaken the risk gate. Unknown vol returns 1.0 (no change).
    """
    rv = realized_vol_annualized(closes, window, bars_per_year)
    if rv is None or rv <= 0:
        return 1.0
    return max(floor, min(1.0, target_vol / rv))


def atr_levels(
    entry_price: float,
    atr_value: float,
    sl_mult: float = 2.0,
    tp_mult: float = 4.0,
    min_reward_risk: float = 1.5,
) -> Dict[str, float]:
    """Long stop/take-profit prices from ATR multiples. Raises if inputs are invalid."""
    if entry_price <= 0 or atr_value <= 0:
        raise ValueError("entry_price and atr_value must be positive")
    if sl_mult <= 0 or tp_mult / sl_mult < min_reward_risk:
        raise ValueError(f"reward:risk must be >= {min_reward_risk}")
    stop = entry_price - sl_mult * atr_value
    if stop <= 0:
        raise ValueError("ATR stop would be non-positive")
    tp = entry_price + tp_mult * atr_value
    return {
        "stop_loss_price": round(stop, 8),
        "take_profit_price": round(tp, 8),
        "stop_loss_pct": round((entry_price - stop) / entry_price * 100, 4),
        "take_profit_pct": round((tp - entry_price) / entry_price * 100, 4),
    }
