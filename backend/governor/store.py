"""SQLite persistence for summaries, paper trials, decisions and the shelved-strategy watchlist (GOVERNOR_DB_PATH)."""
import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "governor.db")
_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    path = os.environ.get("GOVERNOR_DB_PATH", _DEFAULT)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    c = sqlite3.connect(path, timeout=10)
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS summaries (slug TEXT PRIMARY KEY, received_ts INTEGER, viable INTEGER, data TEXT);
        CREATE TABLE IF NOT EXISTS trials (
            id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT, strategy_id TEXT, mode TEXT, status TEXT,
            start_ts INTEGER, end_ts INTEGER, config TEXT, state TEXT, report TEXT,
            decision TEXT, approval TEXT, decided_ts INTEGER);
        CREATE TABLE IF NOT EXISTS watchlist (
            slug TEXT PRIMARY KEY, strategy_id TEXT, favorable TEXT, last_check_ts INTEGER, last_regime TEXT,
            consecutive_matches INTEGER DEFAULT 0, status TEXT, note TEXT, meta TEXT);
        CREATE TABLE IF NOT EXISTS integrated (slug TEXT PRIMARY KEY, strategy_id TEXT, approved_ts INTEGER, trial_id INTEGER);
        CREATE TABLE IF NOT EXISTS live_authorization (slug TEXT PRIMARY KEY, strategy_id TEXT, trial_id INTEGER, authorized_ts INTEGER, max_order_usdt REAL, revoked_ts INTEGER);
        """
    )
    return c


def _j(v: Any) -> Optional[str]:
    return None if v is None else json.dumps(v, default=str)


def _load(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    for k in ("data", "config", "state", "report", "favorable", "meta"):
        if k in d and d[k]:
            d[k] = json.loads(d[k])
    return d


def save_summary(slug: str, data: dict, viable: bool) -> None:
    with _lock, _conn() as c:
        c.execute("INSERT INTO summaries (slug, received_ts, viable, data) VALUES (?,?,?,?) ON CONFLICT(slug) DO UPDATE SET "
                  "received_ts=excluded.received_ts, viable=excluded.viable, data=excluded.data", (slug, int(time.time() * 1000), int(viable), _j(data)))


def get_summary(slug: str) -> Optional[Dict[str, Any]]:
    with _lock, _conn() as c:
        r = c.execute("SELECT * FROM summaries WHERE slug = ?", (slug,)).fetchone()
    return _load(r) if r else None


def list_summaries() -> List[Dict[str, Any]]:
    with _lock, _conn() as c:
        rows = c.execute("SELECT slug, received_ts, viable FROM summaries ORDER BY received_ts DESC").fetchall()
    return [dict(r) for r in rows]


def create_trial(slug: str, strategy_id: str, mode: str, start_ts: int, end_ts: int, config: dict, state: dict) -> int:
    with _lock, _conn() as c:
        cur = c.execute("INSERT INTO trials (slug, strategy_id, mode, status, start_ts, end_ts, config, state) VALUES (?,?,?,?,?,?,?,?)",
                        (slug, strategy_id, mode, "RUNNING", start_ts, end_ts, _j(config), _j(state)))
        return int(cur.lastrowid)


def update_trial(trial_id: int, **fields: Any) -> None:
    if not fields:
        return
    cols = {"state", "report", "config"}
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = [(_j(v) if k in cols else v) for k, v in fields.items()]
    with _lock, _conn() as c:
        c.execute(f"UPDATE trials SET {sets} WHERE id = ?", (*vals, trial_id))


def get_trial(trial_id: int) -> Optional[Dict[str, Any]]:
    with _lock, _conn() as c:
        r = c.execute("SELECT * FROM trials WHERE id = ?", (trial_id,)).fetchone()
    return _load(r) if r else None


def list_trials(status: Optional[str] = None) -> List[Dict[str, Any]]:
    q, a = "SELECT id, slug, strategy_id, mode, status, start_ts, end_ts, decision, approval, decided_ts FROM trials", []
    if status:
        q += " WHERE status = ?"
        a.append(status)
    with _lock, _conn() as c:
        rows = c.execute(q + " ORDER BY id DESC", a).fetchall()
    return [dict(r) for r in rows]


def upsert_watch(slug: str, strategy_id: str, favorable: List[str], note: str = "", meta: Optional[dict] = None) -> None:
    with _lock, _conn() as c:
        c.execute("INSERT INTO watchlist (slug, strategy_id, favorable, status, note, meta) VALUES (?,?,?,?,?,?) ON CONFLICT(slug) DO UPDATE SET "
                  "favorable=excluded.favorable, status=excluded.status, note=excluded.note, meta=excluded.meta, consecutive_matches=0",
                  (slug, strategy_id, _j(favorable), "SHELVED", note, _j(meta)))


def update_watch(slug: str, **fields: Any) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = [(_j(v) if k == "favorable" else v) for k, v in fields.items()]
    with _lock, _conn() as c:
        c.execute(f"UPDATE watchlist SET {sets} WHERE slug = ?", (*vals, slug))


def list_watch() -> List[Dict[str, Any]]:
    with _lock, _conn() as c:
        rows = c.execute("SELECT * FROM watchlist ORDER BY slug").fetchall()
    return [_load(r) for r in rows]


def set_integrated(slug: str, strategy_id: str, trial_id: int) -> None:
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO integrated (slug, strategy_id, approved_ts, trial_id) VALUES (?,?,?,?)",
                  (slug, strategy_id, int(time.time() * 1000), trial_id))


def list_integrated() -> List[Dict[str, Any]]:
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM integrated").fetchall()]


def set_live_authorization(slug: str, strategy_id: str, trial_id: int, max_order_usdt: float) -> None:
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO live_authorization (slug, strategy_id, trial_id, authorized_ts, max_order_usdt, revoked_ts) VALUES (?,?,?,?,?,NULL)",
                  (slug, strategy_id, trial_id, int(time.time() * 1000), max_order_usdt))


def revoke_live_authorization(slug: str) -> bool:
    with _lock, _conn() as c:
        cur = c.execute("UPDATE live_authorization SET revoked_ts = ? WHERE slug = ? AND revoked_ts IS NULL", (int(time.time() * 1000), slug))
        return cur.rowcount > 0


def get_live_authorization(slug: str) -> Optional[Dict[str, Any]]:
    """Active (non-revoked) authorization for a strategy slug, or None."""
    with _lock, _conn() as c:
        r = c.execute("SELECT * FROM live_authorization WHERE slug = ? AND revoked_ts IS NULL", (slug,)).fetchone()
    return dict(r) if r else None


def list_live_authorizations() -> List[Dict[str, Any]]:
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM live_authorization ORDER BY authorized_ts DESC").fetchall()]
