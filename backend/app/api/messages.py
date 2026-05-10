from typing import Optional

import aiosqlite
from fastapi import APIRouter

from app.core import database

router = APIRouter()


@router.get("/api/messages")
async def get_messages(
    platform: Optional[str] = None,
    chat: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    category: Optional[str] = None,
    urgency_min: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
):
    conditions: list[str] = []
    params: list = []

    if platform:
        conditions.append("m.platform = ?")
        params.append(platform)
    if chat:
        conditions.append("m.chat_name LIKE ?")
        params.append(f"%{chat}%")
    if since:
        conditions.append("m.timestamp >= ?")
        params.append(since)
    if until:
        conditions.append("m.timestamp <= ?")
        params.append(until)
    if category:
        conditions.append("a.category = ?")
        params.append(category)
    if urgency_min is not None:
        conditions.append("a.urgency >= ?")
        params.append(urgency_min)

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    async with aiosqlite.connect(str(database.DB_PATH), timeout=30) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            f"""SELECT m.*, a.category, a.urgency, a.summary
                FROM messages m
                LEFT JOIN analysis_results a ON a.message_id = m.id
                {where}
                ORDER BY m.timestamp DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        )

    return [dict(r) for r in rows]
