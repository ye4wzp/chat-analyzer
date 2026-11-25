from pathlib import Path

import aiosqlite
from fastapi import APIRouter, HTTPException

from app.core import database
from app.core.runners import run_import, run_qq_sync, run_sync, run_telegram_sync
from app.core.tasks import create_task, is_task_running, spawn_cancellable
from app.models.sync import ImportRequest, SyncRequest

router = APIRouter()


@router.get("/api/sync/health")
async def sync_health(stale_days: int = 7):
    """Per-platform sync freshness plus the chats that haven't synced in a
    while. Powers the sidebar's real data status (previously hardcoded)."""
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        db.row_factory = aiosqlite.Row
        platforms = await db.execute_fetchall(
            """SELECT s.platform,
                      COUNT(*) AS chats,
                      STRFTIME('%Y-%m-%dT%H:%M:%SZ', MAX(s.updated_at)) AS last_sync,
                      (SELECT MAX(timestamp) FROM messages m WHERE m.platform = s.platform) AS last_message_ts
               FROM sync_state s GROUP BY s.platform"""
        )
        stale = await db.execute_fetchall(
            """SELECT platform, chat_id,
                      STRFTIME('%Y-%m-%dT%H:%M:%SZ', updated_at) AS last_sync
               FROM sync_state
               WHERE updated_at < datetime('now', ?)
               ORDER BY updated_at LIMIT 20""",
            (f"-{int(stale_days)} days",),
        )
    from app.core.tasks import list_tasks
    from app.services.sync.result import result_status
    latest = {}
    for task in list_tasks():
        for platform in task.get("request", {}).get("platforms", []):
            r = task.get("results", {}).get(platform)
            status = result_status(r) if r and not task.get("resume_all") and task["status"] in ("done", "partial", "error") else task["status"]
            latest[platform] = {"platform": platform, "status": status, "task_id": task["id"]}
    return {"platforms": [dict(r) for r in platforms], "stale": [dict(r) for r in stale], "runs": list(latest.values())}


SYNC_TASK_TYPES = ("sync_all", "sync_wechat", "sync_qq", "sync_telegram")


@router.post("/api/sync/wechat")
async def sync_wechat(body: SyncRequest = SyncRequest()):
    # Syncs never run concurrently (SQLite write contention) — same guard as
    # /sync/all, so a double-click or an overlapping sync 409s instead of
    # spawning a second wx-cli writer.
    if any(is_task_running(t) for t in SYNC_TASK_TYPES):
        raise HTTPException(409, "同步任务正在运行")
    task_id = create_task("sync_wechat")
    spawn_cancellable(task_id, run_sync(task_id, body.new_only, mode=body.mode))
    return {"task_id": task_id}


@router.post("/api/sync/all")
async def sync_all(body: SyncRequest = SyncRequest()):
    """Trigger WeChat + QQ + Telegram sync sequentially in a single task.
    This avoids database-locked errors from concurrent writes to SQLite."""
    if any(is_task_running(t) for t in SYNC_TASK_TYPES):
        raise HTTPException(409, "同步任务正在运行")
    task_id = create_task("sync_all")
    spawn_cancellable(task_id, _run_sync_all(task_id, body.new_only, mode=body.mode))
    return {"tasks": [{"type": "sync_all", "task_id": task_id}]}


async def _run_sync_all(task_id: str, new_only: bool, *, mode=None):
    from app.core.runners import run_platforms_sync
    await run_platforms_sync(task_id, ["wechat", "qq", "telegram"], new_only=new_only, mode=mode)


@router.post("/api/import/qq")
async def import_qq(body: ImportRequest):
    if not Path(body.path).exists():
        raise HTTPException(400, f"File not found: {body.path}")
    task_id = create_task("import_qq")
    spawn_cancellable(task_id, run_import(task_id, "qq", body.path))
    return {"task_id": task_id}


@router.post("/api/import/telegram")
async def import_telegram(body: ImportRequest):
    if not Path(body.path).exists():
        raise HTTPException(400, f"File not found: {body.path}")
    task_id = create_task("import_telegram")
    spawn_cancellable(task_id, run_import(task_id, "telegram", body.path))
    return {"task_id": task_id}
