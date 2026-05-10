import aiosqlite
from fastapi import APIRouter

from app.core import database

router = APIRouter()


@router.get("/api/chats")
async def get_chats():
    async with aiosqlite.connect(str(database.DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            """SELECT platform, chat_id, chat_name, chat_type,
                      COUNT(*) as msg_count,
                      MIN(timestamp) as earliest,
                      MAX(timestamp) as latest
               FROM messages
               GROUP BY platform, chat_id
               ORDER BY latest DESC"""
        )

    return [dict(r) for r in rows]
