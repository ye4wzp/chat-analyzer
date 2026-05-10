import aiosqlite
from fastapi import APIRouter, HTTPException

from app.core import database

router = APIRouter()


@router.get("/api/chats")
async def get_chats():
    async with aiosqlite.connect(str(database.DB_PATH), timeout=30) as db:
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


def _hour_from_ts(ts: str) -> int | None:
    """Best-effort hour-of-day extraction tolerant of WeChat ISO-no-tz, Telegram
    ISO-with-tz, and QQ Unix epoch all coexisting in messages.timestamp."""
    if not ts:
        return None
    s = str(ts).strip()
    if s.isdigit():
        from datetime import datetime, timezone
        try:
            n = int(s)
            if n > 10**12:
                n //= 1000
            return datetime.fromtimestamp(n, tz=timezone.utc).astimezone().hour
        except (ValueError, OSError, OverflowError):
            return None
    # ISO format: HH always at chars 11-13 e.g. "2026-04-10T17:07:07"
    if len(s) >= 13 and s[10] in ("T", " "):
        try:
            return int(s[11:13])
        except ValueError:
            return None
    return None


@router.get("/api/chats/{platform}/{chat_id}/profile")
async def chat_profile(platform: str, chat_id: str):
    """Aggregate stats + cached LLM summary for one chat.

    Caller can render: hourly activity sparkline, top senders, urgency mix,
    knowledge items sourced from this chat, and the LLM-generated 1-paragraph
    description (if previously cached via POST /summarize)."""
    async with aiosqlite.connect(str(database.DB_PATH), timeout=30) as db:
        db.row_factory = aiosqlite.Row

        meta_rows = await db.execute_fetchall(
            """SELECT platform, chat_id, chat_name, chat_type,
                      COUNT(*) AS msg_count,
                      MIN(timestamp) AS earliest,
                      MAX(timestamp) AS latest,
                      COUNT(DISTINCT sender_id) AS distinct_senders
               FROM messages WHERE platform=? AND chat_id=?""",
            (platform, chat_id),
        )
        if not meta_rows or meta_rows[0]["msg_count"] == 0:
            raise HTTPException(404, "聊天不存在或无消息")
        meta = dict(meta_rows[0])
        chat_name = meta["chat_name"] or ""

        # Top senders by message count.
        sender_rows = await db.execute_fetchall(
            """SELECT COALESCE(sender_name, sender_id, '?') AS name, COUNT(*) AS n
               FROM messages WHERE platform=? AND chat_id=?
               GROUP BY sender_name, sender_id ORDER BY n DESC LIMIT 5""",
            (platform, chat_id),
        )

        # Urgency distribution from analysis_results joined back to messages.
        urgency_rows = await db.execute_fetchall(
            """SELECT a.urgency AS urgency, COUNT(*) AS n
               FROM analysis_results a JOIN messages m ON m.id = a.message_id
               WHERE m.platform=? AND m.chat_id=? AND a.urgency IS NOT NULL
               GROUP BY a.urgency ORDER BY a.urgency""",
            (platform, chat_id),
        )

        # Knowledge items sourced from this chat (matched by chat_name since
        # that's what the analyzer stores).
        knowledge_rows = await db.execute_fetchall(
            """SELECT id, title, content, tags, created_at
               FROM knowledge_items WHERE source_chat=?
               ORDER BY created_at DESC LIMIT 50""",
            (chat_name,),
        )

        # Hourly histogram. Pull only the timestamp column; hour-extract in
        # Python so we don't need to teach SQLite three timestamp formats.
        ts_rows = await db.execute_fetchall(
            "SELECT timestamp FROM messages WHERE platform=? AND chat_id=?",
            (platform, chat_id),
        )
        hours = [0] * 24
        for r in ts_rows:
            h = _hour_from_ts(r["timestamp"])
            if h is not None and 0 <= h < 24:
                hours[h] += 1

        # Cached LLM summary, if any.
        prof_rows = await db.execute_fetchall(
            "SELECT summary, summary_generated_at FROM chat_profiles WHERE platform=? AND chat_id=?",
            (platform, chat_id),
        )
        summary = None
        summary_generated_at = None
        if prof_rows:
            summary = prof_rows[0]["summary"]
            summary_generated_at = prof_rows[0]["summary_generated_at"]

    return {
        "platform": meta["platform"],
        "chat_id": meta["chat_id"],
        "chat_name": chat_name,
        "chat_type": meta["chat_type"],
        "msg_count": int(meta["msg_count"]),
        "distinct_senders": int(meta["distinct_senders"]),
        "earliest": meta["earliest"],
        "latest": meta["latest"],
        "hours": hours,
        "top_senders": [{"name": r["name"], "count": int(r["n"])} for r in sender_rows],
        "urgency_dist": [{"urgency": int(r["urgency"]), "count": int(r["n"])} for r in urgency_rows],
        "knowledge": [dict(r) for r in knowledge_rows],
        "summary": summary,
        "summary_generated_at": summary_generated_at,
    }


@router.post("/api/chats/{platform}/{chat_id}/profile/summarize")
async def summarize_chat(platform: str, chat_id: str):
    """Generate (or refresh) the LLM summary for a chat. Synchronous — small
    enough that we don't need a task; the call returns the new summary directly.
    For very large chats we cap the input at the most recent 100 messages."""
    from app.services.analyzer import AnalyzerService

    async with aiosqlite.connect(str(database.DB_PATH), timeout=30) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            """SELECT id, sender_name, content, timestamp
               FROM messages WHERE platform=? AND chat_id=?
               ORDER BY timestamp DESC LIMIT 100""",
            (platform, chat_id),
        )
    if not rows:
        raise HTTPException(404, "聊天没有消息")
    msgs = [dict(r) for r in reversed(rows)]  # chronological for the LLM

    svc = AnalyzerService()
    summary = await svc._summarize(msgs)
    if not summary:
        raise HTTPException(502, f"LLM 未返回内容: {svc.last_llm_error or '未知错误'}")

    async with aiosqlite.connect(str(database.DB_PATH), timeout=30) as db:
        await db.execute(
            """INSERT INTO chat_profiles (platform, chat_id, summary, summary_generated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(platform, chat_id) DO UPDATE SET
                 summary=excluded.summary,
                 summary_generated_at=excluded.summary_generated_at""",
            (platform, chat_id, summary),
        )
        await db.commit()

    return {"summary": summary}
