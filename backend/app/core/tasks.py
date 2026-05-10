"""In-memory task tracking shared across API routers and scheduler."""
import asyncio
import uuid

# task_id -> {type, status, progress, message, ...}
_tasks: dict[str, dict] = {}

# task_id -> analysis results pending review
_pending_results: dict[str, list[dict]] = {}

# task_id -> asyncio.Task handle, used by /tasks/{id}/cancel
_task_handles: dict[str, asyncio.Task] = {}


def create_task(task_type: str) -> str:
    task_id = uuid.uuid4().hex[:8]
    _tasks[task_id] = {"type": task_type, "status": "pending", "progress": 0, "message": ""}
    return task_id


def register_handle(task_id: str, handle: asyncio.Task) -> None:
    """Record the asyncio.Task so /tasks/{id}/cancel can interrupt it."""
    _task_handles[task_id] = handle
    handle.add_done_callback(lambda _t: _task_handles.pop(task_id, None))


def cancel_task(task_id: str) -> bool:
    handle = _task_handles.get(task_id)
    if handle is None or handle.done():
        return False
    return handle.cancel()


def spawn_cancellable(task_id: str, coro) -> asyncio.Task:
    """Run `coro` in a fresh asyncio.Task and register its handle so the task
    can later be cancelled via cancel_task(). Catches CancelledError so the
    task list reflects the cancellation instead of being stuck in 'running'."""
    async def runner() -> None:
        try:
            await coro
        except asyncio.CancelledError:
            _tasks[task_id].update(status="cancelled", message="已取消", progress=0)
            raise

    handle = asyncio.create_task(runner())
    register_handle(task_id, handle)
    return handle


def is_task_running(task_type: str) -> bool:
    return any(t["type"] == task_type and t["status"] == "running" for t in _tasks.values())


def make_progress(task_id: str):
    def cb(pct: int, msg: str):
        _tasks[task_id].update(progress=pct, message=msg)
    return cb
