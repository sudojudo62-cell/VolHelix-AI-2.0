"""Governing-agent workflow: ingest summary -> start 72h paper trial -> tick on live data -> decide -> human approval."""
import json
import time
from typing import Any, Dict, List, Optional

import numpy as np

from backend.governor import store
from backend.governor.report import current_regime, decide
from backend.governor.trial import TrialState, adapter_for, advance, fetch_live, find_strategy
from research.lab.data import MarketData
from research.lab.engine import Costs
from research.lab.summary import StrategySummary
from research.lab.venues import INTERVAL_MS

HOURS = 72.0


class GovernorError(Exception):
    pass


def ingest_summary(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate against the schema and store. This is how a research agent's summary file reaches the dashboard."""
    s = StrategySummary.model_validate(data)
    store.save_summary(s.source.slug, json.loads(s.model_dump_json()), s.viable)
    return {"slug": s.source.slug, "viable": s.viable, "recommended_trial": bool(s.recommended_trial), "results": len(s.results)}


def start_trial(slug: str, mode: str = "live", hours: float = HOURS, now_ms: Optional[int] = None, first_bar_ts: Optional[int] = None) -> int:
    """Start a paper trial from a stored summary's recommended configuration. `mode='replay'` is for pipeline tests only."""
    summ = store.get_summary(slug)
    if not summ:
        raise GovernorError(f"no summary ingested for {slug}")
    rec = summ["data"].get("recommended_trial")
    if not rec:
        raise GovernorError(f"{slug}: summary has no recommended trial (no cell produced results)")
    now = now_ms or int(time.time() * 1000)
    bar = INTERVAL_MS[rec["interval"]]
    first = first_bar_ts if first_bar_ts is not None else (now // bar) * bar - bar    # latest fully closed bar
    cfg = {**rec, "slug": slug, "hours": hours, "first_bar_ts": first}
    find_strategy(slug, rec["strategy_id"])  # fail now, not 72h later, if the plugin is missing on this machine
    start = first + bar
    return store.create_trial(slug, rec["strategy_id"], mode, start, start + int(hours * 3_600_000), cfg, TrialState().to_dict())


def _costs(cfg: Dict[str, Any]) -> Costs:
    c = cfg.get("costs_bps") or {}
    return Costs(c.get("fee", Costs.for_venue(cfg["venue"]).fee_bps), c.get("slippage", Costs.for_venue(cfg["venue"]).slippage_bps))


def _finalize(trial: Dict[str, Any], st: TrialState, regime: Optional[str]) -> Dict[str, Any]:
    cfg = trial["config"]
    summ = store.get_summary(trial["slug"])["data"]
    report = decide(summ, cfg, st, cfg["hours"], regime)
    report["curve"] = st.curve[-400:]
    report["mode"] = trial["mode"]
    approval = "pending" if report["recommendation"] == "INTEGRATE" else None
    store.update_trial(trial["id"], status="FINISHED", report=report, decision=report["recommendation"], approval=approval, decided_ts=int(time.time() * 1000), state=st.to_dict())
    if report["recommendation"] == "SHELVE":
        store.upsert_watch(trial["slug"], trial["strategy_id"], cfg.get("favorable_regimes", []), report["summary"],
                           {"venue": cfg["venue"], "asset": cfg["assets"][0]})
    return report


def tick_trial(trial_id: int, now_ms: Optional[int] = None, adapter=None) -> Dict[str, Any]:
    """One live update: fetch closed candles, book returns, decide new weights; finalize when the 72h are up."""
    trial = store.get_trial(trial_id)
    if not trial:
        raise GovernorError(f"trial {trial_id} not found")
    if trial["status"] != "RUNNING":
        return {"status": trial["status"], "processed": 0}
    if trial["mode"] == "replay":
        raise GovernorError("replay trials are run with run_replay(), not tick_trial()")
    cfg, now = trial["config"], now_ms or int(time.time() * 1000)
    st = TrialState.from_dict(trial["state"])
    adapter = adapter or adapter_for(cfg["venue"])
    strat = find_strategy(trial["slug"], trial["strategy_id"])
    processed = 0
    try:
        md = fetch_live(adapter, cfg["assets"], cfg["interval"], now, strat.requires_funding)
        processed = advance(strat, cfg["params"], _costs(cfg), md, st, end_ts=trial["end_ts"], min_ts=cfg["first_bar_ts"])
    except Exception as exc:  # data outage: record it, keep the trial alive; coverage check will judge it
        st.errors.append(f"{now}: fetch failed: {type(exc).__name__}: {str(exc)[:160]}")
    if now >= trial["end_ts"]:
        report = _finalize(trial, st, current_regime(adapter, cfg["assets"][0], now))
        return {"status": "FINISHED", "processed": processed, "recommendation": report["recommendation"]}
    store.update_trial(trial_id, state=st.to_dict())
    return {"status": "RUNNING", "processed": processed, "equity": st.equity, "bars": st.bars}


def run_replay(trial_id: int, md: MarketData) -> Dict[str, Any]:
    """Pipeline test: drive a trial through historical candles as if they were live. Not evidence of live performance."""
    trial = store.get_trial(trial_id)
    cfg = trial["config"]
    st = TrialState.from_dict(trial["state"])
    strat = find_strategy(trial["slug"], trial["strategy_id"])
    advance(strat, cfg["params"], _costs(cfg), md, st, end_ts=trial["end_ts"], min_ts=cfg["first_bar_ts"])
    from research.lab.regime import regime_labels
    lab = regime_labels(md.close[:, 0], md.interval)[-1]
    report = _finalize(trial, st, None if lab == "warmup" else str(lab))
    return {"status": "FINISHED", "recommendation": report["recommendation"], "bars": st.bars}


def approve(trial_id: int) -> Dict[str, Any]:
    t = store.get_trial(trial_id)
    if not t or t["decision"] != "INTEGRATE" or t["approval"] != "pending":
        raise GovernorError("only a finished trial recommended INTEGRATE and still pending can be approved")
    store.update_trial(trial_id, approval="approved", decided_ts=int(time.time() * 1000))
    store.set_integrated(t["slug"], t["strategy_id"], trial_id)
    return {"approved": True, "slug": t["slug"], "scope": "paper sleeve only; live trading is unaffected"}


def reject(trial_id: int) -> Dict[str, Any]:
    t = store.get_trial(trial_id)
    if not t or t["decision"] != "INTEGRATE" or t["approval"] != "pending":
        raise GovernorError("only a pending INTEGRATE recommendation can be rejected")
    store.update_trial(trial_id, approval="rejected")
    return {"approved": False}


def check_watchlist(now_ms: Optional[int] = None, adapter_factory=adapter_for) -> List[Dict[str, Any]]:
    """For shelved strategies: is the live regime now one the backtest found favorable? Two consecutive hits = ready to re-trial."""
    now, out = now_ms or int(time.time() * 1000), []
    for w in store.list_watch():
        if w["status"] not in ("SHELVED", "READY_FOR_RETRIAL") or not w.get("meta"):
            continue
        reg = current_regime(adapter_factory(w["meta"]["venue"]), w["meta"]["asset"], now)
        hits = (w["consecutive_matches"] + 1) if reg and reg in (w["favorable"] or []) else 0
        status = "READY_FOR_RETRIAL" if hits >= 2 else "SHELVED"
        store.update_watch(w["slug"], last_check_ts=now, last_regime=reg, consecutive_matches=hits, status=status)
        out.append({"slug": w["slug"], "regime": reg, "favorable": w["favorable"], "status": status})
    return out
