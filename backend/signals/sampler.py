"""Background sampler: logs a live-feed signal per symbol and labels forward returns. Reads feeds only; never trades."""
import asyncio
from typing import Optional

from loguru import logger

from backend.config import settings
from backend.marketdata.hub import hub
from backend.signals import signal_log
from backend.signals.live_feed import compute_live_snapshot

QUALIFY_SCORE = 0.70  # same bar as the Master Strategy gate; logged for calibration, not used to trade

_task: Optional[asyncio.Task] = None


def sample_once() -> int:
    """One pass over all subscribed symbols. Returns the number of signals logged."""
    logged = 0
    for sym in list(hub.books.keys()):
        snap = compute_live_snapshot(hub.get_state(sym), sym)
        if not snap["live"]:
            continue  # never log a row without a real price
        conf = snap["confluence"]
        score = conf.get("score") or 0.0
        veto = bool(conf.get("veto"))
        valid = (not veto) and score >= QUALIFY_SCORE
        signal_log.log_signal(
            sym, "live_flow", "live", snap["price"], score=score, valid=valid, direction="BUY",
            decision="vetoed" if veto else ("qualified" if valid else "below_threshold"),
            reason=conf.get("veto_reason") or "; ".join(conf.get("reasons") or []), features=snap["features"], ts=snap["ts"],
        )
        signal_log.label_pending(sym, snap["price"], snap["ts"])
        logged += 1
    return logged


async def _loop() -> None:
    interval = max(5, settings.LIVE_SIGNAL_INTERVAL_SEC)
    while True:
        try:
            await asyncio.sleep(interval)
            await asyncio.get_running_loop().run_in_executor(None, sample_once)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let sampling errors kill the task or the API
            logger.warning(f"Live signal sampler error: {exc}")


def start() -> None:
    global _task
    if settings.LIVE_SIGNAL_LOGGING_ENABLED and settings.FLOW_ENABLED and (_task is None or _task.done()):
        _task = asyncio.create_task(_loop())


def stop() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
    _task = None
