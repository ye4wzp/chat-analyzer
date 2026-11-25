import asyncio
import json

import aiosqlite
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.core import database
from app.core.tasks import _tasks, _TERMINAL, cancel_task, list_tasks as _list_tasks

router = APIRouter()


@router.post("/api/tasks/{task_id}/retry")
async def retry_sync(task_id: str):
    from app.api.sync import SYNC_TASK_TYPES
    from app.core.tasks import is_task_running, spawn_cancellable
    from app.services.sync.result import resume_scope, WECHAT_FOLD_GROUP
    from app.core.runners import run_platforms_sync
    old = _tasks.get(task_id)
    if not old or old.get("type") not in SYNC_TASK_TYPES or not old.get("request"):
        raise HTTPException(404, "没有可恢复的同步记录")
    if old.get("status") not in _TERMINAL or any(is_task_running(t) for t in SYNC_TASK_TYPES):
        raise HTTPException(409, "同步任务正在运行")
    req = old["request"]
    selections = {}
    skips = {}
    live_only = set()
    interrupted = old["status"] in ("interrupted", "cancelled") or old.get("resume_all", False)
    for platform in req["platforms"]:
        result = old.get("results", {}).get(platform, {})
        run, selected, done, cached_live = resume_scope(platform, result, req, interrupted)
        if run:
            selections[platform] = selected
            skips[platform] = done
            if platform == "wechat":
                live_only = cached_live
    if old["status"] == "done":
        raise HTTPException(409, "没有失败或待补会话")
    wx = old.get("results", {}).get("wechat", {})
    if any(d["chat_id"] == WECHAT_FOLD_GROUP for d in wx.get("details", [])):
        wx["details"] = [d for d in wx["details"] if d["chat_id"] != WECHAT_FOLD_GROUP]
        wx["targets"] = max(0, wx.get("targets", 1) - 1)
        wx["excluded"] = wx.get("excluded", 0) + 1
        if "target_ids" in wx:
            wx["target_ids"] = [cid for cid in wx["target_ids"] if cid != WECHAT_FOLD_GROUP]
    # Continue the same durable record so successive interruptions keep the full scope.
    old.update(status="pending", message="准备继续未完成会话", progress=0, resume_all=False)
    spawn_cancellable(task_id, run_platforms_sync(task_id, list(selections), new_only=req.get("new_only", False),
                      mode=req.get("mode"), selections=selections, skips=skips, live_only=live_only))
    return {"task_id": task_id}


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
                if _tasks[task_id]["status"] in _TERMINAL:
                    break
            else:
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
