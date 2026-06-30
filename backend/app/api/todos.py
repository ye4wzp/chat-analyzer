import json

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core import database

router = APIRouter()


class TodoUpdate(BaseModel):
    done: bool


def _action_items(raw: object) -> list[str]:
    """key_entities stores {knowledge_id, tags}; action_items holds the todo text.
    Return a clean list of strings from whichever shape we get."""
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return [raw]
    else:
        parsed = raw
    if isinstance(parsed, list):
        return [str(x) for x in parsed if x]
    return [str(parsed)] if parsed else []


@router.get("/api/todos")
async def list_todos(include_done: bool = False, limit: int = 200):
    """Open (or all) todo items extracted by the analyzer, newest first.
    Joins back to messages for chat/time context."""
    done_clause = "" if include_done else " AND a.done = 0"
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            f"""SELECT a.id, a.urgency, a.summary, a.action_items, a.done, a.analyzed_at,
                       m.platform, m.chat_id, m.chat_name, m.content, m.timestamp
                FROM analysis_results a
                JOIN messages m ON m.id = a.message_id
                WHERE a.category = 'todo'{done_clause}
                ORDER BY a.done ASC, a.urgency DESC, m.timestamp DESC
                LIMIT ?""",
            (limit,),
        )
    out = []
    for r in rows:
        d = dict(r)
        d["action_items"] = _action_items(d.get("action_items"))
        d["done"] = bool(d["done"])
        out.append(d)
    return out


@router.get("/api/todos/stats")
async def todo_stats():
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            """SELECT COUNT(*) AS total,
                      COALESCE(SUM(CASE WHEN done = 0 THEN 1 END), 0) AS open,
                      COALESCE(SUM(CASE WHEN done = 0 AND urgency >= 4 THEN 1 END), 0) AS urgent
               FROM analysis_results WHERE category = 'todo'"""
        )
    r = rows[0]
    return {"total": int(r["total"]), "open": int(r["open"]), "urgent": int(r["urgent"])}


@router.patch("/api/todos/{todo_id}")
async def update_todo(todo_id: int, body: TodoUpdate):
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        cursor = await db.execute(
            "UPDATE analysis_results SET done = ? WHERE id = ? AND category = 'todo'",
            (1 if body.done else 0, todo_id),
        )
        await db.commit()
    if cursor.rowcount == 0:
        raise HTTPException(404, "待办不存在")
    return {"ok": True, "done": body.done}
