import aiosqlite
from fastapi import APIRouter

from app.core import database

router = APIRouter()


async def _stats_ready(db: aiosqlite.Connection) -> bool:
    rows = await db.execute_fetchall("SELECT value FROM stats_state WHERE key = 'built_at'")
    return bool(rows)


@router.get("/api/dashboard")
async def dashboard():
    """Home KPIs. Message counts come from the stats rollups when built (the
    raw COUNT / COUNT(DISTINCT) pair took ~3s at 1.8M rows); knowledge data is
    small and always read live."""
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        db.row_factory = aiosqlite.Row

        if await _stats_ready(db):
            tot = await db.execute_fetchall(
                "SELECT COALESCE(SUM(n), 0) AS msgs, COUNT(DISTINCT platform || '|' || chat_id) AS chats FROM stats_chats"
            )
            total_messages = int(tot[0]["msgs"]) if tot else 0
            total_chats = int(tot[0]["chats"]) if tot else 0
            platforms = await db.execute_fetchall(
                "SELECT platform, SUM(n) AS count FROM stats_chats GROUP BY platform"
            )
            daily = await db.execute_fetchall(
                """SELECT day AS date, platform, SUM(n) AS count FROM stats_daily
                   WHERE day >= date('now', 'localtime', '-30 days')
                   GROUP BY day, platform ORDER BY day"""
            )
            recent_active_row = await db.execute_fetchall(
                "SELECT COALESCE(SUM(n), 0) AS c FROM stats_daily WHERE day >= date('now', 'localtime', '-7 days')"
            )
        else:
            total = await db.execute_fetchall("SELECT COUNT(*) as c FROM messages")
            total_messages = total[0]["c"] if total else 0
            chats = await db.execute_fetchall("SELECT COUNT(DISTINCT chat_id) as c FROM messages")
            total_chats = chats[0]["c"] if chats else 0
            platforms = await db.execute_fetchall(
                "SELECT platform, COUNT(*) as count FROM messages GROUP BY platform"
            )
            # Message timestamps are local-naive; compare against local now, not
            # UTC, or the 7/30-day boundaries drift by the tz offset.
            daily = await db.execute_fetchall(
                """SELECT substr(timestamp, 1, 10) as date, platform, COUNT(*) as count
                   FROM messages
                   WHERE timestamp >= datetime('now', 'localtime', '-30 days')
                   GROUP BY date, platform
                   ORDER BY date"""
            )
            recent_active_row = await db.execute_fetchall(
                "SELECT COUNT(*) as c FROM messages WHERE timestamp >= datetime('now', 'localtime', '-7 days')"
            )

        knowledge_count = await db.execute_fetchall("SELECT COUNT(*) as c FROM knowledge_items")
        total_knowledge = knowledge_count[0]["c"] if knowledge_count else 0

        recent = await db.execute_fetchall(
            """SELECT id, title, content, source_chat, source_message_ids,
                      tags, extended_content, batch_id, created_at
               FROM knowledge_items ORDER BY created_at DESC LIMIT 10"""
        )

    return {
        "total_messages": total_messages,
        "total_chats": total_chats,
        "total_knowledge": total_knowledge,
        "platforms": [{"platform": r["platform"], "count": int(r["count"])} for r in platforms],
        "recent_knowledge": [dict(r) for r in recent],
        "daily_counts": [{"date": r["date"], "platform": r["platform"], "count": int(r["count"])} for r in daily],
        "recent_active": int(recent_active_row[0]["c"]) if recent_active_row else 0,
    }
