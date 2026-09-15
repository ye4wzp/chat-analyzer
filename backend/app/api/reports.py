import aiosqlite
from fastapi import APIRouter, HTTPException

from app.core import database
from app.core.runners import run_report
from app.core.tasks import create_task, is_task_running, spawn_cancellable
from app.models.reports import ReportGenerateRequest
from app.services.digest import PERIODS

router = APIRouter()


@router.post("/api/reports/generate")
async def generate_report(body: ReportGenerateRequest = ReportGenerateRequest()):
    if body.period not in PERIODS:
        raise HTTPException(400, f"未知报告周期: {body.period}")
    if is_task_running("report"):
        raise HTTPException(409, "报告生成任务已在运行")
    task_id = create_task("report")
    spawn_cancellable(task_id, run_report(task_id, body.period, year=body.year))
    return {"task_id": task_id}


@router.get("/api/reports")
async def list_reports(limit: int = 50):
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            """SELECT id, period, period_start, period_end, created_at
               FROM reports ORDER BY id DESC LIMIT ?""",
            (min(limit, 200),),
        )
    return [dict(r) for r in rows]


@router.get("/api/reports/{report_id}")
async def get_report(report_id: int):
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall("SELECT * FROM reports WHERE id=?", (report_id,))
    if not rows:
        raise HTTPException(404, "报告不存在")
    return dict(rows[0])


@router.delete("/api/reports/{report_id}")
async def delete_report(report_id: int):
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        cur = await db.execute("DELETE FROM reports WHERE id=?", (report_id,))
        await db.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "报告不存在")
    return {"ok": True}
