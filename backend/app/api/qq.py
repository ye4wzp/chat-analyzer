import httpx
from fastapi import APIRouter, HTTPException

from app.core.config import load_config
from app.core.runners import run_qq_install, run_qq_sync
from app.core.tasks import create_task, is_task_running, spawn_cancellable

router = APIRouter()


@router.post("/api/qq/test")
async def qq_test():
    from app.services.sync.qq_qce import QCEError, test_connection
    cfg = load_config().qq
    if not cfg.token:
        raise HTTPException(400, "请先填写 QCE token")
    try:
        return await test_connection(cfg)
    except QCEError as e:
        raise HTTPException(400, str(e))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"无法连接 QCE: {e}")


@router.post("/api/sync/qq")
async def sync_qq():
    if is_task_running("sync_qq"):
        raise HTTPException(409, "QQ 同步正在运行")
    task_id = create_task("sync_qq")
    spawn_cancellable(task_id, run_qq_sync(task_id))
    return {"task_id": task_id}


@router.get("/api/qq/launcher/status")
async def qq_launcher_status():
    from app.services.sync.qq_launcher import status
    return await status()


@router.post("/api/qq/launcher/install")
async def qq_launcher_install(force: bool = False):
    if is_task_running("qq_install"):
        raise HTTPException(409, "安装正在进行中")
    task_id = create_task("qq_install")
    spawn_cancellable(task_id, run_qq_install(task_id, force))
    return {"task_id": task_id}


@router.post("/api/qq/launcher/start")
async def qq_launcher_start():
    from app.services.sync.qq_launcher import LauncherError, start
    try:
        return await start()
    except LauncherError as e:
        raise HTTPException(400, str(e))


@router.post("/api/qq/launcher/stop")
async def qq_launcher_stop():
    from app.services.sync.qq_launcher import LauncherError, stop
    try:
        return await stop()
    except LauncherError as e:
        raise HTTPException(400, str(e))


@router.get("/api/qq/launcher/logs")
async def qq_launcher_logs(tail: int = 200):
    from app.services.sync.qq_launcher import logs
    return {"logs": await logs(tail=tail)}
