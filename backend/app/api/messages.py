from typing import Optional

import aiosqlite
from fastapi import APIRouter

from app.core import database
from app.core.time_utils import add_time_filters

router = APIRouter()


@router.get("/api/messages")
async def get_messages(
    platform: Optional[str] = None,
    chat_id: Optional[str] = None,
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
    if chat_id:
        conditions.append("m.chat_id = ?")
        params.append(chat_id)
    elif chat:
        conditions.append("m.chat_name LIKE ?")
        params.append(f"%{chat}%")
    add_time_filters(conditions, params, "m.timestamp", since, until)
    if category:
        conditions.append("a.category = ?")
        params.append(category)
    if urgency_min is not None:
        conditions.append("a.urgency >= ?")
        params.append(urgency_min)

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
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
