import uuid

import aiosqlite
from fastapi import APIRouter, HTTPException

from app.core import database
from app.core.config import load_config, save_config
from app.core.runners import run_tag_batch
from app.core.tasks import create_task, is_task_running, spawn_cancellable
from app.models.tags import (
    ContactTagAdd, LinkIdsRequest, TagBatchRequest, TagCreate,
    TagInsightRequest, TagUpdate, TagVipRequest,
)
from app.services.tagger import TaggerService, get_or_create_tag, persist_suggestions

router = APIRouter()


def _placeholders(n: int) -> str:
    return ",".join("?" * n)


# --- tag dictionary -------------------------------------------------------

@router.get("/api/tags")
async def list_tags(status: str | None = None):
    """Tag dictionary with per-tag usage counts (confirmed / suggested links)."""
    where = "WHERE t.status=?" if status else ""
    params = (status,) if status else ()
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            f"""SELECT t.id, t.name, t.color, t.source, t.status, t.created_at,
                       COUNT(CASE WHEN l.status='confirmed' THEN 1 END) AS confirmed_count,
                       COUNT(CASE WHEN l.status='suggested' THEN 1 END) AS suggested_count
                FROM contact_tags t
                LEFT JOIN contact_tag_links l ON l.tag_id = t.id
                {where}
                GROUP BY t.id
                ORDER BY t.status, confirmed_count DESC, t.name""",
            params,
        )
    return [dict(r) for r in rows]


@router.post("/api/tags")
async def create_tag(body: TagCreate):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "标签名不能为空")
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        try:
            cursor = await db.execute(
                "INSERT INTO contact_tags (name, color, source, status) VALUES (?,?,'preset','active')",
                (name, body.color),
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            raise HTTPException(409, f"标签「{name}」已存在")
    return {"id": cursor.lastrowid, "name": name}


# --- AI suggestion review (literal paths before /{tag_id}) ----------------

@router.post("/api/tags/suggest/batch")
async def suggest_batch(body: TagBatchRequest = TagBatchRequest()):
    if is_task_running("tag"):
        raise HTTPException(409, "打标签任务已在运行")
    task_id = create_task("tag")
    spawn_cancellable(task_id, run_tag_batch(
        task_id, body.include_groups, body.only_untagged, body.msg_limit, body.max_contacts,
    ))
    return {"task_id": task_id}


@router.get("/api/tags/suggestions")
async def list_suggestions():
    """Pending AI suggestions awaiting review, newest batch first."""
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            """SELECT l.id AS link_id, l.platform, l.chat_id, l.confidence, l.reason,
                      l.batch_id, l.created_at,
                      t.id AS tag_id, t.name AS tag_name, t.status AS tag_status,
                      (SELECT chat_name FROM messages m
                       WHERE m.platform=l.platform AND m.chat_id=l.chat_id
                       ORDER BY timestamp DESC LIMIT 1) AS contact_name
               FROM contact_tag_links l JOIN contact_tags t ON t.id = l.tag_id
               WHERE l.status='suggested'
               ORDER BY l.created_at DESC, l.platform, l.chat_id, l.confidence DESC"""
        )
    return [dict(r) for r in rows]


@router.post("/api/tags/confirm")
async def confirm_links(body: LinkIdsRequest):
    """Confirm selected suggested links and activate any pending tags they use."""
    if not body.link_ids:
        return {"confirmed": 0}
    ph = _placeholders(len(body.link_ids))
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        cursor = await db.execute(
            f"UPDATE contact_tag_links SET status='confirmed' WHERE id IN ({ph}) AND status='suggested'",
            body.link_ids,
        )
        await db.execute(
            f"""UPDATE contact_tags SET status='active'
                WHERE status='pending' AND id IN (
                    SELECT tag_id FROM contact_tag_links WHERE id IN ({ph})
                )""",
            body.link_ids,
        )
        await db.commit()
    return {"confirmed": cursor.rowcount}


@router.post("/api/tags/reject")
async def reject_links(body: LinkIdsRequest):
    """Delete selected suggested links and prune AI tags left with no links."""
    if not body.link_ids:
        return {"rejected": 0}
    ph = _placeholders(len(body.link_ids))
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        cursor = await db.execute(
            f"DELETE FROM contact_tag_links WHERE id IN ({ph}) AND status='suggested'",
            body.link_ids,
        )
        await db.execute(
            """DELETE FROM contact_tags
               WHERE status='pending' AND source='ai'
                 AND id NOT IN (SELECT DISTINCT tag_id FROM contact_tag_links)"""
        )
        await db.commit()
    return {"rejected": cursor.rowcount}


