"""Factor sweep: strategy x venue x interval x asset-set x params, scored with 6-month walk-forward out-of-sample returns.

Headline numbers are OUT-OF-SAMPLE: for every fold the parameters are chosen on the train window only and scored on the next
unseen window. Full-sample numbers are kept for factor attribution but labeled in-sample. The Deflated Sharpe uses the TOTAL
number of configurations evaluated in the sweep, so adding venues/params makes the bar higher, not lower.
"""
import json
import subprocess
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import product
from typing import Dict, List, Optional, Sequence

import numpy as np

from backend.quant.backtest import deflated_sharpe
from research.lab import metrics as M
from research.lab.data import DataQualityError, MarketData, build_market_data, build_universe, cross_check
from research.lab.engine import Costs, LookaheadError, assert_causal, simulate
from research.lab.plugin import Strategy
from research.lab.regime import regime_labels, regime_performance
from research.lab.summary import CellResult, RecommendedTrial, Source, StrategySummary
from research.lab.venues import INTERVAL_MS, NotSupported, VenueAdapter, VenueError, get_venue

DAY = 86_400_000
GATES = {"min_oos_return_pct": 0.0, "min_dsr": 0.5, "max_drawdown_pct": 35.0, "min_oos_bars_days": 60, "min_independent_bets": 8}
BET_TURNOVER = 0.25   # a bar whose gross weight change is >= 25% of NAV starts a new "bet" (vol-target jitter does not)
MIN_FOLD_DAYS = 5     # a regime counts in a fold only with this many days there
FOLD_CONSISTENCY = 2 / 3


@dataclass
class SweepConfig:
    venues: Sequence[str]
    assets: Sequence[str]
    intervals: Sequence[str]
    end_ms: int
    months: int = 6                 # months of OUT-OF-SAMPLE evaluation (the window also needs `train_days` of data before it)
    warmup_days: int = 90           # extra history before the analysis window so slow indicators are warm
    train_days: int = 60
    test_days: int = 30
    max_combos: int = 400
    costs: Dict[str, Costs] = field(default_factory=dict)
    reference_venue: str = "kucoin"

    @property
    def start_ms(self) -> int:
        """Start of the analysis window = first training bar; 6 test folds of 30d follow `train_days` later."""
        return self.end_ms - (self.train_days + self.months * 30) * DAY


def expand_grid(grid: Dict[str, Sequence], limit: int) -> List[dict]:
    keys = list(grid)
    combos = [dict(zip(keys, v)) for v in product(*(grid[k] for k in keys))] or [{}]
    if len(combos) > limit:
        raise ValueError(f"param grid has {len(combos)} combinations (> {limit}); shrink it: every extra trial lowers the Deflated Sharpe")
    return combos


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


