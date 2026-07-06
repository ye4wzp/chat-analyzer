import json

import aiosqlite
from fastapi import APIRouter, HTTPException

from app.core import database
from app.core.runners import run_analyze, spawn_embed_if_needed
from app.core.tasks import create_task, is_task_running, spawn_cancellable
from app.models.analyze import AnalyzeRequest, ConfirmRequest

router = APIRouter()


@router.post("/api/analyze")
async def analyze(body: AnalyzeRequest = AnalyzeRequest()):
    if is_task_running("analyze"):
        raise HTTPException(409, "分析任务已在运行")
    task_id = create_task("analyze")
    spawn_cancellable(task_id, run_analyze(task_id, body))
    return {"task_id": task_id}


@router.get("/api/analyze/pending")
async def pending_batches():
    """Batches awaiting review — includes scheduled runs nobody has looked at."""
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            """SELECT b.task_id, b.summary, b.created_at, COUNT(p.id) AS item_count
               FROM analysis_batches b
               JOIN pending_items p ON p.task_id = b.task_id AND p.status = 'pending'
               WHERE b.status = 'pending'
               GROUP BY b.task_id
               ORDER BY b.created_at DESC"""
        )
    return [dict(r) for r in rows]


def _item_row_to_dict(row) -> dict:
    d = dict(row)
    try:
        d["tags"] = json.loads(d.get("tags") or "[]")
    except json.JSONDecodeError:
        d["tags"] = []
    return d


async def _load_pending(db, task_id: str) -> list[dict]:
    rows = await db.execute_fetchall(
        """SELECT id, title, content, category, urgency, tags, source_chat, source_message_ids, batch_id
           FROM pending_items WHERE task_id = ? AND status = 'pending' ORDER BY id""",
        (task_id,),
    )
    return [_item_row_to_dict(r) for r in rows]


@router.get("/api/analyze/{task_id}/results")
async def get_analysis_results(task_id: str):
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        db.row_factory = aiosqlite.Row
        batch = await db.execute_fetchall(
            "SELECT task_id FROM analysis_batches WHERE task_id = ?", (task_id,)
        )
        if not batch:
            raise HTTPException(404, "分析结果不存在或已过期")
        return await _load_pending(db, task_id)


@router.post("/api/analyze/{task_id}/confirm")
async def confirm_analysis(task_id: str, body: ConfirmRequest):
    from app.services.embedder import find_semantic_duplicates
    from app.services.knowledge import save_knowledge_items

    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        db.row_factory = aiosqlite.Row
        items = await _load_pending(db, task_id)
    if not items:
        raise HTTPException(404, "分析结果不存在或已过期")

    selected_ids = set(body.ids)
    selected = [it for it in items if it["id"] in selected_ids]
    duplicate_idx = await find_semantic_duplicates(selected, task_id=task_id)
    to_save = [it for i, it in enumerate(selected) if i not in duplicate_idx]

    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        await db.execute("PRAGMA busy_timeout=30000")
        saved = await save_knowledge_items(db, to_save)
        if selected:
            placeholders = ",".join("?" * len(selected))
            await db.execute(
                f"UPDATE pending_items SET status='saved' WHERE id IN ({placeholders})",
                [it["id"] for it in selected],
            )
        # Unselected rows become 'rejected' — they feed the extraction prompt's
        # negative examples so the model stops proposing the same kind of item.
        await db.execute(
            "UPDATE pending_items SET status='rejected' WHERE task_id=? AND status='pending'",
            (task_id,),
        )
        await db.execute(
            "UPDATE analysis_batches SET status='confirmed' WHERE task_id=?", (task_id,)
        )
        await db.commit()

    if saved:
        spawn_embed_if_needed()
    return {
        "saved": saved,
        "skipped": len(items) - saved,
        "duplicates": len(selected) - len(to_save) + (len(to_save) - saved),
    }


@router.post("/api/analyze/{task_id}/discard")
async def discard_analysis(task_id: str):
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        cur = await db.execute(
            "UPDATE analysis_batches SET status='discarded' WHERE task_id=? AND status='pending'",
            (task_id,),
        )
        await db.execute(
            "UPDATE pending_items SET status='rejected' WHERE task_id=? AND status='pending'",
            (task_id,),
        )
        await db.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "分析结果不存在或已过期")
    return {"ok": True}
