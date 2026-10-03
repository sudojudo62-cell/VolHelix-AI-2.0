"""Pair Scanner API. Reads the latest snapshot written by `python -m research.scanner`.

/latest is public and paper-only. /ticket returns SUGGESTED order parameters only when the strategy has a live authorization
(set by a human after the 72h paper trial), the model's out-of-sample health is ok, and the asset is a confident top-k pick.
It never places an order; execution still goes through the live adapter's own safeguards and manual confirmation.
"""
import hmac
import json
import math
import os
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException

from backend.config import settings
from backend.governor import store

router = APIRouter(prefix="/api/scanner", tags=["scanner"])
SLUG = "pair_ranker"
MAX_SNAPSHOT_AGE_MS = 3 * 3_600_000
STOP_SIGMAS = 1.5
TARGET_SIGMAS = 2.0


def _auth(token: Optional[str]) -> None:
    want = settings.GOVERNOR_API_TOKEN or settings.LIVE_API_TOKEN
    if not want:
        raise HTTPException(403, detail="GOVERNOR_API_TOKEN / LIVE_API_TOKEN is not configured; this endpoint is disabled")
    if not token or not hmac.compare_digest(token, want):
        raise HTTPException(401, detail="Invalid or missing X-Live-Token")


def _path() -> str:
    from research.scanner import snapshot_path
    return snapshot_path()


def _load() -> Optional[Dict[str, Any]]:
    try:
        with open(_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


@router.get("/latest")
def latest():
    snap = _load()
    auth = store.get_live_authorization(SLUG)
    if snap is None:
        return {"available": False, "reason": "no snapshot yet; run `python -m research.scanner`", "paper_only": True,
                "live_authorized": bool(auth)}
    snap["age_ms"] = int(time.time() * 1000) - int(snap.get("generated_at", 0))
    snap["paper_only"] = True
    snap["live_authorized"] = bool(auth)
    return snap


@router.post("/snapshot")
def snapshot(venue: str = "kucoin", interval: str = "1h", horizon_h: float = 24, top_k: int = 3,
             x_live_token: Optional[str] = Header(default=None)):
    """Rebuild the snapshot from public market data (slow: network + fit)."""
    _auth(x_live_token)
    from research.lab.venues import get_venue
    from research.scanner import build_snapshot, write_snapshot
    from research.universe import DEFAULT_UNIVERSE
    try:
        snap = build_snapshot(get_venue(venue), DEFAULT_UNIVERSE, interval, horizon_h, top_k)
    except Exception as exc:
        raise HTTPException(502, detail=f"snapshot failed: {str(exc)[:300]}")
    write_snapshot(snap)
    return {"available": snap.get("available"), "generated_at": snap.get("generated_at"), "reason": snap.get("reason")}


@router.get("/ticket")
def ticket(asset: str, x_live_token: Optional[str] = Header(default=None)):
    """Suggested order parameters for a top-k asset. Refuses unless every gate passes."""
    _auth(x_live_token)
    auth = store.get_live_authorization(SLUG)
    if not auth:
        raise HTTPException(403, detail="pair_ranker is not live-authorized (needs a finished, approved 72h paper trial)")
    snap = _load()
    if not snap or not snap.get("available"):
        raise HTTPException(409, detail="no usable snapshot")
    if int(time.time() * 1000) - int(snap["generated_at"]) > MAX_SNAPSHOT_AGE_MS:
        raise HTTPException(409, detail="snapshot is stale")
    if snap["health"]["status"] != "ok":
        raise HTTPException(409, detail=f"model health is {snap['health']['status']}")
    row = next((r for r in snap["ranking"] if r["asset"].upper() == asset.upper()), None)
    if row is None:
        raise HTTPException(404, detail="asset not in snapshot")
    if not row["in_top_k"] or "low_confidence" in row["flags"]:
        raise HTTPException(409, detail="asset is not a confident top-k pick")
    interval_h = snap["horizon_h"]
    from research.lab.venues import INTERVAL_MS
    bars = max(snap["horizon_h"] * 3_600_000 / INTERVAL_MS[snap["interval"]], 1)
    sigma = row["vol_per_bar"] * math.sqrt(bars)
    px = row["price"]
    return {
        "asset": row["asset"], "venue": snap["venue"], "side": "BUY", "reference_price": px,
        "suggested_stop": px * (1 - STOP_SIGMAS * sigma), "suggested_target": px * (1 + TARGET_SIGMAS * sigma),
        "horizon_h": interval_h, "max_order_usdt": auth["max_order_usdt"], "top_k_probability": row["top_k_probability"],
        "note": "Suggestion only. Submit through the live adapter, which applies its own switches, risk gate and manual confirmation.",
    }