def evaluate_cell(strategy: Strategy, md: MarketData, a0: int, combos: List[dict], cfg: SweepConfig, trial_sr: Dict[str, List[float]]) -> Optional[dict]:
    """Walk-forward one (venue, assets, interval) cell. `a0` = first analysis bar (earlier bars only warm indicators)."""
    costs = cfg.costs.get(md.venue) or Costs.for_venue(md.venue)
    bpy = md.bars_per_year
    assert_causal(strategy, md, combos[0])
    if len(combos) > 1:
        assert_causal(strategy, md, combos[-1])
    sims = [simulate(md, strategy.weights(md, p), costs) for p in combos]
    bar = INTERVAL_MS[md.interval]
    train, test = int(cfg.train_days * DAY / bar), int(cfg.test_days * DAY / bar)
    n = md.n
    # full-sample (in-sample) stats per combo -> factor attribution + trial-variance estimate
    full = []
    for p, s in zip(combos, sims):
        r = s.returns[a0:]
        full.append({"params": p, "sharpe": M.sharpe(r, bpy), "return_pct": M.total_return_pct(r)})
        trial_sr.setdefault(md.interval, []).append(M.sharpe(r, bpy) / np.sqrt(bpy))  # per-period SR, pooled ONLY within an interval
    folds, oos_r, oos_turn, picks = [], [], [], []
    start = a0
    while start + train + test <= n:
        tr, te = slice(start, start + train), slice(start + train, start + train + test)
        scores = [(M.sharpe(s.returns[tr], bpy), -float(s.turnover[tr].sum()), i) for i, s in enumerate(sims)]
        best_i = max(scores)[2]
        r = sims[best_i].returns[te]
        oos_r.append(r)
        oos_turn.append(sims[best_i].turnover[te])
        picks.append(json.dumps(combos[best_i], sort_keys=True))
        folds.append({"params": combos[best_i], "train_sharpe": float(scores[best_i][0]), "oos_return_pct": M.total_return_pct(r),
                      "oos_sharpe": M.sharpe(r, bpy), "start_ts": int(md.ts[te.start]), "end_ts": int(md.ts[te.stop - 1])})
        start += test
    if not folds:
        return None
    r_oos, t_oos = np.concatenate(oos_r), np.concatenate(oos_turn)
    first_oos = a0 + train
    stats = M.cell_stats(r_oos, md.interval, bpy, t_oos)
    # buy-and-hold benchmark over the same OOS bars: equal-weight long, one entry cost
    oos_sl = slice(first_oos, first_oos + len(r_oos))
    prev = md.close[oos_sl.start - 1:oos_sl.stop - 1]
    r_b = (md.close[oos_sl] / prev - 1).mean(axis=1)
    bench = M.total_return_pct(r_b) - 100 * costs.per_unit
    bench_dd = M.max_drawdown_pct(r_b)
    best_full = max(full, key=lambda d: d["sharpe"])
    labels = regime_labels(md.ts, md.close[:, 0])[first_oos:first_oos + len(r_oos)]
    modal = json.loads(Counter(picks).most_common(1)[0][0])
    sk, ku = M.moments(r_oos)
    # DSR must count independent bets, not bars: a strategy that flips direction 3 times has ~4 bets however fine the bars are
    n_bets = int(max(2, 1 + int((t_oos >= BET_TURNOVER).sum())))
    bar_days = bar / DAY
    fold_pos: Dict[str, List[bool]] = {}
    off = 0
    for r in oos_r:
        lab = labels[off:off + len(r)]
        off += len(r)
        for g in set(lab):
            if g != "warmup" and (lab == g).sum() * bar_days >= MIN_FOLD_DAYS:
                fold_pos.setdefault(g, []).append(bool(r[lab == g].mean() > 0))
    regimes = regime_performance(r_oos, labels, bpy, md.interval)
    for d in regimes:
        v = fold_pos.get(d["regime"], [])
        d["folds_observed"] = len(v)
        d["fold_positive_frac"] = (sum(v) / len(v)) if v else None
    return {
        "stats": stats, "folds": folds, "bench": bench, "bench_dd": bench_dd, "full": full, "best_full": best_full, "modal": modal,
        "regimes": regimes, "n_bets": n_bets, "window72": M.window_stats(r_oos, md.interval, 72.0),
        "oos_sr_per_period": M.sharpe(r_oos, bpy) / np.sqrt(bpy), "n_obs": len(r_oos), "skew": sk, "kurt": ku,
    }


