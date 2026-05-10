import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.core.runners import run_import, run_sync
from app.core.tasks import create_task
from app.models.sync import ImportRequest, SyncRequest

router = APIRouter()


@router.post("/api/sync/wechat")
async def sync_wechat(body: SyncRequest = SyncRequest()):
    task_id = create_task("sync_wechat")
    asyncio.create_task(run_sync(task_id, body.new_only))
    return {"task_id": task_id}


@router.post("/api/import/qq")
async def import_qq(body: ImportRequest):
    if not Path(body.path).exists():
        raise HTTPException(400, f"File not found: {body.path}")
    task_id = create_task("import_qq")
    asyncio.create_task(run_import(task_id, "qq", body.path))
    return {"task_id": task_id}


@router.post("/api/import/telegram")
async def import_telegram(body: ImportRequest):
    if not Path(body.path).exists():
        raise HTTPException(400, f"File not found: {body.path}")
    task_id = create_task("import_telegram")
    asyncio.create_task(run_import(task_id, "telegram", body.path))
    return {"task_id": task_id}
