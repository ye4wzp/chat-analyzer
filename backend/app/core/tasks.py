"""In-memory task tracking shared across API routers and scheduler."""
import uuid

# task_id -> {type, status, progress, message, ...}
_tasks: dict[str, dict] = {}

# task_id -> analysis results pending review
_pending_results: dict[str, list[dict]] = {}


def create_task(task_type: str) -> str:
    task_id = uuid.uuid4().hex[:8]
    _tasks[task_id] = {"type": task_type, "status": "pending", "progress": 0, "message": ""}
    return task_id


def is_task_running(task_type: str) -> bool:
    return any(t["type"] == task_type and t["status"] == "running" for t in _tasks.values())


def make_progress(task_id: str):
    def cb(pct: int, msg: str):
        _tasks[task_id].update(progress=pct, message=msg)
    return cb
