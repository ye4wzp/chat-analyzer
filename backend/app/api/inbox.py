"""Triggers / inbox API.

The Layout polls /api/inbox/unread_count for the bell-icon badge. The Inbox
page lists unread + recent items with the original message content joined in,
so the user can read context without leaving the page.
"""
import aiosqlite
from fastapi import APIRouter, HTTPException

from app.core import database

router = APIRouter()


@router.get("/api/inbox/unread_count")
async def unread_count():
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        rows = await db.execute_fetchall("SELECT COUNT(*) AS n FROM keyword_triggers WHERE read = 0")
        return {"count": int(rows[0][0]) if rows else 0}


@router.get("/api/inbox")
async def list_triggers(
    only_unread: bool = False,
    keyword: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    cond: list[str] = []
    params: list = []
    if only_unread:
        cond.append("t.read = 0")
    if keyword:
        cond.append("t.keyword = ?")
        params.append(keyword)
    where = ("WHERE " + " AND ".join(cond)) if cond else ""

    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            f"""SELECT t.id, t.keyword, t.message_id, t.matched_at, t.read,
                       m.platform, m.chat_id, m.chat_name, m.sender_name,
                       m.content, m.timestamp
                FROM keyword_triggers t
                LEFT JOIN messages m ON m.id = t.message_id
                {where}
                ORDER BY t.matched_at DESC
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        )
    return [dict(r) for r in rows]


@router.post("/api/inbox/{trigger_id}/read")
async def mark_read(trigger_id: int):
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        cur = await db.execute("UPDATE keyword_triggers SET read = 1 WHERE id = ?", (trigger_id,))
        await db.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "触发记录不存在")
    return {"ok": True}


@router.post("/api/inbox/read_all")
async def mark_all_read():
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        await db.execute("UPDATE keyword_triggers SET read = 1 WHERE read = 0")
        await db.commit()
    return {"ok": True}


@router.post("/api/inbox/scan")
async def trigger_scan():
    """Manually run a keyword scan now. Useful after editing the keyword list
    if the user doesn't want to wait for the next sync."""
    from app.services.triggers import scan_for_matches
    n = await scan_for_matches()
    return {"new_triggers": n}


@router.post("/api/inbox/rescan/{keyword}")
async def rescan(keyword: str):
    """Drop existing triggers for `keyword` and rescan from history. Use after
    refining a keyword that previously matched too much/too little."""
    from app.services.triggers import rescan_keyword
    n = await rescan_keyword(keyword)
    return {"new_triggers": n}
