"""Dashboard API for the governing agent. Reads are public; every write needs X-Live-Token (GOVERNOR_API_TOKEN or LIVE_API_TOKEN)."""
import hmac
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from backend.config import settings
from backend.governor import service, store

router = APIRouter(prefix="/api/governor", tags=["governor"])


def _auth(token: Optional[str]) -> None:
    want = settings.GOVERNOR_API_TOKEN or settings.LIVE_API_TOKEN
    if not want:
        raise HTTPException(403, detail="GOVERNOR_API_TOKEN / LIVE_API_TOKEN is not configured; writes are disabled")
    if not token or not hmac.compare_digest(token, want):
        raise HTTPException(401, detail="Invalid or missing X-Live-Token")


@router.get("/overview")
def overview():
    return {"summaries": store.list_summaries(), "trials": store.list_trials(), "watchlist": store.list_watch(), "integrated": store.list_integrated()}


@router.get("/summaries/{slug}")
def get_summary(slug: str):
    s = store.get_summary(slug)
    if not s:
        raise HTTPException(404, detail="not found")
    return s


@router.get("/trials/{trial_id}")
def get_trial(trial_id: int):
    t = store.get_trial(trial_id)
    if not t:
        raise HTTPException(404, detail="not found")
    return t


@router.post("/summaries")
def post_summary(data: Dict[str, Any], x_live_token: Optional[str] = Header(default=None)):
    """Where a research agent's summary file is 'sent to the dashboard'."""
    _auth(x_live_token)
    try:
        return service.ingest_summary(data)
    except Exception as exc:
        raise HTTPException(422, detail=f"invalid summary: {str(exc)[:500]}")


class StartRequest(BaseModel):
    slug: str
    hours: float = service.HOURS


@router.post("/trials")
def start_trial(req: StartRequest, x_live_token: Optional[str] = Header(default=None)):
    """Creates a live paper trial. A runner process (`python -m backend.governor.run loop`) must be running to advance it."""
    _auth(x_live_token)
    try:
        return {"trial_id": service.start_trial(req.slug, "live", req.hours)}
    except (service.GovernorError, KeyError) as exc:
        raise HTTPException(422, detail=str(exc))


@router.post("/trials/{trial_id}/approve")
def approve(trial_id: int, x_live_token: Optional[str] = Header(default=None)):
    _auth(x_live_token)
    try:
        return service.approve(trial_id)
    except service.GovernorError as exc:
        raise HTTPException(409, detail=str(exc))


@router.post("/trials/{trial_id}/reject")
def reject(trial_id: int, x_live_token: Optional[str] = Header(default=None)):
    _auth(x_live_token)
    try:
        return service.reject(trial_id)
    except service.GovernorError as exc:
        raise HTTPException(409, detail=str(exc))


class AuthorizeRequest(BaseModel):
    confirm: str
    max_order_usdt: float


@router.post("/trials/{trial_id}/authorize-live")
def authorize_live(trial_id: int, req: AuthorizeRequest, x_live_token: Optional[str] = Header(default=None)):
    """After the 72h paper window and a human approval: allow this strategy to produce live order tickets (not auto-trading)."""
    _auth(x_live_token)
    try:
        return service.authorize_live(trial_id, req.confirm, req.max_order_usdt)
    except service.GovernorError as exc:
        raise HTTPException(409, detail=str(exc))


@router.post("/live-authorization/{slug}/revoke")
def revoke_live(slug: str, x_live_token: Optional[str] = Header(default=None)):
    _auth(x_live_token)
    return service.revoke_live(slug)


@router.get("/live-authorization")
def live_authorizations():
    return {"authorizations": store.list_live_authorizations()}