@router.patch("/api/tags/{tag_id}")
async def update_tag(tag_id: int, body: TagUpdate):
    updates: dict = {}
    if body.name is not None:
        updates["name"] = body.name.strip()
    if body.color is not None:
        updates["color"] = body.color
    if body.status is not None:
        if body.status not in ("active", "pending"):
            raise HTTPException(400, "status 只能是 active 或 pending")
        updates["status"] = body.status
    if not updates:
        raise HTTPException(400, "没有可更新的字段")
    set_clause = ", ".join(f"{k}=?" for k in updates)
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        try:
            await db.execute(f"UPDATE contact_tags SET {set_clause} WHERE id=?", [*updates.values(), tag_id])
            await db.commit()
        except aiosqlite.IntegrityError:
            raise HTTPException(409, "标签名重复")
    return {"ok": True}


@router.delete("/api/tags/{tag_id}")
async def delete_tag(tag_id: int):
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("DELETE FROM contact_tag_links WHERE tag_id=?", (tag_id,))
        await db.execute("DELETE FROM contact_tags WHERE id=?", (tag_id,))
        await db.commit()
    return {"ok": True}


@router.get("/api/tags/{tag_id}/contacts")
async def contacts_for_tag(tag_id: int):
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            """SELECT l.platform, l.chat_id, l.confidence, l.reason, l.source, l.status,
                      (SELECT chat_name FROM messages m
                       WHERE m.platform=l.platform AND m.chat_id=l.chat_id
                       ORDER BY timestamp DESC LIMIT 1) AS contact_name
               FROM contact_tag_links l
               WHERE l.tag_id=? AND l.status='confirmed'
               ORDER BY l.confidence DESC""",
            (tag_id,),
        )
    return [dict(r) for r in rows]


async def _tag_or_404(db: aiosqlite.Connection, tag_id: int) -> str:
    row = await (await db.execute("SELECT name FROM contact_tags WHERE id=?", (tag_id,))).fetchone()
    if not row:
        raise HTTPException(404, "标签不存在")
    return row[0]


async def _confirmed_contacts(db: aiosqlite.Connection, tag_id: int) -> list[dict]:
    """(platform, chat_id, name) of contacts confirmed under this tag."""
    rows = await db.execute_fetchall(
        """SELECT l.platform, l.chat_id,
                  (SELECT chat_name FROM messages m
                   WHERE m.platform=l.platform AND m.chat_id=l.chat_id
                   ORDER BY timestamp DESC LIMIT 1) AS name
           FROM contact_tag_links l
           WHERE l.tag_id=? AND l.status='confirmed'""",
        (tag_id,),
    )
    return [dict(r) for r in rows]


@router.post("/api/tags/{tag_id}/vip")
async def tag_to_vip(tag_id: int, body: TagVipRequest):
    """Sync this tag's confirmed contacts into vip_contacts (pre-filter
    whitelist). action='add' adds their names; 'remove' removes them."""
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        db.row_factory = aiosqlite.Row
        await _tag_or_404(db, tag_id)
        names = [c["name"] for c in await _confirmed_contacts(db, tag_id) if c["name"]]

    cfg = load_config()
    current = set(cfg.vip_contacts)
    if body.action == "remove":
        affected = sum(1 for n in names if n in current)
        cfg.vip_contacts = [v for v in cfg.vip_contacts if v not in set(names)]
    else:
        new = [n for n in names if n not in current]
        affected = len(new)
        cfg.vip_contacts.extend(new)
    save_config(cfg)
    return {"action": body.action, "affected": affected, "vip_count": len(cfg.vip_contacts)}


@router.post("/api/tags/{tag_id}/insight")
async def tag_insight(tag_id: int, body: TagInsightRequest = TagInsightRequest()):
    """LLM aggregate summary across all contacts sharing this tag. Synchronous;
    capped at max_contacts × msg_limit messages to keep the prompt bounded."""
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        db.row_factory = aiosqlite.Row
        tag_name = await _tag_or_404(db, tag_id)
        contacts = await _confirmed_contacts(db, tag_id)
        if not contacts:
            raise HTTPException(400, "该标签下还没有已确认的联系人")
        total = len(contacts)
        contacts = contacts[: body.max_contacts]
        payload = []
        for c in contacts:
            rows = await db.execute_fetchall(
                """SELECT sender_name, content, timestamp FROM messages
                   WHERE platform=? AND chat_id=? ORDER BY timestamp DESC LIMIT ?""",
                (c["platform"], c["chat_id"], body.msg_limit),
            )
            if rows:
                payload.append({"name": c["name"] or c["chat_id"], "messages": [dict(r) for r in reversed(rows)]})

    svc = TaggerService()
    insight = await svc.summarize_group(tag_name, payload)
    if not insight:
        raise HTTPException(502, f"LLM 未返回内容: {svc.last_llm_error or '未知错误'}")
    return {"insight": insight, "contact_count": len(payload), "truncated": total > len(contacts)}


