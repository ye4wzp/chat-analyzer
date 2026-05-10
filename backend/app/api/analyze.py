import aiosqlite
from fastapi import APIRouter, HTTPException

from app.core import database
from app.core.runners import run_analyze
from app.core.tasks import _pending_results, create_task, spawn_cancellable
from app.models.analyze import AnalyzeRequest, ConfirmRequest

router = APIRouter()


@router.post("/api/analyze")
async def analyze(body: AnalyzeRequest = AnalyzeRequest()):
    task_id = create_task("analyze")
    spawn_cancellable(task_id, run_analyze(task_id, body))
    return {"task_id": task_id}


@router.get("/api/analyze/{task_id}/results")
async def get_analysis_results(task_id: str):
    if task_id not in _pending_results:
        raise HTTPException(404, "分析结果不存在或已过期")
    return _pending_results[task_id]


@router.post("/api/analyze/{task_id}/confirm")
async def confirm_analysis(task_id: str, body: ConfirmRequest):
    from app.services.knowledge import save_knowledge_items

    if task_id not in _pending_results:
        raise HTTPException(404, "分析结果不存在或已过期")

    results = _pending_results.pop(task_id)
    save_indices = set(body.ids)
    to_save = [r for i, r in enumerate(results) if i in save_indices]

    async with aiosqlite.connect(str(database.DB_PATH), timeout=30) as db:
        await save_knowledge_items(db, to_save)
        await db.commit()

    return {"saved": len(to_save), "skipped": len(results) - len(to_save)}
