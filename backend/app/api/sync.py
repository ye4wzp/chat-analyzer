from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.core.runners import run_import, run_qq_sync, run_sync, run_telegram_sync
from app.core.tasks import create_task, is_task_running, spawn_cancellable
from app.models.sync import ImportRequest, SyncRequest

router = APIRouter()


@router.post("/api/sync/wechat")
async def sync_wechat(body: SyncRequest = SyncRequest()):
    task_id = create_task("sync_wechat")
    spawn_cancellable(task_id, run_sync(task_id, body.new_only))
    return {"task_id": task_id}


@router.post("/api/sync/all")
async def sync_all(body: SyncRequest = SyncRequest()):
    """Trigger WeChat + QQ + Telegram sync in parallel. Returns a list of
    task ids; the global task bar polls /api/tasks for progress."""
    started: list[dict] = []

    if not is_task_running("sync_wechat"):
        tid = create_task("sync_wechat")
        spawn_cancellable(tid, run_sync(tid, body.new_only))
        started.append({"type": "sync_wechat", "task_id": tid})

    if not is_task_running("sync_qq"):
        tid = create_task("sync_qq")
        spawn_cancellable(tid, run_qq_sync(tid))
        started.append({"type": "sync_qq", "task_id": tid})

    if not is_task_running("sync_telegram"):
        tid = create_task("sync_telegram")
        spawn_cancellable(tid, run_telegram_sync(tid))
        started.append({"type": "sync_telegram", "task_id": tid})

    return {"tasks": started}


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
