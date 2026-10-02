"""Unified hub: one overview endpoint across VolHelix and every registered sibling project.

Sibling projects are declared in ``hub.json`` (override path with HUB_REGISTRY).
VolHelix itself is always included and read in-process.
"""
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter

from backend.config import settings
from backend.engine.auto_trader import auto_trader
from backend.store.portfolio_store import portfolio_store

router = APIRouter()

REGISTRY_PATH = Path(os.getenv("HUB_REGISTRY", Path(__file__).resolve().parents[2] / "hub.json"))
PROBE_TIMEOUT_S = 2.0


def load_registry() -> List[Dict[str, Any]]:
    try:
        return list(json.loads(REGISTRY_PATH.read_text()).get("projects", []))
    except (OSError, ValueError):
        return []


async def _probe(client: httpx.AsyncClient, project: Dict[str, Any]) -> Dict[str, Any]:
    url = project.get("health_url")
    result = {**project, "status": "unconfigured", "latency_ms": None}
    if not url:
        return result
    start = time.monotonic()
    try:
        resp = await client.get(url)
        result["status"] = "online" if resp.status_code < 400 else "degraded"
        result["latency_ms"] = round((time.monotonic() - start) * 1000)
    except httpx.HTTPError:
        result["status"] = "offline"
    return result


def _volhelix_card() -> Dict[str, Any]:
    card: Dict[str, Any] = {
        "id": "volhelix",
        "name": "VolHelix AI",
        "kind": "trading-swarm",
        "description": "Multi-agent crypto spot volatility swarm with deterministic risk gate.",
        "repo": "sudojudo62-cell/VolHelix-AI-2.0",
        "dashboard_url": "/",
        "status": "online",
        "latency_ms": 0,
    }
    try:
        card["portfolio"] = portfolio_store.get_snapshot().model_dump()
    except Exception:
        card["portfolio"] = None
    snap = card["portfolio"] or {}
    card["risk"] = {
        "max_daily_drawdown_pct": settings.MAX_DAILY_DRAWDOWN * 100,
        "daily_drawdown_pct": max(0.0, -float(snap.get("daily_pnl_pct", 0.0) or 0.0)),  # percent, same unit as above
    }
    try:
        card["auto_trader"] = auto_trader.status()
    except Exception:
        card["auto_trader"] = None
    return card


@router.get("/api/hub/projects")
def hub_projects():
    return {"projects": [{"id": "volhelix", "name": "VolHelix AI"}, *load_registry()]}


@router.get("/api/hub/overview")
async def hub_overview():
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S) as client:
        siblings = await asyncio.gather(*(_probe(client, p) for p in load_registry()))
    projects = [_volhelix_card(), *siblings]
    return {
        "generated_at": time.time(),
        "projects": projects,
        "summary": {
            "total": len(projects),
            "online": sum(1 for p in projects if p["status"] == "online"),
        },
    }
