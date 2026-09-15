import asyncio
import json

import aiosqlite
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.core import database
from app.core.tasks import _tasks, cancel_task, list_tasks as _list_tasks

router = APIRouter()

EVENTS_HEARTBEAT_TICKS = 25  # 1s poll interval → keepalive comment every ~25s


@router.get("/api/tasks")
async def list_tasks():
    """All in-memory tasks. Kept as a fallback for clients without SSE."""
    return _list_tasks()


@router.get("/api/events")
async def global_events():
    """One multiplexed SSE channel for the whole UI: task list + inbox unread
    count, pushed only when they change. Replaces the frontend's fixed-interval
    polling of /tasks and /inbox/unread_count."""
    async def snapshot(db: aiosqlite.Connection) -> str:
        tasks = _list_tasks()
        try:
            rows = await db.execute_fetchall("SELECT COUNT(*) FROM keyword_triggers WHERE read = 0")
            unread = int(rows[0][0]) if rows else 0
        except Exception:
            unread = 0
        return json.dumps({"tasks": tasks, "unread": unread}, ensure_ascii=False)

    async def event_stream():
        # One connection for the stream's lifetime — the old code reconnected
        # (and span a fresh thread) every second per client for a single COUNT.
        async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
            last_payload = None
            idle_ticks = 0
            while True:
                payload = await snapshot(db)
                if payload != last_payload:
                    yield f"data: {payload}\n\n"
                    last_payload = payload
                    idle_ticks = 0
                else:
                    idle_ticks += 1
                    if idle_ticks >= EVENTS_HEARTBEAT_TICKS:
                        yield ": ping\n\n"
                        idle_ticks = 0
                await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/api/tasks/{task_id}/cancel")
async def cancel(task_id: str):
    if task_id not in _tasks:
        raise HTTPException(404, "任务不存在")
    if _tasks[task_id]["status"] != "running":
        raise HTTPException(409, "任务未在运行")
    if not cancel_task(task_id):
        raise HTTPException(409, "任务已结束或无法取消")
    return {"ok": True}


@router.get("/api/tasks/{task_id}/events")
async def task_events(task_id: str):
    if task_id not in _tasks:
        raise HTTPException(404, "任务不存在")

    async def event_stream():
        while True:
            if task_id in _tasks:
                public = {k: v for k, v in _tasks[task_id].items() if not k.startswith("_")}
                yield f"data: {json.dumps(public)}\n\n"
                if _tasks[task_id]["status"] in ("done", "error", "cancelled"):
                    break
            else:
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
