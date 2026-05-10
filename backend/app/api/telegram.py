import asyncio

from fastapi import APIRouter, HTTPException

from app.core.runners import run_telegram_sync
from app.core.tasks import create_task, is_task_running
from app.models.telegram import TelegramLoginConfirm, TelegramLoginStart

router = APIRouter()


@router.post("/api/telegram/login/start")
async def telegram_login_start(body: TelegramLoginStart):
    from app.services.sync.telegram_live import start_login
    try:
        return await start_login(body.api_id, body.api_hash, body.phone)
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/api/telegram/login/confirm")
async def telegram_login_confirm(body: TelegramLoginConfirm):
    from app.services.sync.telegram_live import confirm_code
    try:
        return await confirm_code(body.phone, body.code, body.password)
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/api/telegram/status")
async def telegram_status():
    from app.services.sync.telegram_live import status
    try:
        return await status()
    except Exception as e:
        return {"logged_in": False, "error": str(e)}


@router.post("/api/telegram/logout")
async def telegram_logout():
    from app.services.sync.telegram_live import logout
    await logout()
    return {"ok": True}


@router.post("/api/sync/telegram")
async def sync_telegram():
    if is_task_running("sync_telegram"):
        raise HTTPException(409, "Telegram 同步正在运行")
    task_id = create_task("sync_telegram")
    asyncio.create_task(run_telegram_sync(task_id))
    return {"task_id": task_id}
