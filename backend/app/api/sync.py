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
    """Trigger WeChat + QQ + Telegram sync sequentially in a single task.
    This avoids database-locked errors from concurrent writes to SQLite."""
    running_types = ("sync_all", "sync_wechat", "sync_qq", "sync_telegram")
    if any(is_task_running(t) for t in running_types):
        raise HTTPException(409, "同步任务正在运行")
    task_id = create_task("sync_all")
    spawn_cancellable(task_id, _run_sync_all(task_id, body.new_only))
    return {"tasks": [{"type": "sync_all", "task_id": task_id}]}


async def _run_sync_all(task_id: str, new_only: bool):
    """Run all platform syncs sequentially to avoid SQLite write contention."""
    from app.core.tasks import _tasks
    _tasks[task_id]["status"] = "running"
    _tasks[task_id]["progress"] = 5
    results: list[str] = []
    failures: list[str] = []
    successes = 0

    try:
        from app.services.backup import maybe_backup_before_sync
        await maybe_backup_before_sync()
    except Exception:
        pass

    # 1. WeChat
    _tasks[task_id]["message"] = "正在同步微信..."
    try:
        from app.services.sync.wechat import sync_new_messages, sync_sessions
        count = await (sync_new_messages() if new_only else sync_sessions())
        results.append(f"微信 +{count}")
        successes += 1
    except Exception as e:
        msg = f"微信失败: {e}"
        results.append(msg)
        failures.append(msg)

    _tasks[task_id]["progress"] = 35

    # 2. QQ
    _tasks[task_id]["message"] = "正在同步 QQ..."
    try:
        from app.services.sync.qq_qce import sync_all as qq_sync
        from app.core.tasks import make_progress as _mp
        r = await qq_sync(progress=_mp(task_id))
        results.append(f"QQ +{r['imported']}")
        successes += 1
    except Exception as e:
        msg = f"QQ失败: {e}"
        results.append(msg)
        failures.append(msg)

    _tasks[task_id]["progress"] = 70

    # 3. Telegram
    _tasks[task_id]["message"] = "正在同步 Telegram..."
    try:
        from app.services.sync.telegram_live import sync_all as tg_sync
        from app.core.tasks import make_progress as _mp2
        r = await tg_sync(progress=_mp2(task_id))
        results.append(f"Telegram +{r['imported']}")
        successes += 1
    except Exception as e:
        msg = f"Telegram失败: {e}"
        results.append(msg)
        failures.append(msg)

    if successes:
        try:
            from app.services.triggers import scan_for_matches
            await scan_for_matches()
        except Exception:
            pass

    _tasks[task_id].update(
        status="error" if failures else "done",
        progress=100,
        message=("全量同步部分失败: " if failures else "全量同步完成: ") + " | ".join(results),
    )


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
