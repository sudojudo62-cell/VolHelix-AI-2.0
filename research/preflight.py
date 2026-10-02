#!/usr/bin/env python3
"""Network preflight for a research session: can we reach every venue API (and optionally the source URLs)?

    python -m research.preflight [--sources URL ...]

Exit code 1 if any venue is unreachable. A blocked host is reported with its HTTP status or error so you know exactly what
to allow in the environment's network settings.
"""
import argparse
import sys

import httpx

VENUE_PINGS = {
    "kucoin": ("GET", "https://api.kucoin.com/api/v1/timestamp", None),
    "dydx": ("GET", "https://indexer.dydx.trade/v4/time", None),
    "hyperliquid": ("POST", "https://api.hyperliquid.xyz/info", {"type": "meta"}),
    "deribit": ("GET", "https://www.deribit.com/api/v2/public/get_time", None),
    "bitfinex": ("GET", "https://api-pub.bitfinex.com/v2/platform/status", None),
    "bitmex": ("GET", "https://www.bitmex.com/api/v1/announcement", None),
    "uniswap(the graph)": ("GET", "https://gateway.thegraph.com/api/", None),
    "binance (control: VolHelix live feed)": ("GET", "https://api.binance.com/api/v3/ping", None),
}


def check(method: str, url: str, body=None) -> str:
    try:
        r = httpx.request(method, url, json=body, timeout=15, follow_redirects=True)
        return f"HTTP {r.status_code}"
    except Exception as exc:  # report, never raise
        return f"FAILED ({type(exc).__name__}: {str(exc)[:80]})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", nargs="*", default=[])
    a = ap.parse_args()
    bad = 0
    for name, (m, u, b) in VENUE_PINGS.items():
        res = check(m, u, b)
        code = int(res.split()[1]) if res.startswith("HTTP") else 0
        if name.startswith("uniswap"):
            ok = code in (200, 400, 401, 403, 404)          # the Graph gateway answers 4xx without a key: reachable is enough
        else:
            ok = 200 <= code < 300
        control = name.startswith("binance")
        # 451 = the host is geo-blocking this environment; fine for research venues' data, but it means the live VolHelix feed won't work here
        label = "OK  " if ok else ("WARN" if control else "BAD ")
        bad += 0 if ok or control else 1
        print(f"{label} {name:40s} {res}")
    for u in a.sources:
        res = check("GET", u)
        print(f"{'OK ' if res.startswith('HTTP 2') else 'BAD'} source {u} {res}")
        bad += 0 if res.startswith("HTTP 2") else 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
