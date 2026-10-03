#!/usr/bin/env python3
"""Pair Scanner snapshot: current cross-sectional scores for a venue's token universe, with confidence and model health.

    python -m research.scanner --venue kucoin --interval 1h --horizon-h 24 --top-k 3 [--loop 900] [--out path.json]

Writes JSON the dashboard's /scanner page reads (backend/data/scanner_latest.json by default, or SCANNER_SNAPSHOT_PATH).
READ-ONLY public data. The scores are model output with an out-of-sample health check, not trade instructions.
"""
import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from research.lab.data import build_universe
from research.lab.venues import INTERVAL_MS, VenueAdapter, get_venue
from research.models import ranker as R
from research.universe import DEFAULT_UNIVERSE

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend", "data", "scanner_latest.json")
SCHEMA = "1.0"


def snapshot_path() -> str:
    return os.environ.get("SCANNER_SNAPSHOT_PATH", DEFAULT_PATH)


def build_snapshot(adapter: VenueAdapter, assets: Sequence[str], interval: str = "1h", horizon_h: float = 24, top_k: int = 3,
                   train_days: int = 90, now_ms: Optional[int] = None, health_days: int = 14, ridge: float = 10.0,
                   n_boot: int = 100) -> Dict[str, Any]:
    now = now_ms or int(time.time() * 1000)
    bar = INTERVAL_MS[interval]
    end = (now // bar) * bar                                    # only fully closed bars
    start = end - (train_days + 37 + health_days + 5) * 86_400_000
    md, dropped = build_universe(adapter, assets, interval, start, end, fresh=True, min_assets=8, allow_head_gap_bars=0)
    H = R.bars_for_hours(horizon_h, interval)
    train = R.bars_for_hours(train_days * 24, interval)
    refit = R.bars_for_hours(7 * 24, interval)
    F, y = R.build_features(md.close, md.volume, interval), R.forward_returns(md.close, H)
    r = md.n - 1
    beta = R.fit_at(F, y, r, H, train, ridge)
    if beta is None:
        return {"schema": SCHEMA, "available": False, "reason": "not enough labeled history to fit the model", "venue": adapter.name,
                "generated_at": now, "dropped_assets": dropped}
    scores = F[r] @ beta
    prob = R.bootstrap_topk_probability(F, y, r, H, train, ridge, top_k, n_boot=n_boot)

    # model health: causal walk-forward predictions vs what actually happened (only bars whose horizon has fully elapsed)
    health_bars = R.bars_for_hours(health_days * 24, interval)
    first = max(R.bars_for_hours(30 * 24, interval) + H, r - health_bars - H)
    wf, _ = R.walk_forward_scores(F, y, H, train, refit, ridge, first)
    ic = R.rank_ic(wf[:r - H + 1], y[:r - H + 1])
    ic = ic[~np.isnan(ic)]
    n_eff = max(len(ic) / max(H, 1), 1.0)
    mean_ic = float(ic.mean()) if len(ic) else None
    t_stat = float(mean_ic / (ic.std(ddof=1) / np.sqrt(n_eff))) if len(ic) > 5 and ic.std(ddof=1) > 0 else None
    status = "insufficient_data" if mean_ic is None or n_eff < 5 else ("ok" if (mean_ic > 0 and (t_stat or 0) > 1.0) else "degraded")

    raw = {name: F[r, :, i] for i, name in enumerate(R.FEATURE_NAMES)}
    nb24 = R.bars_for_hours(24, interval)
    quote_vol = md.volume[-nb24:].sum(axis=0) * md.close[-1]
    lr = np.diff(np.log(md.close[-(nb24 + 1):]), axis=0)
    vol_bar = lr.std(axis=0, ddof=1) if len(lr) > 2 else np.zeros(len(md.assets))     # raw per-bar volatility (used for suggested stop/target)
    liq_cut = float(np.percentile(quote_vol, 25))
    order = np.argsort(-scores)
    ranking: List[Dict[str, Any]] = []
    for rank, j in enumerate(order, start=1):
        flags = []
        if prob is not None and rank <= top_k and prob[j] < 0.5:
            flags.append("low_confidence")
        if quote_vol[j] <= liq_cut:
            flags.append("thin_liquidity")
        ranking.append({"asset": md.assets[j], "rank": rank, "score": float(scores[j]), "top_k_probability": None if prob is None else float(prob[j]),
                        "price": float(md.close[-1, j]), "quote_volume_recent": float(quote_vol[j]), "vol_per_bar": float(vol_bar[j]),
                        "features_z": {k: float(v[j]) for k, v in raw.items()}, "in_top_k": rank <= top_k, "flags": flags})
    return {
        "schema": SCHEMA, "available": True, "generated_at": now, "venue": adapter.name, "kind": adapter.kind, "interval": interval,
        "as_of_bar_open": int(md.ts[-1]), "horizon_h": horizon_h, "top_k": top_k, "n_assets": len(md.assets), "dropped_assets": dropped,
        "model": {"type": "ridge, cross-sectional", "train_days": train_days, "ridge": ridge, "features": R.FEATURE_NAMES,
                  "coefficients": {n: float(b) for n, b in zip(R.FEATURE_NAMES, beta)}},
        "health": {"mean_rank_ic": mean_ic, "ic_t_stat": t_stat, "n_bars": int(len(ic)), "window_days": health_days, "status": status,
                   "note": "rank IC of causal walk-forward predictions vs realised forward returns; > 0 and t > 1 required for 'ok'"},
        "ranking": ranking,
        "disclaimer": "Model output with an out-of-sample health check. Paper-trading research only; not a trade instruction.",
    }


def write_snapshot(snap: Dict[str, Any], path: Optional[str] = None) -> str:
    path = path or snapshot_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(snap, f)
    os.replace(tmp, path)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--venue", default="kucoin")
    ap.add_argument("--assets", nargs="+", default=DEFAULT_UNIVERSE)
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--horizon-h", type=float, default=24)
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--train-days", type=int, default=90)
    ap.add_argument("--out", default=None)
    ap.add_argument("--loop", type=int, default=0, help="repeat every N seconds (0 = once)")
    a = ap.parse_args(argv)
    adapter = get_venue(a.venue)
    while True:
        snap = build_snapshot(adapter, a.assets, a.interval, a.horizon_h, a.top_k, a.train_days)
        path = write_snapshot(snap, a.out)
        if snap.get("available"):
            top = [f"{r['asset']}({r['top_k_probability']:.2f})" if r["top_k_probability"] is not None else r["asset"] for r in snap["ranking"][:a.top_k]]
            print(f"{time.strftime('%H:%M:%S')} {a.venue} top-{a.top_k}: {', '.join(top)} | health {snap['health']['status']} IC={snap['health']['mean_rank_ic']} -> {path}")
        else:
            print(f"snapshot unavailable: {snap.get('reason')}")
        if not a.loop:
            return 0 if snap.get("available") else 2
        time.sleep(a.loop)


if __name__ == "__main__":
    sys.exit(main())
