import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.core.tasks import _tasks

router = APIRouter()


@router.get("/api/tasks")
async def list_tasks():
    """All in-memory tasks. Used by the global task bar in the UI."""
    return [{"id": tid, **t} for tid, t in _tasks.items()]


@router.get("/api/tasks/{task_id}/events")
async def task_events(task_id: str):
    async def event_stream():
        while True:
            if task_id in _tasks:
                data = json.dumps(_tasks[task_id])
                yield f"data: {data}\n\n"
                if _tasks[task_id]["status"] in ("done", "error"):
                    break
            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