# --- per-contact tags -----------------------------------------------------

@router.get("/api/contacts/tags")
async def all_contact_tags():
    """All confirmed (contact -> tag) links in one shot, so the contacts list can
    render tag chips without an N+1 fan-out."""
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            """SELECT l.platform, l.chat_id, t.id AS tag_id, t.name, t.color
               FROM contact_tag_links l JOIN contact_tags t ON t.id = l.tag_id
               WHERE l.status='confirmed'"""
        )
    return [dict(r) for r in rows]


@router.get("/api/contacts/{platform}/{chat_id}/tags")
async def contact_tags(platform: str, chat_id: str):
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            """SELECT l.id AS link_id, l.confidence, l.reason, l.source, l.status, l.batch_id,
                      t.id AS tag_id, t.name, t.color
               FROM contact_tag_links l JOIN contact_tags t ON t.id = l.tag_id
               WHERE l.platform=? AND l.chat_id=?
               ORDER BY l.status, l.confidence DESC""",
            (platform, chat_id),
        )
    return [dict(r) for r in rows]


@router.post("/api/contacts/{platform}/{chat_id}/tags/suggest")
async def suggest_for_contact(platform: str, chat_id: str, msg_limit: int = 100):
    """Synchronous single-contact AI tagging. Persists suggestions and returns them."""
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        db.row_factory = aiosqlite.Row
        active_tags = [
            r["name"] for r in await db.execute_fetchall(
                "SELECT name FROM contact_tags WHERE status='active' ORDER BY name"
            )
        ]
        rows = await db.execute_fetchall(
            """SELECT id, sender_name, content, timestamp FROM messages
               WHERE platform=? AND chat_id=? ORDER BY timestamp DESC LIMIT ?""",
            (platform, chat_id, msg_limit),
        )
        name_row = await db.execute_fetchall(
            """SELECT chat_name FROM messages WHERE platform=? AND chat_id=?
               ORDER BY timestamp DESC LIMIT 1""",
            (platform, chat_id),
        )
    if not rows:
        raise HTTPException(404, "联系人没有消息")
    msgs = [dict(r) for r in reversed(rows)]
    contact_name = name_row[0]["chat_name"] if name_row else ""

    svc = TaggerService()
    suggestions = await svc.suggest_tags(contact_name or "", msgs, active_tags)
    if not suggestions and svc.analyzer.llm_fail_count:
        raise HTTPException(502, f"LLM 未返回内容: {svc.last_llm_error or '未知错误'}")

    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        await db.execute("PRAGMA busy_timeout=30000")
        await persist_suggestions(db, platform, chat_id, suggestions, uuid.uuid4().hex[:8])
        await db.commit()
    return {"suggestions": suggestions}


@router.post("/api/contacts/{platform}/{chat_id}/tags")
async def add_contact_tag(platform: str, chat_id: str, body: ContactTagAdd):
    """Manually attach a tag to a contact (lands as confirmed)."""
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        if body.tag_id is not None:
            row = await (await db.execute("SELECT id FROM contact_tags WHERE id=?", (body.tag_id,))).fetchone()
            if not row:
                raise HTTPException(404, "标签不存在")
            tag_id = body.tag_id
        elif body.name and body.name.strip():
            tag_id = await get_or_create_tag(
                db, body.name.strip(), source="preset", status="active", color=body.color
            )
        else:
            raise HTTPException(400, "需要 tag_id 或 name")
        await db.execute(
            """INSERT INTO contact_tag_links (platform, chat_id, tag_id, source, status)
               VALUES (?,?,?,'manual','confirmed')
               ON CONFLICT(platform, chat_id, tag_id) DO UPDATE SET status='confirmed'""",
            (platform, chat_id, tag_id),
        )
        await db.commit()
    return {"ok": True, "tag_id": tag_id}


@router.delete("/api/contacts/{platform}/{chat_id}/tags/{tag_id}")
async def remove_contact_tag(platform: str, chat_id: str, tag_id: int):
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        await db.execute(
            "DELETE FROM contact_tag_links WHERE platform=? AND chat_id=? AND tag_id=?",
            (platform, chat_id, tag_id),
        )
        await db.commit()
    return {"ok": True}
