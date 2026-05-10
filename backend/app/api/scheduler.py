from datetime import datetime, timedelta

from fastapi import APIRouter

from app.core.config import load_config, save_config
from app.models.scheduler import SchedulerUpdate

router = APIRouter()


def _next_run(last_at: str | None, interval_min: int) -> str | None:
    if not last_at or interval_min <= 0:
        return None
    last = datetime.fromisoformat(last_at)
    return (last + timedelta(minutes=interval_min)).isoformat()


@router.get("/api/scheduler")
async def get_scheduler():
    cfg = load_config()
    s = cfg.scheduler
    return {
        "sync_enabled": s.sync_enabled,
        "sync_interval_minutes": s.sync_interval_minutes,
        "last_sync_at": s.last_sync_at,
        "next_sync_at": _next_run(s.last_sync_at, s.sync_interval_minutes) if s.sync_enabled else None,
        "qq_enabled": s.qq_enabled,
        "qq_interval_minutes": s.qq_interval_minutes,
        "last_qq_sync_at": s.last_qq_sync_at,
        "next_qq_sync_at": _next_run(s.last_qq_sync_at, s.qq_interval_minutes) if s.qq_enabled else None,
        "telegram_enabled": s.telegram_enabled,
        "telegram_interval_minutes": s.telegram_interval_minutes,
        "last_telegram_sync_at": s.last_telegram_sync_at,
        "next_telegram_sync_at": _next_run(s.last_telegram_sync_at, s.telegram_interval_minutes) if s.telegram_enabled else None,
        "analyze_enabled": s.analyze_enabled,
        "analyze_interval_minutes": s.analyze_interval_minutes,
        "last_analyze_at": s.last_analyze_at,
        "next_analyze_at": _next_run(s.last_analyze_at, s.analyze_interval_minutes) if s.analyze_enabled else None,
    }


@router.put("/api/scheduler")
async def update_scheduler(body: SchedulerUpdate):
    cfg = load_config()
    s = cfg.scheduler
    now_iso = datetime.now().isoformat()

    # When a toggle flips OFF→ON, anchor last_X_at to "now" so the next run is
    # `now + interval` instead of firing immediately because last_at is stale.
    def _flip_on(prev: bool, incoming: bool | None) -> bool:
        return incoming is True and not prev

    if body.sync_enabled is not None:
        if _flip_on(s.sync_enabled, body.sync_enabled):
            s.last_sync_at = now_iso
        s.sync_enabled = body.sync_enabled
    if body.sync_interval_minutes is not None:
        s.sync_interval_minutes = body.sync_interval_minutes
    if body.analyze_enabled is not None:
        if _flip_on(s.analyze_enabled, body.analyze_enabled):
            s.last_analyze_at = now_iso
        s.analyze_enabled = body.analyze_enabled
    if body.analyze_interval_minutes is not None:
        s.analyze_interval_minutes = body.analyze_interval_minutes
    if body.qq_enabled is not None:
        if _flip_on(s.qq_enabled, body.qq_enabled):
            s.last_qq_sync_at = now_iso
        s.qq_enabled = body.qq_enabled
    if body.qq_interval_minutes is not None:
        s.qq_interval_minutes = body.qq_interval_minutes
    if body.telegram_enabled is not None:
        if _flip_on(s.telegram_enabled, body.telegram_enabled):
            s.last_telegram_sync_at = now_iso
        s.telegram_enabled = body.telegram_enabled
    if body.telegram_interval_minutes is not None:
        s.telegram_interval_minutes = body.telegram_interval_minutes
    save_config(cfg)
    return await get_scheduler()
