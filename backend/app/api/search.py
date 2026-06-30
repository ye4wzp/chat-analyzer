from typing import Optional

import aiosqlite
from fastapi import APIRouter

from app.core import database
from app.core.time_utils import add_time_filters

router = APIRouter()


@router.get("/api/search")
async def search(
    keyword: str,
    platform: Optional[str] = None,
    category: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    conditions = ["m.content LIKE ?"]
    params: list = [f"%{keyword}%"]

    if platform:
        conditions.append("m.platform = ?")
        params.append(platform)
    if category:
        conditions.append("a.category = ?")
        params.append(category)
    add_time_filters(conditions, params, "m.timestamp", since, until)

    where = " AND ".join(conditions)

    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            f"""SELECT m.*, a.category, a.urgency, a.summary
                FROM messages m
                LEFT JOIN analysis_results a ON a.message_id = m.id
                WHERE {where}
                ORDER BY m.timestamp DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        )

    return [dict(r) for r in rows]
