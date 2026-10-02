"""Durable audit trail + small state store for the live adapter (sqlite, path override: LIVE_DB_PATH)."""
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "live.db")
_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    path = os.environ.get("LIVE_DB_PATH", _DEFAULT)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    c = sqlite3.connect(path, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute(
        """CREATE TABLE IF NOT EXISTS live_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            client_order_id TEXT UNIQUE NOT NULL,
            symbol TEXT, side TEXT, order_type TEXT,
            notional REAL, mode TEXT, status TEXT, reason TEXT,
            request TEXT, response TEXT
        )"""
    )
    c.execute("CREATE TABLE IF NOT EXISTS live_state (key TEXT PRIMARY KEY, value TEXT)")
    return c


def utc_day_start_ms(now_ms: Optional[int] = None) -> int:
    now = datetime.fromtimestamp((now_ms or int(time.time() * 1000)) / 1000, tz=timezone.utc)
    return int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)


def record_order(client_order_id: str, symbol: str, side: str, order_type: str, notional: float, mode: str,
                 status: str, reason: str = "", request: Optional[Dict[str, Any]] = None) -> bool:
    """Insert an audit row. Returns False if client_order_id already exists (idempotency)."""
    with _lock, _conn() as c:
        try:
            c.execute(
                "INSERT INTO live_orders (ts, client_order_id, symbol, side, order_type, notional, mode, status, reason, request) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (int(time.time() * 1000), client_order_id, symbol, side, order_type, notional, mode, status, reason,
                 json.dumps(request, default=str) if request is not None else None),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def update_order(client_order_id: str, status: str, reason: str = "", response: Optional[Dict[str, Any]] = None) -> None:
    with _lock, _conn() as c:
        c.execute(
            "UPDATE live_orders SET status = ?, reason = ?, response = ? WHERE client_order_id = ?",
            (status, reason, json.dumps(response, default=str) if response is not None else None, client_order_id),
        )


def executed_notional_today(now_ms: Optional[int] = None) -> float:
    """Sum of notional for orders that really executed (mode=live, status SUBMITTED/FILLED) since UTC midnight."""
    with _lock, _conn() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(notional), 0) FROM live_orders WHERE mode = 'live' "
            "AND status IN ('SUBMITTED','FILLED','FLATTENED') AND ts >= ?",
            (utc_day_start_ms(now_ms),),
        ).fetchone()
    return float(row[0])


def recent_orders(limit: int = 100) -> List[Dict[str, Any]]:
    with _lock, _conn() as c:
        rows = c.execute("SELECT * FROM live_orders ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("request", "response"):
            d[k] = json.loads(d[k]) if d.get(k) else None
        out.append(d)
    return out


def get_state(key: str, default: Optional[str] = None) -> Optional[str]:
    with _lock, _conn() as c:
        row = c.execute("SELECT value FROM live_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_state(key: str, value: str) -> None:
    with _lock, _conn() as c:
        c.execute("INSERT INTO live_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
