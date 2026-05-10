import aiosqlite
from fastapi import APIRouter

from app.core import database

router = APIRouter()


@router.get("/api/dashboard")
async def dashboard():
    async with aiosqlite.connect(str(database.DB_PATH)) as db:
        db.row_factory = aiosqlite.Row

        total = await db.execute_fetchall("SELECT COUNT(*) as c FROM messages")
        total_messages = total[0]["c"] if total else 0

        chats = await db.execute_fetchall("SELECT COUNT(DISTINCT chat_id) as c FROM messages")
        total_chats = chats[0]["c"] if chats else 0

        knowledge_count = await db.execute_fetchall("SELECT COUNT(*) as c FROM knowledge_items")
        total_knowledge = knowledge_count[0]["c"] if knowledge_count else 0

        platforms = await db.execute_fetchall(
            "SELECT platform, COUNT(*) as count FROM messages GROUP BY platform"
        )

        recent = await db.execute_fetchall(
            "SELECT * FROM knowledge_items ORDER BY created_at DESC LIMIT 10"
        )

        daily = await db.execute_fetchall(
            """SELECT substr(timestamp, 1, 10) as date, platform, COUNT(*) as count
               FROM messages
               WHERE timestamp >= datetime('now', '-30 days')
               GROUP BY date, platform
               ORDER BY date"""
        )
        recent_active_row = await db.execute_fetchall(
            "SELECT COUNT(*) as c FROM messages WHERE timestamp >= datetime('now', '-7 days')"
        )

    return {
        "total_messages": total_messages,
        "total_chats": total_chats,
        "total_knowledge": total_knowledge,
        "platforms": [dict(r) for r in platforms],
        "recent_knowledge": [dict(r) for r in recent],
        "daily_counts": [dict(r) for r in daily],
        "recent_active": recent_active_row[0]["c"] if recent_active_row else 0,
    }
