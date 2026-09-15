"""Background scheduler that fires sync/analyze tasks at user-configured intervals.

All timestamps are tz-aware UTC. Run state lives in the scheduler_state table
(config.json only holds the enabled/interval settings), so the loop never
rewrites config.json and can't clobber concurrent user edits.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from app.core import database
from app.core.config import load_config
from app.core.runners import run_analyze, run_qq_sync, run_report, run_sync, run_tag_batch, run_telegram_sync
from app.core.tasks import create_task, is_task_running, spawn_cancellable
from app.models.analyze import AnalyzeRequest

SYNC_TYPES = ("sync_all", "sync_wechat", "sync_qq", "sync_telegram")

_LOCAL_TZ = datetime.now().astimezone().tzinfo or timezone.utc


def parse_run_stamp(value: str | None) -> datetime | None:
    """Parse a stored run stamp to aware UTC. Naive values are legacy stamps
    written in local time by pre-scheduler_state versions."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_LOCAL_TZ)
    return dt.astimezone(timezone.utc)


async def get_scheduler_state() -> dict[str, datetime]:
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        rows = await db.execute_fetchall("SELECT job, last_run_at FROM scheduler_state")
    state = {}
    for job, last_run_at in rows:
        parsed = parse_run_stamp(last_run_at)
        if parsed:
            state[job] = parsed
    return state


async def mark_job_run(job: str, when: datetime | None = None) -> None:
    stamp = (when or datetime.now(timezone.utc)).isoformat()
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        await db.execute("PRAGMA busy_timeout=30000")
        await db.execute(
            """INSERT INTO scheduler_state (job, last_run_at, updated_at)
               VALUES (?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(job) DO UPDATE SET last_run_at=excluded.last_run_at, updated_at=CURRENT_TIMESTAMP""",
            (job, stamp),
        )
        await db.commit()


async def _seed_legacy_state() -> None:
    """One-time import of run stamps that older versions kept in config.json."""
    cfg = load_config()
    legacy = {
        "sync_wechat": cfg.scheduler.last_sync_at,
        "sync_qq": cfg.scheduler.last_qq_sync_at,
        "sync_telegram": cfg.scheduler.last_telegram_sync_at,
        "analyze": cfg.scheduler.last_analyze_at,
        "tag": cfg.scheduler.last_tag_at,
    }
    state = await get_scheduler_state()
    for job, raw in legacy.items():
        if job in state:
            continue
        parsed = parse_run_stamp(raw)
        if parsed:
            await mark_job_run(job, parsed)


async def _notify_open_todos(min_urgency: int) -> None:
    from app.services.triggers import notify_urgent_todos
    try:
        await notify_urgent_todos(min_urgency)
    except Exception as e:
        logging.warning("todo remind failed: %s", e)


async def scheduler_loop():
    try:
        await _seed_legacy_state()
    except Exception as e:
        logging.warning("scheduler legacy seed failed: %s", e)

    while True:
        await asyncio.sleep(30)
        try:
            cfg = load_config()
            s = cfg.scheduler
            now = datetime.now(timezone.utc)
            state = await get_scheduler_state()

            def due(job: str, enabled: bool, interval_min: int) -> bool:
                if not enabled or interval_min <= 0 or is_task_running(job):
                    return False
                last = state.get(job)
                return last is None or now >= last + timedelta(minutes=interval_min)

            # Syncs run one per tick, never concurrently with each other —
            # /api/sync/all exists for the same reason (SQLite write contention).
            sync_running = any(is_task_running(t) for t in SYNC_TYPES)
            sync_jobs = (
                ("sync_wechat", s.sync_enabled, s.sync_interval_minutes, lambda tid: run_sync(tid, True)),
                ("sync_qq", s.qq_enabled, s.qq_interval_minutes, run_qq_sync),
                ("sync_telegram", s.telegram_enabled, s.telegram_interval_minutes, run_telegram_sync),
            )
            for job, enabled, interval, make_coro in sync_jobs:
                if sync_running:
                    break
                if due(job, enabled, interval):
                    task_id = create_task(job)
                    spawn_cancellable(task_id, make_coro(task_id))
                    await mark_job_run(job, now)
                    sync_running = True

            # Analyze waits out syncs so it never reads a half-synced window.
            if not sync_running and due("analyze", s.analyze_enabled, s.analyze_interval_minutes):
                task_id = create_task("analyze")
                spawn_cancellable(task_id, run_analyze(task_id, AnalyzeRequest()))
                await mark_job_run("analyze", now)

            if due("tag", s.tag_enabled, s.tag_interval_minutes):
                task_id = create_task("tag")
                spawn_cancellable(task_id, run_tag_batch(task_id, False, True, 100, s.tag_max_contacts))
                await mark_job_run("tag", now)

            if due("todo_remind", s.todo_remind_enabled, s.todo_remind_interval_minutes):
                await mark_job_run("todo_remind", now)
                asyncio.create_task(_notify_open_todos(s.todo_remind_min_urgency))

            # Digest reports: fixed cadences; both share the "report" task type
            # so the two jobs (and manual runs) can't overlap.
            report_jobs = (
                ("report_weekly", s.report_weekly_enabled, 7 * 1440, "weekly"),
                ("report_monthly", s.report_monthly_enabled, 30 * 1440, "monthly"),
            )
            for job, enabled, interval, period in report_jobs:
                if not is_task_running("report") and due(job, enabled, interval):
                    task_id = create_task("report")
                    spawn_cancellable(task_id, run_report(task_id, period, notify=True))
                    await mark_job_run(job, now)
        except Exception as e:
            logging.warning("scheduler loop error: %s", e)
