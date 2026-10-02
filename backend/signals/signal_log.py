"""Persistent signal log with forward-return labels (roadmap step 2).

Every signal evaluation (paper scan or live feed) is stored with its features, decision and price, then labeled
later with the realized forward return at 1h / 4h / 24h. That history is what Kelly sizing and the 0.70 confluence
threshold must be calibrated against (see docs/STRATEGY_RESEARCH.md).

Synchronous sqlite3 with a short-lived connection per call: safe from the auto-trader's worker threads, the
event loop (calls are sub-millisecond) and tests. The DB path can be overridden with SIGNAL_DB_PATH.
"""
import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

HORIZONS = {"1h": 3_600_000, "4h": 14_400_000, "24h": 86_400_000}
_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "signals.db")
_lock = threading.Lock()


def _path() -> str:
    return os.environ.get("SIGNAL_DB_PATH", _DEFAULT_PATH)


def _conn() -> sqlite3.Connection:
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS signal_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            source TEXT NOT NULL,
            mode TEXT NOT NULL,
            direction TEXT,
            score REAL,
            valid INTEGER,
            decision TEXT,
            reason TEXT,
            price REAL NOT NULL,
            features TEXT,
            ret_1h REAL, ret_4h REAL, ret_24h REAL,
            label_ts_1h INTEGER, label_ts_4h INTEGER, label_ts_24h INTEGER
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signal_symbol_ts ON signal_log(symbol, ts)")
    return conn


def log_signal(
    symbol: str,
    source: str,
    mode: str,
    price: float,
    score: Optional[float] = None,
    valid: Optional[bool] = None,
    direction: Optional[str] = None,
    decision: Optional[str] = None,
    reason: Optional[str] = None,
    features: Optional[Dict[str, Any]] = None,
    ts: Optional[int] = None,
) -> Optional[int]:
    """Insert one signal. Rows without a positive price are rejected (a return cannot be labeled without one)."""
    if not price or price <= 0:
        return None
    with _lock, _conn() as conn:
        cur = conn.execute(
            "INSERT INTO signal_log (ts, symbol, source, mode, direction, score, valid, decision, reason, price, features) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                ts if ts is not None else int(time.time() * 1000), symbol.upper(), source, mode, direction, score,
                None if valid is None else int(bool(valid)), decision, reason, float(price),
                json.dumps(features, default=str) if features is not None else None,
            ),
        )
        return cur.lastrowid


def label_pending(symbol: str, price: float, now_ms: Optional[int] = None) -> int:
    """Fill forward returns for rows whose horizon has elapsed, using the price observed now.

    The label is the first price seen *after* the horizon (label_ts records when), so it can be slightly late
    but never early or fabricated.
    """
    if not price or price <= 0:
        return 0
    now_ms = now_ms or int(time.time() * 1000)
    updated = 0
    with _lock, _conn() as conn:
        for name, ms in HORIZONS.items():
            cur = conn.execute(
                f"UPDATE signal_log SET ret_{name} = (? / price) - 1, label_ts_{name} = ? "
                f"WHERE symbol = ? AND ret_{name} IS NULL AND ts + ? <= ?",
                (float(price), now_ms, symbol.upper(), ms, now_ms),
            )
            updated += cur.rowcount
    return updated


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    d["features"] = json.loads(d["features"]) if d.get("features") else None
    d["valid"] = None if d["valid"] is None else bool(d["valid"])
    return d


def recent(limit: int = 100, symbol: Optional[str] = None, mode: Optional[str] = None) -> List[Dict[str, Any]]:
    q, args = "SELECT * FROM signal_log WHERE 1=1", []
    if symbol:
        q += " AND symbol = ?"
        args.append(symbol.upper())
    if mode:
        q += " AND mode = ?"
        args.append(mode)
    q += " ORDER BY ts DESC, id DESC LIMIT ?"
    args.append(max(1, min(limit, 1000)))
    with _lock, _conn() as conn:
        return [_row(r) for r in conn.execute(q, args).fetchall()]


def calibration(mode: Optional[str] = None, horizon: str = "4h") -> List[Dict[str, Any]]:
    """Average forward return by score bucket (0.1 wide), net of nothing: add fees when judging edge."""
    if horizon not in HORIZONS:
        raise ValueError(f"horizon must be one of {sorted(HORIZONS)}")
    col = f"ret_{horizon}"
    q = (f"SELECT CAST(score*10 AS INTEGER)/10.0 AS bucket, COUNT(*) AS n, AVG({col}) AS avg_ret, "
         f"AVG({col} > 0) AS hit_rate FROM signal_log WHERE {col} IS NOT NULL AND score IS NOT NULL")
    args: List[Any] = []
    if mode:
        q += " AND mode = ?"
        args.append(mode)
    q += " GROUP BY bucket ORDER BY bucket"
    with _lock, _conn() as conn:
        return [dict(r) for r in conn.execute(q, args).fetchall()]
