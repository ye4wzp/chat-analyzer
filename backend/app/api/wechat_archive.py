from typing import Literal

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core import database
from app.services.wechat_archive import PUBLIC, UNSCANNED

router = APIRouter(prefix="/api/wechat-archive")


class AccountAction(BaseModel):
    chat_ids: list[str] = Field(min_length=1, max_length=1000)
    action: Literal["enable", "pause", "retry"]


@router.post("")
async def update_accounts(request: AccountAction):
    ids = list(dict.fromkeys(request.chat_ids))
    if any(not cid.startswith("gh_") for cid in ids):
        raise HTTPException(400, "只能选择微信的公众号")
    async with aiosqlite.connect(database.DB_PATH, timeout=30) as db:
        await db.execute("BEGIN IMMEDIATE")
        for cid in ids:
            if request.action == "enable":
                if not await db.execute_fetchall(f"SELECT 1 FROM messages WHERE {PUBLIC} AND chat_id=? LIMIT 1", (cid,)):
                    raise HTTPException(404, "所选公众号中有会话没有本地推送，请刷新列表")
                await db.execute("""INSERT INTO wechat_archive_accounts(chat_id) VALUES(?)
                    ON CONFLICT(chat_id) DO UPDATE SET enabled=1""", (cid,))
            elif request.action == "pause":
                await db.execute("UPDATE wechat_archive_accounts SET enabled=0 WHERE chat_id=?", (cid,))
            else:
                await db.execute("""UPDATE wechat_archive_items SET error=NULL WHERE chat_id=?
                    AND chat_id IN (SELECT chat_id FROM wechat_archive_accounts WHERE enabled=1)
                    AND error IS NOT NULL AND url NOT IN (SELECT url FROM wechat_articles)""", (cid,))
        await db.commit()
    return {"accounts": len(ids)}


@router.get("")
async def archive_status():
    async with aiosqlite.connect(database.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN")
        accounts = await db.execute_fetchall(f"""
            SELECT a.chat_id,a.enabled,{UNSCANNED} AS scanning,
                COUNT(s.url) AS saved,
                COUNT(CASE WHEN i.url IS NOT NULL AND s.url IS NULL AND i.error IS NULL THEN 1 END) AS pending,
                COUNT(CASE WHEN s.url IS NULL AND i.error IS NOT NULL THEN 1 END) AS failed
            FROM wechat_archive_accounts a LEFT JOIN wechat_archive_items i USING(chat_id)
            LEFT JOIN wechat_articles s ON s.url=i.url GROUP BY a.chat_id ORDER BY a.rowid DESC""")
        errors = await db.execute_fetchall("""SELECT i.chat_id,i.error,COUNT(*) AS n FROM wechat_archive_items i
            WHERE i.error IS NOT NULL AND NOT EXISTS (SELECT 1 FROM wechat_articles s WHERE s.url=i.url)
            GROUP BY i.chat_id,i.error ORDER BY n DESC""")
        cooldown = await db.execute_fetchall("SELECT MAX(cooldown_until) FROM wechat_archive_items WHERE cooldown_until>unixepoch()")
        by_chat = {}
        for cid, reason, count in errors:
            by_chat.setdefault(cid, []).append({"reason": reason, "count": count})
        return {"accounts": [{**dict(a), "errors": by_chat.get(a["chat_id"], [])} for a in accounts],
                "cooldown_until": cooldown[0][0]}
