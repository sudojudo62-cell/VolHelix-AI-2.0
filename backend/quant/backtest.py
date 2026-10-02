"""Cost-aware long/flat backtester with walk-forward validation and the Deflated Sharpe Ratio.

Execution model (conservative, no lookahead):
- A signal computed from candles[: i + 1] (i.e. at bar i's close) is executed at bar i + 1's open.
- Fees are charged per side on notional; slippage moves every fill against us.
- With stops enabled, a bar that touches both stop and target is assumed to hit the stop first.
"""
import math
from dataclasses import dataclass, field
from itertools import product
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from scipy.stats import norm

from backend.quant.signals import atr, vol_size_multiplier

Candle = Dict[str, float]  # keys: open, high, low, close
# signal_fn(candles_up_to_and_including_i) -> True if we want to be long after bar i
SignalFn = Callable[[Sequence[Candle]], bool]

EULER_GAMMA = 0.5772156649015329


@dataclass
class Costs:
    fee_bps: float = 10.0       # Binance spot taker default is 10 bps per side
    slippage_bps: float = 5.0   # adverse, per fill


@dataclass
class StopConfig:
    atr_period: int = 14
    sl_mult: float = 2.0
    tp_mult: float = 4.0


@dataclass
class BacktestResult:
    equity: List[float]
    returns: List[float]
    trades: List[Dict[str, float]] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)


def _fill(price: float, side: str, costs: Costs) -> float:
    slip = costs.slippage_bps / 1e4
    return price * (1 + slip) if side == "buy" else price * (1 - slip)


def sharpe(returns: Sequence[float], bars_per_year: float = 365.0) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    sd = math.sqrt(var)
    return 0.0 if sd == 0 else mean / sd * math.sqrt(bars_per_year)


def _metrics(equity: List[float], returns: List[float], trades: List[Dict[str, float]], bars_per_year: float) -> Dict[str, float]:
    peak, max_dd = equity[0], 0.0
    for e in equity:
        peak = max(peak, e)
        max_dd = max(max_dd, (peak - e) / peak if peak > 0 else 0.0)
    wins = [t["pnl_pct"] for t in trades if t["pnl_pct"] > 0]
    losses = [-t["pnl_pct"] for t in trades if t["pnl_pct"] <= 0]
    gross_loss = sum(losses)
    return {
        "total_return_pct": (equity[-1] / equity[0] - 1) * 100,
        "sharpe": sharpe(returns, bars_per_year),
        "max_drawdown_pct": max_dd * 100,
        "trades": float(len(trades)),
        "win_rate_pct": (len(wins) / len(trades) * 100) if trades else 0.0,
        "profit_factor": (sum(wins) / gross_loss) if gross_loss > 0 else (float("inf") if wins else 0.0),
        "bars": float(len(returns)),
    }


def run_backtest(
    candles: Sequence[Candle],
    signal_fn: SignalFn,
    costs: Costs = Costs(),
    stops: Optional[StopConfig] = None,
    vol_target: Optional[float] = None,
    bars_per_year: float = 365.0,
    warmup: int = 1,
    initial_equity: float = 10_000.0,
) -> BacktestResult:
    """Long/flat backtest. `warmup` bars are skipped before signals are evaluated."""
    n = len(candles)
    if n < warmup + 2:
        raise ValueError("not enough candles")
    cash, qty = initial_equity, 0.0
    entry_px = stop_px = tp_px = 0.0
    equity: List[float] = [initial_equity]
    trades: List[Dict[str, float]] = []
    fee = costs.fee_bps / 1e4

    def close_position(px: float, i: int) -> None:
        nonlocal cash, qty
        proceeds = qty * _fill(px, "sell", costs)
        cash += proceeds - proceeds * fee
        trades.append({"exit_bar": float(i), "pnl_pct": (proceeds * (1 - fee)) / entry_cost * 100 - 100})
        qty = 0.0

    entry_cost = 0.0
    want_long = False
    for i in range(1, n):
        c = candles[i]
        # 1) execute last bar's decision at this bar's open
        if want_long and qty == 0:
            a = None
            if stops:
                hist = candles[:i]
                a = atr([x["high"] for x in hist], [x["low"] for x in hist], [x["close"] for x in hist], stops.atr_period)
                if a is None:  # fail closed: never hold a position without its stop
                    equity.append(cash + qty * c["close"])
                    want_long = bool(signal_fn(candles[: i + 1])) if i >= warmup else False
                    continue
            mult = 1.0
            if vol_target:
                mult = vol_size_multiplier([x["close"] for x in candles[:i]], vol_target, bars_per_year=bars_per_year)
            spend = cash * mult
            px = _fill(c["open"], "buy", costs)
            qty = spend * (1 - fee) / px
            cash -= spend
            entry_cost = spend
            entry_px = px
            if stops and a:
                stop_px, tp_px = entry_px - stops.sl_mult * a, entry_px + stops.tp_mult * a
        elif not want_long and qty > 0:
            close_position(c["open"], i)
        # 2) intrabar stop / target while holding (stop first if both touched)
        if qty > 0 and stops and stop_px > 0:
            if c["low"] <= stop_px:
                close_position(min(c["open"], stop_px), i)
            elif c["high"] >= tp_px:
                close_position(max(c["open"], tp_px), i)
        # 3) mark to market at close, then decide for next bar
        equity.append(cash + qty * c["close"])
        want_long = bool(signal_fn(candles[: i + 1])) if i >= warmup else False
    if qty > 0:  # liquidate at the final close so trade stats include the open position
        close_position(candles[-1]["close"], n - 1)
        equity[-1] = cash
    returns = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity))]
    return BacktestResult(equity, returns, trades, _metrics(equity, returns, trades, bars_per_year))


