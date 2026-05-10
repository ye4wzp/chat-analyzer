import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.core.tasks import _tasks, cancel_task

router = APIRouter()


@router.get("/api/tasks")
async def list_tasks():
    """All in-memory tasks. Used by the global task bar in the UI."""
    return [{"id": tid, **t} for tid, t in _tasks.items()]


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
    async def event_stream():
        while True:
            if task_id in _tasks:
                data = json.dumps(_tasks[task_id])
                yield f"data: {data}\n\n"
                if _tasks[task_id]["status"] in ("done", "error", "cancelled"):
                    break
            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
