from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from app.core.config import load_config, save_config
from app.core.scheduler import get_scheduler_state, mark_job_run
from app.models.scheduler import SchedulerUpdate

router = APIRouter()

# (config field prefix, scheduler_state job key, API last/next field stem)
_JOBS = (
    ("sync", "sync_wechat", "sync"),
    ("qq", "sync_qq", "qq_sync"),
    ("telegram", "sync_telegram", "telegram_sync"),
    ("analyze", "analyze", "analyze"),
    ("tag", "tag", "tag"),
    ("todo_remind", "todo_remind", "todo_remind"),
)


@router.get("/api/scheduler")
async def get_scheduler():
    cfg = load_config()
    s = cfg.scheduler
    state = await get_scheduler_state()

    out: dict = {
        "tag_max_contacts": s.tag_max_contacts,
        "todo_remind_min_urgency": s.todo_remind_min_urgency,
    }
    for prefix, job, stem in _JOBS:
        enabled = getattr(s, f"{prefix}_enabled")
        interval = getattr(s, f"{prefix}_interval_minutes")
        last = state.get(job)
        next_run = last + timedelta(minutes=interval) if (enabled and last and interval > 0) else None
        out[f"{prefix}_enabled"] = enabled
        out[f"{prefix}_interval_minutes"] = interval
        out[f"last_{stem}_at"] = last.isoformat() if last else None
        out[f"next_{stem}_at"] = next_run.isoformat() if next_run else None
    return out


@router.put("/api/scheduler")
async def update_scheduler(body: SchedulerUpdate):
    cfg = load_config()
    s = cfg.scheduler
    now = datetime.now(timezone.utc)

    # When a toggle flips OFF→ON, anchor the run stamp to "now" so the next run
    # is `now + interval` instead of firing immediately off a stale stamp.
    for prefix, job, _stem in _JOBS:
        enabled = getattr(body, f"{prefix}_enabled")
        interval = getattr(body, f"{prefix}_interval_minutes")
        if enabled is not None:
            if enabled and not getattr(s, f"{prefix}_enabled"):
                await mark_job_run(job, now)
            setattr(s, f"{prefix}_enabled", enabled)
        if interval is not None:
            setattr(s, f"{prefix}_interval_minutes", interval)

    if body.tag_max_contacts is not None:
        s.tag_max_contacts = body.tag_max_contacts
    if body.todo_remind_min_urgency is not None:
        s.todo_remind_min_urgency = body.todo_remind_min_urgency

    save_config(cfg)
    return await get_scheduler()
