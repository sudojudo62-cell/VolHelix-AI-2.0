"""Public-data adapters for the research venues: KuCoin, dYdX v4, Uniswap v3, Hyperliquid, Deribit, BitMEX.

Every adapter returns candles as ascending, de-duplicated dicts {ts, open, high, low, close, volume} (ts = bar open,
ms epoch) and, for perpetuals, funding events {ts, rate} where `rate` is the fraction charged per funding event.
Only PUBLIC endpoints are used: no keys, no orders. Uniswap needs a free The Graph API key (THEGRAPH_API_KEY).

These adapters were written from the exchanges' public API documentation and unit-tested against recorded response
SHAPES with a mocked transport only. They have NOT been exercised against the live APIs from the build sandbox, so the
first real run is also their integration test: use `validate_candles` and the cross-venue price check before trusting data.
"""
import os
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

import httpx

from research.lab import cache

Candle = Dict[str, float]
INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "6h": 21_600_000, "1d": 86_400_000}


class VenueError(Exception):
    pass


class NotSupported(VenueError):
    pass


def _dedupe_sorted(rows: List[Candle], start_ms: int, end_ms: int) -> List[Candle]:
    seen: Dict[int, Candle] = {}
    for r in rows:
        if start_ms <= r["ts"] < end_ms:
            seen[int(r["ts"])] = r
    return [seen[k] for k in sorted(seen)]


