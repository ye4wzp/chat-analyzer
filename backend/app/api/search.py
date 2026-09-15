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
    kw = keyword.strip()
    limit = max(1, min(limit, 500))
    # Trigram FTS needs >= 3 chars; shorter queries (common for 2-char Chinese
    # words) and FTS-less builds fall back to a LIKE scan.
    use_fts = database.FTS_ENABLED and len(kw) >= 3

    def build(fts: bool) -> tuple[str, list]:
        if fts:
            conditions = ["m.id IN (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?)"]
            params: list = ['"' + kw.replace('"', '""') + '"']
        else:
            conditions = ["m.content LIKE ?"]
            params = [f"%{kw}%"]
        if platform:
            conditions.append("m.platform = ?")
            params.append(platform)
        if category:
            conditions.append("a.category = ?")
            params.append(category)
        add_time_filters(conditions, params, "m.timestamp", since, until)
        return " AND ".join(conditions), params

    # Explicit columns — never ship raw_data (full sync JSON) to the client.
    sql_tpl = """SELECT m.id, m.platform, m.chat_id, m.chat_name, m.chat_type,
                        m.sender_id, m.sender_name, m.content, m.msg_type, m.timestamp, m.source_id,
                        a.category, a.urgency, a.summary
                 FROM messages m
                 LEFT JOIN analysis_results a ON a.message_id = m.id
                 WHERE {where}
                 ORDER BY m.timestamp DESC LIMIT ? OFFSET ?"""

    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        db.row_factory = aiosqlite.Row
        where, params = build(use_fts)
        try:
            rows = await db.execute_fetchall(sql_tpl.format(where=where), params + [limit, offset])
        except aiosqlite.OperationalError:
            if not use_fts:
                raise
            where, params = build(False)
            rows = await db.execute_fetchall(sql_tpl.format(where=where), params + [limit, offset])

    return [dict(r) for r in rows]
