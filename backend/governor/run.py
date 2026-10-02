#!/usr/bin/env python3
"""Governing-agent CLI for an always-on host.

    python -m backend.governor.run ingest research/summaries/trend_following.json
    python -m backend.governor.run start trend_following            # 72h paper trial on LIVE public candles
    python -m backend.governor.run loop --interval-sec 300          # ticks every running trial, checks the watchlist
    python -m backend.governor.run status
    python -m backend.governor.run report 3
    python -m backend.governor.run approve 3                        # human decision; paper sleeve only
    python -m backend.governor.run replay trend_following           # pipeline smoke test on historical candles (not evidence)
"""
import argparse
import json
import sys
import time

from backend.governor import service, store


def _print(x) -> None:
    print(json.dumps(x, indent=2, default=str))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest"); p.add_argument("path")
    p = sub.add_parser("start"); p.add_argument("slug"); p.add_argument("--hours", type=float, default=service.HOURS)
    p = sub.add_parser("tick"); p.add_argument("trial_id", type=int)
    p = sub.add_parser("loop"); p.add_argument("--interval-sec", type=int, default=300)
    sub.add_parser("status")
    p = sub.add_parser("report"); p.add_argument("trial_id", type=int)
    p = sub.add_parser("approve"); p.add_argument("trial_id", type=int)
    p = sub.add_parser("reject"); p.add_argument("trial_id", type=int)
    p = sub.add_parser("replay"); p.add_argument("slug"); p.add_argument("--hours", type=float, default=service.HOURS)
    a = ap.parse_args(argv)

    if a.cmd == "ingest":
        with open(a.path) as f:
            _print(service.ingest_summary(json.load(f)))
    elif a.cmd == "start":
        _print({"trial_id": service.start_trial(a.slug, "live", a.hours)})
    elif a.cmd == "tick":
        _print(service.tick_trial(a.trial_id))
    elif a.cmd == "loop":
        while True:
            for t in store.list_trials("RUNNING"):
                try:
                    _print({"trial": t["id"], **service.tick_trial(t["id"])})
                except Exception as exc:  # keep the loop alive; the error is visible and the trial keeps its state
                    print(f"trial {t['id']}: {type(exc).__name__}: {exc}", file=sys.stderr)
            _print({"watchlist": service.check_watchlist()})
            time.sleep(a.interval_sec)
    elif a.cmd == "status":
        _print({"summaries": store.list_summaries(), "trials": store.list_trials(), "watchlist": store.list_watch(), "integrated": store.list_integrated()})
    elif a.cmd == "report":
        t = store.get_trial(a.trial_id)
        _print(t["report"] if t and t.get("report") else {"error": "no report yet"})
    elif a.cmd == "approve":
        _print(service.approve(a.trial_id))
    elif a.cmd == "reject":
        _print(service.reject(a.trial_id))
    elif a.cmd == "replay":
        _replay(a.slug, a.hours)
    return 0


def _replay(slug: str, hours: float) -> None:
    """Replay the most recent `hours` of real candles for the summary's recommended config through the full trial path."""
    from backend.governor.trial import adapter_for
    from research.lab.data import build_market_data
    from research.lab.venues import INTERVAL_MS
    summ = store.get_summary(slug)
    if not summ or not summ["data"].get("recommended_trial"):
        raise SystemExit(f"no recommended trial for {slug}")
    rec = summ["data"]["recommended_trial"]
    bar = INTERVAL_MS[rec["interval"]]
    end = (int(time.time() * 1000) // bar) * bar
    md = build_market_data(adapter_for(rec["venue"]), rec["assets"], rec["interval"], end - 760 * bar, end,
                           with_funding=False)
    first = int(md.ts[-1]) - int(hours * 3_600_000) + bar - bar
    tid = service.start_trial(slug, "replay", hours, now_ms=first + bar, first_bar_ts=first)
    _print({"trial_id": tid, **service.run_replay(tid, md)})


if __name__ == "__main__":
    sys.exit(main())