def buy_and_hold(candles: Sequence[Candle], costs: Costs = Costs(), bars_per_year: float = 365.0) -> BacktestResult:
    return run_backtest(candles, lambda _h: True, costs, bars_per_year=bars_per_year)


def deflated_sharpe(
    sr_per_period: float,
    n_obs: int,
    n_trials: int,
    sr_variance: float,
    skew: float = 0.0,
    kurt: float = 3.0,
) -> float:
    """Probability that the true Sharpe exceeds what the best of `n_trials` random strategies would show.

    All Sharpe inputs are per-period (NOT annualized). `sr_variance` is the variance of the trials'
    per-period Sharpe ratios. Returns a probability in [0, 1]; >= 0.95 is the usual bar.
    """
    if n_obs < 3 or n_trials < 1:
        return 0.0
    if n_trials == 1 or sr_variance <= 0:
        sr0 = 0.0
    else:
        sr0 = math.sqrt(sr_variance) * (
            (1 - EULER_GAMMA) * norm.ppf(1 - 1 / n_trials) + EULER_GAMMA * norm.ppf(1 - 1 / (n_trials * math.e))
        )
    denom = 1 - skew * sr_per_period + (kurt - 1) / 4 * sr_per_period**2
    if denom <= 0:
        return 0.0
    z = (sr_per_period - sr0) * math.sqrt(n_obs - 1) / math.sqrt(denom)
    return float(norm.cdf(z))


def _moments(returns: Sequence[float]) -> Tuple[float, float]:
    n = len(returns)
    mean = sum(returns) / n
    m2 = sum((r - mean) ** 2 for r in returns) / n
    if m2 == 0:
        return 0.0, 3.0
    m3 = sum((r - mean) ** 3 for r in returns) / n
    m4 = sum((r - mean) ** 4 for r in returns) / n
    return m3 / m2**1.5, m4 / m2**2


def walk_forward(
    candles: Sequence[Candle],
    make_signal: Callable[..., SignalFn],
    param_grid: Dict[str, Sequence],
    train_bars: int,
    test_bars: int,
    costs: Costs = Costs(),
    bars_per_year: float = 365.0,
    **backtest_kwargs,
) -> Dict[str, object]:
    """Rolling walk-forward: pick params on the train window, evaluate them on the next unseen test window.

    Returns stitched out-of-sample returns, per-fold picks, and a Deflated Sharpe for the OOS series that
    accounts for the number of parameter combinations tried per fold.
    """
    names = list(param_grid)
    combos = [dict(zip(names, vals)) for vals in product(*(param_grid[k] for k in names))]
    oos_returns: List[float] = []
    folds: List[Dict[str, object]] = []
    all_train_sr: List[float] = []
    start = 0
    while start + train_bars + test_bars <= len(candles):
        train = candles[start : start + train_bars]
        # the test slice is prefixed with the train tail so indicators are warm; those bars are not scored
        test_full = candles[start + train_bars - 200 if start + train_bars - 200 > start else start : start + train_bars + test_bars]
        warm = len(test_full) - test_bars
        scored: List[Tuple[float, Dict]] = []
        for p in combos:
            try:
                res = run_backtest(train, make_signal(**p), costs, bars_per_year=bars_per_year, warmup=1, **backtest_kwargs)
            except ValueError:
                continue
            s = res.metrics["sharpe"]
            scored.append((s, p))
            all_train_sr.append(s / math.sqrt(bars_per_year))
        if scored:
            best_sr, best_p = max(scored, key=lambda t: t[0])
            res = run_backtest(test_full, make_signal(**best_p), costs, bars_per_year=bars_per_year, warmup=1, **backtest_kwargs)
            fold_ret = res.returns[warm - 1 :]  # returns[k] belongs to candle k + 1
            oos_returns.extend(fold_ret)
            folds.append({"params": best_p, "train_sharpe": best_sr, "oos_sharpe": sharpe(fold_ret, bars_per_year), "oos_bars": len(fold_ret)})
        start += test_bars
    oos_sr = sharpe(oos_returns, bars_per_year)
    out: Dict[str, object] = {"folds": folds, "oos_returns": oos_returns, "oos_sharpe": oos_sr, "n_trials": len(combos)}
    if len(oos_returns) > 3 and all_train_sr:
        mean = sum(all_train_sr) / len(all_train_sr)
        var = sum((s - mean) ** 2 for s in all_train_sr) / max(1, len(all_train_sr) - 1)
        skew, kurt = _moments(oos_returns)
        per_period = oos_sr / math.sqrt(bars_per_year)
        out["deflated_sharpe_prob"] = deflated_sharpe(per_period, len(oos_returns), len(combos), var, skew, kurt)
    return out