def aggregate_candles(rows: List[Candle], bar_ms: int) -> List[Candle]:
    """Build `bar_ms` candles (aligned to UTC multiples of bar_ms) from finer ones. Incomplete buckets are dropped."""
    if not rows:
        return []
    src = rows[1]["ts"] - rows[0]["ts"] if len(rows) > 1 else bar_ms
    need = bar_ms // src
    buckets: Dict[int, List[Candle]] = {}
    for r in rows:
        buckets.setdefault((r["ts"] // bar_ms) * bar_ms, []).append(r)
    out = []
    for ts in sorted(buckets):
        b = buckets[ts]
        if len(b) < need:
            continue  # partial bucket (range edge or missing source bars): never emit a synthetic bar
        out.append({"ts": ts, "open": b[0]["open"], "high": max(x["high"] for x in b), "low": min(x["low"] for x in b),
                    "close": b[-1]["close"], "volume": sum(x["volume"] for x in b)})
    return out


class VenueAdapter(ABC):
    name = "base"
    kind = "spot"            # spot | perp | dex
    has_funding = False
    min_request_gap_s = 0.15
    day_offset_ms = 0            # UTC offset of this venue's daily candle open (0 = 00:00 UTC)
    max_history_candles = None   # hard cap on how far back the API serves (informational; reported in data quality)
    intervals: Dict[str, Any] = {}
    assets: Dict[str, str] = {}  # canonical asset -> venue symbol

    def __init__(self, client: Optional[httpx.Client] = None, sleep: Callable[[float], None] = time.sleep):
        self.client = client or httpx.Client(timeout=25, headers={"User-Agent": "volhelix-research-lab/1.0"})
        self._sleep = sleep
        self._last = 0.0

    # ── http with politeness + retry ────────────────────────────────────────
    def _request(self, method: str, url: str, **kw) -> Any:
        for attempt in range(6):
            wait = self.min_request_gap_s - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.client.request(method, url, **kw)
            except httpx.TransportError as exc:
                if attempt == 5:
                    raise VenueError(f"{self.name}: transport error {exc}") from exc
                self._sleep(min(2 ** attempt, 20))
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                if attempt == 5:
                    raise VenueError(f"{self.name}: HTTP {r.status_code} after retries")
                self._sleep(min(2 ** attempt, 30))
                continue
            if r.status_code >= 400:
                raise VenueError(f"{self.name}: HTTP {r.status_code} {r.text[:200]}")
            return r.json()
        raise VenueError(f"{self.name}: request failed")

    symbol_fmt = None            # e.g. "{asset}-USDT": lets wide universes work without a hand-written map; the API rejects unlisted symbols

    def venue_symbol(self, asset: str) -> str:
        if asset in self.assets:
            return self.assets[asset]
        if self.symbol_fmt:
            return self.symbol_fmt.format(asset=asset)
        raise NotSupported(f"{self.name} has no mapping for {asset}")

    # ── cached public API ───────────────────────────────────────────────────
    def supports(self, interval: str) -> bool:
        return interval in self.intervals or self._aggregable(interval)

    def _aggregable(self, interval: str) -> bool:
        return (interval in INTERVAL_MS and interval not in self.intervals and "1h" in self.intervals
                and INTERVAL_MS[interval] % INTERVAL_MS["1h"] == 0 and interval != "1d")

    def candles(self, asset: str, interval: str, start_ms: int, end_ms: int, fresh: bool = False) -> List[Candle]:
        """`fresh=True` skips the disk cache (live ticks). Intervals the venue lacks (4h, 6h) are aggregated from its 1h bars."""
        if not self.supports(interval):
            raise NotSupported(f"{self.name} does not support interval {interval}")
        if interval not in self.intervals:
            bar = INTERVAL_MS[interval]
            lo, hi = (start_ms // bar) * bar, (end_ms // bar) * bar
            return aggregate_candles(self.candles(asset, "1h", lo, hi, fresh=fresh), bar)
        key = {"kind": "candles", "venue": self.name, "asset": asset, "interval": interval, "start": start_ms, "end": end_ms}
        hit = None if fresh else cache.get(key)
        if hit is not None:
            return hit
        rows = _dedupe_sorted(self._fetch_candles(self.venue_symbol(asset), interval, start_ms, end_ms), start_ms, end_ms)
        if not fresh:
            cache.put(key, rows)
        return rows

    def funding(self, asset: str, start_ms: int, end_ms: int, fresh: bool = False) -> List[Dict[str, float]]:
        if not self.has_funding:
            raise NotSupported(f"{self.name} has no funding rates")
        key = {"kind": "funding", "venue": self.name, "asset": asset, "start": start_ms, "end": end_ms}
        hit = None if fresh else cache.get(key)
        if hit is not None:
            return hit
        rows = self._fetch_funding(self.venue_symbol(asset), start_ms, end_ms)
        rows = sorted({int(r["ts"]): r for r in rows if start_ms <= r["ts"] < end_ms}.values(), key=lambda r: r["ts"])
        if not fresh:
            cache.put(key, rows)
        return rows

    @abstractmethod
    def _fetch_candles(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> List[Candle]: ...

    def _fetch_funding(self, symbol: str, start_ms: int, end_ms: int) -> List[Dict[str, float]]:
        raise NotSupported(f"{self.name} has no funding rates")


# ── KuCoin spot ─────────────────────────────────────────────────────────────
class KuCoin(VenueAdapter):
    name, kind = "kucoin", "spot"
    symbol_fmt = "{asset}-USDT"
    intervals = {"1m": "1min", "5m": "5min", "15m": "15min", "1h": "1hour", "4h": "4hour", "1d": "1day"}
    assets = {"BTC": "BTC-USDT", "ETH": "ETH-USDT", "SOL": "SOL-USDT", "BNB": "BNB-USDT", "XRP": "XRP-USDT",
              "ADA": "ADA-USDT", "DOGE": "DOGE-USDT", "AVAX": "AVAX-USDT", "LINK": "LINK-USDT"}
    base = "https://api.kucoin.com"

    def _fetch_candles(self, symbol, interval, start_ms, end_ms):
        step = INTERVAL_MS[interval] * 1400  # API returns at most 1500 rows
        out: List[Candle] = []
        cur = start_ms
        while cur < end_ms:
            stop = min(cur + step, end_ms)
            body = self._request("GET", f"{self.base}/api/v1/market/candles",
                                 params={"type": self.intervals[interval], "symbol": symbol,
                                         "startAt": cur // 1000, "endAt": stop // 1000})
            if body.get("code") != "200000":
                raise VenueError(f"kucoin: {body}")
            # each row: [time(s), open, close, high, low, volume, turnover]; newest first
            for t, o, c, h, l, v, _ in body.get("data") or []:
                out.append({"ts": int(t) * 1000, "open": float(o), "high": float(h), "low": float(l), "close": float(c), "volume": float(v)})
            cur = stop
        return out


# ── dYdX v4 (indexer) ───────────────────────────────────────────────────────
class DYDX(VenueAdapter):
    name, kind, has_funding = "dydx", "perp", True
    symbol_fmt = "{asset}-USD"
    intervals = {"1m": "1MIN", "5m": "5MINS", "15m": "15MINS", "1h": "1HOUR", "4h": "4HOURS", "1d": "1DAY"}
    assets = {"BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD", "AVAX": "AVAX-USD", "LINK": "LINK-USD",
              "DOGE": "DOGE-USD", "ADA": "ADA-USD", "XRP": "XRP-USD"}
    base = "https://indexer.dydx.trade/v4"

    @staticmethod
    def _iso(ms: int) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ms / 1000))

    @staticmethod
    def _parse_iso(s: str) -> int:
        import calendar
        return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")) * 1000

    def _fetch_candles(self, symbol, interval, start_ms, end_ms):
        out: List[Candle] = []
        to = end_ms
        for _ in range(10_000):  # walk backwards: 100 rows per call, newest first
            body = self._request("GET", f"{self.base}/candles/perpetualMarkets/{symbol}",
                                 params={"resolution": self.intervals[interval], "limit": 100, "toISO": self._iso(to), "fromISO": self._iso(start_ms)})
            rows = body.get("candles") or []
            if not rows:
                break
            for r in rows:
                out.append({"ts": self._parse_iso(r["startedAt"]), "open": float(r["open"]), "high": float(r["high"]),
                            "low": float(r["low"]), "close": float(r["close"]), "volume": float(r.get("baseTokenVolume", 0) or 0)})
            oldest = min(self._parse_iso(r["startedAt"]) for r in rows)
            if oldest <= start_ms or len(rows) < 100:
                break
            to = oldest  # next page ends where this one started
        return out

    def _fetch_funding(self, symbol, start_ms, end_ms):
        out: List[Dict[str, float]] = []
        before = self._iso(end_ms)
        for _ in range(10_000):
            body = self._request("GET", f"{self.base}/historicalFunding/{symbol}", params={"limit": 100, "effectiveBeforeOrAt": before})
            rows = body.get("historicalFunding") or []
            if not rows:
                break
            for r in rows:
                out.append({"ts": self._parse_iso(r["effectiveAt"]), "rate": float(r["rate"])})  # hourly rate
            oldest = min(self._parse_iso(r["effectiveAt"]) for r in rows)
            if oldest <= start_ms or len(rows) < 100:
                break
            before = self._iso(oldest - 1000)
        return out


# ── Hyperliquid ─────────────────────────────────────────────────────────────
class Hyperliquid(VenueAdapter):
    name, kind, has_funding = "hyperliquid", "perp", True
    symbol_fmt = "{asset}"
    max_history_candles = 5000   # the info API serves only the most recent ~5000 candles per interval
    intervals = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
    assets = {a: a for a in ("BTC", "ETH", "SOL", "BNB", "XRP", "AVAX", "LINK", "DOGE", "ADA")}
    url = "https://api.hyperliquid.xyz/info"

    def _fetch_candles(self, symbol, interval, start_ms, end_ms):
        out: List[Candle] = []
        step = INTERVAL_MS[interval] * 4500  # the API serves at most ~5000 candles per call
        cur = start_ms
        while cur < end_ms:
            stop = min(cur + step, end_ms)
            rows = self._request("POST", self.url, json={"type": "candleSnapshot", "req": {"coin": symbol, "interval": self.intervals[interval], "startTime": cur, "endTime": stop}})
            for r in rows or []:
                out.append({"ts": int(r["t"]), "open": float(r["o"]), "high": float(r["h"]), "low": float(r["l"]), "close": float(r["c"]), "volume": float(r["v"])})
            cur = stop
        return out

    def _fetch_funding(self, symbol, start_ms, end_ms):
        out: List[Dict[str, float]] = []
        cur = start_ms
        for _ in range(1000):
            rows = self._request("POST", self.url, json={"type": "fundingHistory", "coin": symbol, "startTime": cur, "endTime": end_ms}) or []
            if not rows:
                break
            out.extend({"ts": int(r["time"]), "rate": float(r["fundingRate"])} for r in rows)  # hourly rate
            last = max(int(r["time"]) for r in rows)
            if last <= cur or len(rows) < 500:
                break
            cur = last + 1
        return out


# ── Deribit ─────────────────────────────────────────────────────────────────
class Deribit(VenueAdapter):
    name, kind, has_funding = "deribit", "perp", True
    day_offset_ms = 8 * 3_600_000   # daily candles open at 08:00 UTC, not 00:00 (found by the pilot run)
    intervals = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "1d": "1D"}  # no native 4h resolution; not faked
    assets = {"BTC": "BTC-PERPETUAL", "ETH": "ETH-PERPETUAL"}
    base = "https://www.deribit.com/api/v2/public"

    def _fetch_candles(self, symbol, interval, start_ms, end_ms):
        out: List[Candle] = []
        step = INTERVAL_MS[interval] * 4500
        cur = start_ms
        while cur < end_ms:
            stop = min(cur + step, end_ms)
            body = self._request("GET", f"{self.base}/get_tradingview_chart_data",
                                 params={"instrument_name": symbol, "start_timestamp": cur, "end_timestamp": stop, "resolution": self.intervals[interval]})
            res = body.get("result") or {}
            for t, o, h, l, c, v in zip(res.get("ticks", []), res.get("open", []), res.get("high", []), res.get("low", []), res.get("close", []), res.get("volume", [])):
                out.append({"ts": int(t), "open": float(o), "high": float(h), "low": float(l), "close": float(c), "volume": float(v)})
            cur = stop
        return out

    def _fetch_funding(self, symbol, start_ms, end_ms):
        out: List[Dict[str, float]] = []
        step = 30 * 86_400_000  # API limits the window; request month by month
        cur = start_ms
        while cur < end_ms:
            stop = min(cur + step, end_ms)
            body = self._request("GET", f"{self.base}/get_funding_rate_history", params={"instrument_name": symbol, "start_timestamp": cur, "end_timestamp": stop})
            for r in body.get("result") or []:
                out.append({"ts": int(r["timestamp"]), "rate": float(r["interest_1h"])})  # hourly funding component
            cur = stop
        return out


# ── BitMEX ──────────────────────────────────────────────────────────────────
class BitMEX(VenueAdapter):
    name, kind, has_funding = "bitmex", "perp", True
    min_request_gap_s = 1.1  # strict public rate limits
    intervals = {"1m": "1m", "5m": "5m", "1h": "1h", "1d": "1d"}
    assets = {"BTC": "XBTUSD", "ETH": "ETHUSD"}
    base = "https://www.bitmex.com/api/v1"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._state: Dict[str, str] = {}      # per-instance (a shared class dict leaked contract states between adapters)

    def _check_active(self, symbol: str) -> None:
        """BitMEX retires contracts (the pilot found XBTUSD/ETHUSD 'Settled' on 2026-09-16). Refuse them with a clear reason."""
        if symbol not in self._state:
            rows = self._request("GET", f"{self.base}/instrument", params={"symbol": symbol, "columns": "symbol,state,expiry"}) or []
            self._state[symbol] = (rows[0].get("state") if rows else "Unknown") or "Unknown"
            if rows and rows[0].get("expiry"):
                self._state[symbol] += f" (expiry {rows[0]['expiry']})"
        if not self._state[symbol].startswith(("Open", "Unknown")):
            raise NotSupported(f"bitmex {symbol} is not an active contract: {self._state[symbol]}")

    @staticmethod
    def _iso(ms: int) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ms / 1000))

    @staticmethod
    def _parse_iso(s: str) -> int:
        import calendar
        return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")) * 1000

    def _fetch_candles(self, symbol, interval, start_ms, end_ms):
        self._check_active(symbol)
        out: List[Candle] = []
        cur = start_ms
        bar = INTERVAL_MS[interval]
        while cur < end_ms:
            rows = self._request("GET", f"{self.base}/trade/bucketed", params={"binSize": self.intervals[interval], "partial": "false", "symbol": symbol, "count": 1000, "reverse": "false", "startTime": self._iso(cur), "endTime": self._iso(end_ms)}) or []
            if not rows:
                break
            for r in rows:
                if r.get("open") is None:
                    continue
                # BitMEX stamps buckets at their CLOSE; shift to the bar open so all venues share one convention
                o, h, l, c = (float(r[k]) for k in ("open", "high", "low", "close"))
                # BitMEX opens each bucket at the previous close, so open can fall outside [low, high]; widen the range to include it
                out.append({"ts": self._parse_iso(r["timestamp"]) - bar, "open": o, "high": max(h, o, c), "low": min(l, o, c), "close": c, "volume": float(r.get("volume") or 0)})
            nxt = self._parse_iso(rows[-1]["timestamp"]) + 1
            if nxt <= cur or len(rows) < 1000:
                break
            cur = nxt
        return out

    def _fetch_funding(self, symbol, start_ms, end_ms):
        out: List[Dict[str, float]] = []
        cur = start_ms
        for _ in range(1000):
            rows = self._request("GET", f"{self.base}/funding", params={"symbol": symbol, "count": 500, "reverse": "false", "startTime": self._iso(cur), "endTime": self._iso(end_ms)}) or []
            if not rows:
                break
            out.extend({"ts": self._parse_iso(r["timestamp"]), "rate": float(r["fundingRate"])} for r in rows)  # per 8h
            nxt = self._parse_iso(rows[-1]["timestamp"]) + 1
            if nxt <= cur or len(rows) < 500:
                break
            cur = nxt
        return out


# ── Bitfinex spot (long public history; replaces BitMEX, whose perpetuals are retired) ──
class Bitfinex(VenueAdapter):
    name, kind = "bitfinex", "spot"
    min_request_gap_s = 1.0
    intervals = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "1d": "1D"}
    assets = {"BTC": "tBTCUSD", "ETH": "tETHUSD", "SOL": "tSOLUSD", "XRP": "tXRPUSD", "ADA": "tADAUSD", "AVAX": "tAVAX:USD", "LINK": "tLINK:USD", "DOGE": "tDOGE:USD"}
    base = "https://api-pub.bitfinex.com/v2"

    def _fetch_candles(self, symbol, interval, start_ms, end_ms):
        out: List[Candle] = []
        cur = start_ms
        while cur < end_ms:
            rows = self._request("GET", f"{self.base}/candles/trade:{self.intervals[interval]}:{symbol}/hist",
                                 params={"start": cur, "end": end_ms, "limit": 10000, "sort": 1}) or []
            if not rows:
                break
            for t, o, c, h, l, v in rows:   # bitfinex column order: MTS, OPEN, CLOSE, HIGH, LOW, VOLUME
                out.append({"ts": int(t), "open": float(o), "high": float(h), "low": float(l), "close": float(c), "volume": float(v)})
            nxt = int(rows[-1][0]) + 1
            if nxt <= cur or len(rows) < 10000:
                break
            cur = nxt
        return out


