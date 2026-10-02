"""Decision rules for a finished trial. The agent RECOMMENDS; a person approves (see governor.service.approve)."""
from typing import Any, Dict, List, Optional

import numpy as np

from backend.governor.trial import START_EQUITY, TrialState, metrics_from_state

# thresholds are deliberately conservative and visible in every report
RULES = {"min_bar_coverage": 0.90, "dd_multiple_of_oos": 1.5, "dd_floor_pct": 8.0, "min_bars_for_sharpe": 20}


def current_regime(adapter, asset: str, now_ms: int) -> Optional[str]:
    """Regime of the last completed UTC day, from ~300 daily candles, using the SAME definition as the backtest tags."""
    from research.lab.regime import latest_regime
    try:
        day = 86_400_000
        end = (now_ms // day) * day
        rows = adapter.candles(asset, "1d", end - 300 * day, end, fresh=True)
        if len(rows) < 150:
            return None
        return latest_regime(np.array([r["ts"] for r in rows]), np.array([r["close"] for r in rows], dtype=float), now_ms)
    except Exception:
        return None


def decide(summary: Dict[str, Any], config: Dict[str, Any], st: TrialState, hours: float, regime: Optional[str]) -> Dict[str, Any]:
    interval = config["interval"]
    m = metrics_from_state(st, interval)
    expected_bars = hours * 3_600_000 / {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000, "15m": 900_000, "5m": 300_000, "1m": 60_000}[interval]
    coverage = st.bars / max(expected_bars, 1)
    best = next((r for r in summary["results"] if r["strategy_id"] == config["strategy_id"] and r["venue"] == config["venue"]
                 and r["assets"] == config["assets"] and r["interval"] == interval), None)
    oos_dd = best["oos_max_drawdown_pct"] if best else 10.0
    dd_limit = max(RULES["dd_floor_pct"], RULES["dd_multiple_of_oos"] * oos_dd)
    fav, unfav = config.get("favorable_regimes", []), config.get("unfavorable_regimes", [])
    # Regime evidence is only used as a gate when the backtest identified enough data to name favorable regimes AND the current
    # regime is known; otherwise it is reported as a warning rather than silently passing or failing the strategy.
    if not fav or regime is None:
        why_unknown = "backtest could not identify a favorable regime with enough days" if not fav else "current regime could not be computed"
        regime_check = {"name": "market regime favorable", "ok": True, "detail": f"NOT GATED: {why_unknown} (now {regime or 'unknown'})", "gated": False}
    else:
        regime_check = {"name": "market regime favorable", "ok": regime in fav,
                        "detail": f"now {regime}; favorable {fav}; unfavorable {unfav or 'none'}", "gated": True}
    checks: List[Dict[str, Any]] = [
        {"name": "backtest viable", "ok": bool(summary.get("viable")), "detail": "; ".join(summary.get("viability_reasons", [])) or "passed OOS gates"},
        {"name": "trial integrity", "ok": coverage >= RULES["min_bar_coverage"] and not st.errors and st.gaps == 0,
         "detail": f"{st.bars}/{expected_bars:.0f} bars ({coverage:.0%}), {st.gaps} gaps, {len(st.errors)} errors"},
        {"name": "drawdown within limit", "ok": m["max_drawdown_pct"] <= dd_limit, "detail": f"{m['max_drawdown_pct']:.2f}% vs limit {dd_limit:.2f}%"},
        {"name": "return not below backtest p10", "ok": m["return_pct"] >= config.get("expected_72h_p10_pct", 0.0),
         "detail": f"trial {m['return_pct']:.2f}% vs backtest 72h p10 {config.get('expected_72h_p10_pct', 0.0):.2f}% (mean {config.get('expected_72h_return_pct', 0.0):.2f}%)"},
        regime_check,
    ]
    ok = all(c["ok"] for c in checks)
    if ok:
        rec, why = "INTEGRATE", "All checks passed on live data; awaiting human approval to run as a paper sleeve."
    else:
        rec = "SHELVE"
        why = "Failed: " + ", ".join(c["name"] for c in checks if not c["ok"])
    revisit = None
    if rec == "SHELVE":
        revisit = {"when_regime_in": fav, "note": "Re-trial when the live regime matches; the watchlist checks this automatically." if fav else
                   "No favorable regime was identified in the backtest; revisit only after new research."}
    warnings = [c["detail"] for c in checks if c.get("gated") is False]
    return {"recommendation": rec, "summary": why, "checks": checks, "warnings": warnings, "trial": m, "regime_now": regime, "revisit": revisit,
            "rules": RULES, "start_equity": START_EQUITY}
