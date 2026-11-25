from typing import Optional

import aiosqlite
from fastapi import APIRouter, HTTPException

from app.services.wechat_articles import article_links
from app.core import database
from app.core.time_utils import add_time_filters

router = APIRouter()

# Explicit column list — never ship raw_data (full per-message sync JSON) or
# created_at to the client; the frontend Message type uses neither. source_id
# is kept: it's small and identifies a message across syncs.
_MSG_COLS = (
    "m.id, m.platform, m.chat_id, m.chat_name, m.chat_type, m.sender_id, "
    "m.sender_name, m.content, m.msg_type, m.timestamp, m.source_id"
)
MAX_PAGE = 500


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
    ids: Optional[str] = None,
    before_ts: Optional[str] = None,
    before_id: Optional[int] = None,
):
    conditions: list[str] = []
    params: list = []
    if (before_ts is None) != (before_id is None):
        raise HTTPException(400, "分页时间和消息 ID 必须同时提供")
    if before_ts is not None:
        conditions.append("(m.timestamp < ? OR (m.timestamp = ? AND m.id < ?))")
        params.extend([before_ts, before_ts, before_id])
    if ids is not None:
        parts = ids.split(",")
        if not parts or len(parts) > 100 or any(not p.isascii() or not p.isdigit() or len(p) > 18 for p in parts):
            raise HTTPException(400, "消息 ID 必须是最多 100 个逗号分隔的整数")
        selected = list(dict.fromkeys(int(p) for p in parts))
        conditions.append("m.id IN (" + ",".join("?" for _ in selected) + ")")
        params.extend(selected)

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
    limit = max(1, min(limit, MAX_PAGE))

    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        db.row_factory = aiosqlite.Row
        public_account = platform == "wechat" and bool(chat_id and chat_id.startswith("gh_"))
        extra = ", m.raw_data" if public_account else ""
        rows = await db.execute_fetchall(
            f"""SELECT {_MSG_COLS}{extra}, a.category, a.urgency, a.summary
                FROM messages m
                LEFT JOIN analysis_results a ON a.message_id = m.id
                {where}
                ORDER BY m.timestamp DESC, m.id DESC LIMIT ? OFFSET ?""",
            params + [limit, max(0, offset) if before_ts is None else 0],
        )

    messages = [dict(r) for r in rows]
    if public_account:
        for msg in messages:
            msg["article_links"] = article_links(msg["content"], msg.pop("raw_data"))
    return messages