# ── Uniswap v3 (subgraph; DEX AMM, no order book) ───────────────────────────
class Uniswap(VenueAdapter):
    """Hourly/daily pool prices from the Uniswap v3 Ethereum subgraph. Requires THEGRAPH_API_KEY.

    A DEX has no funding, no shorting and no L2 book: only spot-style long/flat strategies map onto it.
    Subgraph OHLC quotes token0 in token1, which is USD-per-asset for WBTC/USDC but asset-per-USD for USDC/WETH, so prices
    below $1 for these assets are inverted automatically (high/low swap). Always cross-check against another venue.
    """
    name, kind = "uniswap", "dex"
    intervals = {"1h": "poolHourDatas", "1d": "poolDayDatas"}
    pools = {"ETH": "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",   # USDC/WETH 0.05%
             "BTC": "0x99ac8ca7087fa4a2a1fb6357269965a2014abc35"}   # WBTC/USDC 0.3%
    assets = {"ETH": "ETH", "BTC": "BTC"}
    subgraph_id = "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV"
    min_request_gap_s = 0.3

    def _url(self) -> str:
        key = os.environ.get("THEGRAPH_API_KEY")
        if not key:
            raise NotSupported("uniswap needs THEGRAPH_API_KEY (free key from The Graph Studio)")
        return f"https://gateway.thegraph.com/api/{key}/subgraphs/id/{self.subgraph_id}"

    def _fetch_candles(self, symbol, interval, start_ms, end_ms):
        pool = self.pools[symbol]
        entity, bar = self.intervals[interval], INTERVAL_MS[interval]
        out: List[Candle] = []
        cur = start_ms // 1000
        while cur * 1000 < end_ms:
            q = f'{{ {entity}(first: 1000, orderBy: periodStartUnix, orderDirection: asc, where: {{pool: "{pool}", periodStartUnix_gte: {cur}}}) {{ periodStartUnix open high low close volumeUSD }} }}'
            body = self._request("POST", self._url(), json={"query": q})
            if body.get("errors"):
                raise VenueError(f"uniswap: {body['errors']}")
            rows = (body.get("data") or {}).get(entity) or []
            if not rows:
                break
            for r in rows:
                o, h, l, c = (float(r[k]) for k in ("open", "high", "low", "close"))
                out.append({"ts": int(r["periodStartUnix"]) * 1000, "open": o, "high": h, "low": l, "close": c, "volume": float(r.get("volumeUSD", 0) or 0)})
            nxt = int(rows[-1]["periodStartUnix"]) + bar // 1000
            if nxt <= cur or len(rows) < 1000:
                break
            cur = nxt
        closes = sorted(r["close"] for r in out if r["close"] > 0)
        if closes and closes[len(closes) // 2] < 1.0:  # asset-per-USD quote -> flip to USD-per-asset
            out = [{"ts": r["ts"], "open": 1 / r["open"], "high": 1 / r["low"], "low": 1 / r["high"], "close": 1 / r["close"], "volume": r["volume"]}
                   for r in out if min(r["open"], r["high"], r["low"], r["close"]) > 0]
        return out


REGISTRY = {c.name: c for c in (KuCoin, DYDX, Hyperliquid, Deribit, Bitfinex, BitMEX, Uniswap)}


def get_venue(name: str, **kw) -> VenueAdapter:
    if name not in REGISTRY:
        raise VenueError(f"unknown venue {name}; choose from {sorted(REGISTRY)}")
    return REGISTRY[name](**kw)