def run_sweep(strategies: Sequence[Strategy], source: Source, cfg: SweepConfig, adapters: Optional[Dict[str, VenueAdapter]] = None) -> StrategySummary:
    adapters = adapters or {}
    t0 = time.time()
    skipped: List[Dict[str, str]] = []
    quality: List[dict] = []
    cells: List[dict] = []
    trial_sr: Dict[str, List[float]] = {}
    n_trials = 0
    ref_md: Dict[tuple, MarketData] = {}
    ref_univ: Dict[str, MarketData] = {}
    warm_start = cfg.start_ms - cfg.warmup_days * DAY
    warm_bars_for = lambda interval: int(cfg.warmup_days * DAY / INTERVAL_MS[interval])

    for strat in strategies:
        combos = expand_grid(strat.param_grid, cfg.max_combos)
        for vname in cfg.venues:
            try:
                ad = adapters.get(vname) or get_venue(vname)
            except VenueError as exc:
                skipped.append({"strategy": strat.id, "venue": vname, "reason": str(exc)})
                continue
            if ad.kind not in strat.allowed_venue_kinds:
                skipped.append({"strategy": strat.id, "venue": vname, "reason": f"strategy not applicable to {ad.kind} venues"})
                continue
            if strat.requires_funding and not ad.has_funding:
                skipped.append({"strategy": strat.id, "venue": vname, "reason": "needs funding rates"})
                continue
            for interval in cfg.intervals:
                if not ad.supports(interval):
                    skipped.append({"strategy": strat.id, "venue": vname, "interval": interval, "reason": "interval not supported"})
                    continue
                if interval == "1d" and ad.day_offset_ms:
                    skipped.append({"strategy": strat.id, "venue": vname, "interval": interval,
                                    "reason": f"daily candles open at +{ad.day_offset_ms // 3_600_000}h UTC (different day boundary than other venues); test 1h/4h instead"})
                    continue
                for aset in strat.asset_sets(cfg.assets):
                    tag = f"{strat.id}/{vname}/{'+'.join(aset)}/{interval}"
                    dropped: Dict[str, str] = {}
                    try:
                        if getattr(strat, "tolerant_universe", False):
                            md, dropped = build_universe(ad, aset, interval, warm_start, cfg.end_ms, with_funding=ad.has_funding,
                                                         min_assets=getattr(strat, "min_assets", 8), allow_head_gap_bars=warm_bars_for(interval))
                            aset = list(md.assets)
                            tag = f"{strat.id}/{vname}/{len(aset)}assets/{interval}"
                        else:
                            md = build_market_data(ad, aset, interval, warm_start, cfg.end_ms, with_funding=ad.has_funding,
                                                   allow_head_gap_bars=warm_bars_for(interval))
                    except (NotSupported, DataQualityError, VenueError) as exc:
                        skipped.append({"strategy": strat.id, "venue": vname, "assets": "+".join(aset)[:80], "interval": interval, "reason": str(exc)[:300]})
                        continue
                    window = cfg.end_ms - cfg.start_ms
                    if int(md.ts[-1]) - max(int(md.ts[0]), cfg.start_ms) < 0.97 * window:
                        skipped.append({"strategy": strat.id, "venue": vname, "assets": "+".join(aset), "interval": interval,
                                        "reason": f"history shorter than the required {cfg.train_days}d train + {cfg.months}mo out-of-sample; refusing to report a shorter window"})
                        continue
                    warm_avail = max(0.0, (cfg.start_ms - int(md.ts[0])) / DAY)
                    if warm_avail < strat.warmup_days:
                        skipped.append({"strategy": strat.id, "venue": vname, "assets": "+".join(aset), "interval": interval,
                                        "reason": f"only {warm_avail:.0f}d of warm-up history available, strategy needs {strat.warmup_days}d"})
                        continue
                    quality.append({"cell": tag, "warmup_days_available": round(warm_avail, 1), "warmup_days_requested": cfg.warmup_days,
                                    "dropped_assets": dropped, **{a: q for a, q in md.quality.items()}})
                    a0 = int(np.searchsorted(md.ts, cfg.start_ms))
                    if vname == cfg.reference_venue:
                        ref_md[(interval, tuple(aset))] = md
                        ref_univ[interval] = md
                    try:
                        res = evaluate_cell(strat, md, a0, combos, cfg, trial_sr)
                    except LookaheadError:
                        raise  # a lookahead bug invalidates every number: fail loudly
                    if res is None:
                        skipped.append({"strategy": strat.id, "venue": vname, "assets": "+".join(aset), "interval": interval, "reason": "not enough history for one train+test fold"})
                        continue
                    n_trials += len(combos) * max(strat.internal_trials, 1)
                    xc = None
                    ref = ref_md.get((interval, tuple(aset))) or (ref_univ.get(interval) if getattr(strat, "tolerant_universe", False) else None)
                    if ref is None and len(aset) > 1:   # multi-asset cell: check every asset against single-asset reference series
                        singles = [ref_md.get((interval, (a,))) for a in aset]
                        if all(x is not None for x in singles):
                            checks = [cross_check(md, x, a) for a, x in zip(aset, singles)]
                            bad = next((c for c in checks if c["ok"] is False), None)
                            xc = bad or ({"ok": True} if all(c["ok"] for c in checks) else {"ok": None, "reason": "too few shared bars"})
                    if ref is not None and vname != cfg.reference_venue:
                        common_assets = [a for a in aset if a in ref.assets]
                        xc = cross_check(md, ref, common_assets[0]) if common_assets else {"ok": None, "reason": "no shared asset"}
                        if xc["ok"] is False:
                            skipped.append({"strategy": strat.id, "venue": vname, "assets": "+".join(aset)[:80], "interval": interval, "reason": f"price mismatch vs {cfg.reference_venue} (ratio {xc['median_ratio']:.3f}); discarded"})
                            continue
                    quality[-1]["price_check"] = ("verified vs " + cfg.reference_venue) if (xc and xc["ok"]) else \
                        ("reference venue" if vname == cfg.reference_venue else "NOT VERIFIED (" + (xc["reason"] if xc else "no reference data") + ")")
                    cells.append({"strat": strat, "md": md, "aset": aset, "interval": interval, "res": res, "xc": xc, "combos": combos})

    # Per-period Sharpe scales with sqrt(bars), so the trial-variance used to set the deflation threshold MUST come from the
    # same interval as the cell being scored (pooling 1h and 1d variances made 1h unpassable and 1d too lenient).
    sr_var_by_interval = {i: (float(np.var(v, ddof=1)) if len(v) > 1 else 0.0) for i, v in trial_sr.items()}
    results: List[CellResult] = []
    for c in cells:
        r, st = c["res"], c["res"]["stats"]
        dsr = deflated_sharpe(r["oos_sr_per_period"], min(r["n_obs"], r["n_bets"]), max(n_trials, 1), sr_var_by_interval.get(c["interval"], 0.0), r["skew"], r["kurt"])
        results.append(CellResult(
            strategy_id=c["strat"].id, venue=c["md"].venue, assets=c["aset"], interval=c["interval"],
            oos_return_pct=st["return_pct"], oos_sharpe=st["sharpe"], oos_max_drawdown_pct=st["max_drawdown_pct"], oos_bars=st["bars"],
            oos_last_30d_return_pct=st["last_30d_return_pct"], benchmark_oos_return_pct=r["bench"], benchmark_oos_max_drawdown_pct=r["bench_dd"],
            turnover_per_day=st["turnover_per_day"],
            deflated_sharpe_prob=float(dsr), folds=r["folds"], best_full_sample={**r["best_full"], "note": "in-sample; do not trade on this"},
            regime_performance=r["regimes"], modal_params=r["modal"], window_72h_pct=r["window72"],
            independent_bets=r["n_bets"],
        ))
    results.sort(key=lambda x: x.oos_return_pct, reverse=True)

    def passes(x: CellResult) -> List[str]:
        why = []
        if x.oos_return_pct <= GATES["min_oos_return_pct"]:
            why.append(f"OOS net return {x.oos_return_pct:.1f}% <= 0")
        if x.oos_return_pct <= x.benchmark_oos_return_pct and x.oos_max_drawdown_pct >= 0.5 * x.benchmark_oos_max_drawdown_pct:
            why.append(f"does not beat buy-and-hold ({x.benchmark_oos_return_pct:.1f}%) on return, nor halve its drawdown")
        if x.independent_bets < GATES["min_independent_bets"]:
            why.append(f"only {x.independent_bets} independent bets in the OOS window < {GATES['min_independent_bets']}")
        if (x.deflated_sharpe_prob or 0) < GATES["min_dsr"]:
            why.append(f"deflated Sharpe {x.deflated_sharpe_prob:.2f} < {GATES['min_dsr']}")
        if x.oos_max_drawdown_pct > GATES["max_drawdown_pct"]:
            why.append(f"max drawdown {x.oos_max_drawdown_pct:.0f}% > {GATES['max_drawdown_pct']:.0f}%")
        return why

    viable = [x for x in results if not passes(x)]
    best = viable[0] if viable else (results[0] if results else None)
    reasons = ["passed all out-of-sample gates"] if viable else (passes(best) if best else ["no cell produced results (see skipped)"])
    rec = None
    if best:
        # a regime is only called favorable/unfavorable when its sign is consistent across folds (else it is left unnamed)
        def _frac(d):
            return d.get("fold_positive_frac")
        fav = [d["regime"] for d in best.regime_performance if d["sufficient"] and d["ann_return_pct"] > 0 and (_frac(d) or 0) >= FOLD_CONSISTENCY]
        unfav = [d["regime"] for d in best.regime_performance if d["sufficient"] and d["ann_return_pct"] <= 0 and _frac(d) is not None and _frac(d) <= 1 - FOLD_CONSISTENCY]
        w = best.window_72h_pct
        c = cfg.costs.get(best.venue) or Costs.for_venue(best.venue)
        rec = RecommendedTrial(strategy_id=best.strategy_id, venue=best.venue, assets=best.assets, interval=best.interval,
                               params=best.modal_params, expected_72h_return_pct=w.get("mean", 0.0), expected_72h_std_pct=w.get("std", 0.0),
                               expected_72h_p10_pct=w.get("p10", 0.0), favorable_regimes=fav, unfavorable_regimes=unfav,
                               costs_bps={"fee": c.fee_bps, "slippage": c.slippage_bps})
    return StrategySummary(
        source=source, strategy_ids=[s.id for s in strategies],
        run={"months": cfg.months, "start_ms": cfg.start_ms, "end_ms": cfg.end_ms, "venues": list(cfg.venues), "assets": list(cfg.assets),
             "intervals": list(cfg.intervals), "train_days": cfg.train_days, "test_days": cfg.test_days, "n_trials": n_trials,
             "trial_sharpe_variance_by_interval": sr_var_by_interval, "gates": GATES, "commit": _git_commit(), "seconds": round(time.time() - t0, 1),
             "finished_at": int(time.time() * 1000)},
        results=results, factor_attribution=_attribution(cells, results), data_quality=quality, skipped=skipped,
        viable=bool(viable), viability_reasons=reasons, recommended_trial=rec,
        caveats=["Headline numbers are walk-forward out-of-sample, net of assumed fees/slippage (not exchange-verified).",
                 "Backtests assume next-bar-open fills and no market impact; thin DEX/perp liquidity can be worse.",
                 "Perp leverage is capped and liquidation is not modeled.",
                 "Six months contains few independent regimes; favorable regimes are descriptive, not a forecast."],
    )


