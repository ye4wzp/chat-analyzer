"""Task progress plus a bounded durable journal for resumable sync tasks."""
import asyncio
import time
import uuid
import json
import os
import sqlite3
import logging
from contextlib import closing

# task_id -> {type, status, progress, message, _finished_at?, ...}
_tasks: dict[str, dict] = {}

# task_id -> asyncio.Task handle, used by /tasks/{id}/cancel
_task_handles: dict[str, asyncio.Task] = {}

# Keep finished tasks visible for 1h then drop, capped at 200 total entries.
TASK_TTL_SECONDS = 3600
TASK_MAX_ENTRIES = 200

_TERMINAL = {"done", "error", "cancelled", "partial", "interrupted"}


def _store():
    # Separate small journal: progress writes must not compete with message ingestion.
    from app.core import database
    path = database.DB_PATH.with_name("sync-tasks.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)
    db = sqlite3.connect(path, timeout=5)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated REAL NOT NULL)")
    except sqlite3.Error:
        db.close()
        raise
    return db


class TaskState(dict):
    def __init__(self, task_id, values):
        super().__init__(values)
        self.task_id = task_id
        self.last_saved = 0.0

    def save(self, force=False):
        if not self.get("type", "").startswith("sync_"):
            return
        now = time.time()
        if not force and now - self.last_saved < 1:
            return
        try:
            with closing(_store()) as db, db:
                db.execute("INSERT OR REPLACE INTO tasks VALUES (?,?,?)",
                           (self.task_id, json.dumps(self, ensure_ascii=False), now))
                db.execute("DELETE FROM tasks WHERE id IN (SELECT id FROM tasks ORDER BY updated DESC LIMIT -1 OFFSET 200)")
            self.last_saved = now
        except (OSError, sqlite3.Error):
            dict.__setitem__(self, "journal_error", True)
            logging.exception("Unable to persist sync task")

    def update(self, *args, **kwargs):
        super().update(*args, **kwargs)
        self.save(force="status" in kwargs or "results" in kwargs or "request" in kwargs)

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.save(force=key in ("status", "results", "request"))


def restore_tasks():
    from app.core import database
    if not database.DB_PATH.with_name("sync-tasks.db").exists():
        return
    try:
        with closing(_store()) as db:
            rows = db.execute("SELECT id,payload FROM tasks ORDER BY updated").fetchall()
    except (OSError, sqlite3.Error):
        logging.exception("Unable to restore sync journal; continuing without old tasks")
        return
    for tid, payload in rows:
        try:
            values = json.loads(payload)
            if not isinstance(values, dict) or not isinstance(values.get("type"), str) or not values["type"].startswith("sync_"):
                raise ValueError("InvalidTaskPayload")
            state = TaskState(tid, values)
        except (ValueError, TypeError):
            logging.warning("Skipping invalid sync task payload: %s", tid)
            continue
        if state.get("status") not in _TERMINAL:
            state.update(status="interrupted", message="上次同步因程序退出中断，可继续同步", progress=0)
        _tasks[tid] = state


def _prune() -> None:
    now = time.time()
    drop: list[str] = []
    for tid, t in _tasks.items():
        if t.get("status") in _TERMINAL:
            fin = t.get("_finished_at")
            if fin is None:
                t["_finished_at"] = now  # mark first time we see it terminal
            elif now - fin > TASK_TTL_SECONDS and not t.get("type", "").startswith("sync_"):
                drop.append(tid)
    for tid in drop:
        _tasks.pop(tid, None)

    # Hard cap: if still over MAX, drop oldest finished first.
    if len(_tasks) > TASK_MAX_ENTRIES:
        finished = sorted(
            (tid for tid, t in _tasks.items() if t.get("status") in _TERMINAL),
            key=lambda tid: _tasks[tid].get("_finished_at") or 0,
        )
        for tid in finished[: len(_tasks) - TASK_MAX_ENTRIES]:
            _tasks.pop(tid, None)


def list_tasks() -> list[dict]:
    """Snapshot the task dict, pruning stale entries first."""
    _prune()
    return [{"id": tid, **{k: v for k, v in t.items() if not k.startswith("_")}}
            for tid, t in _tasks.items()]


def create_task(task_type: str) -> str:
    _prune()
    task_id = uuid.uuid4().hex[:8]
    _tasks[task_id] = TaskState(task_id, {"type": task_type, "status": "pending", "progress": 0, "message": ""})
    _tasks[task_id].save(force=True)
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
        except Exception as exc:
            from app.services.sync.result import error_reason
            logging.error("Background task %s failed: %s", task_id, error_reason(exc))
            _tasks[task_id].update(status="error", message=f"任务异常（{error_reason(exc)}），可重试", progress=0, resume_all=True)

    handle = asyncio.create_task(runner())
    register_handle(task_id, handle)
    return handle


def is_task_running(task_type: str) -> bool:
    return any(t["type"] == task_type and t["status"] not in _TERMINAL for t in _tasks.values())


def make_progress(task_id: str):
    def cb(pct: int, msg: str):
        _tasks[task_id].update(progress=pct, message=msg)
    return cb