def _attribution(cells: List[dict], results: List[CellResult]) -> Dict[str, List[dict]]:
    """Mean in-sample Sharpe/return grouped by each factor level, plus mean OOS return by venue/asset/interval."""
    groups: Dict[str, Dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for c in cells:
        for f in c["res"]["full"]:
            groups["venue"][c["md"].venue].append(f)
            groups["interval"][c["interval"]].append(f)
            groups["assets"]["+".join(c["aset"])].append(f)
            for k, v in f["params"].items():
                groups[f"param:{k}"][str(v)].append(f)
    out: Dict[str, List[dict]] = {}
    for fac, lv in groups.items():
        out[fac] = sorted(({"level": k, "n": len(v), "mean_insample_sharpe": float(np.mean([x["sharpe"] for x in v])),
                            "mean_insample_return_pct": float(np.mean([x["return_pct"] for x in v]))} for k, v in lv.items()),
                          key=lambda d: -d["mean_insample_sharpe"])
    for fac, key in (("oos_by_venue", lambda r: r.venue), ("oos_by_interval", lambda r: r.interval), ("oos_by_assets", lambda r: "+".join(r.assets))):
        g: Dict[str, list] = defaultdict(list)
        for r in results:
            g[key(r)].append(r.oos_return_pct)
        out[fac] = sorted(({"level": k, "n": len(v), "mean_oos_return_pct": float(np.mean(v))} for k, v in g.items()), key=lambda d: -d["mean_oos_return_pct"])
    return out
